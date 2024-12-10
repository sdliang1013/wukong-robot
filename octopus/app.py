#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import hashlib
import os
import signal
import sys
from datetime import datetime

import fire
import urllib3

from octopus.robot import config, log, utils, constants
from octopus.robot.assistant import VoiceAssistant
from octopus.robot.Conversation import Conversation
from octopus.robot.LifeCycleHandler import LifeCycleEvent, LifeCycleHandler
from octopus.robot.Sender import WebSocketSender, ACTION_ROBOT_WRITE
from octopus.robot.jobs import ScreenControlJob, ClearVoiceJob
from octopus.robot.schedulers import DeferredScheduler
from octopus.web import server

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = log.getLogger(__name__)


class Octopus(object):
    _profiling = False
    _debug = False

    def __init__(self):
        self.gui = None
        self.life_cycle_event = None
        self.sender = None
        self.robot = None
        self.conversation = None
        self.life_cycle_handler = None
        self.scheduler = None

    def init(self):
        print(
            """
********************************************************
*          octopus - 中文语音对话机器人 - {}      *
********************************************************

            后台管理端：http://{}:{}
            如需退出，可以按 Ctrl-4 组合键

""".format(
                utils.get_file_content(
                    os.path.join(constants.APP_PATH, "VERSION"), "r"
                ).strip(),
                config.get("/server/host", "0.0.0.0"),
                config.get("/server/port", "5001"),
            )
        )
        self.gui = None
        self.life_cycle_event = LifeCycleEvent()  # 生命周期事件
        self.sender = WebSocketSender()  # 信息发送
        self.conversation = Conversation(
            life_cycle_event=self.life_cycle_event,
            profiling=self._profiling,
            sender=self.sender,
        )
        self.life_cycle_handler = LifeCycleHandler.singleton(
            conversation=self.conversation, sender=self.sender
        )
        self.life_cycle_event.set_handler(handler=self.life_cycle_handler)
        self.robot = VoiceAssistant(octopus=self)
        # do something
        self.init_jobs()
        self.sender.start()
        self.robot.init()
        self.life_cycle_event.fire_event("init")
        self.conversation.set_on_stream(
            on_stream=lambda message, resp_uuid, data=None, user_id=None: self.sender
            and self.sender.put_message(
                action=ACTION_ROBOT_WRITE,
                message=message,
                data=data,
                resp_uuid=resp_uuid,
                user_id=user_id,
            )
        )
        self.conversation.say_simple(
            msg=f"{config.get('first_name', '主人')}{self.get_greeting()}！",
            cache=True,
        )

    def init_jobs(self):
        self.scheduler = DeferredScheduler()
        # add jobs
        ScreenControlJob(scheduler=self.scheduler)
        ClearVoiceJob(scheduler=self.scheduler)
        self.scheduler.start()

    def get_greeting(self):
        # 获取当前小时
        current_hour = datetime.now().hour

        if 5 <= current_hour < 12:
            return "上午好"
        elif 12 <= current_hour < 14:
            return "中午好"
        elif 14 <= current_hour < 18:
            return "下午好"
        else:
            return "晚上好"

    def _signal_handler(self, signal, frame):
        utils.clean()
        self.life_cycle_event.fire_event("killed")
        self.stop()

    def run(self):
        self.init()
        # capture SIGINT signal, e.g., Ctrl+C
        signal.signal(signal.SIGINT, self._signal_handler)
        # 后台管理端
        server.run(octopus=self, debug=self._debug)
        # 启动
        self.robot.start()

    def help(self):
        print(
            """=====================================================================================
    python3 octopus.py [命令]
    可选命令：
      md5                      - 用于计算字符串的 md5 值，常用于密码设置
      profiling                - 运行过程中打印耗时数据
    如需更多帮助，请访问：https://octopus.hahack.com/#/run
====================================================================================="""
        )

    def md5(self, password):
        """
        计算字符串的 md5 值
        """
        return hashlib.md5(str(password).encode("utf-8")).hexdigest()

    def restart(self):
        """
        重启 chat-robot
        """
        logger.info("程序重启...")
        try:
            self.stop()
        except AttributeError:
            pass
        python = sys.executable
        os.execl(python, python, *sys.argv)

    def profiling(self):
        """
        运行过程中打印耗时数据
        """
        logger.info("性能调优")
        self._profiling = True
        self.run()

    def debug(self):
        """
        调试模式启动服务
        """
        logger.info("进入调试模式")
        self._debug = True
        self.run()

    def stop(self):
        self.scheduler and self.scheduler.stop()
        self.sender and self.sender.stop()
        self.robot and self.robot.stop()


def main():
    if len(sys.argv) == 1:
        app = Octopus()
        app.run()
    elif "-h" in (sys.argv):
        app = Octopus()
        app.help()
    else:
        fire.Fire(Octopus)


if __name__ == "__main__":
    main()
