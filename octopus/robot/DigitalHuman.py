import base64
import hashlib
import hmac
import json
import random
import requests
import threading
import time
import uuid
import websocket
from abc import ABCMeta, abstractmethod
from urllib.parse import quote

from octopus.robot import config, log, schedulers
from octopus.robot.compt import ThreadManager

# 通用播报：
# 2hands_forward1
# left_side1
# right_side1

# 入退场：
# waving_hand

# 指引播报：
# emphasize
# left_up1
# right_up1

# 情绪反应：
# compliment_state
# support
# action_type = ["2hands_forward1", "left_side1", "right_side1", "waving_hand", "emphasize", "left_up1", "right_up1", "compliment_state", "support"]
action_type = ["2hands_forward1", "left_side1", "right_side1", "left_up1", "right_up1"]

logger = log.getLogger(__name__)


class AbstractDigitalHuman(object):
    """
    Generic parent class for all DigitalHuman engines
    """

    __metaclass__ = ABCMeta

    # @abstractmethod
    # def init(self):
    #     pass

    @classmethod
    def get_config(cls):
        return {}

    @classmethod
    def get_instance(cls):
        profile = cls.get_config()
        instance = cls(**profile)
        return instance

    @abstractmethod
    def speak(self, reqId, text, seq, isFinal):
        pass

    @abstractmethod
    def interrupt(self, req_id=None):
        pass

    @abstractmethod
    def info(self) -> dict:
        return {}


