# -*- coding: utf-8 -*-
import json
import queue
import ssl
from abc import ABCMeta, abstractmethod
import threading
import time
from typing import Optional, Union, Any

import websocket

from octopus.robot import config, constants, log
from octopus.robot.compt import ThreadManager
from octopus.robot.sdk.VolcengineSpeech import StreamLmClient

logger = log.getLogger(__name__)


class AsrResponse:

    def __init__(self, text: str, is_amend: bool = False, seq=None, **kwargs):
        self.text = text
        self.is_amend = is_amend
        self.seq = seq


class AbstractRTAsr(object):
    __metaclass__ = ABCMeta

    @classmethod
    def get_instance(cls, **kwargs):
        return cls(**kwargs)

    def __init__(self, **kwargs):
        pass

    def connect(self, on_message, **kwargs) -> any:
        """获取链接"""
        ...

    @abstractmethod
    def run(self, conn, **kwargs):
        """执行"""
        ...

    @abstractmethod
    def stop(self, conn, **kwargs):
        """停止"""
        ...

    @abstractmethod
    def send_meta(self, conn, data: Union[str, dict, Any] = None, **kwargs):
        """发送元信息"""
        ...

    @abstractmethod
    def send_voice(self, conn, data: Union[str, bytes], **kwargs):
        """发送声音"""
        ...

    @classmethod
    def gen_url(cls, base_url, parameter=None):
        if not parameter:
            return base_url
        # 按字典序拼接待计算签名的字符串
        signing_content = "&".join(
            f"{k}={parameter[k]}" for k in sorted(parameter.keys())
        )
        # 拼接访问接口的完整 URL
        return f"{base_url}?{signing_content}"


class FunRTAsr(AbstractRTAsr):
    SLUG = "funasr"

    def __init__(self, mode=None, **kwargs):
        """
        mode: offLine   表示推理模式为一句话识别
              online    表示推理模式为实时语音识别
              2pass     表示为实时语音识别,并且说话句尾采用离线模
        use_itn: 输出结果中是否包含标点和逆文本正则化,默认True
        chunk_size: 表示流式模型latency配置, [5,10,5],表示当前音频为600ms,并且回看300ms,又看300ms。
        hot_words: 如果使用热词,用于纠正谐音词, 格式为["热词 权重","阿里巴巴 20","通义实验室 30"]
        """
        super().__init__(**kwargs)
        self.url = config.get(item="/funasr/url", default="wss://localhost:10096/wss")
        self.mode = mode or config.get(item="/funasr/mode", default="2pass")
        self.use_itn = config.get(item="/funasr/use_itn", default=False)
        self.chunk_size = config.get(item="/funasr/chunk_size", default=[5, 10, 5])
        self.chunk_interval = config.get(item="/funasr/chunk_interval", default=10)
        self.hot_words = ""
        hot_words = config.get(item="/funasr/hot_words", default=None)
        if hot_words:
            self.hot_words = self._load_hotwords(hot_words)

    def connect(
        self, on_message, on_open=None, on_error=None, on_close=None, **kwargs
    ) -> any:
        return websocket.WebSocketApp(
            url=self.gen_url(self.url),
            on_message=self.wrap_message(on_message=on_message),
            on_open=self.wrap_open(on_open=on_open),
            on_error=self.wrap_error(on_error=on_error),
            on_close=self.wrap_close(on_close=on_close),
        )

    def run(self, conn: websocket.WebSocketApp, **kwargs):
        conn.run_forever(
            ping_interval=20,
            skip_utf8_validation=True,
            sslopt=dict(cert_reqs=ssl.CERT_NONE),
        )

    def stop(self, conn: websocket.WebSocketApp, **kwargs):
        if conn:
            conn.close()

    def send_meta(
        self,
        conn: websocket.WebSocketApp,
        data: Union[str, dict] = None,
        is_speaking=True,
        **kwargs,
    ):
        """
        is_speaking 表示断句尾点,例如,vad切割点,或者一条wav结束
        """
        ops_data = {
            "mode": self.mode,
            "chunk_size": self.chunk_size,
            "chunk_interval": self.chunk_interval,
            "is_speaking": is_speaking,
            "hotwords": self.hot_words,
            "itn": self.use_itn,
            "wav_name": "microphone",
        }
        conn.send_text(json.dumps(ops_data))

    def send_voice(
        self, conn: websocket.WebSocketApp, data: Union[str, bytes], **kwargs
    ):
        conn.send_bytes(data=data)

    def wrap_message(self, on_message):
        def rev_message(ws, message, *args, **kwargs):
            data = json.loads(s=message)
            logger.debug(f"FunAsr WebSocket Received Data: {data}")
            # 封装成统一格式
            on_message(
                AsrResponse(
                    text=data.get("text", None), is_amend=data["mode"] == "2pass-offline"
                )
            )

        return rev_message

    def wrap_error(self, on_error):
        def wrapper(ws, error):
            logger.error("FunAsr WebSocket Error: %s", str(error))
            if on_error:
                on_error(ws, error)

        return wrapper

    def wrap_close(self, on_close):
        def wrapper(ws, status, message):
            logger.info("FunAsr WebSocket Connection closed, reconnect.")
            if on_close:
                on_close(ws, status, message)

        return wrapper

    def wrap_open(self, on_open):
        def wrapper(ws):
            logger.info("FunAsr WebSocket Connection opened")
            # 发送MetaInfo
            self.send_meta(conn=ws, is_speaking=True)
            if on_open:
                on_open(ws)

        return wrapper

    def _load_hotwords(self, hot_words) -> str:
        # load file
        if isinstance(hot_words, str):
            with open(
                file=constants.getConfigData(hot_words), mode="r", encoding="utf8"
            ) as f:
                hot_words = f.readlines()
        # hot words
        fst_dict = {}
        for line in hot_words:
            line = line.strip()
            # 空判断
            if not line:
                continue
            # 分词处理
            words = line.split(" ")
            if len(words) != 2:
                logger.warning(
                    "Please checkout format of hot words: <word> <hot_level>"
                )
                continue
            try:
                fst_dict[words[0]] = int(words[1])
            except ValueError:
                logger.critical("Please checkout format of hot words", exc_info=True)
        return json.dumps(fst_dict)


