# -*- coding: utf-8 -*-
import threading
import time
from abc import ABCMeta, abstractmethod

from octopus.robot import config, log, utils, RTAsr
from octopus.robot.Sender import ACTION_USER_SPEAK
from octopus.robot.compt import ThreadManager, Robot
from octopus.robot.enums import AssistantEvent

logger = log.getLogger(__name__)


class AbstractRecongnizer(object):
    __metaclass__ = ABCMeta

    @classmethod
    def get_instance(cls, **kwargs):
        instance = cls(**kwargs)
        return instance

    def __init__(self, **kwargs):
        pass

    @abstractmethod
    def start(self):
        pass

    @abstractmethod
    def stop(self):
        pass

    @abstractmethod
    def listen(self, **kwargs):
        pass

    @abstractmethod
    def commit_listen(self, **kwargs):
        pass

    @abstractmethod
    def recognize(self, **kwargs):
        pass


class RealTimeRecognizer(AbstractRecongnizer):
    SLUG = "realtime"

    def __init__(
        self, bot: Robot, asr: RTAsr.RTAsrClient, conversation, sender, **kwargs
    ) -> None:
        super().__init__(**kwargs)
        self.bot = bot
        self.asr = asr
        self.conversation = conversation
        self.sender = sender
        self.running = threading.Event()
        self.listening = threading.Event()  # 聆听标记
        self.recognizing = threading.Event()  # 识别查询内容标记
        self.msg_lock = threading.Lock()
        self.need_query = True
        self.detect_end = True  # False-关键字并且online状态;True-关键字并且offline
        self.listen_data = list()  # 聆听内容
        self.query_data = list()  # 查询内容
        self.keywords = dict(
            (kw, len(kw))
            for kw in config.get(item="/realtime/keywords", default=["你好", "小惠"])
        )
        self.interrupt_time = config.get("/realtime/interrupt_time", 1)
        self.silent_threshold = config.get("/realtime/silent_threshold", 3)
        self.recording_threshold = config.get("/realtime/recording_timeout", 50)
        self.interval_time = config.get("/realtime/interval_time", 0.2)
        self.asr.add_handler(self._on_message)

    def start(self):
        self.running.set()

    def stop(self):
        self.running.clear()

    def listen(self, text: str = None, end: bool = False, **kwargs):
        self.listening.set()
        # listen_data 和 query_data 同步进行
        self.listen_data.clear()
        self.query_data.clear()
        # 处理 text 和 end
        self.detect_end = end
        if text:
            self._append_text(text=text, is_amend=end)
        # 开始聆听
        ThreadManager.new(target=self._run_listen).start()
        # 启动listen_query
        ThreadManager.new(target=self._listen_query).start()

    def commit_listen(self, **kwargs):
        # 提交聆听
        self._on_listened()

    def recognize(self, **kwargs):
        self.recognizing.set()
        # 开始聆听
        while self.recognizing.is_set():
            data = self.bot.get_audio_data(timeout=1)
            if data and self.asr.is_ok():
                self.asr.send_voice(data=data)
                self.bot.get_audio_queue().task_done()

    def _on_message(self, data: RTAsr.AsrResponse, *args, **kwargs):
        if not self.listening.is_set() and not self.recognizing.is_set():
            return
        text = data.text
        is_amend = data.is_amend
        # 空判断
        if not text:
            return
        text = utils.stripStartPunc(text)
        logger.debug("%s: %s", "识别结果" if is_amend else "实时内容", text)
        with self.msg_lock:
            # 去掉关键词之前的无效内容(基于online和offline特性)
            if is_amend and not self.detect_end:
                self.detect_end = True
                text = self._clear_kw(text=text)
            # 附加查询内容
            if text:
                self._append_text(text=text, is_amend=is_amend)

    def _run_listen(self):
        while self.listening.is_set():
            data = self.bot.get_audio_data(timeout=1)
            if data and self.asr.is_ok():
                self.asr.send_voice(data=data)
                self.bot.get_audio_queue().task_done()

    def _listen_query(
        self,
    ):
        """
        定时检查query内容
        如果 recording 超过时长, 或者 silent > threshold, 则返回
        """
        try:
            recording_count = 0
            silent_count = 0
            len_last = len(self.listen_data)
            while self.listening.is_set():
                # 超过时长
                recording_count += 1
                if recording_count > self.recording_threshold:
                    return True
                len_now = len(self.listen_data)
                # 空内容
                if len_now == 0:
                    time.sleep(self.interval_time)
                    continue
                # 判断静音阈值
                if len_now and silent_count > self.silent_threshold:
                    return True
                # 计算静音
                if len_now <= len_last:
                    silent_count += 1
                else:
                    silent_count = 0
                len_last = len_now
                time.sleep(self.interval_time)
        except:
            logger.critical("数字人走神了.", exc_info=True)
            raise
        finally:
            self._on_listened()

    def _on_listened(self, clear_data: bool = False):
        self.listening.clear()
        self.bot.action(event=AssistantEvent.LISTENED)
        if clear_data:
            self.listen_data.clear()

    def _on_queried(self, clear_data: bool = False):
        self.recognizing.clear()
        query = "".join(self.query_data)
        self.bot.action(event=AssistantEvent.RECOGNIZED, query=query)
        if clear_data:
            self.query_data.clear()
        # 发送消息
        self.sender.put_message(
            action=ACTION_USER_SPEAK,
            data={"end": True},
            message=query,
            t=0,
        )

    def _clear_kw(
        self,
        text: str,
    ) -> str:
        for kw, l_kw in self.keywords.items():
            idx = text.find(kw)
            if idx >= 0:
                return utils.stripStartPunc(text[l_kw + idx :])
        return text

    def _append_text(self, text, is_amend):
        # 页面打断, 忽略打断前的内容(基于online和offline特性)
        if is_amend and self.conversation.in_break_time(self.interrupt_time):
            self.conversation.clear_break_time()
            return
        if not is_amend:  # 聆听内容
            self.listen_data.append(text)
            self.sender.put_message(
                action=ACTION_USER_SPEAK,
                data={"end": False},
                message=text,
                t=0,
            )
        else:  # 查询内容
            self.query_data.append(text)
            # 停止识别(基于online和offline特性)
            if not self.listening.is_set():
                self._on_queried()


def get_recongnizer_by_slug(slug, **kwargs) -> AbstractRecongnizer:
    """
    Returns:
        A Recongnizer implementation available on the current platform
    """
    if not slug or type(slug) is not str:
        raise TypeError("Invalid slug '%s'", slug)

    selects = list(
        filter(
            lambda _cls: hasattr(_cls, "SLUG") and _cls.SLUG == slug, get_recongnizers()
        )
    )
    if len(selects) == 0:
        raise ValueError("No Recongnizer found for slug '%s'" % slug)
    else:
        if len(selects) > 1:
            logger.warning(
                "WARNING: Multiple Recongnizer found for slug '%s'. "
                + "This is most certainly a bug.",
                slug,
            )
        select = selects[0]
        logger.info("使用 %s 语音识别", select.SLUG)
        return select.get_instance(**kwargs)


def get_recongnizers():
    def get_subclasses(sub_cls):
        subclasses = set()
        for subclass in sub_cls.__subclasses__():
            subclasses.add(subclass)
            subclasses.update(get_subclasses(subclass))
        return subclasses

    return [
        _cls
        for _cls in list(get_subclasses(AbstractRecongnizer))
        if hasattr(_cls, "SLUG") and _cls.SLUG
    ]