class TecentDigitalHuman(AbstractDigitalHuman):
    """
    腾讯云智能数智人: https://cloud.tencent.com/product/ivh
    """

    SLUG = "tencent-dh"

    def __init__(
        self, base_url, wss_url, access_token, app_key, project_id, user_id, **kwargs
    ):
        super(self.__class__, self).__init__()
        self.base_url = base_url
        self.wss_url = wss_url
        self.access_token = access_token
        self.app_key = app_key
        self.project_id = project_id
        self.user_id = user_id
        self.http_req = requests.session()
        self.session_id = None
        self.session_status = None  # 1：进行中，2：已关闭，3：准备中，4：建流失败
        self.session_started = False  # 会话是否开启
        self.play_stream_addr = None
        self.ws_cmd = None
        self.thread_ws_cmd = None  # 指令长连接线程
        self.ws_cmd_ok = threading.Event()
        self.sche_heart = None  # 心跳任务
        # 准备就绪
        self.be_ready()

    @classmethod
    def get_config(cls):
        # Try to get ali_yuyin config from config
        return config.get("tencent-dh", {})

    def info(self) -> dict:
        return dict(
            session_id=self.session_id,
            play_stream_addr=self.play_stream_addr,
            cmd_status=self.ws_cmd_ok.is_set(),
        )

    def CreateSession(self):
        """
        1. 新建直播流会话
        """
        url = f"{self.base_url}/createsession"
        # header = {}
        params = {"appkey": self.app_key, "timestamp": int(time.time())}
        data = {
            # "Header": {
            # },
            "Payload": {
                "ReqId": uuid.uuid4().hex,
                "VirtualmanProjectId": self.project_id,
                "UserId": self.user_id,
                "Protocol": "webrtc",
                "DriverType": 1,
            }
        }
        try:
            response = self.http_req.post(
                url=self.GenReqURL(url, params),
                #  headers=header,
                json=data,
            )
            if response.status_code == 200:
                responseData = response.json()
                logger.info("CreateSession: %s", responseData)
                code = responseData["Header"]["Code"]
                if code == 0:
                    self.session_id = responseData["Payload"]["SessionId"]
                    self.session_status = responseData["Payload"]["SessionStatus"]
                    self.play_stream_addr = responseData["Payload"]["PlayStreamAddr"]
                else:
                    logger.error(f"新建直播流会话失败，response: {responseData}")
                logger.info(
                    "SessionStatus: %s,PlayStreamAddr: %s",
                    self.session_status,
                    self.play_stream_addr,
                )
                return responseData
            else:
                logger.error(f"{self.SLUG} 新建直播流会话出错了: {response.text}")
                return response.text
        except Exception as e:
            logger.critical("新建直播流会话失败：%s", str(e), exc_info=True)

    def ListSessionOfProjectId(self):
        """
        2. 查询数智人项目下的会话列表
        """
        url = f"{self.base_url}/listsessionofprojectid"
        params = {"appkey": self.app_key, "timestamp": int(time.time())}
        data = {
            "Payload": {
                "ReqId": uuid.uuid4().hex,
                "VirtualmanProjectId": self.project_id,
            }
        }
        try:
            response = self.http_req.post(url=self.GenReqURL(url, params), json=data)
            if response.status_code == 200:
                responseData = response.json()
                logger.info("ListSessionOfProjectId: %s", responseData)
                code = responseData["Header"]["Code"]
                if code != 0:
                    logger.error(
                        f"查询数智人项目下的会话列表失败，response: {response}"
                    )
                return responseData["Payload"]["Sessions"]
            else:
                logger.error(
                    f"{self.SLUG} 查询数智人项目下的会话列表出错了: {response.text}"
                )
                return response.text
        except Exception as e:
            logger.critical("查询数智人项目下的会话列表失败：%s", str(e), exc_info=True)

    def get_session_status(self, session_id=None):
        """
        2. 查询会话状态
        """
        url = f"{self.base_url}/statsession"
        params = {"appkey": self.app_key, "timestamp": int(time.time())}
        data = {
            "Payload": {
                "ReqId": uuid.uuid4().hex,
                "SessionId": session_id or self.session_id,
            }
        }
        try:
            response = self.http_req.post(url=self.GenReqURL(url, params), json=data)
            if response.status_code == 200:
                responseData = response.json()
                logger.info("SessionStatus: %s", responseData)
                code = responseData["Header"]["Code"]
                if code == 0:
                    self.session_status = responseData["Payload"]["SessionStatus"]
                    self.session_started = responseData["Payload"]["IsSessionStarted"]
                    self.play_stream_addr = responseData["Payload"]["PlayStreamAddr"]
                else:
                    logger.error(f"查询数智人会话状态失败，response: {response}")
                return responseData
            else:
                logger.error(f"{self.SLUG} 查询数智人会话状态出错了: {response.text}")
                return response.text
        except Exception as e:
            logger.critical("查询数智人会话状态失败：%s", str(e), exc_info=True)

    def StartSession(self):
        """
        3. 开启会话
        """
        url = f"{self.base_url}/startsession"
        params = {"appkey": self.app_key, "timestamp": int(time.time())}
        data = {"Payload": {"ReqId": uuid.uuid4().hex, "SessionId": self.session_id}}
        try:
            response = self.http_req.post(url=self.GenReqURL(url, params), json=data)
            if response.status_code == 200:
                responseData = response.json()
                logger.info(f"StartSession: {responseData}")
                code = responseData["Header"]["Code"]
                self.session_started = code == 0
                if code != 0:
                    logger.error(f"开启会话失败失败，response: {response}")
                return responseData
            else:
                logger.error(f"{self.SLUG} 开启会话失败出错了: {response.text}")
                return response.text
        except Exception as e:
            logger.critical("开启会话失败：%s", str(e), exc_info=True)

    def CloseSession(self, session_id=None):
        """
        4. 关闭会话
        """
        url = f"{self.base_url}/closesession"
        params = {"appkey": self.app_key, "timestamp": int(time.time())}
        data = {
            "Payload": {
                "ReqId": uuid.uuid4().hex,
                "SessionId": session_id or self.session_id,
            }
        }
        try:
            response = self.http_req.post(url=self.GenReqURL(url, params), json=data)
            if response.status_code == 200:
                responseData = response.json()
                logger.info(f"CloseSession: {responseData}")
                code = responseData["Header"]["Code"]
                if code == 0:
                    self.session_id = None
                    self.session_started = False
                    self.session_status = 2
                else:
                    logger.error(f"关闭会话失败，response: {response}")
                return responseData
            else:
                logger.error(f"{self.SLUG} 关闭会话失败出错了: {response.text}")
                return response.text
        except Exception as e:
            logger.critical("关闭会话失败：%s", str(e), exc_info=True)

    def CreateCmdChannel(self):
        """
        5. 创建长链接通道(流式)
        """
        # 启动长连接
        self.thread_ws_cmd = ThreadManager.new(target=self.cmd_run_forever)
        self.thread_ws_cmd.start()

    def send_cmd(self, cmd, data, req_id=None):
        try:
            payload = {
                "Payload": {
                    "ReqId": req_id or uuid.uuid4().hex,
                    "SessionId": self.session_id,
                    "Command": cmd,
                    "Data": data,
                }
            }
            if self.ws_cmd:
                self.ws_cmd.send(json.dumps(payload))
            else:
                logger.error("直播流会话未准备好.")
        except Exception as e:
            logger.critical("发送命令失败：%s", str(e), exc_info=True)

    def speak(self, reqId, text, seq, isFinal):
        data = {
            # "Text": text and f"<insert-action type=\"{random.choice(seq=action_type)}\"/> {text}",
            "Text": text,
            "Seq": seq,
            "IsFinal": isFinal,
            "IsSentence": True,
            # "SmartActionEnabled": True 此参数只对 3D 数字人有效
        }
        logger.debug("speak: %s", text)
        self.send_cmd(cmd="SEND_STREAMTEXT", data=data, req_id=reqId)
        # 用户可以在函数内部生成时间戳, 只需要传入appkey和accessToken即可获取访问接口所需的公共参数和签名

    def interrupt(self, req_id=None):
        logger.debug("interrupt speak")
        self.send_cmd(
            cmd="SEND_STREAMTEXT", data={"Interrupt": True, "Seq": 1}, req_id=req_id
        )

    def cmd_ping(self):
        logger.debug("digital-human heart beat.")
        self.send_cmd(cmd="SEND_HEARTBEAT", data={"Text": "PING"})

    def GenSignature(self, signing_content):
        # 计算 HMAC-SHA256 值
        h = hmac.new(
            self.access_token.encode(), signing_content.encode(), hashlib.sha256
        )
        # 将 HMAC-SHA256 值进行 Base64 编码
        hash_in_base64 = base64.b64encode(h.digest()).decode()
        # URL encode
        encode_sign = quote(hash_in_base64)
        # 拼接签名
        signature = f"&signature={encode_sign}"
        return signature

    def GenReqURL(self, base_url, parameter):
        # 按字典序拼接待计算签名的字符串
        signing_content = "&".join(
            f"{k}={parameter[k]}" for k in sorted(parameter.keys())
        )
        # 计算签名
        signature = self.GenSignature(signing_content)
        # 拼接访问接口的完整 URL
        return f"{base_url}?{signing_content}{signature}"

    def closeAllSession(self):
        for session in self.ListSessionOfProjectId():
            self.CloseSession(session["SessionId"])

    def query_session_info(self):
        if self.session_id:
            return self.session_id
        for session in self.ListSessionOfProjectId():
            self.session_id = session["SessionId"]
            self.session_status = session["SessionStatus"]
            self.play_stream_addr = session["PlayStreamAddr"]
            self.session_started = session["IsSessionStarted"]
            break
        return self.session_id

    def get_cmd_status(self):
        """获取指令长连接状态"""
        return self.ws_cmd and self.ws_cmd.keep_running

    def close_cmd_conn(self):
        """关闭指令长连接"""
        return self.ws_cmd and self.ws_cmd.close()

    def be_ready(self):
        # 先查询状态
        self.query_session_info()

        # 创建直播流
        if not self.session_id or self.session_status in [2, 4]:
            self.CreateSession()
        if not self.session_id:
            return

        # 直播流准备好了, 才能开启会话
        time.sleep(1)
        self.get_session_status()
        self.wait_for(condition=lambda: self.session_status != 3, wait_once=6)
        if self.session_status != 1:
            logger.error(f"直播流状态错误: {self.session_status}")
            return
        if not self.session_started:
            self.StartSession()

        # 会话创建后, 才能创建指令通道
        time.sleep(1)
        self.get_session_status()
        self.wait_for(condition=lambda: self.session_started, wait_once=3)
        if not self.session_started:
            logger.error("直播会话未开启.")
            return
        self.CreateCmdChannel()
        # 启动心跳
        self.sche_heart and self.sche_heart.clear()
        self.sche_heart = schedulers.SimpleScheduler()
        self.sche_heart.add_job(action=self.cmd_ping, interval=120)
        self.sche_heart.start()
        time.sleep(1)

    def wait_for(self, condition, wait_once: int, limit: int = 10):
        count = 0
        while count < limit and not condition():
            self.get_session_status()
            time.sleep(wait_once)
            count += 1

    def on_cmd_message(self, ws, message):
        logger.debug(f"DigitalHuman WebSocket Received message: {message}")

    def on_cmd_error(self, ws, error):
        logger.error(f"DigitalHuman WebSocket Error: {error}")

    def on_cmd_close(self, ws, status, message):
        self.ws_cmd_ok.clear()
        logger.info("DigitalHuman WebSocket Connection closed")

    def on_cmd_open(self, ws):
        self.ws_cmd_ok.set()
        logger.info("DigitalHuman WebSocket Connection opened")

    def cmd_run_forever(self):
        params = {
            "appkey": self.app_key,
            "timestamp": int(time.time()),
            "requestid": self.session_id,
        }
        while True:
            self.ws_cmd = websocket.WebSocketApp(
                url=self.GenReqURL(self.wss_url, params),
                on_open=self.on_cmd_open,
                on_message=self.on_cmd_message,
                on_error=self.on_cmd_error,
                on_close=self.on_cmd_close,
            )
            self.ws_cmd.run_forever()
            self.ws_cmd.close()
            time.sleep(5)