class VolcengineRTAsr(AbstractRTAsr):
    SLUG = "volcengine"

    def __init__(self, mode=None, chunk_time=None, **kwargs):
        """
        seg_duration: 录音时间长度
        mode: offLine   表示推理模式为一句话识别(考虑成本, 目前只支持offline)
              online    表示推理模式为实时语音识别
              2pass     表示为实时语音识别,并且说话句尾采用离线模
        hot_words: 如果使用热词,用于纠正谐音词, 格式为["热词 权重","阿里巴巴 20","通义实验室 30"]
        """
        super().__init__(**kwargs)
        self.mode = mode or "offline"
        self.chunk_time = chunk_time or 100
        self.hot_words = []
        self.app_id = config.get(item="/volcengine/app_id")
        self.token = config.get(item="/volcengine/token")
        hot_words = config.get(item="/volcengine/hot_words", default=None)
        if hot_words:
            self.hot_words = self._load_hotwords(hot_words)
        self.vol_engine = StreamLmClient(
            app_id=self.app_id, token=self.token, format="pcm", hot_words=self.hot_words
        )
        self.only_end = self.mode == "offline"

    def connect(
        self, on_message, on_open=None, on_error=None, on_close=None, **kwargs
    ) -> any:
        if self.mode == "online":
            return self.vol_engine.conn_online(
                on_message=self.wrap_message(on_message=on_message),
                on_open=self.wrap_open(on_open=on_open),
                on_error=self.wrap_error(on_error=on_error),
                on_close=self.wrap_close(on_close=on_close),
            )
        return self.vol_engine.conn_offline(
            on_message=self.wrap_message(on_message=on_message),
            on_open=self.wrap_open(on_open=on_open),
            on_error=self.wrap_error(on_error=on_error),
            on_close=self.wrap_close(on_close=on_close),
        )

    def run(self, conn: websocket.WebSocketApp, **kwargs):
        conn.run_forever(
            ping_interval=20,
            skip_utf8_validation=True,
            sslopt=dict(cert_reqs=ssl.CERT_NONE),
        )

    def stop(self, conn: websocket.WebSocketApp, **kwargs):
        if conn:
            conn.close()

    def send_meta(
        self,
        conn: websocket.WebSocketApp,
        data: Union[str, dict] = None,
        is_speaking=True,
        **kwargs,
    ):
        """
        is_speaking 表示断句尾点,例如,vad切割点,或者一条wav结束
        """
        self.vol_engine.send_meta_info(ws=conn)

    def send_voice(
        self, conn: websocket.WebSocketApp, data: Union[str, bytes], **kwargs
    ):
        self.vol_engine.send_audio_data(ws=conn, chunk_data=data)

    def wrap_message(self, on_message):
        def rev_message(ws, message, *args, **kwargs):
            logger.debug("Volcengine WebSocket Received Data: %s", message)
            data = StreamLmClient.parse_message(message)
            # 封装成统一格式
            on_message(
                AsrResponse(
                    text=data.get("payload_msg", {})
                    .get("result", {})
                    .get("text", None),
                    is_amend=self.only_end or data.get("is_last_package", False),
                    seq=data.get("payload_sequence", None),
                )
            )

        return rev_message

    def wrap_error(self, on_error):
        def wrapper(ws, error):
            logger.error("Volcengine WebSocket Error: %s", str(error))
            if on_error:
                on_error(ws, error)

        return wrapper

    def wrap_close(self, on_close):
        def wrapper(ws, status, message):
            logger.info("Volcengine WebSocket Connection closed, reconnect.")
            if on_close:
                on_close(ws, status, message)

        return wrapper

    def wrap_open(self, on_open):
        def wrapper(ws):
            logger.info("Volcengine WebSocket Connection opened")
            # 发送MetaInfo
            self.send_meta(conn=ws)
            if on_open:
                on_open(ws)

        return wrapper

    def _load_hotwords(self, hot_words) -> list:
        # load file
        if isinstance(hot_words, str):
            with open(
                file=constants.getConfigData(hot_words), mode="r", encoding="utf8"
            ) as f:
                hot_words = f.readlines()
        # hot words
        word_list = []
        for line in hot_words:
            line = line.strip()
            # 空判断
            if not line:
                continue
            # 分词处理
            word_list.append(line.split(" ")[0])
        return word_list


