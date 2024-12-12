# -*- coding: utf-8 -*-
import queue
import threading
import time
from typing import Optional

import numpy

from octopus.robot import config, log, RTAsr
from octopus.robot.agent import get_agent_by_slug
from octopus.robot.compt import Robot, ThreadManager, StateMachine, TimeoutMonitor
from octopus.robot.detector import get_detector_by_slug
from octopus.robot.enums import AssistantStatus, AssistantEvent
from octopus.robot.recognizer import get_recongnizer_by_slug

logger = log.getLogger(__name__)


class VoiceAssistant(Robot):
    """语音助手"""

    def __init__(self, octopus, timeout_monitor: TimeoutMonitor, **kwargs) -> None:
        self.octopus = octopus
        self.timeout_monitor = timeout_monitor
        self.running = threading.Event()  # 运行标记
        self.listener = VoiceListener(timeout_monitor=timeout_monitor)  # 麦克风控制
        self.machine = StateMachine()  # 状态机
        self.audio_queue = queue.Queue()  # 语音队列
        # 组件
        self.detector = None  # 语音检测组件
        self.recognizer = None  # 语音识别组件
        self.agent = None  # 智能体组件
        self.asr = RTAsr.RTAsrClient()
        # 日志
        self.flag_log = threading.Event()

    def init(self):
        self._init_components()
        self._init_machine()

    def start(self):
        self.running.set()
        self.machine.init_status(AssistantStatus.DEFAULT)
        # 组件
        self.asr.connect()
        self.listener.start(on_voice=self._on_voice)
        self.detector.start()
        self.recognizer.start()
        self.agent.start()

    def stop(self):
        self.running.clear()
        # 组件
        self.asr.disconnect()
        self.listener.stop()
        self.agent.stop()
        self.recognizer.stop()
        self.detector.stop()

    def is_running(self):
        return self.running.is_set()

    def action(self, event: AssistantEvent, **kwargs):
        self.machine.send_event(event=event, **kwargs)

    def status(self):
        return self.machine.get_status()

    def open_log(self):
        self.flag_log.set()

    def close_log(self):
        self.flag_log.clear()

    def get_audio_queue(self):
        return self.audio_queue

    def get_audio_data(self, block=True, timeout=None):
        try:
            return self.audio_queue.get(block=block, timeout=timeout)
        except queue.Empty:
            return None

    def _init_components(self):
        self.detector = get_detector_by_slug(
            slug=config.get("/detector", "realtime"), bot=self, asr=self.asr
        )
        self.recognizer = get_recongnizer_by_slug(
            slug=config.get("/recongnizer", "realtime"),
            bot=self,
            asr=self.asr,
            conversation=self.octopus.conversation,
            sender=self.octopus.sender,
        )
        self.agent = get_agent_by_slug(
            slug=config.get("/agent", "conversation"),
            bot=self,
            conversation=self.octopus.conversation,
        )

    def _init_machine(self):
        # 默认->聆听
        self.machine.regedit(
            from_status=AssistantStatus.DEFAULT,
            to_status=AssistantStatus.LISTEN,
            event=AssistantEvent.DETECTED,
            call=self._on_detected_,
        )
        # 聆听->查询
        self.machine.regedit(
            from_status=AssistantStatus.LISTEN,
            to_status=AssistantStatus.RECOGNIZE,
            event=AssistantEvent.LISTENED,
            call=self._on_listened_,
        )
        # 查询->响应
        self.machine.regedit(
            from_status=AssistantStatus.RECOGNIZE,
            to_status=AssistantStatus.RESPONSE,
            event=AssistantEvent.RECOGNIZED,
            call=self._on_recognized_,
        )
        # 响应->默认
        self.machine.regedit(
            from_status=AssistantStatus.RESPONSE,
            to_status=AssistantStatus.DEFAULT,
            event=AssistantEvent.RESPONDED,
            call=self._on_responded_,
        )
        # 响应->聆听
        self.machine.regedit(
            from_status=AssistantStatus.RESPONSE,
            to_status=AssistantStatus.LISTEN,
            event=AssistantEvent.DETECTED,
            call=self._on_ctrl_ask_again_,
        )
        # 人工操作
        # 唤醒
        self.machine.regedit(
            from_status=AssistantStatus.DEFAULT,
            to_status=AssistantStatus.LISTEN,
            event=AssistantEvent.CTRL_WAKEUP,
            call=self._on_ctrl_wakeup_,
        )
        # 点击提交
        self.machine.regedit(
            from_status=AssistantStatus.LISTEN,
            to_status=AssistantStatus.RECOGNIZE,
            event=AssistantEvent.CTRL_COMMIT_LISTEN,
            call=self._on_ctrl_commit_listen_,
        )
        # 停止回答
        self.machine.regedit(
            from_status=AssistantStatus.RESPONSE,
            to_status=AssistantStatus.DEFAULT,
            event=AssistantEvent.CTRL_STOP_RESP,
            call=self._on_ctrl_stop_resp_,
        )
        # 再次提问
        self.machine.regedit(
            from_status=AssistantStatus.RESPONSE,
            to_status=AssistantStatus.LISTEN,
            event=AssistantEvent.CTRL_ASK_AGAIN,
            call=self._on_ctrl_ask_again_,
        )
        self.machine.regedit(
            from_status=AssistantStatus.DEFAULT,
            to_status=AssistantStatus.LISTEN,
            event=AssistantEvent.CTRL_ASK_AGAIN,
            call=self._on_detected_,
        )
        # 提交查询
        self.machine.regedit(
            from_status=AssistantStatus.DEFAULT,
            to_status=AssistantStatus.RESPONSE,
            event=AssistantEvent.CTRL_QUERY,
            call=self._on_ctrl_query_,
        )

    def _on_voice(self, rec_data: bytes):
        self.audio_queue.put(rec_data)

    def _on_detected_(self, from_status, to_status, event, **kwargs):
        self._log(from_status, to_status, event, **kwargs)
        # 中断
        # self.conversation.stop_response(**kwargs)
        # 监听
        self.recognizer.listen(**kwargs)

    def _on_listened_(self, from_status, to_status, event, **kwargs):
        self._log(from_status, to_status, event, **kwargs)
        self.listener.resume_listen()
        self.recognizer.recognize(**kwargs)

    def _on_recognized_(self, from_status, to_status, event, **kwargs):
        self._log(from_status, to_status, event, **kwargs)
        self.detector.detect()
        self.agent.response(**kwargs)

    def _on_responded_(self, from_status, to_status, event, **kwargs):
        self._log(from_status, to_status, event, **kwargs)

    def _on_ctrl_wakeup_(self, from_status, to_status, event, **kwargs):
        self._log(from_status, to_status, event, **kwargs)
        self.detector.skip_detect(**kwargs)

    def _on_ctrl_commit_listen_(self, from_status, to_status, event, **kwargs):
        self._log(from_status, to_status, event, **kwargs)
        # 中断
        self.listener.pause_listen(time_limit=2)
        self.recognizer.commit_listen(**kwargs)

    def _on_ctrl_stop_resp_(self, from_status, to_status, event, **kwargs):
        self._log(from_status, to_status, event, **kwargs)
        # 中断
        self.agent.stop_response(**kwargs)

    def _on_ctrl_ask_again_(self, from_status, to_status, event, **kwargs):
        self._log(from_status, to_status, event, **kwargs)
        # 响应中断
        self.agent.stop_response(**kwargs)
        # 再次聆听
        self.recognizer.listen(**kwargs)

    def _on_ctrl_query_(self, from_status, to_status, event, **kwargs):
        self._log(from_status, to_status, event, **kwargs)
        # 响应
        self.agent.response(**kwargs)

    def _log(self, from_status, to_status, event, **kwargs):
        if self.flag_log.is_set():
            logger.info(
                "from=%s, to=%s, event=%s",
                from_status.value,
                to_status.value,
                event.value,
            )
        else:
            logger.debug(
                "from=%s, to=%s, event=%s",
                from_status.value,
                to_status.value,
                event.value,
            )


