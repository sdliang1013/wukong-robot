# -*- coding: utf-8 -*-
import time
from typing import Tuple
import uuid
import cProfile
import pstats
import io
import re
import os
import threading
import traceback

from concurrent.futures import ThreadPoolExecutor, as_completed

from snowboy import snowboydecoder

from robot.LifeCycleHandler import LifeCycleHandler
from robot.Brain import Brain
from robot.Scheduler import Scheduler
from robot.sdk import History
from robot.Sender import (
    StatusData,
    ACTION_USER_SPEAK,
    ACTION_ROBOT_THINK,
    STAGE_UNDERSTAND,
    STAGE_SEARCH,
    ACTION_ROBOT_SPEAK,
)
from robot import (
    AI,
    ASR,
    config,
    constants,
    logging,
    NLU,
    Player,
    statistic,
    TTS,
    DigitalHuman,
    utils,
)

re_tts = {
    "full": [
        re.compile(pattern=r"```.+```"),
        re.compile(pattern=r"!\[[^\]]*\]\([^\)]*\)"),
    ],
    "pair": {r"```": r"```", r"![": r")"},
    "char": ["**"],
}


logger = logging.getLogger(__name__)


class Conversation(object):

    def __init__(self, profiling=False, sender=None):
        self.brain, self.asr, self.ai, self.tts, self.nlu = None, None, None, None, None
        self.dh_enabled = config.get("/dh_engine/enable", False)
        self.dh = None
        self.reInit()
        self.scheduler = Scheduler(self)
        # 历史会话消息
        self.history = History.History()
        # 沉浸模式，处于这个模式下，被打断后将自动恢复这个技能
        self.match_plugin = None
        self.immersive_mode = None
        self.is_recording = False
        self.profiling = profiling
        self.on_say = None
        self.on_stream = None
        self.has_pardon = False
        self.player = Player.OrderPlayer()
        self.sender = sender
        self.life_cycle_handler = LifeCycleHandler(self, sender=sender)
        self.tts_count = 0
        self.tts_index = 0
        self.tts_lock = threading.Lock()
        self.play_lock = threading.Lock()

    def _lastCompleted(self, index, onCompleted):
        # logger.debug(f"{index}, {self.tts_index}, {self.tts_count}")
        if index >= self.tts_count - 1:
            # logger.debug(f"执行onCompleted")
            onCompleted and onCompleted()

    def _ttsAction(self, msg, cache, index, onCompleted=None):
        if msg:
            voice = ""
            if utils.getCache(msg):
                logger.info(f"第{index}段TTS命中缓存，播放缓存语音")
                voice = utils.getCache(msg)
                with self.play_lock:
                    self.player.play(
                        src=voice,
                        delete=not cache,
                        onCompleted=lambda: self._lastCompleted(index, onCompleted),
                        index=index,
                    )
                return voice
            else:
                try:
                    voice = self.tts.get_speech(msg)
                    logger.info(msg=f"第{index}段TTS合成成功。msg: {msg}")
                    with self.play_lock:
                        logger.info(f"即将播放第{index}段TTS。msg: {msg}")
                        self.player.play(
                            src=voice,
                            delete=not cache,
                            onCompleted=lambda: self._lastCompleted(index, onCompleted),
                            index=index,
                        )
                    return voice
                except Exception as e:
                    logger.error(f"语音合成失败：{e}", stack_info=True)
                    traceback.print_exc()
                    return None

    def getHistory(self):
        return self.history

    def interrupt(self):
        if self.player and self.player.is_playing():
            self.player.stop()
        if self.immersive_mode:
            self.brain.pause()
        if self.dh:
            self.dh.interrupt()

    def reInit(self):
        """重新初始化"""
        try:
            self.asr = ASR.get_engine_by_slug(config.get("asr_engine", "tencent-asr"))
            self.ai = AI.get_robot_by_slug(config.get("robot", "tuling"))
            self.tts = TTS.get_engine_by_slug(config.get("tts_engine", "baidu-tts"))
            if self.dh_enabled:
                self.dh = DigitalHuman.get_engine_by_slug(
                    config.get("/dh_engine/provider", "tencent-dh")
                )
            self.nlu = NLU.get_engine_by_slug(config.get("nlu_engine", "unit"))
            self.player = Player.OrderPlayer()
            self.brain = Brain(self)
            self.brain.printPlugins()
        except Exception as e:
            logger.critical(f"对话初始化失败：{e}", stack_info=True)

    def checkRestore(self):
        if self.immersive_mode:
            logger.info("处于沉浸模式，恢复技能")
            self.life_cycle_handler.onRestore()
            self.brain.restore()

    def _InGossip(self, query):
        return self.immersive_mode in ["Gossip"] and not "闲聊" in query

    def doResponse(self, query, UUID="", onSay=None, onStream=None):
        """
        响应指令

        :param query: 指令
        :UUID: 指令的UUID
        :onSay: 朗读时的回调
        :onStream: 流式输出时的回调
        """
        statistic.report(1)
        self.interrupt()
        self.appendHistory(0, query, UUID)

        if onSay:
            self.on_say = onSay

        if onStream:
            self.on_stream = onStream

        if query.strip() == "":
            self.pardon()
            return

        # lastImmersiveMode = self.immersiveMode
        # todo 先屏蔽NLU处理
        # parsed = self.doParse(query)
        # if self._InGossip(query) or not self.brain.query(query, parsed):
        # else:
        #     # 命中技能
        #     if lastImmersiveMode and lastImmersiveMode != self.matchPlugin:
        #         if self.player:
        #             if self.player.is_playing():
        #                 logger.debug("等说完再checkRestore")
        #                 self.player.appendOnCompleted(lambda: self.checkRestore())
        #         else:
        #             logger.debug("checkRestore")
        #             self.checkRestore()
        parsed = {"Domain": "", "Intent": "", "Slot": query}
        # 进入闲聊
        if "闭嘴" in query or "暂停" in query:
            # 停止说话
            self.player.stop()
        else:
            # 没命中技能，使用机器人回复
            if self.ai.support_stream():
                self.sender.send_message(
                    action=ACTION_ROBOT_THINK,
                    data=StatusData(stage=STAGE_SEARCH, status="start"),
                    message="开始查找资料",
                )
                stream = self.ai.stream_chat(query)
                self.sender.send_message(
                    action=ACTION_ROBOT_THINK,
                    data=StatusData(stage=STAGE_SEARCH, status="end"),
                    message="查找资料结束",
                )
                self.stream_say(stream, True, onCompleted=self.checkRestore)
            else:
                self.sender.send_message(
                    action=ACTION_ROBOT_THINK,
                    data=StatusData(stage=STAGE_SEARCH, status="start"),
                    message="开始查找资料",
                )
                msg = self.ai.chat(query, parsed)
                self.sender.send_message(
                    action=ACTION_ROBOT_THINK,
                    data=StatusData(stage=STAGE_SEARCH, status="end"),
                    message="查找资料结束",
                )
                self.say(msg, True, onCompleted=self.checkRestore)

    def doParse(self, query):
        args = {
            "service_id": config.get("/unit/service_id", "S13442"),
            "api_key": config.get("/unit/api_key", "w5v7gUV3iPGsGntcM84PtOOM"),
            "secret_key": config.get(
                "/unit/secret_key", "KffXwW6E1alcGplcabcNs63Li6GvvnfL"
            ),
        }
        return self.nlu.parse(query, **args)

    def setImmersiveMode(self, slug):
        self.immersive_mode = slug

    def getImmersiveMode(self):
        return self.immersive_mode

    def converse(self, fp, callback=None):
        """核心对话逻辑"""
        logger.info("结束录音")
        self.life_cycle_handler.onThink()
        self.is_recording = False
        if self.profiling:
            logger.info("性能调试已打开")
            pr = cProfile.Profile()
            pr.enable()
            self.doConverse(fp, callback)
            pr.disable()
            s = io.StringIO()
            sortby = "cumulative"
            ps = pstats.Stats(pr, stream=s).sort_stats(sortby)
            ps.print_stats()
            print(s.getvalue())
        else:
            self.doConverse(fp, callback)

    def doConverse(self, fp, callback=None, onSay=None, onStream=None):
        self.interrupt()
        try:
            query = self.asr.transcribe(fp)
        except Exception as e:
            logger.critical(f"ASR识别失败：{e}", stack_info=True)
            traceback.print_exc()
        utils.check_and_delete(fp)
        try:
            self.doResponse(query, callback, onSay, onStream)
        except Exception as e:
            logger.critical(f"回复失败：{e}", stack_info=True)
            traceback.print_exc()
        utils.clean()

    def appendHistory(self, t, text, UUID="", plugin=""):
        """将会话历史加进历史记录"""
        if t in (0, 1) and text:
            if text.endswith(",") or text.endswith("，"):
                text = text[:-1]
            if UUID == "" or UUID == None or UUID == "null":
                UUID = str(uuid.uuid1())
            # 将图片处理成HTML
            pattern = r"https?://.+\.(?:png|jpg|jpeg|bmp|gif|JPG|PNG|JPEG|BMP|GIF)"
            url_pattern = r"^https?://.+"
            imgs = re.findall(pattern, text)
            for img in imgs:
                text = text.replace(
                    img,
                    f'<a data-fancybox="images" href="{img}"><img src={img} class="img fancybox"></img></a>',
                )
            urls = re.findall(url_pattern, text)
            for url in urls:
                text = text.replace(url, f'<a href={url} target="_blank">{url}</a>')
            self.life_cycle_handler.onResponse(t, text)
            self.history.add_message(
                {
                    "type": t,
                    "text": text,
                    "time": time.strftime(
                        "%Y-%m-%d %H:%M:%S", time.localtime(time.time())
                    ),
                    "uuid": UUID,
                    "plugin": plugin,
                }
            )

    def _onCompleted(self, msg):
        pass

    def pardon(self):
        if not self.has_pardon:
            self.say("抱歉，刚刚没听清，能再说一遍吗？", cache=True)
            self.has_pardon = True
        else:
            self.say("没听清呢")
            self.has_pardon = False
        self.sender.send_message(
            action=ACTION_ROBOT_SPEAK,
            data=StatusData(stage="", status="end"),
            message="抱歉，刚刚没听清，能再说一遍吗？",
        )

    def _tts_line(self, line, cache, index=0, onCompleted=None):
        """
        对单行字符串进行 TTS 并返回合成后的音频
        :param line: 字符串
        :param cache: 是否缓存 TTS 结果
        :param index: 合成序号
        :param onCompleted: 播放完成的操作
        """
        line = line.strip()
        pattern = r"http[s]?://.+"
        if re.match(pattern, line):
            logger.info("内容包含URL，屏蔽后续内容")
            return None
        line.replace("- ", "")
        if line:
            result = self._ttsAction(line, cache, index, onCompleted)
            return result
        return None

    def _tts(self, lines, cache, onCompleted=None):
        """
        对字符串进行 TTS 并返回合成后的音频
        :param lines: 字符串列表
        :param cache: 是否缓存 TTS 结果
        """
        # 重置index
        self.player.reset_index(0)
        audios = []
        pattern = r"http[s]?://.+"
        logger.info("_tts")
        with self.tts_lock:
            with ThreadPoolExecutor(max_workers=5) as pool:
                all_task = []
                index = 0
                for line in lines:
                    if re.match(pattern, line):
                        logger.info("内容包含URL，屏蔽后续内容")
                        self.tts_count -= 1
                        continue
                    if line:
                        task = pool.submit(
                            self._ttsAction, line.strip(), cache, index, onCompleted
                        )
                        index += 1
                        all_task.append(task)
                    else:
                        self.tts_count -= 1
                for future in as_completed(all_task):
                    audio = future.result()
                    if audio:
                        audios.append(audio)
            return audios

    def _after_play(self, msg, audios, plugin=""):
        cached_audios = [
            f"http://{config.get('/server/host')}:{config.get('/server/port')}/audio/{os.path.basename(voice)}"
            for voice in audios
        ]
        if self.on_say:
            logger.info(f"onSay: {msg}, {cached_audios}")
            self.on_say(msg, cached_audios, plugin=plugin)
            self.on_say = None
        utils.lruCache()  # 清理缓存

    def stream_say(self, stream, cache=False, onCompleted=None):
        """
        从流中逐字逐句生成语音
        :param stream: 文字流，可迭代对象
        :param cache: 是否缓存 TTS 结果
        :param onCompleted: 声音播报完成后的回调
        """
        # 重置index
        data_list = []
        left = ""
        audios = []
        index = 0
        skip_tts = False
        skip_prefix = None
        resp_uuid = str(uuid.uuid1())
        reqId = uuid.uuid4().hex
        self.tts_count = 0
        self.player.reset_index(0)
        if onCompleted is None:
            onCompleted = lambda: self._onCompleted(msg)
        for data in stream():
            logger.info(f"stream data: {data}")
            data_list.append(data)
            if self.on_stream:
                logger.info(f"stream_say onStream:{data}{resp_uuid}")
                self.on_stream(data, resp_uuid)
            lines, left, skip, skip_prefix = self.split_tts(
                text=left + data, skip_prefix=skip_prefix
            )
            skip_tts = skip_tts or skip
            # 无需分割
            if not lines:
                continue
            for line in lines:
                # 没有内容就跳过
                if not line or not line.strip():
                    continue
                index, audio = self._play_line(
                    reqId=reqId,
                    line=line,
                    index=index,
                    cache=cache,
                    onCompleted=onCompleted,
                )
                if audio:
                    audios.append(audio)
        # 播放剩余的内容
        if left.strip():
            data_list.append(left)
            index, audio = self._play_line(
                reqId=reqId,
                line=left,
                index=index,
                cache=cache,
                onCompleted=onCompleted,
            )
            if audio:
                audios.append(audio)
        if self.dh:
            self.dh.speak(reqId, "", index + 1, True)
        # if skip_tts:
        #     self._tts_line(line="内容中包含代码，我就不念了", cache=True, index=index, onCompleted=onCompleted)
        msg = "".join(data_list)
        self.appendHistory(1, msg, UUID=resp_uuid, plugin="")
        self._after_play(msg, audios, "")

    def say(self, msg, cache=False, plugin="", onCompleted=None, append_history=True):
        """
        说一句话
        :param msg: 内容
        :param cache: 是否缓存这句话的音频
        :param plugin: 来自哪个插件的消息（将带上插件的说明）
        :param onCompleted: 完成的回调
        :param append_history: 是否要追加到聊天记录
        """
        if self.dh:
            self.dh.speak(uuid.uuid4().hex, msg, 1, True)
        else:
            if append_history:
                self.appendHistory(1, msg, plugin=plugin)
            msg = utils.strip_punctuation(msg).strip()

            if not msg:
                return

            logger.info(f"即将朗读语音：{msg}")
            lines = re.split("。|！|？|\!|\?|\n", msg)
            if onCompleted is None:
                onCompleted = lambda: self._onCompleted(msg)
            self.tts_count = len(lines)
            logger.debug(f"tts_count: {self.tts_count}")
            audios = self._tts(lines, cache, onCompleted)
            self._after_play(msg, audios, plugin)

    def activeListen(self, silent=False):
        """
        主动问一个问题(适用于多轮对话)
        :param silent: 是否不触发唤醒表现（主要用于极客模式）
        :param
        """
        if self.immersive_mode:
            self.player.stop()
        elif self.player.is_playing():
            self.player.join()  # 确保所有音频都播完
        logger.info("进入主动聆听...")
        try:
            # 重复wakeup
            # if not silent:
            #     self.lifeCycleHandler.onWakeup()
            listener = snowboydecoder.ActiveListener(
                [constants.getHotwordModel(config.get("hotword", "wukong.pmdl"))]
            )
            voice = listener.listen(
                silent_count_threshold=config.get("silent_threshold", 15),
                recording_timeout=config.get("recording_timeout", 5) * 4,
            )
            if not silent:
                self.life_cycle_handler.onThink()
            if voice:
                self.sender.send_message(
                    action=ACTION_ROBOT_THINK,
                    data=StatusData(stage=STAGE_UNDERSTAND, status="start"),
                    message="开始理解您的内容",
                )
                query = self.asr.transcribe(voice)
                self.sender.send_message(
                    action=ACTION_USER_SPEAK,
                    data=StatusData(stage=STAGE_UNDERSTAND, status="end"),
                    message=query,
                )
                utils.check_and_delete(voice)
                return query
            return ""
        except Exception as e:
            self.sender.send_message(
                action=ACTION_ROBOT_SPEAK,
                data=StatusData(stage="", status="end"),
                message="抱歉, 遇到些问题，能再说一遍吗？",
            )
            logger.error(f"主动聆听失败：{e}", stack_info=True)
            traceback.print_exc()
            return ""

    def play(self, src, delete=False, onCompleted=None, volume=1):
        """播放一个音频"""
        if self.player:
            self.interrupt()
        self.player = Player.SoxPlayer()
        self.player.play(src, delete=delete, onCompleted=onCompleted)

    def _play_line(self, reqId, line, index, cache, onCompleted) -> Tuple[int, str]:
        audio = ""
        if self.dh:
            self.dh.speak(reqId, line, index + 1, False)
            self.tts_count += 1
            index += 1
        else:
            audio = self._tts_line(
                line=line, cache=cache, index=index, onCompleted=onCompleted
            )
            if audio:
                self.tts_count += 1
                index += 1
        return index, audio

    @classmethod
    def split_tts(
        cls, text: str, skip_prefix: str = None
    ) -> Tuple[list, str, bool, str]:
        """
        返回: (分割列表, 分割后剩下内容, 是否跳过tts, 跳过的起始关键字)
        """
        left = ""
        lines = []
        # 先剔除非TTS字符
        skip, text, prefix, prefix_str = cls.skip_tts(text=text, prefix=skip_prefix)
        # 按标点分割
        if text and text.strip():
            lines = utils.split_paragraph(text=text, token_min_n=4)
            if lines and not utils.end_punctuation(lines[-1]):
                left = lines.pop(-1)
        return (
            lines,
            left + prefix_str,
            skip,
            prefix,
        )

    @classmethod
    def skip_tts(cls, text: str, prefix: str = None) -> Tuple[bool, str, str, str]:
        """
        返回: 是否有忽略, 剩下内容, 起始关键字, 起始关键字的内容
        """
        skip = False
        # prefix not None, 匹配结尾
        if prefix:
            suffix = re_tts["pair"].get(prefix, prefix)
            idx = text.find(suffix)
            if idx > -1:
                skip = True
                text = text[idx + len(suffix) :]
            logger.debug("cut suffix: %s", text)
        # 去掉全匹配
        for rec in re_tts["full"]:
            skip = skip or (rec.match(text) is not None)
            text = rec.sub(repl="", string=text)
        logger.debug("cut full: %s", text)
        # 去掉特殊字符
        for ch in re_tts["char"]:
            text = text.replace(ch, "")
        logger.debug("cut char: %s", text)
        # 匹配开头
        prefix = ""
        prefix_str = ""
        for pre in re_tts["pair"]:
            idx = text.find(pre)
            if idx > -1:
                skip = True
                prefix = pre
                prefix_str = text[idx:]
                text = text[:idx]
                break
        logger.debug("find prefix: %s", prefix)
        return skip, text, prefix, prefix_str
