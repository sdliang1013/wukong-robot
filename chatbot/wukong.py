#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import hashlib
import os
import signal
import sys
from datetime import datetime

import fire
import urllib3

from chatbot.robot import config, log, utils, constants
from chatbot.robot.assistant import VoiceAssistant
from chatbot.robot.Conversation import Conversation
from chatbot.robot.LifeCycleHandler import LifeCycleEvent, LifeCycleHandler
from chatbot.robot.Sender import WebSocketSender, ACTION_ROBOT_WRITE
from chatbot.robot.Updater import Updater
from chatbot.robot.jobs import ScreenControlJob, ClearVoiceJob
from chatbot.robot.schedulers import DeferredScheduler
from chatbot.server import server
from chatbot.tools import make_json, solr_tools

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = log.getLogger(__name__)


class Wukong(object):
    _profiling = False
    _debug = False

    def __init__(self):
        self.gui = None
        self._interrupted = None
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
*          chat-robot - 中文语音对话机器人 - {}      *
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
        self._interrupted = False
        self.life_cycle_event = LifeCycleEvent()  # 生命周期事件
        self.sender = WebSocketSender()  # 信息发送
        self.conversation = Conversation(life_cycle_event=self.life_cycle_event,
                                         profiling=self._profiling,
                                         sender=self.sender)
        self.life_cycle_handler = LifeCycleHandler.singleton(conversation=self.conversation,
                                                             sender=self.sender)
        self.life_cycle_event.set_handler(handler=self.life_cycle_handler)
        self.robot = VoiceAssistant(wukong=self)
        # do something
        self.init_jobs()
        self.sender.start()
        self.robot.init()
        self.life_cycle_event.fire_event("init")
        self.conversation.set_on_stream(
            on_stream=lambda message, resp_uuid, data=None, user_id=None:
            self.sender and self.sender.put_message(action=ACTION_ROBOT_WRITE,
                                                    message=message, data=data,
                                                    resp_uuid=resp_uuid,
                                                    user_id=user_id, ))
        self.conversation.say_simple(msg=f"{config.get('first_name', '主人')}{self.get_greeting()}！",
                                     cache=True, )

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
        self._interrupted = True
        utils.clean()
        self.life_cycle_event.fire_event("killed")

    def _interrupt_callback(self):
        return self._interrupted

    def run(self):
        self.init()
        # capture SIGINT signal, e.g., Ctrl+C
        signal.signal(signal.SIGINT, self._signal_handler)
        # 后台管理端
        server.run(wk=self, debug=self._debug)
        # 启动
        self.robot.start()
        # try:
        #     # 初始化离线唤醒
        #     self.detector.detect(wukong=self)
        # except AttributeError as e:
        #     logger.exception("初始化离线唤醒功能失败")
        #     pass
        # finally:
        #     self.stop()

    def help(self):
        print(
            """=====================================================================================
    python3 wukong.py [命令]
    可选命令：
      md5                      - 用于计算字符串的 md5 值，常用于密码设置
      update                   - 手动更新 chat-robot
      upload [thredNum]        - 手动上传 QA 集语料，重建 solr 索引。
                                 threadNum 表示上传时开启的线程数（可选。默认值为 10）
      profiling                - 运行过程中打印耗时数据
    如需更多帮助，请访问：https://wukong.hahack.com/#/run
====================================================================================="""
        )

    def md5(self, password):
        """
        计算字符串的 md5 值
        """
        return hashlib.md5(str(password).encode("utf-8")).hexdigest()

    def update(self):
        """
        更新 chat-robot
        """
        updater = Updater()
        return updater.update()

    def fetch(self):
        """
        检测 chat-robot 的更新
        """
        updater = Updater()
        updater.fetch()

    def upload(self, threadNum=10):
        """
        手动上传 QA 集语料，重建 solr 索引
        """
        try:
            qaJson = os.path.join(constants.TEMP_PATH, "qa_json")
            make_json.run(constants.getQAPath(), qaJson)
            solr_tools.clear_documents(
                config.get("/anyq/host", "0.0.0.0"),
                "collection1",
                config.get("/anyq/solr_port", "8900"),
            )
            solr_tools.upload_documents(
                config.get("/anyq/host", "0.0.0.0"),
                "collection1",
                config.get("/anyq/solr_port", "8900"),
                qaJson,
                threadNum,
            )
        except Exception as e:
            logger.critical("上传失败：%s", str(e), exc_info=True)

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
        # self.detector and self.detector.terminate()


def main():
    if len(sys.argv) == 1:
        wu_kong = Wukong()
        wu_kong.run()
    elif "-h" in (sys.argv):
        wu_kong = Wukong()
        wu_kong.help()
    else:
        fire.Fire(Wukong)


if __name__ == "__main__":
    main()
