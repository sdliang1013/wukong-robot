# -*- coding: utf-8 -*-
import asyncio
import os
import platform
import queue
import signal
import subprocess
import threading
import time
from contextlib import contextmanager
from ctypes import CFUNCTYPE, c_char_p, c_int, cdll

from chatbot.robot import log, utils
from chatbot.robot.compt import ThreadManager

logger = log.getLogger(__name__)


def py_error_handler(filename, line, function, err, fmt):
    pass


ERROR_HANDLER_FUNC = CFUNCTYPE(None, c_char_p, c_int, c_char_p, c_int, c_char_p)

c_error_handler = ERROR_HANDLER_FUNC(py_error_handler)


@contextmanager
def no_alsa_error():
    try:
        asound = cdll.LoadLibrary("libasound.so")
        asound.snd_lib_error_set_handler(c_error_handler)
        yield
        asound.snd_lib_error_set_handler(None)
    except:
        yield
        pass


_player_ = None


def play(fname, delete=False, onCompleted=None, wait_seconds=None):
    player = getPlayerByFileName(fname)
    player.play(src=fname, delete=delete,
                onCompleted=onCompleted, wait_seconds=wait_seconds)


def stop():
    global _player_
    if _player_:
        _player_.stop()


def getPlayerByFileName(fname):
    global _player_
    foo, ext = os.path.splitext(fname)
    if ext in [".mp3", ".wav"]:
        if not _player_ or not _player_.is_alive():
            _player_ = SoxPlayer()
        return _player_


class AbstractPlayer(object):
    def __init__(self, **kwargs):
        super(AbstractPlayer, self).__init__()

    def play(self):
        pass

    def play_block(self):
        pass

    def stop(self):
        pass

    def is_playing(self):
        return False

    def join(self):
        pass


class SoxPlayer(AbstractPlayer):
    SLUG = "SoxPlayer"

    def __init__(self, **kwargs):
        super(SoxPlayer, self).__init__(**kwargs)
        self.playing = False
        self.proc = None
        self.playing_src = None
        self.playing_del = False
        self.empty_calls = []
        # 创建一个锁用于保证同一时间只有一个音频在播放
        self.play_lock = threading.Lock()
        self.play_queue = self._init_queue()  # 播放队列
        self.consumer_thread = ThreadManager.new(target=self.play_loop)
        self.consumer_thread.start()
        self.loop = asyncio.new_event_loop()  # 创建事件循环
        self.thread_loop = ThreadManager.new(target=self.loop.run_forever)
        self.thread_loop.start()

    def execute_on_completed(self, res, on_completed):
        # 单个播放完成
        if res and on_completed:
            on_completed()
        # 全部播放完成
        if self.play_queue.empty():
            for empty_call in self.empty_calls:
                if empty_call:
                    empty_call()

    def play_loop(self):
        while True:
            (src, onCompleted, delete) = self.play_queue.get()
            if not src:
                continue
            with self.play_lock:
                logger.debug("开始播放音频：%s", src)
                self.playing_src = src
                self.playing_del = delete
                res = False
                try:
                    res = self.doPlay(src)
                    self.play_queue.task_done()
                finally:
                    self.playing_src = None
                    self.playing_del = False
                    # 将 onCompleted() 方法的调用放到事件循环的线程中执行
                    self.loop.call_soon_threadsafe(
                        self.execute_on_completed, res, onCompleted
                    )
                    if delete:
                        utils.check_and_delete(src)

    def doPlay(self, src):
        system = platform.system()
        if system == "Darwin":
            cmd = ["afplay", str(src)]
        else:
            cmd = ["play", str(src)]
        logger.debug("Executing %s", " ".join(cmd))
        self.proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        self.playing = True
        self.proc.wait()
        self.playing = False
        logger.debug("播放完成：%s", src)
        return self.proc and self.proc.returncode == 0

    def play(self, src, delete=False, onCompleted=None, wait_seconds: int = 0, **kwargs):
        if not src:
            logger.warning("path should not be none")
            return
        if os.path.exists(src) or src.startswith("http"):
            self.play_queue.put((src, onCompleted, delete))
            if wait_seconds:
                time.sleep(wait_seconds)
        else:
            logger.error("path not exists: %s", src, stack_info=True)

    def preappend_completed(self, onCompleted):
        onCompleted and self.empty_calls.insert(0, onCompleted)

    def append_on_completed(self, onCompleted):
        onCompleted and self.empty_calls.append(onCompleted)

    def play_block(self):
        self.play_loop()

    def stop(self):
        if self.proc:
            self.proc.terminate()
            self.proc.kill()
            self.proc = None
        if self.playing_del and self.playing_src:
            utils.check_and_delete(self.playing_src)
            self.playing_src = None
            self.playing_del = False
        self.playing = False
        self.empty_calls = []
        self._clear_queue()

    def is_playing(self):
        return self.playing or not self.play_queue.empty()

    def join(self):
        if self.play_queue.empty():
            return
        self.play_queue.join()

    def _clear_queue(self):
        while not self.play_queue.empty():
            self.play_queue.get()
            self.play_queue.task_done()

    def is_alive(self):
        return self.consumer_thread.is_alive() and self.thread_loop.is_alive()

    def _init_queue(self):
        return queue.Queue()


