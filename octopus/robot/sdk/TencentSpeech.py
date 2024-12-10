# -*- coding: utf-8 -*-
import base64
import hashlib
import hmac
import json
import threading
import time
import uuid
from abc import ABCMeta, abstractmethod
from urllib import parse as url_parse

from websocket import ABNF, WebSocketApp

from octopus.robot import log

logger = log.getLogger(__name__)

_PROTOCOL = "wss://"
_HOST_TTS = "tts.cloud.tencent.com"
_HOST_ASR = "asr.cloud.tencent.com"
# 实时语音合成
_PATH_RT = "/stream_ws"
_ACTION_RT = "TextToStreamAudioWS"
# 流式文本语音合成
_PATH_FLOW = "/stream_wsv2"
_ACTION_FLOW = "TextToStreamAudioWSv2"
# 实时语音识别
_PATH_ASR = "/asr/v2"

NOTOPEN = 0
STARTED = 1
OPENED = 2
FINAL = 3
ERROR = 4
CLOSED = 5

Flow_ACTION_SYNTHESIS = "ACTION_SYNTHESIS"
Flow_ACTION_COMPLETE = "ACTION_COMPLETE"


class Credential:
    def __init__(self, secret_id, secret_key, token=""):
        self.secret_id = secret_id
        self.secret_key = secret_key
        self.token = token


class SynthesisListener(object):
    __metaclass__ = ABCMeta

    @abstractmethod
    def on_synthesis_start(self, ws, session_id): ...

    @abstractmethod
    def on_synthesis_end(self, ws): ...

    @abstractmethod
    def on_synthesis_fail(self, ws, response): ...

    @abstractmethod
    def on_audio_result(self, ws, audio_bytes): ...

    @abstractmethod
    def on_text_result(self, ws, response): ...


# 实时识别语音使用
class RecognizeListener(object):
    __metaclass__ = ABCMeta
    """
    reponse:  
    on_recognition_start的返回只有voice_id字段。
    on_fail 只有voice_id、code、message字段。
    on_recognition_complete没有result字段。
    其余消息包含所有字段。
    字段名	类型	
    code	Integer	
    message	String	
    voice_id	String
    message_id	String
    result	Result	
    final	Integer	

    Result的结构体格式为:
    slice_type	Integer	
    index	Integer	
    start_time	Integer	
    end_time	Integer	
    voice_text_str	String	
    word_size	Integer	
    word_list	Word Array

    Word的类型为:
    word    String 
    start_time Integer 
    end_time Integer 
    stable_flag：Integer 
    """

    @abstractmethod
    def on_recognition_start(self, ws, response):
        pass

    @abstractmethod
    def on_sentence_begin(self, ws, response):
        pass

    @abstractmethod
    def on_sentence_temp(self, ws, response):
        pass

    @abstractmethod
    def on_sentence_end(self, ws, response):
        pass

    @abstractmethod
    def on_recognition_complete(self, ws, response):
        pass

    @abstractmethod
    def on_fail(self, ws, response):
        pass


