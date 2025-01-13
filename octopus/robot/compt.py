# -*- coding: utf-8 -*-
from abc import ABCMeta, abstractmethod
import asyncio
import collections
import subprocess
import threading
import time
from collections import deque
from ctypes import cast, POINTER
from enum import Enum
from typing import Callable, List, Tuple, Dict, Optional

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

class EventLoopManager:

    def __init__(self):
        self.thread = ThreadManager.new(target=self._run_forever)
        self.loop = None

    def create_task(self, coro, name=None):
        if self._check_loop():
            return self.loop.create_task(coro, name=name)

    def call_soon(self, callback, *args, context=None):
        if self._check_loop():
            return self.loop.call_soon(callback, *args, context=context)

    def call_soon_threadsafe(self, callback, *args, context=None):
        if self._check_loop():
            return self.loop.call_soon_threadsafe(callback, *args, context=context)

    def run_in_executor(self, executor, func, *args):
        if self._check_loop():
            return self.loop.run_in_executor(executor, func, *args)

    def start(self):
        self.thread.start()

    def stop(self):
        self.call_soon_threadsafe(self._stop_loop)
        while self.loop.is_running():
            time.sleep(0.01)
        self.loop.close()

    def _run_forever(self):
        try:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.loop.run_forever()
            logger.info("event loop stopped.")
        except Exception as e:
            logger.critical("EventLoop运行失败: %s", str(e), stack_info=True)

    def _stop_loop(self, *args):
        self.loop.stop()

    def _check_loop(self) -> bool:
        if not self.loop:
            logger.error("event loop未初始化.")
            return False
        return True

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


class TimeoutMonitor:

    def __init__(self):
        self.data_dict = dict()
        self.key_list = list()
        self.running = threading.Event()  # 运行标识
        self.not_none = threading.Event()  # 非空标记
        self.changed = threading.Event()  # 改变标记
        self.lock = threading.Lock()
        self.interval_time = 0.01

    def start(self):
        self.running.set()
        ThreadManager.new(target=self._run).start()

    def stop(self):
        self.running.clear()
        self.not_none.set()

    def put(self, key: str, timeout: float, handle: Callable):
        end_time = time.time() + timeout
        with self.lock:
            # 清理
            if key in self.key_list:
                self.key_list.remove(key)
            # 更新dict
            self.data_dict[key] = (end_time, handle)
            # 按时间先后写入队列
            idx = len(self.key_list)
            for i, k in enumerate(self.key_list):
                end_k, _ = self.data_dict.get(k)
                if end_k > end_time:
                    idx = i
                    break
            self.key_list.insert(idx, key)
            # 状态设置
            self.changed.set()
            self.not_none.set()

    def pop(self, key: str):
        with self.lock:
            if key in self.key_list:
                self.key_list.remove(key)
            # 状态设置
            if self.data_dict.pop(key, None):
                self.changed.set()
            if not self.key_list:
                self.not_none.clear()

    def _run(self):
        while self.running.is_set():
            self.not_none.wait()
            self.changed.clear()
            # 判断队列非空
            if not self.key_list:
                continue
            # 检查第一个timeout
            key = self.key_list[0]
            end_time, handle = self.data_dict.get(key, (None, None))
            # 空处理
            if not end_time:
                self.pop(key=key)
                continue
            timeout = self._check_timeout(end_time=end_time)
            if timeout:
                ThreadManager.new(target=handle).start()  # 执行处理
                self.pop(key=key)  # 移除已处理的项

    def _check_timeout(self, end_time: time) -> bool:
        while self.running.is_set():
            # 如果队列改变, 重新检查
            if self.changed.is_set():
                return False
            # 超时
            if time.time() > end_time:
                return True
            time.sleep(self.interval_time)
        return False


class ByteBuffer(object):
    """Ring buffer to hold audio from PortAudio"""

    def __init__(self, size=1024):
        self._buf = collections.deque(maxlen=size)

    def extend(self, data):
        """Adds data to the end of buffer"""
        self._buf.extend(data)

    def get(self):
        """Retrieves data from the beginning of buffer and clears it"""
        tmp = bytes(bytearray(self._buf))
        self._buf.clear()
        return tmp

    def clear(self):
        """clear data"""
        self._buf.clear()

