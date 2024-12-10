# -*- coding: utf-8 -*-
import threading
from abc import ABCMeta, abstractmethod
from typing import Tuple


from chatbot.robot import config, log, utils, RTAsr
from chatbot.robot.compt import CircularQueue, ThreadManager, Robot
from chatbot.robot.enums import AssistantEvent

logger = log.getLogger(__name__)

class AbstractDetector(object):
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
    def detect(self, **kwargs):
        pass

    @abstractmethod
    def skip_detect(self, **kwargs):
        pass
    

class RealTimeDetector(AbstractDetector):
    SLUG = "realtime"
    
    def __init__(self, bot: Robot, asr: RTAsr.RTAsrClient, **kwargs) -> None:
        super().__init__(**kwargs)
        self.bot = bot
        self.asr = asr
        self.running = threading.Event()
        self.detecting = threading.Event()
        self.msg_lock = threading.Lock()
        self.detect_queue = CircularQueue(2)
        self.thread_main = None
        self.ok_final = True  # False-关键字并且online状态;True-关键字并且offline
        self.chunk_time = config.get(item="/voice/chunk_time", default=100)
        self.interval_time = self.chunk_time / 1000.0
        self.keywords = dict((kw, len(kw)) for kw in config.get(
            item="/realtime/keywords", default=["你好", "小惠"]))
        self.asr.add_handler(self._on_message)

    def start(self):
        self.running.set()
        # 开始检测
        self.thread_main = ThreadManager.new(target=self._run_detect)
        self.thread_main.start()

    def stop(self):
        self.running.clear()
        self.detecting.set()
        self.thread_main.join()

    def detect(self, **kwargs):
        self.detecting.set()

    def skip_detect(self, **kwargs):
        # 跳过检测
        self._on_detected(end=True)

    def _run_detect(self):
        self.detecting.set()
        while self.running.is_set():
            self.detecting.wait()
            data = self.bot.get_audio_data(timeout=1)
            if data and self.asr.is_ok():
                self.asr.send_voice(data=data)
                self.bot.get_audio_queue().task_done()

    def _on_message(self, data: RTAsr.AsrResponse, *args, **kwargs):
        if not self.detecting.is_set():
            return
        text = data.text
        end = data.is_end
        # 空判断
        if not text:
            return
        text = utils.stripStartPunc(text)
        logger.debug("%s: %s", '识别结果' if end else '实时内容', text)
        with self.msg_lock:
            # 关键字检测
            ok, text = self._detect_message(text=text, end=end)
            if ok:
                self._on_detected(text=text, end=end)

    def _detect_message(self, text: str, end: bool) -> Tuple[bool, str]:
        content = text
        if not end:
            self.detect_queue.enqueue(text)
            content = "".join(self.detect_queue.all())
        return self._detect_words(text=content)

    def _detect_words(self, text: str, ) -> Tuple[bool, str]:
        for kw, l_kw in self.keywords.items():
            idx = text.find(kw)
            if idx >= 0:
                return True, utils.stripStartPunc(text[l_kw + idx:])
        return False, ""

    def _on_detected(self, text: str = None, end: bool = False):
        self.detecting.clear()
        self.detect_queue.clear()
        self.bot.action(event=AssistantEvent.DETECTED, text=text, end=end)


def get_detector_by_slug(slug, **kwargs) -> AbstractDetector:
    """
    Returns:
        A detector implementation available on the current platform
    """
    if not slug or type(slug) is not str:
        raise TypeError("Invalid slug '%s'", slug)

    selects = list(
        filter(
            lambda _cls: hasattr(_cls, "SLUG") and _cls.SLUG == slug, get_detectors()
        )
    )
    if len(selects) == 0:
        raise ValueError("No detector found for slug '%s'" % slug)
    else:
        if len(selects) > 1:
            logger.warning(
                "WARNING: Multiple detector found for slug '%s'. "
                + "This is most certainly a bug." % slug
            )
        select = selects[0]
        logger.info(f"使用 {select.SLUG} 关键词检测")
        return select.get_instance(**kwargs)


def get_detectors():
    def get_subclasses(sub_cls):
        subclasses = set()
        for subclass in sub_cls.__subclasses__():
            subclasses.add(subclass)
            subclasses.update(get_subclasses(subclass))
        return subclasses

    return [
        _cls
        for _cls in list(get_subclasses(AbstractDetector))
        if hasattr(_cls, "SLUG") and _cls.SLUG
    ]