class SpeechSynthesizer:
    """实时语音合成"""

    def __init__(self, app_id: str, credential: Credential):
        self.app_id = app_id
        self.credential = credential
        self.status = NOTOPEN
        self.ws = None
        self.wst = None

        self.text = "欢迎使用腾讯云实时语音合成"
        self.voice_type = 0
        self.codec = "pcm"
        self.sample_rate = 16000
        self.volume = 0
        self.speed = 0
        self.session_id = ""
        self.enable_subtitle = True

    def set_voice_type(self, voice_type):
        self.voice_type = voice_type

    def set_codec(self, codec):
        self.codec = codec

    def set_sample_rate(self, sample_rate):
        self.sample_rate = sample_rate

    def set_speed(self, speed):
        self.speed = speed

    def set_volume(self, volume):
        self.volume = volume

    def set_text(self, text):
        self.text = text

    def set_enable_subtitle(self, enable_subtitle):
        self.enable_subtitle = enable_subtitle

    def __gen_signature(self, params):
        sort_dict = sorted(params.keys())
        sign_str = "GET" + _HOST_TTS + _PATH_RT + "?"
        for key in sort_dict:
            sign_str = sign_str + key + "=" + str(params[key]) + "&"
        sign_str = sign_str[:-1]
        secret_key = self.credential.secret_key.encode("utf-8")
        sign_str = sign_str.encode("utf-8")
        hmac_str = hmac.new(secret_key, sign_str, hashlib.sha1).digest()
        s = base64.b64encode(hmac_str)
        s = s.decode("utf-8")
        return s

    def __gen_params(self, session_id):
        self.session_id = session_id

        params = dict()
        params["Action"] = _ACTION_RT
        params["AppId"] = int(self.app_id)
        params["SecretId"] = self.credential.secret_id
        params["ModelType"] = 1
        params["VoiceType"] = self.voice_type
        params["Codec"] = self.codec
        params["SampleRate"] = self.sample_rate
        params["Speed"] = self.speed
        params["Volume"] = self.volume
        params["SessionId"] = self.session_id
        params["Text"] = self.text
        params["EnableSubtitle"] = self.enable_subtitle

        timestamp = int(time.time())
        params["Timestamp"] = timestamp
        params["Expired"] = timestamp + 24 * 60 * 60
        return params

    def __create_query_string(self, param):
        param["Text"] = url_parse.quote(param["Text"])

        param = sorted(param.items(), key=lambda d: d[0])

        url = _PROTOCOL + _HOST_TTS + _PATH_RT

        signstr = url + "?"
        for x in param:
            tmp = x
            for t in tmp:
                signstr += str(t)
                signstr += "="
            signstr = signstr[:-1]
            signstr += "&"
        signstr = signstr[:-1]
        return signstr

    def start(self, listener: SynthesisListener):
        logger.info("synthesizer start: begin")

        def _close_conn(reason):
            ta = time.time()
            self.ws.close()
            tb = time.time()
            logger.info(
                "client has closed connection ({}), cost {} ms".format(
                    reason, int((tb - ta) * 1000)
                )
            )

        def _on_data(ws, data, opcode, flag):
            # NOTE print all message that client received
            # logger.info("data={} opcode={} flag={}".format(data, opcode, flag))
            if opcode == ABNF.OPCODE_BINARY:
                listener.on_audio_result(ws, data)  # <class 'bytes'>
            elif opcode == ABNF.OPCODE_TEXT:
                resp = json.loads(data)  # WSResponseMessage
                if resp["code"] != 0:
                    logger.error(
                        "server synthesis fail request_id={} code={} msg={}".format(
                            resp["request_id"], resp["code"], resp["message"]
                        )
                    )
                    listener.on_synthesis_fail(ws, resp)
                    return
                if "final" in resp and resp["final"] == 1:
                    logger.info("recv FINAL frame")
                    self.status = FINAL
                    _close_conn("after recv final")
                    listener.on_synthesis_end(
                        ws,
                    )
                    return
                if "result" in resp:
                    if (
                        "subtitles" in resp["result"]
                        and resp["result"]["subtitles"] is not None
                    ):
                        listener.on_text_result(ws, resp)
                    return
            else:
                logger.error("invalid on_data code, opcode=".format(opcode))

        def _on_error(ws, error):
            if self.status == FINAL or self.status == CLOSED:
                return
            self.status = ERROR
            logger.error("error={}, session_id={}".format(error, self.session_id))
            _close_conn("after recv error")

        def _on_close(ws, close_status_code, close_msg):
            logger.info(
                "conn closed, close_status_code={} close_msg={}".format(
                    close_status_code, close_msg
                )
            )
            self.status = CLOSED

        def _on_open(ws):
            logger.info("conn opened")
            self.status = OPENED

        session_id = str(uuid.uuid1())
        params = self.__gen_params(session_id)
        signature = self.__gen_signature(params)
        requrl = self.__create_query_string(params)

        autho = url_parse.quote(signature)
        requrl += "&Signature=%s" % autho

        self.ws = WebSocketApp(
            requrl, None, on_error=_on_error, on_close=_on_close, on_data=_on_data
        )
        self.ws.on_open = _on_open

        self.wst = threading.Thread(target=self.ws.run_forever)
        self.wst.daemon = True
        self.wst.start()
        self.status = STARTED
        listener.on_synthesis_start(self.ws, session_id)

        logger.info("synthesizer start: end")

    def wait(self):
        logger.info("synthesizer wait: begin")
        if self.ws:
            if self.wst and self.wst.is_alive():
                self.wst.join()
        logger.info("synthesizer wait: end")


