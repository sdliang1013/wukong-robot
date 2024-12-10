# -*- coding: utf-8 -*-
from abc import ABCMeta, abstractmethod
import subprocess
import threading
import time
from collections import deque
from ctypes import cast, POINTER
from enum import Enum
from typing import List, Tuple, Dict, Optional

import serial

from octopus.robot import log, utils

logger = log.getLogger(__name__)


class StateMachine:

    def __init__(self):
        self.status = None
        self.changes: Dict[str, tuple] = {}

    def regedit(
        self, from_status: Enum, to_status: Enum, event: Enum, call=None, replace=False
    ):
        key = self.get_key(status=from_status, event=event)
        if key in self.changes and not replace:
            raise RuntimeError(
                f"重复注册的状态和事件: status={from_status.value}, event={event.value}"
            )
        self.changes.update({key: (to_status, call)})

    def init_status(self, status: Enum):
        self.status = status

    def send_event(self, event: Enum, **kwargs):
        dst = self.next_status(event=event)
        if not dst:
            logger.error("status=%s, event=%s 未定义", self.status.value, event.value)
            return
        s_from = self.status
        # 变更状态
        self.status = dst[0]
        # 执行处理
        if dst[1]:
            dst[1](s_from, self.status, event, **kwargs)

    def next_status(self, event: Enum) -> Optional[tuple]:
        return self.changes.get(self.get_key(status=self.status, event=event), None)

    def get_status(self):
        return self.status

    @classmethod
    def get_key(cls, status: Enum, event: Enum) -> str:
        return f"{status.name}_{event.name}"


class CircularQueue:
    """
    环状队列
    """

    def __init__(self, size):
        self.size = size
        self.queue = deque(maxlen=size)

    def enqueue(self, item):
        """
        写入
        """
        if len(self.queue) == self.size:
            self.queue.popleft()  # 覆盖队列头部元素
        self.queue.append(item)

    def dequeue(self):
        """
        取出
        """
        if self.queue:
            return self.queue.popleft()
        raise IndexError("Queue is empty")

    def clear(self):
        self.queue.clear()

    def all(self) -> list:
        q_c = self.queue.copy()
        items = []
        while q_c:
            items.append(q_c.popleft())
        return items

    def __len__(self):
        return len(self.queue)


class StreamStr:
    """
    处理流式字符串: 根据规则, 屏蔽特殊字符
    """

    def __init__(
        self, re_full: list = None, re_pair: dict = None, re_special: list = None
    ):
        """
        re_full: 全匹配内容
        re_pair: 匹配对
        re_special: 特殊字符
        """
        self.re_full = re_full or []
        self.re_pair = re_pair or {}
        self.re_special = re_special or []
        self.txt_cache = ""

    def get_left(self) -> str:
        return self.txt_cache

    def next(self, text: str, clear: bool = False) -> str:
        """
        返回: 下一句内容
        text: 流式内容
        clear: 是否清除匹配项
        """
        text = self.wrap_text(text)
        text, self.txt_cache = self.find(text=text, clear=clear)
        return text

    def split(self, text: str, clear: bool = False, token_min_n: int = None) -> list:
        """
        返回: 按标点分割, 分割列表
        text: 流式内容
        clear: 是否清除匹配项
        token_min_n: 分割最小长度
        """
        lines = []
        # 先剔除特殊字符
        text = self.next(text=text, clear=clear)
        # 按标点分割
        if text and text.strip():
            lines = utils.split_paragraph(text=text, token_min_n=token_min_n or 4)
            if lines and not utils.endPunc(lines[-1]):
                self.txt_cache = lines.pop(-1) + self.txt_cache
        return lines

    def find(self, text: str, clear: bool = False) -> Tuple[str, str]:
        """
        返回: 剩下内容, 起始关键字的内容
        """
        text_strip = text
        # 去掉全匹配
        for rec in self.re_full:
            text_strip = rec.sub(repl="", string=text_strip)
        logger.debug("cut full: %s", text_strip)
        # 去掉特殊字符
        for ch in self.re_special:
            text_strip = text_strip.replace(ch, "")
        logger.debug("cut char: %s", text_strip)
        if clear:
            text = text_strip
        # 匹配开头
        prefix_str = ""
        for pre in self.re_pair.keys():
            idx = text_strip.find(pre)
            if idx > -1:
                prefix_str = text_strip[idx:]
                text = text[: -len(prefix_str)]
                break
        logger.debug("find prefix: %s", prefix_str)
        return text, prefix_str

    def wrap_text(self, text: str):
        if self.txt_cache:
            return self.txt_cache + text
        return text


