# -*- coding: utf-8 -*-
import json
import queue
import threading
import uuid
from dataclasses import dataclass
from tornado.httputil import HTTPServerRequest
from tornado.web import RequestHandler, Application
from tornado.websocket import WebSocketHandler

from octopus.robot import log, config
from octopus.robot.compt import ThreadManager

logger = log.getLogger(__name__)

ACTION_USER_SPEAK = "user_speak"
ACTION_ROBOT_LISTEN = "robot_listen"
ACTION_ROBOT_THINK = "robot_think"
ACTION_ROBOT_WRITE = "robot_write"
ACTION_ROBOT_SPEAK = "robot_speak"
ACTION_ROBOT_SLEEP = "robot_sleep"

STAGE_UNDERSTAND = "理解您说的内容"
STAGE_SEARCH = "查找相关资料"


@dataclass
class StatusData:
    stage: str
    end: bool = False

    def dict(self):
        return {"stage": self.stage, "end": self.end}


class ExtWebSocketHandler(WebSocketHandler, RequestHandler):
    clients = set()

    def initialize(self, **kwargs):
        pass

    def __init__(
        self, application: Application, request: HTTPServerRequest, **kwargs
    ) -> None:
        super().__init__(application, request, **kwargs)
        self.lock = threading.Lock()

    def isValidated(self):
        if not self.get_secure_cookie("validation"):
            return False
        return str(
            object=self.get_secure_cookie("validation"), encoding="utf-8"
        ) == config.get("/server/validate", "")

    def validate(self, validation):
        if validation and '"' in validation:
            validation = validation.replace('"', "")
        return validation == config.get("/server/validate", "") or validation == str(
            object=self.get_cookie("validation")
        )

    def open(self):
        self.clients.add(self)

    def on_close(self):
        self.clients.remove(self)

    def send_response(
        self,
        resp_uuid,
        action: str = None,
        data=None,
        message: str = None,
        plugin="",
        user_id=None,
        t=None,
    ):
        resp = {
            "type": 1 if t is None else t,  # 机器人回复
            "action": action or "new_message",
            "data": data,
            "text": message,
            "uuid": resp_uuid,
            "plugin": plugin,
            "user_id": user_id,
        }
        with self.lock:
            self.write_message(json.dumps(resp))


class WebSocketSender:

    def __init__(self):
        self.clients = ExtWebSocketHandler.clients
        self.running = threading.Event()
        self.queue_msg = queue.Queue()
        self.queue_lock = threading.Lock()
        self.thread = None

    def send_message(
        self,
        action: str,
        data: dict = None,
        message: str = None,
        resp_uuid=None,
        **kwargs
    ):
        logger.info("机器人状态：%s", message)
        resp_uuid = resp_uuid or uuid.uuid4().hex
        for _, client in enumerate(self.clients):
            client.send_response(
                resp_uuid=resp_uuid, action=action, data=data, message=message, **kwargs
            )

    def put_message(
        self,
        action: str,
        data: dict = None,
        message: str = None,
        resp_uuid=None,
        **kwargs
    ):
        self.queue_msg.put(
            item=dict(
                action=action, data=data, message=message, resp_uuid=resp_uuid, **kwargs
            ),
            timeout=3,
        )

    def clear_message(self):
        with self.queue_lock:
            while not self.queue_msg.empty():
                self.queue_msg.get()
                self.queue_msg.task_done()

    def run(self):
        while self.running.is_set():
            data = self.queue_msg.get()
            if data:
                try:
                    self.send_message(**data)
                except:
                    logger.critical("websocket send message err.", exc_info=True)
            self.queue_msg.task_done()

    def start(self):
        self.running.set()
        self.thread = ThreadManager.new(target=self.run)
        self.thread.start()

    def stop(self):
        self.running.clear()
        self.queue_msg.put(None)
        self.thread and self.thread.join()