class FlowingSpeechSynthesizer:
    """流式文本语音合成"""

    def __init__(self, app_id: str, credential: Credential):
        self.app_id = app_id
        self.credential = credential
        self.status = NOTOPEN
        self.ws = None
        self.wst = None

        self.ready = False

        self.voice_type = 0
        self.codec = "pcm"
        self.sample_rate = 16000
        self.volume = 10
        self.speed = 0
        self.session_id = ""
        self.enable_subtitle = 0
        self.emotion_category = ""
        self.emotion_intensity = 100

    def set_voice_type(self, voice_type):
        self.voice_type = voice_type

    def set_emotion_category(self, emotion_category):
        self.emotion_category = emotion_category

    def set_emotion_intensity(self, emotion_intensity):
        self.emotion_intensity = emotion_intensity

    def set_codec(self, codec):
        self.codec = codec

    def set_sample_rate(self, sample_rate):
        self.sample_rate = sample_rate

    def set_speed(self, speed):
        self.speed = speed

    def set_volume(self, volume):
        self.volume = volume

    def set_enable_subtitle(self, enable_subtitle):
        self.enable_subtitle = enable_subtitle

    def __gen_signature(self, params):
        sort_dict = sorted(params.keys())
        sign_str = "GET" + _HOST_TTS + _PATH_FLOW + "?"
        for key in sort_dict:
            sign_str = sign_str + key + "=" + str(params[key]) + "&"
        sign_str = sign_str[:-1]
        secret_key = self.credential.secret_key.encode("utf-8")
        sign_str = sign_str.encode("utf-8")
        hmac_str = hmac.new(secret_key, sign_str, hashlib.sha1).digest()
        s = base64.b64encode(hmac_str)
        s = s.decode("utf-8")
        return s

    def __gen_params(self, session_id):
        self.session_id = session_id

        params = dict()
        params["Action"] = _ACTION_FLOW
        params["AppId"] = int(self.app_id)
        params["SecretId"] = self.credential.secret_id
        params["ModelType"] = 1
        params["VoiceType"] = self.voice_type
        params["Codec"] = self.codec
        params["SampleRate"] = self.sample_rate
        params["Speed"] = self.speed
        params["Volume"] = self.volume
        params["SessionId"] = self.session_id
        params["EnableSubtitle"] = self.enable_subtitle
        if self.emotion_category != "":
            params["EmotionCategory"] = self.emotion_category
            params["EmotionIntensity"] = self.emotion_intensity

        timestamp = int(time.time())
        params["Timestamp"] = timestamp
        params["Expired"] = timestamp + 24 * 60 * 60
        return params

    def __create_query_string(self, param):
        param = sorted(param.items(), key=lambda d: d[0])

        url = _PROTOCOL + _HOST_TTS + _PATH_FLOW

        signstr = url + "?"
        for x in param:
            tmp = x
            for t in tmp:
                signstr += str(t)
                signstr += "="
            signstr = signstr[:-1]
            signstr += "&"
        signstr = signstr[:-1]
        return signstr

    def __new_ws_request_message(self, action, data):
        return {
            "session_id": self.session_id,
            "message_id": str(uuid.uuid1()),
            "action": action,
            "data": data,
        }

    def __do_send(self, action, text):
        ws_request_message = self.__new_ws_request_message(action, text)
        data = json.dumps(ws_request_message)
        opcode = ABNF.OPCODE_TEXT
        logger.info("ws send opcode={} data={}".format(opcode, data))
        self.ws.send(data, opcode)

    def process(self, text, action=Flow_ACTION_SYNTHESIS):
        logger.info("process: action={} data={}".format(action, text))
        self.__do_send(action, text)

    def complete(self, action=Flow_ACTION_COMPLETE):
        logger.info("complete: action={}".format(action))
        self.__do_send(action, "")

    def wait_ready(self, timeout_ms):
        timeout_start = int(time.time() * 1000)
        while True:
            if self.ready:
                return True
            if int(time.time() * 1000) - timeout_start > timeout_ms:
                break
            time.sleep(0.01)
        return False

    def start(self, listener: SynthesisListener):
        logger.info("synthesizer start: begin")

        def _close_conn(reason):
            ta = time.time()
            self.ws.close()
            tb = time.time()
            logger.info(
                "client has closed connection ({}), cost {} ms".format(
                    reason, int((tb - ta) * 1000)
                )
            )

        def _on_data(ws, data, opcode, flag):
            logger.debug("data={} opcode={} flag={}".format(data, opcode, flag))
            if opcode == ABNF.OPCODE_BINARY:
                listener.on_audio_result(ws, data)  # <class 'bytes'>
                pass
            elif opcode == ABNF.OPCODE_TEXT:
                logger.info("recv text data: {}".format(data))
                resp = json.loads(data)  # WSResponseMessage
                if resp["code"] != 0:
                    logger.error(
                        "server synthesis fail request_id={} code={} msg={}".format(
                            resp["request_id"], resp["code"], resp["message"]
                        )
                    )
                    listener.on_synthesis_fail(ws, resp)
                    return
                if "final" in resp and resp["final"] == 1:
                    logger.info("recv FINAL frame")
                    self.status = FINAL
                    _close_conn("after recv final")
                    listener.on_synthesis_end(
                        ws,
                    )
                    return
                if "ready" in resp and resp["ready"] == 1:
                    logger.info("recv READY frame")
                    self.ready = True
                    return
                if "heartbeat" in resp and resp["heartbeat"] == 1:
                    logger.info("recv HEARTBEAT frame")
                    return
                if "result" in resp:
                    if (
                        "subtitles" in resp["result"]
                        and resp["result"]["subtitles"] is not None
                    ):
                        listener.on_text_result(ws, resp)
                    return
            else:
                logger.error("invalid on_data code, opcode=".format(opcode))

        def _on_error(ws, error):
            if self.status == FINAL or self.status == CLOSED:
                return
            self.status = ERROR
            logger.error("error={}, session_id={}".format(error, self.session_id))
            _close_conn("after recv error")

        def _on_close(ws, close_status_code, close_msg):
            logger.info(
                "conn closed, close_status_code={} close_msg={}".format(
                    close_status_code, close_msg
                )
            )
            self.status = CLOSED

        def _on_open(ws):
            logger.info("conn opened")
            self.status = OPENED

        session_id = str(uuid.uuid1())
        params = self.__gen_params(session_id)
        signature = self.__gen_signature(params)
        requrl = self.__create_query_string(params)
        autho = url_parse.quote(signature)
        requrl += "&Signature=%s" % autho

        self.ws = WebSocketApp(
            requrl,
            None,  # header=headers,
            on_error=_on_error,
            on_close=_on_close,
            on_data=_on_data,
        )
        self.ws.on_open = _on_open

        self.status = STARTED
        self.wst = threading.Thread(target=self.ws.run_forever)
        self.wst.daemon = True
        self.wst.start()
        listener.on_synthesis_start(self.ws, session_id)

        logger.info("synthesizer start: end")

    def wait(self):
        logger.info("synthesizer wait: begin")
        if self.ws:
            if self.wst and self.wst.is_alive():
                self.wst.join()
        logger.info("synthesizer wait: end")


