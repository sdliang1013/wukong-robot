# -*- coding: utf-8 -*-
import time
import uuid
from abc import ABCMeta, abstractmethod
import datetime
import dbus

from octopus.robot import config, log, utils
from octopus.robot.schedulers import DeferredScheduler

logger = log.getLogger(__name__)


class ConfigJob(object):
    __metaclass__ = ABCMeta
    SLUG = "default"

    def __init__(self, scheduler: DeferredScheduler, id=None, name=None, **kwargs):
        enabled = config.get(f"/jobs/{self.SLUG}/enabled", True)
        if enabled:
            scheduler.add_job(
                id=id or uuid.uuid4().hex,
                name=name or self.SLUG,
                func=self.run_job,
                **kwargs,
            )
            logger.info("添加任务: %s", name or self.SLUG)

    @abstractmethod
    def run_job(self): ...


class ScreenControlJob(ConfigJob):
    """应用息屏任务(每天19-6点)"""

    SLUG = "screen_control"

    def __init__(self, scheduler: DeferredScheduler, **kwargs):
        self.hour_start = datetime.time(
            hour=config.get(f"/jobs/{self.SLUG}/hour_start", 19)
        )
        self.hour_end = datetime.time(hour=config.get(f"/jobs/{self.SLUG}/hour_end", 6))
        self.interval_minute = 10
        super().__init__(
            scheduler=scheduler,
            trigger="interval",
            minutes=self.interval_minute,
            **kwargs,
        )

    def run_job(self):
        # 是否控制时间
        now_time = datetime.datetime.now().time()
        if not self.valid_time(now_time=now_time):
            return
        try:
            if self.is_on(now_time=now_time):
                self.turn_on()  # 亮屏
                time.sleep(5)
                self.turn_on()  # 亮屏
            else:
                self.turn_off()  # 息屏
        except:
            logger.critical(msg="屏幕控制异常.", exc_info=True)

    def valid_time(self, now_time: datetime.time) -> bool:
        """是否控制时间"""
        if self.hour_start < self.hour_end:
            return self.hour_start <= now_time <= self.hour_end
        return self.hour_start <= now_time or now_time <= self.hour_end

    def is_on(self, now_time: datetime.time) -> bool:
        """是否亮屏, 最后一个周期"""
        minute_now = now_time.hour * 60 + now_time.minute
        minute_end = self.hour_end.hour * 60 + self.hour_end.minute
        return abs(minute_now - minute_end) <= self.interval_minute

    def turn_off(self):
        """关闭屏幕（息屏）"""
        logger.info("执行任务: 关闭屏幕")
        session_bus = dbus.SessionBus()
        screensaver = session_bus.get_object(
            bus_name="org.gnome.ScreenSaver", object_path="/org/gnome/ScreenSaver"
        )
        screensaver_iface = dbus.Interface(
            object=screensaver, dbus_interface="org.gnome.ScreenSaver"
        )
        screensaver_iface.SetActive(True)  # 启动屏幕保护（息屏）

    def turn_on(self):
        """打开屏幕（恢复亮屏）"""
        logger.info("执行任务: 打开屏幕")
        session_bus = dbus.SessionBus()
        screensaver = session_bus.get_object(
            bus_name="org.gnome.ScreenSaver", object_path="/org/gnome/ScreenSaver"
        )
        screensaver_iface = dbus.Interface(
            object=screensaver, dbus_interface="org.gnome.ScreenSaver"
        )
        screensaver_iface.SetActive(False)  # 关闭屏幕保护（亮屏）

    def toggle(self):
        session_bus = dbus.SessionBus()
        screensaver = session_bus.get_object(
            bus_name="org.gnome.ScreenSaver", object_path="/org/gnome/ScreenSaver"
        )
        screensaver_iface = dbus.Interface(
            object=screensaver, dbus_interface="org.gnome.ScreenSaver"
        )
        active = screensaver_iface.GetActive()
        logger.info("切换屏保程序状态: %s", not active)
        screensaver_iface.SetActive(not active)


class ClearVoiceJob(ConfigJob):
    """清理音频文件"""

    SLUG = "clear_voice"

    def __init__(self, scheduler: DeferredScheduler, **kwargs):
        self.file = config.get(f"/jobs/{self.SLUG}/file", "*.mp3")
        self.days = config.get(f"/jobs/{self.SLUG}/days", 7)
        super().__init__(scheduler=scheduler, trigger="interval", days=1, **kwargs)

    def run_job(self):
        """清理缓存"""
        logger.info("执行任务: 清理音频文件")
        utils.clear_voice_cache(file=self.file, days=self.days)
