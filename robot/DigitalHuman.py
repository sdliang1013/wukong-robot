# -*- coding: utf-8 -*-
from abc import ABCMeta, abstractmethod
import hmac
import hashlib
import time
import base64
from urllib.parse import quote
import requests
import uuid
import websocket
from robot import config, logging
import threading
import json

logger = logging.getLogger(__name__)


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
    def CommandChannel(self, reqId, text, seq, ifFinal):
        pass


class TecentDigitalHuman(AbstractDigitalHuman):
    """
    腾讯云智能数智人: https://cloud.tencent.com/product/ivh
    """

    SLUG = "tencent-dh"

    def __init__(
        self, base_url, wss_url, access_token, app_key, project_id, user_id, **args
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
        self.thread_loop = None

        # 准备就绪
        self.be_ready()

    @classmethod
    def get_config(cls):
        # Try to get ali_yuyin config from config
        return config.get("tencent-dh", {})

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
                "ReqId": GenUUID(),
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
                logger.info(f"CreateSession: {responseData}")
                code = responseData["Header"]["Code"]
                if code == 0:
                    self.session_id = responseData["Payload"]["SessionId"]
                    self.session_status = responseData["Payload"]["SessionStatus"]
                    self.play_stream_addr = responseData["Payload"]["PlayStreamAddr"]
                else:
                    logger.critical(f"新建直播流会话失败，response: {responseData}")
                logger.info(
                    f"SessionStatus: {responseData['Payload']['SessionStatus']}, "
                    f"PlayStreamAddr: {responseData['Payload']['PlayStreamAddr']}"
                )
                return responseData
            else:
                logger.critical(f"{self.SLUG} 新建直播流会话出错了: {response.text}")
                return response.text
        except Exception as e:
            logger.critical("新建直播流会话失败，原因：{e}", exc_info=True)

    def ListSessionOfProjectId(self):
        """
        2. 查询数智人项目下的会话列表
        """
        url = f"{self.base_url}/listsessionofprojectid"
        params = {"appkey": self.app_key, "timestamp": int(time.time())}
        data = {"Payload": {"ReqId": GenUUID(), "VirtualmanProjectId": self.project_id}}
        try:
            response = self.http_req.post(url=self.GenReqURL(url, params), json=data)
            if response.status_code == 200:
                responseData = response.json()
                logger.info(f"ListSessionOfProjectId: {responseData}")
                code = responseData["Header"]["Code"]
                if code != 0:
                    logger.critical(
                        f"查询数智人项目下的会话列表失败，response: {response}"
                    )
                return responseData["Payload"]["Sessions"]
            else:
                logger.critical(
                    f"{self.SLUG} 查询数智人项目下的会话列表出错了: {response.text}"
                )
                return response.text
        except Exception as e:
            logger.critical("查询数智人项目下的会话列表失败，原因：{e}", exc_info=True)

    def get_session_status(self, session_id=None):
        """
        2. 查询会话状态
        """
        url = f"{self.base_url}/statsession"
        params = {"appkey": self.app_key, "timestamp": int(time.time())}
        data = {
            "Payload": {"ReqId": GenUUID(), "SessionId": session_id or self.session_id}
        }
        try:
            response = self.http_req.post(url=self.GenReqURL(url, params), json=data)
            if response.status_code == 200:
                responseData = response.json()
                logger.info(f"SessionStatus: {responseData}")
                code = responseData["Header"]["Code"]
                if code == 0:
                    self.session_status = responseData["Payload"]["SessionStatus"]
                    self.session_started = responseData["Payload"]["IsSessionStarted"]
                    self.play_stream_addr = responseData["Payload"]["PlayStreamAddr"]
                else:
                    logger.critical(f"查询数智人会话状态失败，response: {response}")
                return responseData
            else:
                logger.critical(
                    f"{self.SLUG} 查询数智人会话状态出错了: {response.text}"
                )
                return response.text
        except Exception as e:
            logger.critical("查询数智人会话状态失败，原因：{e}", exc_info=True)

    def StartSession(self):
        """
        3. 开启会话
        """
        url = f"{self.base_url}/startsession"
        params = {"appkey": self.app_key, "timestamp": int(time.time())}
        data = {"Payload": {"ReqId": GenUUID(), "SessionId": self.session_id}}
        try:
            response = self.http_req.post(url=self.GenReqURL(url, params), json=data)
            if response.status_code == 200:
                responseData = response.json()
                logger.info(f"StartSession: {responseData}")
                code = responseData["Header"]["Code"]
                self.session_started = code == 0
                if code != 0:
                    logger.critical(f"开启会话失败失败，response: {response}")
                return responseData
            else:
                logger.critical(f"{self.SLUG} 开启会话失败出错了: {response.text}")
                return response.text
        except Exception as e:
            logger.critical("开启会话失败，原因：{e}", exc_info=True)

    def CloseSession(self, session_id=None):
        """
        4. 关闭会话
        """
        url = f"{self.base_url}/closesession"
        params = {"appkey": self.app_key, "timestamp": int(time.time())}
        data = {
            "Payload": {"ReqId": GenUUID(), "SessionId": session_id or self.session_id}
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
                    logger.critical(f"关闭会话失败，response: {response}")
                return responseData
            else:
                logger.critical(f"{self.SLUG} 关闭会话失败出错了: {response.text}")
                return response.text
        except Exception as e:
            logger.critical("关闭会话失败，原因：{e}", exc_info=True)

    def CreateCmdChannel(self):
        """
        5. 创建长链接通道(流式)
        """

        def cmd_run():
            params = {
                "appkey": self.app_key,
                "timestamp": int(time.time()),
                "requestid": self.session_id,
            }
            self.ws_cmd = websocket.WebSocketApp(
                url=self.GenReqURL(self.wss_url, params),
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
            )
            self.ws_cmd.on_open = on_open
            self.ws_cmd.run_forever()

        self.thread_loop = threading.Thread(target=cmd_run)
        self.thread_loop.start()

    def CommandChannel(self, reqId, text, seq, isFinal):
        try:
            data = {
                "Payload": {
                    "ReqId": reqId,
                    "SessionId": self.session_id,
                    "Command": "SEND_STREAMTEXT",
                    "Data": {"Text": text, "Seq": seq, "IsFinal": isFinal},
                }
            }
            logger.info(f"CommandChannel: {text}")
            self.ws_cmd.send(json.dumps(data))
        except Exception as e:
            logger.critical(f"发送命令失败，原因：{e}", exc_info=True)

        # 用户可以在函数内部生成时间戳, 只需要传入appkey和accessToken即可获取访问接口所需的公共参数和签名

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

    def get_cmd_status(self):
        """获取指令长连接状态"""
        return self.ws_cmd and self.ws_cmd.keep_running

    def be_ready(self):
        # 先关闭所有的会话
        self.closeAllSession()

        # 创建直播流
        time.sleep(1)
        self.CreateSession()
        if not self.session_id:
            return

        # 直播流准备好了, 才能开启会话
        time.sleep(1)
        self.get_session_status()
        self.wait_for(condition=lambda: self.session_status != 3, wait_once=6)
        if self.session_status != 1:
            logger.critical(f"直播流状态错误: {self.session_status}")
            return
        self.StartSession()

        # 会话创建后, 才能创建指令通道
        time.sleep(1)
        self.get_session_status()
        self.wait_for(condition=lambda: self.session_started, wait_once=3)
        if not self.session_started:
            logger.critical("直播会话未开启.")
            return
        self.CreateCmdChannel()
        time.sleep(3)

    def wait_for(self, condition, wait_once: int, limit: int = 10):
        count = 0
        while count < limit and not condition():
            self.get_session_status()
            time.sleep(wait_once)
            count += 1


def GenUUID():
    return uuid.uuid4().hex


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


def on_message(ws, message):
    logger.info(f"DigitalHuman WebSocket Received message: {message}")


def on_error(ws, error):
    logger.error(f"DigitalHuman WebSocket Error: {error}")


def on_close(ws, _foo, _bar):
    logger.info("DigitalHuman WebSocket Connection closed")
    ws.run_forever()
    logger.info("DigitalHuman WebSocket Connection reopened")


def on_open(ws):
    logger.info("DigitalHuman WebSocket Connection opened")