class CsvData:
    def __init__(self, split=None, cols=None, file=None, encoding=None):
        """
        @param split 分隔符
        @param cols 列头
        @param file 文件
        @param encoding 编码
        """
        self.split = split or ','
        self.quote = '"'
        self.cols = cols
        self.data = []
        self._empty_line = ""  # 空行
        if self.cols:
            self._empty_line = "".join([self.split for _ in range(len(self.cols))])
        # 加载数据
        if file:
            self.load(file=file, encoding=encoding)

    def load(self, file: str, encoding=None):
        utils.each_line(func=self._load_data, file=file, encoding=encoding or 'utf8')

    def all(self, cols: list = None, order_by: str = None) -> list:
        # 计算要获取的列下标
        idx_cols = [i for i in range(len(self.cols))]
        if cols:
            idx_cols = self._col_index(cols=cols)
        res = list(map(lambda row: self._to_dict(row=row, idx_cols=idx_cols), self.data))
        # 排序
        if order_by:
            res.sort(key=lambda x: x.get(order_by, ""))
        return res

    def query(self, condition: dict, cols: list = None, order_by: str = None) -> list:
        res = []
        i_c_cond = self._cond_tuple(condition=condition)
        # 计算要获取的列下标
        idx_cols = [i for i in range(len(self.cols))]
        if cols:
            idx_cols = self._col_index(cols=cols)
        for row in self.data:
            if self._filter_tuple(row=row, i_c_cond=i_c_cond):
                res.append(self._to_dict(row=row, idx_cols=idx_cols))
        # 排序
        if order_by:
            res.sort(key=lambda x: x.get(order_by, ""))
        return res

    def clear_data(self):
        self.data.clear()

    def _load_data(self, line: str, idx: int):
        # 空判断
        if self._empty_row(line):
            return
        if idx == 0:  # 首行
            if line[0] == '#':  # 加载列头(idx==0 and 以#字符开头)
                self.cols = line[1:].strip().split(self.split)
                self._empty_line = "".join([self.split for _ in range(len(self.cols))])
            else:  # 加载数据
                self.data.append(self._split_line(line=line.strip()))
            if not self.cols:
                raise RuntimeError("未指定列头")
        else:  # 加载数据
            self.data.append(self._split_line(line=line.strip()))

    def _to_dict(self, row: list, idx_cols: list = None) -> dict:
        """输出"""
        row_len = len(row)
        row_data = {}
        for idx in idx_cols:
            row_data.update({self.cols[idx]: row_len > idx and row[idx] or None})
        return row_data

    def _filter_dict(self, row: list, condition: dict) -> bool:
        """
        过滤行
        @param condition {column: value}
        """
        for col, val in condition.items():
            if col not in self.cols:
                raise RuntimeError(f"无效的字段 {col}")
            i = self.cols.index(col)
            if len(row) <= i or row[i] != val:
                return False
        return True

    @classmethod
    def _filter_tuple(cls, row: list, i_c_cond: list) -> bool:
        """
        过滤行
        @param i_c_cond [(index, value/Callable)]
        """
        for idx, val in i_c_cond:
            if len(row) <= idx:  # 下标
                return False
            if isinstance(val, Callable):  # 函数
                if not val(row[idx]):
                    return False
            elif row[idx] != val:  # 值比较
                return False
        return True

    def _col_index(self, cols: list) -> list:
        """列下标"""
        idx_ary = []
        for col in cols:
            if col not in self.cols:
                raise RuntimeError(f"无效的字段 {col}")
            idx_ary.append(self.cols.index(col))
        return idx_ary

    def _cond_tuple(self, condition: dict) -> list:
        """
        查询条件下标
        @return [(index, value)]
        """
        i_c_cond = []
        for col, val in condition.items():
            if val is None or val == "":
                continue
            if col not in self.cols:
                raise RuntimeError(f"无效的字段 {col}")
            i_c_cond.append((self.cols.index(col), val))
        return i_c_cond

    def _empty_row(self, row: str) -> bool:
        return (not row) or row.strip() == self._empty_line

    def _split_line(self, line: str) -> list:
        """
        分割一行数据(不考虑转义""的情况)
        """
        data = []
        tmp = []
        for c in line:
            # 分割
            if c == self.split:
                # 以 " 开头
                if tmp and tmp[0] == self.quote:
                    # 必须以 " 结束
                    if tmp[-1] != self.quote or len(tmp) < 2:
                        tmp.append(c)
                        continue
                    # 掐头去尾
                    tmp.pop(-1)
                    tmp.pop(0)
                data.append("".join(tmp))
                tmp.clear()
            else:
                tmp.append(c)
        # 判断"", 掐头去尾
        if len(tmp) > 1 and tmp[0] == self.quote and tmp[-1] == self.quote:
            tmp.pop(-1)
            tmp.pop(0)
        data.append("".join(tmp))
        return data