class MockDigitalHuman(AbstractDigitalHuman):
    """
    Generic parent class for all DigitalHuman engines
    """

    @classmethod
    def get_config(cls):
        return {}

    @classmethod
    def get_instance(cls):
        profile = cls.get_config()
        instance = cls(**profile)
        return instance

    @abstractmethod
    def speak(self, reqId, text, seq, isFinal):
        pass

    @abstractmethod
    def interrupt(self, req_id=None):
        pass

    @abstractmethod
    def info(self) -> dict:
        return {}


def get_engine_by_slug(slug=None):
    """
    Returns:
        A DigitalHuman Engine implementation available on the current platform

    Raises:
        ValueError if no speaker implementation is supported on this platform
    """

    if not slug or type(slug) is not str:
        raise TypeError("无效的 TTS slug '%s'", slug)

    selected_engines = list(
        filter(
            lambda engine: hasattr(engine, "SLUG") and engine.SLUG == slug,
            get_engines(),
        )
    )

    if len(selected_engines) == 0:
        raise ValueError(f"错误：找不到名为 {slug} 的 DigitalHuman 引擎")
    else:
        if len(selected_engines) > 1:
            logger.warning(f"注意: 有多个 DigitalHuman 名称与指定的引擎名 {slug} 匹配")
        engine = selected_engines[0]
        logger.info(f"使用 {engine.SLUG} DigitalHuman 引擎")
        return engine.get_instance()


def get_engines():
    def get_subclasses(cls):
        subclasses = set()
        for subclass in cls.__subclasses__():
            subclasses.add(subclass)
            subclasses.update(get_subclasses(subclass))
        return subclasses

    return [
        engine
        for engine in list(get_subclasses(AbstractDigitalHuman))
        if hasattr(engine, "SLUG") and engine.SLUG
    ]
