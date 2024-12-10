# -*- coding: utf-8 -*-
import cProfile
import io
import os
import pstats
import re
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Union

from octopus.robot import (
    AI,
    ASR,
    config,
    constants,
    log,
    NLU,
    Player,
    TTS,
    DigitalHuman,
    utils,
)
from octopus.robot.Brain import Brain
from octopus.robot.LifeCycleHandler import LifeCycleEvent
from octopus.robot.Scheduler import Scheduler
from octopus.robot.Sender import (
    StatusData,
    ACTION_ROBOT_LISTEN,
    ACTION_USER_SPEAK,
    ACTION_ROBOT_THINK,
    ACTION_ROBOT_SPEAK,
    STAGE_UNDERSTAND,
    STAGE_SEARCH,
    WebSocketSender,
)
from octopus.robot.compt import StreamStr
from octopus.robot.sdk import History
from octopus.snowboy import snowboydecoder

# tts输出规则
re_tts = {
    "full": [
        re.compile(pattern=r"```.+```"),
        re.compile(pattern=r"!\[[^\]]*\]"),
        re.compile(pattern=r"\[[^\]]*\]"),
        re.compile(pattern=r"\([^\)]*\)"),
    ],
    "pair": {r"```": r"```", r"![": r"]", r"[": r"]", r"(": r")"},
    "special": ["***", "**", "~~"],
}
# TTS输出, 屏蔽关键字
# _detect_ = config.get("/detector", "porcupine")
# re_tts["special"].extend(config.get(f"/{_detect_}/keywords", ["你好", "小惠"]))
# 返回前端文本输出规则
re_out = {
    "full": [
        re.compile(pattern=r"```.+```"),
        re.compile(pattern=r"!\[[^\]]*\]\([^\)]*\)"),
        re.compile(pattern=r"\[[^\]]*\]\([^\)]*\)"),
    ],
    "pair": {r"```": r"```", r"![": r")", r"[": r")"},
    "special": [],
}

logger = log.getLogger(__name__)


