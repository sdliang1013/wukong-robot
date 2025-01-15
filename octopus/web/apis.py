# -*- coding: utf-8 -*-
import hashlib
import uuid

from octopus.robot import config, log
from octopus.robot.Sender import ACTION_USER_SPEAK
from octopus.robot.enums import AssistantEvent, AssistantStatus
from octopus.schemas.core import Response
from octopus.web.core import BaseHandler, ApiBaseHandler
from octopus.srv.navigation import FaqService

logger = log.getLogger(__name__)


class ExtLoginHandler(BaseHandler):
    """登录"""

    def get(self):
        if self.valid_to_json():
            self.write("已登录")

    def post(self):
        data = self.get_body_json()
        pwd_sign = hashlib.md5(data.get("password").encode("utf-8")).hexdigest()
        if data.get("username") == config.get(
            item="/server/username"
        ) and pwd_sign == config.get(item="/server/validate"):
            logger.info("login success")
            self.set_secure_cookie("validation", config.get("/server/validate"))
        else:
            self.set_status(status_code=401, reason="账号或密码错误.")
            self.write("账号或密码错误")
        self.finish()


class ExtLogoutHandler(BaseHandler):
    """登出"""

    def get(self):
        if self.is_validated():
            self.set_secure_cookie("validation", "")
        self.write("已登出")


class DigitalHumanHandler(BaseHandler):
    """数字人接口"""

    def initialize(self, octopus=None, **kwargs):
        super(DigitalHumanHandler, self).initialize(**kwargs)
        self.dh = octopus.conversation.speaker.dh

    def get(self, action: str):
        if not self.valid_to_login():
            return
        if "session-list" == action:
            self.get_session_list()
        elif "session-status" == action:
            self.get_session_status()
        elif "play-info" == action:
            self.get_play_info()
        else:
            self.send_error(status_code=404)

    def post(self, action: str):
        if not self.valid_to_json():
            return
        if "session-create" == action:
            self.create_session()
        elif "session-open" == action:
            self.open_session()
        elif "session-close" == action:
            self.close_session()
        elif "create-cmd" == action:
            self.create_cmd()
        else:
            self.send_error(status_code=404)

    def get_session_list(self):
        func = self.get_func("ListSessionOfProjectId")
        if func:
            self.response(Response.ok(data=func()))

    def get_session_status(self):
        func = self.get_func("get_session_status")
        if func:
            self.response(Response.ok(data=func()))

    def get_play_info(self):
        self.response(Response.ok(data=self.info(dh=self.dh)))

    def create_session(self):
        func = self.get_func("CreateSession")
        if func:
            self.response(Response.ok(data=func()))

    def open_session(self):
        func = self.get_func("StartSession")
        if func:
            self.response(Response.ok(data=func()))

    def close_session(self):
        func = self.get_func("closeAllSession")
        if func:
            self.response(Response.ok(data=func()))

    def create_cmd(self):
        func = self.get_func("CreateCmdChannel")
        if func:
            self.response(Response.ok(data=func()))

    def get_func(self, name: str):
        func = self.dh and getattr(self.dh, name, None)
        if not func:
            self.response(Response.error(message=f"找不到方法: {name}"))
        return func

    @classmethod
    def info(cls, dh):
        data = dh and dh.info()
        data = data or dict(session_id=None, play_stream_addr=None, cmd_status=False)
        detect_engine = config.get("/detector", "porcupine")
        data.update(
            wakeup_words=config.get(f"/{detect_engine}/keywords", ["你好", "小惠"]),
            greeting=config.get("/dh_engine/greeting", "我是AI数字人，直接向我提问吧"),
            greeting_questions=config.get("/dh_engine/greeting_questions"),
            tip_sleep=config.get("/dh_engine/tip_sleep", "请说出“你好”来唤醒我吧!"),
            tip_listen=config.get("/dh_engine/tip_listen", "正在聆听..."),
            video_speak=config.get("/dh_engine/video_speak", ""),
            video_silent=config.get("/dh_engine/video_silent", ""),
            enable=config.get("/dh_engine/enable", False),
        )
        return data