class MusicPlayer(SoxPlayer):
    """
    给音乐播放器插件使用的，
    在 SOXPlayer 的基础上增加了列表的支持，
    并支持暂停和恢复播放
    """

    SLUG = "MusicPlayer"

    def __init__(self, playlist, plugin, **kwargs):
        super(MusicPlayer, self).__init__(**kwargs)
        self.playlist = playlist
        self.plugin = plugin
        self.idx = 0
        self.pausing = False

    def update_playlist(self, playlist):
        super().stop()
        self.playlist = playlist
        self.idx = 0
        self.play()

    def play(self):
        logger.debug("MusicPlayer play")
        path = self.playlist[self.idx]
        super().stop()
        super().play(path, False, self.next)

    def next(self):
        logger.debug("MusicPlayer next")
        super().stop()
        self.idx = (self.idx + 1) % len(self.playlist)
        self.play()

    def prev(self):
        logger.debug("MusicPlayer prev")
        super().stop()
        self.idx = (self.idx - 1) % len(self.playlist)
        self.play()

    def pause(self):
        logger.debug("MusicPlayer pause")
        self.pausing = True
        if self.proc:
            os.kill(self.proc.pid, signal.SIGSTOP)

    def stop(self):
        if self.proc:
            logger.debug(f"MusicPlayer stop {self.proc.pid}")
            self.onCompleteds = []
            os.kill(self.proc.pid, signal.SIGSTOP)
            self.proc.terminate()
            self.proc.kill()
            self.proc = None

    def resume(self):
        logger.debug("MusicPlayer resume")
        self.pausing = False
        self.onCompleteds = [self.next]
        if self.proc:
            os.kill(self.proc.pid, signal.SIGCONT)

    def is_playing(self):
        return self.playing

    def is_pausing(self):
        return self.pausing

    def turnUp(self):
        system = platform.system()
        if system == "Darwin":
            res = subprocess.run(
                ["osascript", "-e", "output volume of (get volume settings)"],
                shell=False,
                capture_output=True,
                universal_newlines=True,
            )
            volume = int(res.stdout.strip())
            volume += 20
            if volume >= 100:
                volume = 100
                self.plugin.say("音量已经最大啦")
            subprocess.run(["osascript", "-e", f"set volume output volume {volume}"])
        elif system == "Linux":
            res = subprocess.run(
                ["amixer sget Master | grep 'Mono:' | awk -F'[][]' '{ print $2 }'"],
                shell=True,
                capture_output=True,
                universal_newlines=True,
            )
            if res.stdout != "" and res.stdout.strip().endswith("%"):
                volume = int(res.stdout.strip().replace("%", ""))
                volume += 20
                if volume >= 100:
                    volume = 100
                    self.plugin.say("音量已经最大啦")
                subprocess.run(["amixer", "set", "Master", f"{volume}%"])
            else:
                subprocess.run(["amixer", "set", "Master", "20%+"])
        else:
            self.plugin.say("当前系统不支持调节音量")
        self.resume()

    def turnDown(self):
        system = platform.system()
        if system == "Darwin":
            res = subprocess.run(
                ["osascript", "-e", "output volume of (get volume settings)"],
                shell=False,
                capture_output=True,
                universal_newlines=True,
            )
            volume = int(res.stdout.strip())
            volume -= 20
            if volume <= 20:
                volume = 20
                self.plugin.say("音量已经很小啦")
            subprocess.run(["osascript", "-e", f"set volume output volume {volume}"])
        elif system == "Linux":
            res = subprocess.run(
                ["amixer sget Master | grep 'Mono:' | awk -F'[][]' '{ print $2 }'"],
                shell=True,
                capture_output=True,
                universal_newlines=True,
            )
            if res.stdout != "" and res.stdout.endswith("%"):
                volume = int(res.stdout.replace("%", "").strip())
                volume -= 20
                if volume <= 20:
                    volume = 20
                    self.plugin.say("音量已经最小啦")
                subprocess.run(["amixer", "set", "Master", f"{volume}%"])
            else:
                subprocess.run(["amixer", "set", "Master", "20%-"])
        else:
            self.plugin.say("当前系统不支持调节音量")
        self.resume()