class VolumeControl:
    device = "@DEFAULT_SINK@"

    @classmethod
    def set_mute(cls, mute: bool):
        """
        true代表是静音，false代表不是静音
        :param mute:
        :return:
        """
        if utils.is_linux():
            cls._set_mute_linux(mute=mute)
        elif utils.is_windows():
            cls._set_mute_win(mute=mute)
        else:
            raise NotImplementedError()

    @classmethod
    def set_volume(cls, volume):
        """设置系统音量（0-100）"""
        if utils.is_linux():
            cls._set_volume_linux(volume=volume)
        elif utils.is_windows():
            cls._set_volume_win(volume=volume)
        else:
            raise NotImplementedError()

    @classmethod
    def get_volume(
        cls,
    ) -> float:
        """系统音量（0-100）"""
        if utils.is_linux():
            return cls._get_volume_linux()
        elif utils.is_windows():
            return cls._get_volume_win()
        else:
            raise NotImplementedError()

    @classmethod
    def _set_mute_win(cls, mute: bool):
        """
        true代表是静音，false代表不是静音
        :param mute:
        :return:
        """
        from ctypes import cast, POINTER
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))

        # 设置静音
        volume.SetMute(1 if mute else 0, None)

    @classmethod
    def _get_volume_win(
        cls,
    ) -> float:
        """
        获取音量值，0.0代表最大，-65.25代表最小
        :return:
        """
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume_ctl = cast(interface, POINTER(IAudioEndpointVolume))
        return volume_ctl.GetMasterVolumeLevel()

    @classmethod
    def _set_volume_win(cls, volume: float):
        """
        volume 介于 [-36.0, 8.0] 或 [-65.25, 0.0]
        :param volume:
        :return:
        """
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume_ctl = cast(interface, POINTER(IAudioEndpointVolume))

        # 设置静音，mute为1代表是静音，为0代表不是静音
        volume_ctl.SetMute(0, None)
        volume_ctl.SetMasterVolumeLevel(volume, None)

    @classmethod
    def _set_mute_linux(cls, mute: bool):
        """
        true代表是静音，false代表不是静音
        :param mute:
        :return:
        """
        mute_state = 1 if mute else 0
        subprocess.run(["pactl", "set-sink-mute", cls.device, mute_state])
        # subprocess.run(["amixer", "-q", "sset", "Capture", "capture", mute_state])

    @classmethod
    def _set_volume_linux(cls, volume):
        """设置系统音量（0-100）"""
        subprocess.run(["pactl", "set-sink-volume", cls.device, f"{volume}%"])

    @classmethod
    def _get_volume_linux(
        cls,
    ) -> float:
        """系统音量（0-100）"""
        output = subprocess.check_output(
            ["pactl", "get-sink-volume", cls.device]
        ).decode()
        for line in output.split("\n"):
            if "Volume:" in line:
                parts = line.split("/")  # 65536 / 100% / 0.00 dB
                volume_str = parts[1].split("%")[0].strip()
                return int(volume_str)
        return 0


class ThreadManager:
    threads: Dict[str, threading.Thread] = dict()

    @classmethod
    def new(
        cls, group=None, target=None, name=None, args=(), kwargs=None, *, daemon=None
    ) -> threading.Thread:
        thread = threading.Thread(
            group=group, target=target, name=name, args=args, kwargs=kwargs, daemon=None
        )
        cls.threads.update({thread.name: thread})
        return thread

    @classmethod
    def get(cls, name) -> threading.Thread:
        return cls.threads.get(name, None)

    @classmethod
    def count(
        cls,
    ) -> int:
        return len(cls.threads)

    @classmethod
    def alives(
        cls,
    ) -> List[threading.Thread]:
        t_alive = list()
        for _, t in cls.threads.items():
            if t.is_alive():
                t_alive.append(t)
        return t_alive

    @classmethod
    def join(cls, timeout=None):
        for _, t in cls.threads.items():
            if t.is_alive():
                t.join(timeout=timeout)


class InfraredDevice:

    def __init__(self, port, baudrate=None, bytesize=None, parity=None, stopbits=None):
        self.port = port
        self.baudrate = baudrate or 9600  # 波特率
        self.bytesize = bytesize or serial.EIGHTBITS  # 数据位
        self.parity = parity or serial.PARITY_NONE  # 奇偶校验
        self.stopbits = stopbits or serial.STOPBITS_ONE  # 停止位

        self.ser: Optional[serial.Serial] = None
        self.reading = threading.Event()  # 检测状态
        self.cmd_detect = bytes.fromhex("55 FF AA")  # 传感器查询
        self.human_on = bytes.fromhex("5ab0a5")  # 有人
        self.human_off = bytes.fromhex("5ab2a5")  # 没人
        self.interval_seconds = 0.3  # 检测间隔时间

    def open(self):
        # 打开控制板串口 波特率：9600（8个数据位，1个停止位，无奇偶校验）
        self.ser = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            bytesize=self.bytesize,
            parity=self.parity,
            stopbits=self.stopbits,
            timeout=5,
        )
        if self.ser.is_open:
            logger.info("红外设备已打开")

    def close(
        self,
    ):
        # 关闭串口
        self.ser.close()
        if not self.ser.is_open:
            logger.info("红外设备已关闭")

    def detect_on(self, on_message) -> threading.Thread:
        """
        读取数据
        @param on_message(is_human) True-有人, False-无人
        """
        if self.reading.is_set():
            raise RuntimeError("已经有一个监听在启动.")

        def run():
            self.reading.set()
            while self.reading.is_set():
                # 发送状态指令
                self.send(data=self.cmd_detect)
                # 从串口读取数据
                d = self.ser.readline()
                # 回调
                on_message(self.is_human(d))
                # 间隔
                time.sleep(self.interval_seconds)

        # 创建线程并运行
        thread = ThreadManager.new(target=run)
        thread.start()

        return thread

    def detect_off(self):
        self.reading.clear()

    def send(self, data: bytes):
        """写入数据"""
        self.ser.write(data)

    def is_human(self, data: bytes) -> bool:
        if data == self.human_on:
            return True
        if data == self.human_off:
            return False
        return False


class Robot(object):
    __metaclass__ = ABCMeta

    @abstractmethod
    def init(self):
        pass

    @abstractmethod
    def start(self):
        pass

    @abstractmethod
    def stop(self):
        pass

    @abstractmethod
    def is_running(self):
        pass

    @abstractmethod
    def action(self, event: Enum, **kwargs):
        pass

    @abstractmethod
    def status(self):
        pass

    @abstractmethod
    def open_log(self):
        pass

    @abstractmethod
    def close_log(self):
        pass


if __name__ == "__main__":
    device = InfraredDevice(port="COM5")
    device.open()
    device.detect_on(on_message=lambda s: print("检测到人体: ", s))
    time.sleep(10)
    device.detect_off()
    time.sleep(1)
    device.close()