class MixedConn:

    def __init__(
        self, conn_fun: websocket.WebSocketApp, conn_volcano: websocket.WebSocketApp
    ):
        self.conn_fun = conn_fun
        self.conn_volcano = conn_volcano


class MixedRTAsr(AbstractRTAsr):
    SLUG = "mixed"

    def __init__(self, chunk_time=None, **kwargs):
        """
        seg_duration: 录音时间长度
        hot_words: 如果使用热词,用于纠正谐音词, 格式为["热词 权重","阿里巴巴 20","通义实验室 30"]
        """
        super().__init__(**kwargs)
        self.asr_fun = FunRTAsr(mode="online")
        self.asr_volcano = VolcengineRTAsr(mode="offline", chunk_time=chunk_time)
        # 消息队列
        self.msg_queue = queue.Queue(maxsize=1024)
        self.msg_status = threading.Event()
        self.msg_thd = None

    def connect(
        self, on_message, on_open=None, on_error=None, on_close=None, **kwargs
    ) -> any:
        func_msg = self.wrap_message(on_message=on_message)
        return MixedConn(
            conn_fun=self.asr_fun.connect(on_message=func_msg),
            conn_volcano=self.asr_volcano.connect(
                on_message=func_msg,
                on_open=on_open,
                on_error=on_error,
                on_close=on_close,
                **kwargs,
            ),
        )

    def run(self, conn: MixedConn, **kwargs):
        # funasr
        t = ThreadManager.new(
            target=self.asr_fun.run, kwargs=dict(conn=conn.conn_fun, **kwargs)
        )
        t.start()
        # volcengine
        self.asr_volcano.run(conn=conn.conn_volcano, **kwargs)

    def stop(self, conn: MixedConn, **kwargs):
        self.asr_fun.stop(conn.conn_fun)
        self.asr_volcano.stop(conn.conn_volcano)

    def send_meta(
        self, conn: MixedConn, data: Union[str, dict] = None, is_speaking=True, **kwargs
    ):
        """
        is_speaking 表示断句尾点,例如,vad切割点,或者一条wav结束
        """
        try:
            self.asr_fun.send_meta(
                conn=conn.conn_fun, data=data, is_speaking=is_speaking, **kwargs
            )
        finally:
            self.asr_volcano.send_meta(
                conn=conn.conn_volcano, data=data, is_speaking=is_speaking, **kwargs
            )

    def send_voice(self, conn: MixedConn, data: Union[str, bytes], **kwargs):
        try:
            self.asr_fun.send_voice(conn=conn.conn_fun, data=data, **kwargs)
        finally:
            self.asr_volcano.send_voice(conn=conn.conn_volcano, data=data, **kwargs)

    def wrap_message(self, on_message):

        def do_message():
            self.msg_status.set()
            while self.msg_status.is_set():
                on_message(self.msg_queue.get())

        # stop if exists
        if self.msg_status.is_set():
            self.msg_status.clear()
            self.msg_queue.put(None)
            self.msg_thd.join()
        # start
        self.msg_thd = threading.Thread(target=do_message)
        self.msg_thd.start()
        # 放入队列中, 提高funasr和volc并发的效率
        return self.msg_queue.put