class ScreenControl:
    HWND_BROADCAST = 0xffff
    WM_SYSCOMMAND = 0x0112
    SC_MONITORPOWER = 0xF170
    monitor_power_off = 2
    SW_SHOW = 5
    ES_DEFAULT = 0x00000000
    ES_CONTINUOUS = 0x00000008  # 防止系统睡眠
    ES_SYSTEM_REQUIRED = 0x00000002  # 唤醒屏幕

    @classmethod
    def turn_on(cls):
        """
        :return:
        """
        if utils.is_linux():
            cls._turn_on_linux()
        elif utils.is_windows():
            cls._turn_on_win()
        else:
            raise NotImplementedError()

    @classmethod
    def turn_off(cls):
        """
        :return:
        """
        if utils.is_linux():
            cls._turn_off_linux()
        elif utils.is_windows():
            cls._turn_off_win()
        else:
            raise NotImplementedError()

    @classmethod
    def _turn_on_linux(cls):
        """打开屏幕（恢复亮屏）"""
        import dbus
        logger.info("执行任务: 打开屏幕")
        session_bus = dbus.SessionBus()
        screensaver = session_bus.get_object(bus_name='org.gnome.ScreenSaver',
                                             object_path='/org/gnome/ScreenSaver')
        screensaver_iface = dbus.Interface(object=screensaver,
                                           dbus_interface='org.gnome.ScreenSaver')
        screensaver_iface.SetActive(False)  # 关闭屏幕保护（亮屏）

    @classmethod
    def _turn_off_linux(cls):
        """关闭屏幕（息屏）"""
        import dbus
        logger.info("执行任务: 关闭屏幕")
        session_bus = dbus.SessionBus()
        screensaver = session_bus.get_object(bus_name='org.gnome.ScreenSaver',
                                             object_path='/org/gnome/ScreenSaver')
        screensaver_iface = dbus.Interface(object=screensaver,
                                           dbus_interface='org.gnome.ScreenSaver')
        screensaver_iface.SetActive(True)  # 启动屏幕保护（息屏）

    @classmethod
    def _turn_on_win(cls):
        # 通过模拟按键唤醒
        import pyautogui
        pyautogui.moveTo(300, 0)
        pyautogui.click()
        pyautogui.press("enter")
        # 设置系统状态，防止系统睡眠并唤醒屏幕
        # import ctypes
        # kernel32 = ctypes.windll.kernel32
        # kernel32.SetThreadExecutionState(cls.ES_CONTINUOUS | cls.ES_SYSTEM_REQUIRED)

    @classmethod
    def _turn_off_win(cls):
        import ctypes
        user32 = ctypes.windll.user32
        # 发送消息使屏幕关闭，参数2表示关闭显示器
        user32.SendMessageW(cls.HWND_BROADCAST, cls.WM_SYSCOMMAND,
                            cls.SC_MONITORPOWER, cls.monitor_power_off)


if __name__ == "__main__":
    monitor = TimeoutMonitor()
    monitor.start()
    monitor.put("t1", 5, lambda: print(5))
    monitor.put("t2", 4, lambda: print(4))
    monitor.put("t3", 3, lambda: print(3))
    time.sleep(2)
    monitor.pop("t3")
    time.sleep(10)
    monitor.stop()