class VoiceListener:

    def __init__(self, timeout_monitor: TimeoutMonitor, **kwargs):
        self.timeout_monitor = timeout_monitor
        # 获取配置
        self.rec_seconds = config.get(item="/voice/rec_seconds", default=5)
        self.db_threshold = config.get(item="/voice/db_threshold", default=37.5)
        self.chunk_time = config.get(item="/voice/chunk_time", default=100)
        self.interval_time = self.chunk_time / 1000.0
        self.thread_audio: Optional[threading.Thread] = None
        self.running = threading.Event()
        self.listening = threading.Event()

    def start(self, on_voice=None):
        self.running.set()
        # 启动声音监听
        self.thread_audio = ThreadManager.new(
            target=self.record_audio, kwargs=dict(on_voice=on_voice)
        )
        self.thread_audio.start()

    def stop(self):
        # 关闭声音监听
        self.running.clear()

    def join(self, timeout=None):
        self.thread_audio.join(timeout=timeout)

    def record_audio(self, on_voice):
        import pyaudio

        rate = 16000
        channel = 1
        chunk = int(rate * 2 * channel * self.chunk_time / 1000)
        # frames = int(rate / 1000 * chunk)
        frames = 4096
        data_silent = b"".join([b"\x00" for i in range(chunk)])

        p = pyaudio.PyAudio()

        stream = p.open(
            format=pyaudio.paInt16,
            channels=channel,
            rate=rate,
            input=True,
            frames_per_buffer=frames,
        )
        # 发送语音前, 要先发送MetaInfo
        while self.running.is_set():
            # last_time = time.time()
            data = stream.read(chunk)
            try:
                # 发送空内容, 触发offline
                if not self.listening.is_set():
                    data = data_silent
                on_voice(data)
            except:
                logger.critical("语音识别异常.", exc_info=True)
            # db = self.calculate_db(data=data)
            # # 开始请求
            # if db > self.db_threshold:
            #     logger.info(msg=f"录音分贝: {db}")
            #     self.record_and_send(stream=stream, rate=rate,
            #                         chunk=chunk, seconds=self.rec_seconds)
            # 计算休眠时间
            # interval_time = self.sleep_time(last_time=last_time)
            # if interval_time <= 0:
            #     interval_time = self.interval_time
            time.sleep(self.interval_time)

    def sleep_time(self, last_time: float) -> float:
        """音频块时长"""
        return self.interval_time - time.time() + last_time

    # 计算分贝的函数
    @classmethod
    def calculate_db(cls, data):
        # Assuming 16-bit signed integer audio
        np_data = numpy.frombuffer(buffer=data, dtype=numpy.int16)
        # rms = math.sqrt(sum([x**2 for x in data])/len(data))
        rms = numpy.sqrt(numpy.mean(np_data**2))
        if rms <= 0:
            return 0
        # db = 20 * math.log10(rms)
        return 20 * numpy.log10(rms)

    @classmethod
    def is_silent(cls, data: bytes):
        return len(data) == data.count(b"\x00")

    @classmethod
    def record_and_send(cls, stream, rate, chunk, seconds, on_voice):
        for _ in range(0, int(rate / chunk * seconds)):
            data = stream.read(num_frames=chunk)
            on_voice(data)

    def resume_listen(self):
        if self.listening.is_set():
            return
        self.listening.set()
        self.timeout_monitor.pop(key=self.__class__.__name__)
        logger.debug("恢复录音")

    def pause_listen(self, time_limit=0):
        self.listening.clear()
        # 时限
        if time_limit:
            self.timeout_monitor.put(
                key=self.__class__.__name__,
                timeout=time_limit,
                handle=self.resume_listen,
            )
        logger.debug("暂停录音")