class RTAsrClient:
    def __init__(self, **kwargs) -> None:
        self.running = threading.Event()
        self.thread_asr: Optional[threading.Thread] = None
        self.chunk_time = config.get(item="/voice/chunk_time", default=100)
        # 实例化RTAsr
        self.rt_asr_conn = None
        self.rt_asr_conn_ok = threading.Event()
        self.rt_asr = self._init_asr()
        self.on_messages = []

    def connect(self):
        if self.running.is_set():
            return
        self.running.set()
        # 连接ASR
        self.thread_asr = ThreadManager.new(
            target=self._run_asr_forever, kwargs=dict(on_message=self._on_message)
        )
        self.thread_asr.start()

    def disconnect(self):
        if not self.running.is_set():
            return
        self.running.clear()
        # 关闭asr
        self.rt_asr.stop(conn=self.rt_asr_conn)

    def add_handler(self, handler):
        self.on_messages.append(handler)

    def is_ok(self) -> bool:
        return self.rt_asr_conn_ok.is_set()

    def send_meta(self, data=None, **kwargs):
        self.rt_asr.send_meta(conn=self.rt_asr_conn, data=data, **kwargs)

    def send_voice(self, data, **kwargs):
        self.rt_asr.send_voice(conn=self.rt_asr_conn, data=data, **kwargs)

    def _init_asr(self):
        """实例化RTAsr"""
        rt_engine = config.get(item="/realtime/engine", default="funasr")
        rt_settings = config.get(item=f"/{rt_engine}", default={})
        return get_rtasr_by_slug(
            slug=rt_engine, chunk_time=self.chunk_time, **rt_settings
        )

    def _run_asr_forever(self, on_message):
        while self.running.is_set():
            self.rt_asr_conn = self.rt_asr.connect(
                on_message=on_message,
                on_open=self._on_asr_open,
                on_close=self._on_asr_close,
            )
            self.rt_asr.run(conn=self.rt_asr_conn)
            self.rt_asr.stop(conn=self.rt_asr_conn)
            time.sleep(5)

    def _on_asr_open(self, ws):
        self.rt_asr_conn_ok.set()
        logger.info("Asr WebSocket Connection opened.")

    def _on_asr_close(self, ws, status, message):
        self.rt_asr_conn_ok.clear()
        logger.info("Asr WebSocket Connection closed, reconnect.")

    def _on_message(self, data: AsrResponse, **kwargs):
        for on_message in self.on_messages:
            on_message(data, **kwargs)


def get_rtasr_by_slug(slug, **kwargs) -> AbstractRTAsr:
    """
    Returns:
        A RTAsr implementation available on the current platform
    """
    if not slug or type(slug) is not str:
        raise TypeError("Invalid slug '%s'", slug)

    selects = list(
        filter(lambda _cls: hasattr(_cls, "SLUG") and _cls.SLUG == slug, get_rtasrs())
    )
    if len(selects) == 0:
        raise ValueError("No RTAsr found for slug '%s'" % slug)
    else:
        if len(selects) > 1:
            logger.warning(
                "WARNING: Multiple RTAsr found for slug '%s'. "
                + "This is most certainly a bug." % slug
            )
        select = selects[0]
        logger.info(f"使用 {select.SLUG} 关键词检测")
        return select.get_instance(**kwargs)


def get_rtasrs():
    def get_subclasses(sub_cls):
        subclasses = set()
        for subclass in sub_cls.__subclasses__():
            subclasses.add(subclass)
            subclasses.update(get_subclasses(subclass))
        return subclasses

    return [
        _cls
        for _cls in list(get_subclasses(AbstractRTAsr))
        if hasattr(_cls, "SLUG") and _cls.SLUG
    ]