# 实时识别语音使用
class SpeechRecognizer:

    def __init__(self, app_id: str, credential: Credential, engine_model_type):
        self.result = ""
        self.credential = credential
        self.app_id = app_id
        self.engine_model_type = engine_model_type
        self.status = NOTOPEN
        self.ws = None
        self.wst = None
        self.voice_id = ""
        self.new_start = 0
        self.filter_dirty = 0
        self.filter_modal = 0
        self.filter_punc = 0
        self.convert_num_mode = 0
        self.word_info = 0
        self.need_vad = 0
        self.vad_silence_time = 0
        self.hotword_id = ""
        self.hotword_list = ""
        self.reinforce_hotword = 0
        self.noise_threshold = 0
        self.voice_format = 4
        self.nonce = ""

    def set_filter_dirty(self, filter_dirty):
        self.filter_dirty = filter_dirty

    def set_filter_modal(self, filter_modal):
        self.filter_modal = filter_modal

    def set_filter_punc(self, filter_punc):
        self.filter_punc = filter_punc

    def set_convert_num_mode(self, convert_num_mode):
        self.convert_num_mode = convert_num_mode

    def set_word_info(self, word_info):
        self.word_info = word_info

    def set_need_vad(self, need_vad):
        self.need_vad = need_vad

    def set_vad_silence_time(self, vad_silence_time):
        self.vad_silence_time = vad_silence_time

    def set_hotword_id(self, hotword_id):
        self.hotword_id = hotword_id

    def set_hotword_list(self, hotword_list):
        self.hotword_list = hotword_list

    def set_voice_format(self, voice_format):
        self.voice_format = voice_format

    def set_nonce(self, nonce):
        self.nonce = nonce

    def set_reinforce_hotword(self, reinforce_hotword):
        self.reinforce_hotword = reinforce_hotword

    def set_noise_threshold(self, noise_threshold):
        self.noise_threshold = noise_threshold

    def format_sign_string(self, param):
        signstr = f"{_HOST_ASR}{_PATH_ASR}/"
        for t in param:
            if "appid" in t:
                signstr += str(t[1])
                break
        signstr += "?"
        for x in param:
            tmp = x
            if "appid" in x:
                continue
            for t in tmp:
                signstr += str(t)
                signstr += "="
            signstr = signstr[:-1]
            signstr += "&"
        signstr = signstr[:-1]
        return signstr

    def create_query_string(self, param):
        signstr = f"{_PROTOCOL}{_HOST_ASR}{_PATH_ASR}/"
        for t in param:
            if "appid" in t:
                signstr += str(t[1])
                break
        signstr += "?"
        for x in param:
            tmp = x
            if "appid" in x:
                continue
            for t in tmp:
                signstr += str(t)
                signstr += "="
            signstr = signstr[:-1]
            signstr += "&"
        signstr = signstr[:-1]
        return signstr

    def sign(self, signstr, secret_key):
        hmacstr = hmac.new(
            secret_key.encode("utf-8"), signstr.encode("utf-8"), hashlib.sha1
        ).digest()
        s = base64.b64encode(hmacstr)
        s = s.decode("utf-8")
        return s

    def create_query_arr(self):
        query_arr = dict()

        query_arr["appid"] = self.app_id
        query_arr["sub_service_type"] = 1
        query_arr["engine_model_type"] = self.engine_model_type
        query_arr["filter_dirty"] = self.filter_dirty
        query_arr["filter_modal"] = self.filter_modal
        query_arr["filter_punc"] = self.filter_punc
        query_arr["needvad"] = self.need_vad
        query_arr["convert_num_mode"] = self.convert_num_mode
        query_arr["word_info"] = self.word_info
        if self.vad_silence_time != 0:
            query_arr["vad_silence_time"] = self.vad_silence_time
        if self.hotword_id != "":
            query_arr["hotword_id"] = self.hotword_id
        if self.hotword_list != "":
            query_arr["hotword_list"] = self.hotword_list

        query_arr["secretid"] = self.credential.secret_id
        query_arr["voice_format"] = self.voice_format
        query_arr["voice_id"] = self.voice_id
        query_arr["timestamp"] = str(int(time.time()))
        if self.nonce != "":
            query_arr["nonce"] = self.nonce
        else:
            query_arr["nonce"] = query_arr["timestamp"]
        query_arr["expired"] = int(time.time()) + 24 * 60 * 60
        query_arr["reinforce_hotword"] = self.reinforce_hotword
        query_arr["noise_threshold"] = self.noise_threshold
        return query_arr

    def stop(self):
        if self.status == OPENED:
            text_str = json.dumps({"type": "end"})
            self.ws.sock.send(text_str)
        if self.ws:
            if self.wst and self.wst.is_alive():
                self.wst.join()
        self.ws.close()

    def write(self, data):
        while self.status == STARTED:
            time.sleep(0.1)
        if self.status == OPENED:
            self.ws.sock.send_binary(data)

    def start(self, listener: RecognizeListener):
        def on_message(ws, message):
            resp = json.loads(message)
            resp["voice_id"] = self.voice_id
            if resp["code"] != 0:
                logger.warning(
                    "%s server recognition fail %s"
                    % (resp["voice_id"], resp["message"])
                )
                listener.on_fail(ws, resp)
                return
            if "final" in resp and resp["final"] == 1:
                self.status = FINAL
                self.result = message
                listener.on_recognition_complete(ws, resp)
                logger.debug("%s recognition complete" % resp["voice_id"])
                return
            if "result" in resp.keys():
                if resp["result"]["slice_type"] == 0:
                    listener.on_sentence_begin(ws, resp)
                    return
                elif resp["result"]["slice_type"] == 2:
                    listener.on_sentence_end(ws, resp)
                    return
                elif resp["result"]["slice_type"] == 1:
                    listener.on_sentence_temp(ws, resp)
                    return

        def on_error(ws, error):
            if self.status == FINAL:
                return
            logger.error(
                "websocket error %s  voice id %s" % (format(error), self.voice_id)
            )
            self.status = ERROR

        def on_close(ws):
            self.status = CLOSED
            logger.debug("websocket closed  voice id %s" % self.voice_id)

        def on_open(ws):
            self.status = OPENED

        query_arr = self.create_query_arr()
        if not self.voice_id:
            query_arr["voice_id"] = str(uuid.uuid1())
            self.voice_id = query_arr["voice_id"]
        query = sorted(query_arr.items(), key=lambda d: d[0])
        signstr = self.format_sign_string(query)

        autho = self.sign(signstr, self.credential.secret_key)
        requrl = self.create_query_string(query)
        autho = url_parse.quote(autho)
        requrl += "&signature=%s" % autho
        self.ws = WebSocketApp(
            requrl, None, on_error=on_error, on_close=on_close, on_message=on_message
        )
        self.ws.on_open = on_open
        self.wst = threading.Thread(target=self.ws.run_forever)
        self.wst.daemon = True
        self.wst.start()
        self.status = STARTED
        response = {"voice_id": self.voice_id}
        listener.on_recognition_start(self.ws, response)
        logger.debug("%s recognition start" % response["voice_id"])