class Conversation(object):

    def __init__(
        self,
        life_cycle_event: LifeCycleEvent,
        profiling=False,
        sender: WebSocketSender = None,
    ):
        self.brain, self.asr, self.ai, self.nlu = None, None, None, None
        self.scheduler = Scheduler(self)
        # 历史会话消息
        self.history = History.History()
        # 沉浸模式，处于这个模式下，被打断后将自动恢复这个技能
        self.matchPlugin = None
        self.immersiveMode = None
        self.profiling = profiling
        self.on_say = None
        self.on_stream = None
        self.sender = sender
        self.life_cycle_event = life_cycle_event
        self.interrupted = threading.Event()  # 中断标记
        self.manual_break_time = None  # 中断时刻
        self.resp_uuid = None  # 当前响应标记
        self.pardon_count = 0
        self.listener = None  # 聆听者
        self.speaker = OrderSpeaker(life_cycle_event=life_cycle_event, sender=sender)
        # 初始化
        self.re_init()

    def re_init(self):
        """重新初始化"""
        try:
            self.speaker.re_init()
            self.asr = ASR.get_engine_by_slug(config.get("asr_engine", "tencent-asr"))
            self.ai = AI.get_robot_by_slug(config.get("robot", "tuling"))
            self.nlu = NLU.get_engine_by_slug(config.get("nlu_engine", "unit"))
            self.brain = Brain(self)
            self.brain.printPlugins()
        except Exception as e:
            logger.critical("对话初始化失败：%s", str(e), exc_info=True)

    def active_listen(self, silent=False, interrupt_check=None):
        """
        主动问一个问题(适用于多轮对话)
        :param silent: 是否不触发唤醒表现（主要用于极客模式）
        :param
        """
        logger.info("进入主动聆听...")
        try:
            self.listener = snowboydecoder.ActiveListener(
                [constants.getHotwordModel(config.get("hotword", "octopus.pmdl"))]
            )
            voice = self.listener.listen(
                interrupt_check=interrupt_check,
                silent_count_threshold=config.get("silent_threshold", 15),
                recording_threshold=config.get("recording_timeout", 5) * 4,
            )
            if not silent:
                self.life_cycle_event.fire_event("think")
            if voice:
                self.sender.put_message(
                    action=ACTION_ROBOT_THINK,
                    data=StatusData(stage=STAGE_UNDERSTAND, end=False).dict(),
                    message="开始理解您的内容",
                )
                query = self.asr.transcribe(voice)
                self.sender.put_message(
                    action=ACTION_USER_SPEAK,
                    data=StatusData(stage=STAGE_UNDERSTAND, end=True).dict(),
                    message=query,
                    t=0,
                )
                utils.check_and_delete(voice)
                return query
            return ""
        except Exception as e:
            self.sender.put_message(
                action=ACTION_ROBOT_LISTEN,
                data=dict(listen=False),
                message="抱歉, 遇到些问题，能再说一遍吗？",
            )
            logger.critical("主动聆听失败：%s", str(e), exc_info=True)
            return ""

    def do_response(self, query, req_uuid=None, onSay=None):
        """
        响应指令

        :param query: 指令
        :param req_uuid: 指令的UUID
        :param onSay: 朗读时的回调
        """
        # 空内容
        if not query:
            self.pardon()
            return
        # 中断之前的响应
        self.interrupt()
        # 停止说话
        if query in ["暂停。", "停止。", "闭嘴。", "停一下。"]:
            self.clear_pardon()
            return

        self._append_history(t=0, text=query, text_id=req_uuid)
        if onSay:
            self.set_on_say(onSay)

        # 清除中断标记
        self.clear_interrupt()
        # 响应内容
        # todo: 调用NLU, 识别指令动作
        # 没命中技能，使用机器人回复
        resp_uuid = uuid.uuid4().hex
        self.resp_uuid = resp_uuid
        # 不用self.resp_uuid, 避免多线程冲突
        self._response_gpt(query=query, resp_uuid=resp_uuid)
        self.clear_pardon()

    def say_simple(
        self,
        msg,
        cache=False,
        plugin=None,
        resp_uuid=None,
        onCompleted=None,
    ):
        """
        说一句话
        :param msg: 内容
        :param cache: 是否缓存这句话的音频
        :param plugin: 插件
        :param resp_uuid:
        :param onCompleted: 完成的回调
        """
        resp_uuid = resp_uuid or uuid.uuid4().hex
        self.speaker.speak_simple(
            msg=msg,
            req_id=resp_uuid,
            cache=cache,
            on_completed=onCompleted,
        )

    def play(self, src, delete=False, onCompleted=None, volume=1, interrupt=False):
        """播放一个音频"""
        self.speaker.play_audio(
            src=src, delete=delete, onCompleted=onCompleted, interrupt=interrupt
        )

    def do_parse(self, query):
        args = {
            "service_id": config.get("/unit/service_id", "S13442"),
            "api_key": config.get("/unit/api_key", "w5v7gUV3iPGsGntcM84PtOOM"),
            "secret_key": config.get(
                "/unit/secret_key", "KffXwW6E1alcGplcabcNs63Li6GvvnfL"
            ),
        }
        return self.nlu.parse(query, **args)

    def interrupt(self, req_id=None, manual=False, interrupt_time=None):
        self.interrupted.set()
        self.speaker.interrupt(req_id or self.resp_uuid)
        if self.immersiveMode:
            self.brain.pause()
        # 清空数据
        if manual:
            self.sender.clear_message()
        # 打断时间
        if interrupt_time:
            time.sleep(interrupt_time)
        # 人为打断(要在sleep之后)
        if manual:
            self.manual_break_time = time.time()

    def clear_interrupt(self):
        self.interrupted.clear()
        self.manual_break_time = None

    def pardon(self):
        self.sender.put_message(
            action=ACTION_USER_SPEAK,
            data=StatusData(stage=STAGE_UNDERSTAND, end=True).dict(),
            message="",
            t=0,
        )
        self.speaker.speak(msg="没听清呢。")
        self.clear_pardon()
        # self.life_cycle_event.fire_event(event="sleep")  # 休眠

    def has_pardon(self, up: bool = False) -> bool:
        if up:
            self.pardon_count += 1
        return self.pardon_count < 2

    def clear_pardon(self):
        self.pardon_count = 0

    def getHistory(self):
        return self.history

    def check_restore(self):
        if self.immersiveMode:
            logger.info("处于沉浸模式，恢复技能")
            self.life_cycle_event.fire_event("restore")
            self.brain.restore()

    def setImmersiveMode(self, slug):
        self.immersiveMode = slug

    def getImmersiveMode(self):
        return self.immersiveMode

    def set_plugin(self, plugin):
        self.matchPlugin = plugin

    def set_on_stream(self, on_stream):
        self.on_stream = on_stream

    def set_on_say(self, on_say):
        self.on_say = on_say

    def do_converse(self, voice, callback=None, onSay=None):
        query = ""
        try:
            query = self.asr.transcribe(voice)
        except Exception as e:
            logger.critical("ASR识别失败：%s", str(e), exc_info=True)
            traceback.print_exc()
        utils.check_and_delete(voice)
        try:
            self.do_response(query=query, onSay=onSay)
        except Exception as e:
            logger.critical("回复失败：%s", str(e), exc_info=True)
            traceback.print_exc()
        utils.clean()

    def stop_listen(self):
        """停止聆听"""
        self.listener.clear_listen()

    def feedback(self, chat_id: str, data_id: str, useful: bool = True):
        """反馈"""
        return self.ai.feedback(
            chat_id=chat_id,
            data_id=data_id,
            is_good=useful,
            opinion=None if useful else "useless",
        )

    def in_break_time(self, limit_time) -> bool:
        """在限定打断时间内"""
        return self.manual_break_time and (
            time.time() - self.manual_break_time < limit_time
        )

    def clear_break_time(self):
        self.manual_break_time = None

    def _response_gpt(self, query, resp_uuid):
        if self.ai and self.ai.support_stream():
            self.sender.put_message(
                action=ACTION_ROBOT_THINK,
                data=StatusData(stage=STAGE_SEARCH, end=False).dict(),
                message="开始查找资料",
            )
            stream = self.ai.stream_chat(
                texts=query, chat_id=resp_uuid, response_id=resp_uuid
            )
            self.sender.put_message(
                action=ACTION_ROBOT_THINK,
                data=StatusData(stage=STAGE_SEARCH, end=True).dict(),
                message="查找资料结束",
            )
            self._stream_say(
                resp_uuid=resp_uuid,
                stream=stream,
                cache=True,
                on_completed=self.check_restore,
            )
        else:
            parsed = {"Domain": "", "Intent": "", "Slot": query}
            self.sender.put_message(
                action=ACTION_ROBOT_THINK,
                data=StatusData(stage=STAGE_SEARCH, end=False).dict(),
                message="开始查找资料",
            )
            msg = self.ai.chat(texts=query, parsed=parsed, chat_id=resp_uuid)
            self.sender.put_message(
                action=ACTION_ROBOT_THINK,
                data=StatusData(stage=STAGE_SEARCH, end=True).dict(),
                message="查找资料结束",
            )
            self._say(
                resp_uuid=resp_uuid,
                msg=msg,
                cache=True,
                onCompleted=self.check_restore,
                with_interrupt=True,
            )

    def _stream_say(self, resp_uuid, stream, cache=False, on_completed=None):
        """
        从流中逐字逐句生成语音
        :param stream: 文字流，可迭代对象
        :param cache: 是否缓存 TTS 结果
        :param on_completed: 声音播报完成后的回调
        """
        # 重置index
        data_list = []
        audios = []
        index = 0
        if on_completed is None:
            on_completed = lambda: self._onCompleted("")
        stream_text = StreamStr(
            re_full=re_out["full"], re_pair=re_out["pair"], re_special=re_out["special"]
        )
        stream_tts = StreamStr(
            re_full=re_tts["full"], re_pair=re_tts["pair"], re_special=re_tts["special"]
        )
        self.speaker.begin_order()
        try:
            for data in stream():
                # 中断
                if self.interrupted.is_set():
                    logger.info("响应已经被中断....")
                    return
                data_list.append(data)
                if self.on_stream:
                    out_next = stream_text.next(text=data, clear=False)
                    if out_next:
                        self.on_stream(
                            message=out_next, resp_uuid=resp_uuid, data=dict(end=False)
                        )
                lines = stream_tts.split(text=data, clear=True)
                # 无需分割
                if not lines:
                    continue
                for line in lines:
                    # 没有内容就跳过
                    if not line or not line.strip():
                        continue
                    # 检测中断again
                    if self.interrupted.is_set():
                        logger.info("响应已经被中断....")
                        return
                    audio = self.speaker.speak_in_order(
                        line=line, req_id=resp_uuid, index=index, cache=cache
                    )
                    index += 1
                    if audio:
                        audios.append(audio)
            # 播放剩余的内容
            audio = self.speaker.speak_in_order(
                line=stream_tts.get_left(),
                req_id=resp_uuid,
                index=index,
                cache=cache,
                is_final=True,
            )
            if audio:
                audios.append(audio)
        finally:
            self.speaker.end_order(timeout=30, on_completed=on_completed)

        msg = "".join(data_list)
        self._after_write(msg=msg, resp_uuid=resp_uuid)
        self._after_speak(msg=msg, audios=audios)

    def _say(
        self,
        resp_uuid,
        msg,
        cache=False,
        plugin="",
        onCompleted=None,
        append_history=True,
        with_interrupt=False,
    ):
        """
        说一句话
        :param msg: 内容
        :param cache: 是否缓存这句话的音频
        :param plugin: 来自哪个插件的消息（将带上插件的说明）
        :param onCompleted: 完成的回调
        :param append_history: 是否要追加到聊天记录
        """
        audios = self.speaker.speak(
            msg=msg,
            req_id=resp_uuid,
            cache=cache,
            plugin=plugin,
            on_completed=onCompleted,
            with_interrupt=with_interrupt,
        )
        self._after_write(
            msg=msg, resp_uuid=resp_uuid, plugin=plugin, append_history=append_history
        )
        self._after_speak(msg=msg, audios=audios, plugin=plugin)

    def _after_write(self, msg, resp_uuid=None, append_history=True, plugin=""):
        """
        输出结束: 历史记录
        :param msg: 内容
        :param audios: 音频
        :param plugin: 来自哪个插件的消息（将带上插件的说明）
        """
        resp_uuid = resp_uuid or self.resp_uuid
        # 结束事件
        self.life_cycle_event.fire_event(
            event="resp_end", text=msg, resp_uuid=resp_uuid
        )
        # 历史记录
        if append_history:
            self._append_history(t=1, text=msg, text_id=resp_uuid, plugin=plugin)

    def _after_speak(self, msg, audios, plugin=""):
        """
        说话结束: 回调
        :param msg: 内容
        :param audios: 音频
        :param plugin: 来自哪个插件的消息（将带上插件的说明）
        """
        # 清理缓存(定时任务去做)
        # self.speaker.clear_cache()
        # 回调
        if self.on_say:
            self.on_say(msg=msg, audio=self.speaker.audio_path(audios), plugin=plugin)

    def _append_history(self, t, text, text_id=None, plugin=None):
        """
        将会话历史加进历史记录
        t: 0-用户; 1-机器人
        """
        if not text or t not in (0, 1):
            return
        if text[-1] in [",", "，"]:
            text = text[:-1]
        if not text_id or text_id == "null":
            text_id = uuid.uuid4().hex
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
        self.history.add_message(
            {
                "type": t,
                "text": text,
                "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time())),
                "uuid": text_id,
                "plugin": plugin,
            }
        )

    def _onCompleted(self, msg):
        pass

    def _InGossip(self, query):
        return self.immersiveMode in ["Gossip"] and not "闲聊" in query


