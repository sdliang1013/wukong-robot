import sys
from octopus.robot import log
from octopus.robot import constants
from abc import ABCMeta, abstractmethod

logger = log.getLogger(__name__)

try:
    sys.path.append(constants.CONTRIB_PATH)
except Exception as e:
    logger.error(f"未检测到插件目录, Error: {e}", stack_info=True)


class AbstractPlugin(metaclass=ABCMeta):
    """技能插件基类"""

    SLUG = "AbstractPlugin"
    IS_IMMERSIVE = False

    def __init__(self, con):
        if self.IS_IMMERSIVE:
            self.isImmersive = self.IS_IMMERSIVE
        else:
            self.isImmersive = False
        self.priority = 0
        self.con = con
        self.nlu = self.con.nlu

    @abstractmethod
    def isValid(self, query, parsed):
        """
        是否适合由该插件处理

        参数：
        query -- 用户的指令字符串
        parsed -- 用户指令经过 NLU 解析后的结果

        返回：
        True: 适合由该插件处理
        False: 不适合由该插件处理
        """
        return False

    @abstractmethod
    def handle(self, query, parsed):
        """
        处理逻辑

        参数：
        query -- 用户的指令字符串
        parsed -- 用户指令经过 NLU 解析后的结果
        """
        pass

    def isValidImmersive(self, query, parsed):
        """
        是否适合在沉浸模式下处理，
        仅适用于有沉浸模式的插件（如音乐等）
        当用户唤醒时，可以响应更多指令集。
        例如：“"上一首"、"下一首" 等
        """
        return False

    def pause(self):
        """
        暂停当前正在处理的任务，
        当处于该沉浸模式下且被唤醒时，
        将自动触发这个方法，
        可以用于强制暂停一个耗时的操作
        """
        return

    def restore(self):
        """
        恢复当前插件，
        仅适用于有沉浸模式的插件（如音乐等）
        当用户误唤醒或者唤醒进行闲聊后，
        可以自动恢复当前插件的处理逻辑
        """
        return

    def play(self, src, delete=False, onCompleted=None, volume=1, interrupt=False):
        """
        播放音频

        :param src: 要播放的音频地址
        :param delete: 播放完成是否要删除，默认不删除
        :param onCompleted: 播放完后的回调
        :param volume: 音量
        :param interrupt: 中断之前的播放
        """
        self.con.play(src, delete, onCompleted, volume, interrupt)

    def say(self, text, cache=False, onCompleted=None, wait=False):
        """
        使用TTS说一句话

        :param text: 要说话的内容
        :param cache: 是否要缓存该音频，默认不缓存
        :param onCompleted: 播放完后的回调
        :param wait: 已废弃
        """
        self.con.say_simple(
            msg=text, cache=cache, plugin=self.SLUG, onCompleted=onCompleted
        )

    def active_listen(self, silent=False):
        if (
            self.SLUG != "geek"
            and self.con.get_immersive_mode()
            and self.con.get_immersive_mode() == "geek"
        ):
            # 极客模式下禁止其他插件主动聆听，以避免异常问题
            self.con.interrupt()
            # self.critical("错误：请退出极客模式后再试")
            self.say("错误：请退出极客模式后再试")
            return ""
        return self.con.active_listen(silent)

    def clearImmersive(self):
        self.con.setImmersiveMode(None)

    def parse(self, query):
        """
        NLU 解析
        """
        return self.con.do_parse(query)