class OrderPlayer(SoxPlayer):
    SLUG = "OrderPlayer"

    def __init__(self, **kwargs):
        super(OrderPlayer, self).__init__(**kwargs)

    def play(self, src, index, delete=False, onCompleted=None, wait_seconds: int = 0):
        if not src:
            logger.warning("path should not be none")
            return
        if os.path.exists(src) or src.startswith("http"):
            self.play_queue.put(index=index, item=(src, onCompleted, delete))
            if wait_seconds:
                time.sleep(wait_seconds)
        else:
            logger.error("path not exists: %s", src, stack_info=True)

    def new_order(self):
        self.play_queue.clear()

    def _clear_queue(self):
        while not self.play_queue.empty():
            self.play_queue.get_notnull()
            self.play_queue.task_done()
        self.play_queue.clear()

    def _init_queue(self):
        return OrderQueue()


class OrderQueue(queue.Queue):
    NULL = type('NULL', (object,), {})

    def __init__(self, **kwargs):
        self._next = 0
        super(OrderQueue, self).__init__(**kwargs)

    def clear(self):
        with self.not_empty:
            self._next = 0
            self.queue.clear()
            self.not_full.notify()

    def put(self, index, item, block=True, timeout=None):
        super().put(item=(index, item), block=block, timeout=timeout)

    def put_nowait(self, index, item):
        super().put_nowait(item=(index, item))

    def get(self, block=True, timeout=None):
        with self.not_empty:
            if not block:
                if self._is_empty():
                    raise queue.Empty
            elif timeout is None:
                while self._is_empty():
                    self.not_empty.wait()
            elif timeout < 0:
                raise ValueError("'timeout' must be a non-negative number")
            else:
                end_time = time.monotonic() + timeout
                while self._is_empty():
                    remaining = end_time - time.monotonic()
                    if remaining <= 0.0:
                        raise queue.Empty
                    self.not_empty.wait(remaining)
            item = self._get()
            self.not_full.notify()
            return item

    def get_notnull(self):
        """返回没有顺序的内容"""
        with self.not_empty:
            if not self._qsize():
                raise queue.Empty
            item = self._get()
            while item is self.NULL and self._qsize():
                item = self._get()
            self.not_full.notify()
            return item

    def _init(self, maxsize):
        self.queue = []

    def _qsize(self):
        return len(self.queue) - self._next

    def _put(self, item):
        index, data = item
        if isinstance(index, dict):
            if "index" not in index:
                raise RuntimeError("非法参数, 参数必须包含: index")
            index = index["index"]
        self._append_item(index=index, item=data)

    def _get(self):
        item = self.queue[self._next]
        self._next += 1
        return item

    def _append_item(self, index, item):
        ext = index - len(self.queue)
        if ext < 0:
            self.queue.pop(index)
            self.queue.insert(index, item)
        else:
            if ext > 0:
                self.queue.extend([self.NULL for _ in range(ext)])
            self.queue.append(item)

    def _is_empty(self):
        return not self._qsize() or self.queue[self._next] is self.NULL


def test(que: queue.Queue):
    while True:
        data = que.get()
        print(data)


if __name__ == '__main__':
    q = OrderQueue()
    t = threading.Thread(target=test, kwargs=dict(que=q))
    t.start()
    q.put(2, dict(index=1))
    q.put(1, (0, 2, 4))
    q.put(0, 0)
    time.sleep(1)
    q.put(4, 3)
    q.put(5, 4)
    q.put(3, 2)
    time.sleep(1)