class DetectorHandler(BaseHandler):
    """关键字检测接口"""

    def post(self, action: str):
        if not self.valid_to_json():
            return
        if "log-on" == action:
            self.start_log()
        elif "log-off" == action:
            self.stop_log()
        else:
            self.send_error(status_code=404)

    def start_log(self):
        self.octopus.robot.open_log()
        self.response(Response.ok())

    def stop_log(self):
        self.octopus.robot.close_log()
        self.response(Response.ok())


class HandControlHandler(BaseHandler):
    """交互过程的一些手工操作接口"""

    def initialize(self, **kwargs):
        super(HandControlHandler, self).initialize(**kwargs)
        self.interrupt_time = config.get("/realtime/interrupt_time", 1000) / 1000

    def get(self, action: str):
        if not self.valid_to_json():
            return
        if "robot-status" == action:
            self.get_robot_status()
        else:
            self.send_error(status_code=404)

    def post(self, action: str):
        if not self.valid_to_json():
            return
        if "wakeup" == action:
            self.wakeup_robot()
        elif "interrupt" == action:
            self.interrupt_robot()
        elif "interrupt-wakeup" == action:
            self.interrupt_and_wakeup()
        elif "sleep" == action:
            self.sleep_robot()
        elif "commit-query" == action:
            self.commit_query()
        else:
            self.send_error(status_code=404)

    def get_robot_status(self):
        # todo: 获取机器人状态
        ...

    def wakeup_robot(self):
        # 手工唤醒
        self.octopus.robot.action(AssistantEvent.CTRL_WAKEUP, manual=True)
        self.response(Response.ok())

    def interrupt_robot(self):
        # 打断说话
        self.octopus.robot.action(AssistantEvent.CTRL_STOP_RESP, manual=True)
        self.response(Response.ok())

    def interrupt_and_wakeup(self):
        # 重新提问
        self.octopus.robot.action(
            AssistantEvent.CTRL_ASK_AGAIN,
            interrupt_time=self.interrupt_time,
            manual=True,
        )
        self.response(Response.ok())

    def sleep_robot(self):
        # 打断说话
        if not self.octopus.robot.status() == AssistantStatus.DEFAULT:
            self.octopus.robot.action(AssistantEvent.CTRL_STOP_RESP, manual=True)
        # 反馈休眠
        if self.octopus and hasattr(self.octopus, "life_cycle_event"):
            self.octopus.life_cycle_event.fire_event(event="sleep")
        self.response(Response.ok())

    def commit_query(self):
        """直接提交"""
        self.octopus.robot.action(AssistantEvent.CTRL_COMMIT_LISTEN)
        self.response(Response.ok())


class TuningControlHandler(BaseHandler):
    """手工操作接口"""

    def get(self, action: str):
        self.send_error(status_code=404)

    def post(self, action: str):
        if not self.valid_to_json():
            return
        if "feedback" == action:
            self.feedback()
        else:
            self.send_error(status_code=404)

    def feedback(self):
        # 反馈
        data = self.get_body_json()
        data_id = data.get("data_id")
        useful = data.get("useful", False)
        if self.octopus and hasattr(self.octopus, "conversation"):
            resp = self.octopus.conversation.feedback(
                chat_id=data_id, data_id=data_id, useful=useful
            )
            self.response(Response.ok(data=resp))


class ChatApiHandler(ApiBaseHandler):
    """聊天API接口"""

    def post_send_query(self) -> Response:
        """手动提交查询"""
        data = self.get_body_json()
        query = data.get("query", None)
        req_uuid = data.get("uuid", uuid.uuid4().hex)
        if not query:
            return Response.error(code=1, message="query text is empty")
        else:
            self.octopus.sender.put_message(
                action=ACTION_USER_SPEAK, data={"end": True}, message=query, t=0
            )
            # 另起线程执行
            self.octopus.robot.action(
                AssistantEvent.CTRL_QUERY, query=query, req_uuid=req_uuid
            )
            return Response.ok()

class NavigationHandler(ApiBaseHandler):

    def get_faq_list(self) -> list:
        """问题列表"""

        return faq_service.faq_all()


# ------------------- init service ---------------------
faq_service = FaqService(file=config.get("/navigation/faq", "faq.list"))
