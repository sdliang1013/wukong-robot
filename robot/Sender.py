# -*- coding: utf-8 -*-
from dataclasses import dataclass
import json
import uuid

from robot import logging, config
from tornado.websocket import WebSocketHandler
from tornado.web import RequestHandler


logger = logging.getLogger(__name__)

ACTION_USER_SPEAK = "user_speak"
ACTION_ROBOT_WEAKUP = "robot_weakup"
ACTION_ROBOT_LISTEN = "robot_listen"
ACTION_ROBOT_THINK = "robot_think"
ACTION_ROBOT_SPEAK = "robot_speak"

STAGE_UNDERSTAND = "理解您说的内容"
STAGE_SEARCH = "查找相关资料"


@dataclass
class StatusData:
    stage: str
    status: str

    def dict(self):
        return {"stage": self.stage, "status": self.status}


class ExtWebSocketHandler(WebSocketHandler, RequestHandler):
    clients = set()

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
        logger.info(f"ExtWebSocket Add: {self}, Count: {len(self.clients)}")

    def on_close(self):
        self.clients.remove(self)
        logger.info(f"ExtWebSocket Remove: {self}, Count: {len(self.clients)}")

    def send_response(
        self, uuid, action: str = None, data=None, message: str = None, plugin=""
    ):
        resp = {
            "action": action or "new_message",
            "data": data,
            "text": message,
            "type": 1,
            "uuid": uuid,
            "plugin": plugin,
        }
        self.write_message(json.dumps(resp))


class WebSocketSender:

    def __init__(self):
        self.clients = ExtWebSocketHandler.clients

    def send_message(self, action: str, data=None, message: str = None):
        if isinstance(data, StatusData):
            data = data.dict()
        resp_uuid = uuid.uuid4().hex
        for client in self.clients:
            client.send_response(
                uuid=resp_uuid, action=action, data=data, message=message
            )