class TestSynthesisListener(SynthesisListener):
    """语音合成"""

    def on_synthesis_start(self, ws, session_id):
        logger.info("on_synthesis_start: session_id={}".format(session_id))

    def on_synthesis_end(self, ws):
        logger.info("on_synthesis_end: -")
        ws.close()

    def on_synthesis_fail(self, ws, response):
        logger.error(
            "on_synthesis_fail: code={} msg={}".format(
                response["code"], response["message"]
            )
        )

    def on_audio_result(self, ws, audio_bytes):
        logger.info(
            "on_audio_result: recv audio bytes, len={}".format(len(audio_bytes))
        )
        with open(file=audio_file, mode="a+b") as f:
            f.write(audio_bytes)

    def on_text_result(self, ws, response):
        session_id = response["session_id"]
        request_id = response["request_id"]
        message_id = response["message_id"]
        result = response["result"]
        subtitles = []
        if "subtitles" in result and len(result["subtitles"]) > 0:
            subtitles = result["subtitles"]
        logger.info(
            "on_text_result: session_id={} request_id={} message_id={}\nsubtitles={}".format(
                session_id, request_id, message_id, subtitles
            )
        )


def test_speech():
    txt = [
        "握手成功之后，",
        "等待服务端",
        "发送 READY 事件，",
        "即可进入",
        "合成阶段。",
        "客户端根",
        "据需要发",
        "送合成文本，",
        "服务端缓存文本，" "并根据标",
        "点符号判断，" "确定接收到",
        "完整句子后，" "调用合成引擎。",
    ]
    credential = Credential(secret_id="secret_id", secret_key="secret_key")
    speech = SpeechSynthesizer(app_id="1305695723", credential=credential)
    speech.set_voice_type(301030)
    speech.set_codec("mp3")
    speech.set_text("".join(txt))
    speech.start(listener=TestSynthesisListener())
    speech.wait()


def test_flow_speech():
    txt = [
        "握手成功之后，",
        "等待服务端",
        "发送 READY 事件，",
        "即可进入",
        "合成阶段。",
        "客户端根",
        "据需要发",
        "送合成文本，",
        "服务端缓存文本，" "并根据标",
        "点符号判断，" "确定接收到",
        "完整句子后，" "调用合成引擎。",
    ]
    credential = Credential(secret_id="secret_id", secret_key="secret_key")
    speech = FlowingSpeechSynthesizer(app_id="1305695723", credential=credential)
    speech.set_voice_type(301030)
    speech.set_codec("mp3")
    speech.start(listener=TestSynthesisListener())
    ready = speech.wait_ready(10000)
    if not ready:
        raise RuntimeError("wss连接失败")
    for t in txt:
        speech.process(t)
        time.sleep(0.1)
    speech.complete()
    speech.wait()


if __name__ == "__main__":
    import os

    audio_file = "d:/data/tencent.mp3"
    if os.path.exists(audio_file):
        os.remove(audio_file)
    # test_speech()
    test_flow_speech()
