import _thread as thread
import multiprocessing
import os
import pickle
import time
import time
from watchdog.observers import Observer

from chatbot.robot import log, config, constants, statistic, Player, utils
from chatbot.robot.ConfigMonitor import ConfigMonitor
from chatbot.robot.Sender import ACTION_ROBOT_LISTEN, ACTION_ROBOT_SLEEP, ACTION_ROBOT_WRITE, ACTION_ROBOT_THINK
from chatbot.robot.sdk import LED

logger = log.getLogger(__name__)

LOCAL_REMINDER = os.path.join(constants.TEMP_PATH, "reminder.pkl")


def singleton(cls):
    _instance = {}

    def inner(conversation, sender=None):
        if cls not in _instance:
            _instance[cls] = cls(conversation=conversation, sender=sender)
        return _instance[cls]

    return inner


"""
抽象出来的生命周期，
方便在这里针对 wukong 的各个状态做定制
"""


# 单例应该在调用上, 不应该限制类本身
# @singleton
class LifeCycleHandler(object):
    _instance = None

    def __init__(self, conversation, sender):
        self._observer = Observer()
        self._unihiker = None
        self._wakeup = None
        self._conversation = conversation
        self.sender = sender

    @classmethod
    def singleton(cls, conversation, sender):
        if not cls._instance:
            cls._instance = cls(conversation=conversation, sender=sender)
        return cls._instance

    def on_init(self):
        """
        chat-robot 初始化
        """
        config.init()
        statistic.report(0)

        # 初始化配置监听器
        config_event_handler = ConfigMonitor(self._conversation)
        self._observer.schedule(config_event_handler, constants.CONFIG_PATH, False)
        self._observer.schedule(config_event_handler, constants.RS_PATH, False)
        self._observer.start()

        # 加载历史提醒
        # self._read_reminders()

        # 行空板
        # self._init_unihiker()
        # LED 灯
        # self._init_LED()
        # Muse 头环
        # self._init_muse()

    def on_sleep(self):
        self.sender.put_message(action=ACTION_ROBOT_SLEEP,
                                message="我先休息一会儿。")

    def on_wakeup(self, is_snowboy=False, notify=True):
        """
        唤醒并进入录音的状态
        """
        if not utils.is_proper_time():
            logger.warning("勿扰模式开启中")
            return
        if is_snowboy:
            logger.info("开始录音")
            utils.setRecordable(True)
        if config.get("/LED/enable", False):
            LED.wakeup()
        if self._unihiker:
            self._unihiker.record(1, "我正在聆听...")
            self._unihiker.wakeup()
        self._conversation.interrupt()
        # self._beep_hi(onCompleted=onCompleted, wait_seconds=0.5)
        if notify:
            self.sender.put_message(action=ACTION_ROBOT_LISTEN,
                                    message="我正在聆听...")

    def on_think(self):
        """
        录音结束并进入思考的状态
        """
        self._beep_lo()
        if self._unihiker:
            self._unihiker.think()
            self._unihiker.record(1, "我正在思考...")
        if config.get("/LED/enable", False):
            LED.think()
        # 返回思考步骤给前端
        self.sender.put_message(action=ACTION_ROBOT_THINK,
                                message="我正在思考...")

    def on_resp_end(self, text="", resp_uuid=None,):
        """
        思考完成并播放结果的状态
        """
        self.sender.put_message(action=ACTION_ROBOT_WRITE,
                                data=dict(end=True),
                                message="",
                                resp_uuid=resp_uuid)
        if self._unihiker:
            text = text[:60] + "..." if len(text) >= 60 else text
            self._unihiker.record(1, text)
        if config.get("/LED/enable", False):
            LED.off()

    def on_restore(self):
        """
        恢复沉浸式技能的状态
        """
        logger.info("onRestore")

    def on_killed(self):
        logger.info("onKill")
        self._observer.stop()

    def _read_reminders(self):
        logger.info("重新加载提醒信息")
        if os.path.exists(LOCAL_REMINDER):
            with open(LOCAL_REMINDER, "rb") as f:
                jobs = pickle.load(f)
                for job in jobs:
                    if "repeat" in job.remind_time or int(time.time()) < int(
                            job.job_id
                    ):
                        logger.info(f"加入提醒: {job.describe}, job_id: {job.job_id}")
                        if not (self._conversation.scheduler.has_job(job.job_id)):
                            self._conversation.scheduler.add_job(
                                job.remind_time,
                                job.original_time,
                                job.content,
                                lambda: self.alarm(
                                    job.remind_time, job.content, job.job_id
                                ),
                                job_id=job.job_id,
                            )

    def _init_unihiker(self):
        global unihiker
        if config.get("/unihiker/enable", False):
            try:
                from chatbot.robot.sdk.Unihiker import Unihiker

                self._unihiker = Unihiker()
                thread.start_new_thread(self._unihiker_shake_event, ())
            except ImportError:
                logger.error("错误：请确保当前硬件环境为行空板", stack_info=True)

    def _init_LED(self):
        if config.get("/LED/enable", False) and config.get("/LED/type") == "aiy":
            thread.start_new_thread(self._aiy_button_event, ())

    def _init_muse(self):
        if config.get("/muse/enable", False):
            try:
                from chatbot.robot import BCI

                self._wakeup = multiprocessing.Event()
                bci = BCI.MuseBCI(self._wakeup)
                bci.start()
                thread.start_new_thread(self._muse_loop_event, ())
            except ImportError:
                logger.error("错误：请确保当前硬件搭配了Muse头环并安装了相关驱动", stack_info=True)

    def _unihiker_shake_event(self):
        """
        行空板摇一摇的监听逻辑
        """
        while True:
            from pinpong.extension.unihiker import accelerometer

            if accelerometer.get_strength() >= 1.5:
                logger.info("行空板摇一摇触发唤醒")
                self._conversation.interrupt()
                query = self._conversation.active_listen()
                self._conversation.do_response(query)
            time.sleep(0.1)

    def _aiy_button_event(self):
        """
        Google AIY VoiceKit 的监听逻辑
        """
        try:
            from aiy.board import Board
        except ImportError:
            logger.error("错误：请确保当前硬件环境为Google AIY VoiceKit并正确安装了驱动", stack_info=True)
            return
        with Board() as board:
            while True:
                board.button.wait_for_press()
                logger.info("Google AIY Voicekit 触发唤醒")
                self._conversation.interrupt()
                query = self._conversation.active_listen()
                self._conversation.do_response(query)

    def _muse_loop_event(self):
        """
        Muse 头环的监听逻辑
        """
        while True:
            self._wakeup.wait()
            self._conversation.interrupt()
            logger.info("Muse 头环触发唤醒")
            query = self._conversation.active_listen()
            self._conversation.do_response(query)
            self._wakeup.clear()

    def _beep_hi(self, onCompleted=None, wait_seconds=None):
        # hi = ["在呢", "请说"]
        # self._conversation.say_simple(msg=random.choice(hi), cache=True)
        Player.play(fname=constants.getRS("beep_hi.wav"),
                    onCompleted=onCompleted,
                    wait_seconds=wait_seconds)

    def _beep_lo(self):
        # self._conversation.say_simple(msg="让我想一想", cache=True)
        Player.play(fname=constants.getRS("beep_lo.wav"))


class LifeCycleEvent(object):

    def __init__(self, handler: LifeCycleHandler = None):
        self.handler = handler

    def set_handler(self, handler: LifeCycleHandler):
        self.handler = handler

    def fire_event(self, event: str, **kwargs):
        if not self.handler:
            raise RuntimeError("LifeCycleHandler has not set.")
        func = getattr(self.handler, f"on_{event}", None)
        if func:
            return func(**kwargs)