class OrderSpeaker:

    def __init__(
        self, life_cycle_event: LifeCycleEvent, sender: WebSocketSender = None
    ):
        self.life_cycle_event = life_cycle_event
        self.sender = sender
        self.interrupted = threading.Event()  # 中断标记
        self.server_host = (
            f"http://{config.get('/server/host')}:{config.get('/server/port')}"
        )
        # 播放器
        self.player = Player.OrderPlayer()
        self.play_lock = threading.Lock()
        # TTS
        self.tts = None
        self.tts_lock = threading.Lock()
        # 数字人
        self.dh_enabled = config.get("/dh_engine/enable", False)
        self.dh = None
        # 列表参数
        self.order_len = 0
        self.order_ok = 0
        self.order_lock = threading.Lock()
        self.speaking = threading.Event()
        # 初始化
        self.re_init()

    def re_init(self):
        """重新初始化"""
        try:
            self.server_host = (
                f"http://{config.get('/server/host')}:{config.get('/server/port')}"
            )
            self.player = Player.OrderPlayer()
            self.tts = TTS.get_engine_by_slug(config.get("tts_engine", "baidu-tts"))
            if self.dh_enabled:
                self.dh = DigitalHuman.get_engine_by_slug(
                    config.get("/dh_engine/provider", "tencent-dh")
                )
        except:
            logger.critical("Speaker初始化失败.", exc_info=True)

    def speak(
        self,
        msg,
        req_id=None,
        cache=False,
        plugin="",
        on_completed=None,
        with_interrupt=False,
    ) -> list:
        """
        说长句子
        :param msg: 内容
        :param cache: 是否缓存这句话的音频
        :param plugin: 来自哪个插件的消息（将带上插件的说明）
        :param on_completed: 完成的回调
        """
        audios = []
        msg = utils.stripEndPunc(msg).strip()
        if not msg:
            return audios
        logger.debug("即将朗读语音：%s", msg)
        # 分割长句
        lines = re.split(r"。|！|？|\!|\?|\n", msg)
        # 重置index
        if self.dh:
            self._dhs(
                lines=lines,
                on_completed=on_completed,
                with_interrupt=with_interrupt,
                req_id=req_id,
            )
        else:
            audios = self._tts(
                lines=lines,
                on_completed=on_completed,
                with_interrupt=with_interrupt,
                cache=cache,
            )
        return audios

    def speak_simple(self, msg, req_id=None, cache=True, on_completed=None):
        """
        说一句话
        """

        def _play_voice(audio):
            self.play_audio(
                src=audio, delete=not cache, onCompleted=on_completed, interrupt=True
            )

        if self.dh:
            self.dh.speak(req_id, msg, 1, True)
        else:
            # 获取音频
            self._get_tts_voice(msg=msg, on_completed=_play_voice)

    def speak_in_order(
        self, line, req_id=None, index=0, cache=True, on_completed=None, is_final=False
    ) -> str:
        """
        结合start_order和end_order使用
        """
        if self.dh:
            self._dh_in_order(
                msg=line,
                req_id=req_id,
                index=index + 1,
                is_final=is_final,
                on_completed=on_completed,
            )
        else:
            return self._tts_in_order(
                msg=line, cache=cache, index=index, on_completed=on_completed
            )

    def play_audio(self, src, delete=False, onCompleted=None, interrupt=False):
        """播放单个音频"""
        if interrupt:
            self.interrupt()
            # 清除中断标记
            self.interrupted.clear()
        Player.play(
            fname=src,
            delete=delete,
            onCompleted=onCompleted,
        )

    def begin_order(self, notify=True):
        """开始列表播放"""
        # 清除中断标记
        self.interrupted.clear()
        if not self._wait_order_start():
            return
        self.speaking.set()
        self.order_len = 0
        self.order_ok = 0
        self.player.new_order()
        # 发送消息: 机器人开始说话
        if notify:
            self.sender.put_message(
                action=ACTION_ROBOT_SPEAK,
                data=StatusData(stage=ACTION_ROBOT_SPEAK, end=False).dict(),
                message="",
            )

    def end_order(self, timeout=None, on_completed=None, notify=True):
        """结束列表播放"""
        # 如果已经end, 不处理
        if not self.speaking.is_set():
            return
        if self.order_len == 0:
            self.speaking.clear()
            return
        if not timeout:
            is_end = self.order_ok >= self.order_len
        else:
            self._wait_order_end(timeout=timeout)
            is_end = True
        if not is_end:
            return
        self.speaking.clear()
        if on_completed:
            on_completed()
        # 发送消息: 机器人开始说话
        if notify:
            self.sender.put_message(
                action=ACTION_ROBOT_SPEAK,
                data=StatusData(stage=ACTION_ROBOT_SPEAK, end=True).dict(),
                message="",
            )

    def interrupt(self, req_id=None):
        """打断"""
        self.interrupted.set()
        if self.dh:
            self.dh.interrupt(req_id=req_id)
        if self.player:
            self.player.stop()

    def clear_cache(self):
        """清理缓存"""
        utils.clear_voice_cache(file=f'*.{self.tts.codec or "mp3"}', days=7)

    def audio_path(self, audios) -> Union[str, list]:
        """音频路径"""
        if isinstance(audios, str):
            return f"{self.server_host}/audio/{os.path.basename(audios)}"
        return [
            f"{self.server_host}/audio/{os.path.basename(voice)}" for voice in audios
        ]

    def _wait_order_start(self, timeout=None) -> bool:
        """等待开始"""
        limit = (timeout or 10) * 10
        count = 0
        while True:
            if self.interrupted.is_set():
                return False
            if not self.speaking.is_set():
                return True
            if count > limit:
                return False
            count += 1
            time.sleep(0.1)

    def _wait_order_end(self, timeout=None) -> bool:
        """等待结束"""
        limit = (timeout or 30) * 10
        count = 0
        while True:
            if self.interrupted.is_set():
                return False
            if self.order_ok >= self.order_len:
                return True
            if count > limit:
                return False
            count += 1
            time.sleep(0.1)

    def _dhs(self, lines, req_id=None, on_completed=None, with_interrupt=False):
        """
        数字人播报: 播放多条语句
        """
        index = 1
        req_id = req_id or uuid.uuid4().hex
        self.begin_order()
        try:
            for line in lines:
                # 检测中断again
                if with_interrupt and self.interrupted.is_set():
                    self.dh.interrupt(req_id=req_id)
                    logger.info("Speak-DH被中断....")
                    return
                self._dh_in_order(msg=line, req_id=req_id, index=index, is_final=False)
                index += 1
            self._dh_in_order(msg="", req_id=req_id, index=index, is_final=True)
        finally:
            self.end_order(timeout=30, on_completed=on_completed)

    def _tts(self, lines, cache, on_completed=None, with_interrupt=False) -> list:
        """
        TTS语音合成: 播放多条语句, 并返回合成后的音频
        :param lines: 字符串列表
        :param cache: 是否缓存 TTS 结果
        """
        audios = []
        with self.tts_lock:
            self.begin_order()
            try:
                with ThreadPoolExecutor(max_workers=3) as pool:
                    all_task = []
                    index = 0
                    canceled = False
                    for line in lines:
                        if not line:
                            continue
                        # 检测中断again
                        if with_interrupt and self.interrupted.is_set():
                            canceled = True
                            logger.info("Speak-TTS被中断....")
                            break
                        task = pool.submit(
                            self._tts_in_order,
                            msg=line.strip(),
                            cache=cache,
                            index=index,
                            on_completed=None,
                        )
                        index += 1
                        all_task.append(task)
                    # 取消
                    if canceled:
                        for future in all_task:
                            if not future.done():
                                future.cancel()
                        return []
                    # 等待任务结束
                    for future in as_completed(all_task):
                        audio = future.result()
                        if audio:
                            audios.append(audio)
            finally:
                self.end_order(timeout=30, on_completed=on_completed)
        return audios

    def _dh_in_order(self, msg, req_id, index, is_final=False, on_completed=None):
        """数字人播报: 单条"""
        self.order_len += 1
        self.dh.speak(req_id, msg, index, is_final)
        self._on_item_completed(on_completed=on_completed)

    def _tts_in_order(self, msg, cache, index, on_completed=None) -> str:
        """TTS语音合成: 单条"""
        if not msg:
            return ""

        def _play_voice(audio):
            # 空判断
            if not audio or not os.path.exists(audio):
                return
            with self.play_lock:
                self.order_len += 1
                self.player.play(
                    src=audio,
                    delete=not cache,
                    onCompleted=self._wrap_item_completed(on_completed=on_completed),
                    index=index,
                )

        # 获取音频
        return self._get_tts_voice(msg=msg, index=index, on_completed=_play_voice)

    def _get_tts_voice(self, msg, index=0, on_completed=None):
        voice = utils.get_voice_cache(msg)
        if voice:
            logger.debug("第%s段TTS命中缓存，播放缓存语音", index)
        else:
            try:
                # voice = self.tts.get_speech(phrase=msg, on_completed=on_completed)
                voice = self.tts.get_speech(phrase=msg)
                logger.debug("第%s段TTS合成成功。msg: %s", index, msg)
            except Exception as e:
                logger.critical("语音合成失败：%s", str(e), exc_info=True)
        if voice and on_completed:
            on_completed(voice)
        return voice

    def _on_item_completed(self, on_completed=None):
        with self.order_lock:
            if self.speaking.is_set():
                self.order_ok += 1
        if on_completed:
            on_completed()

    def _wrap_item_completed(self, on_completed=None):
        def refer():
            self._on_item_completed(on_completed=on_completed)

        return refer
