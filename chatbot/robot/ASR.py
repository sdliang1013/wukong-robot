# -*- coding: utf-8 -*-
import time
from abc import ABCMeta, abstractmethod

import requests
from aip import AipSpeech

from chatbot.robot import log, utils, config
from chatbot.robot.sdk import AliSpeech, XunfeiSpeech, BaiduSpeech, FunASREngine
from chatbot.robot.sdk.TencentSpeech import Credential, RecognizeListener, SpeechRecognizer

logger = log.getLogger(__name__)


class AbstractASR(object):
    """
    Generic parent class for all ASR engines
    """

    __metaclass__ = ABCMeta

    @classmethod
    def get_config(cls):
        return {}

    @classmethod
    def get_instance(cls):
        profile = cls.get_config()
        instance = cls(**profile)
        return instance

    @abstractmethod
    def transcribe(self, fp):
        pass


class AzureASR(AbstractASR):
    """
    微软的语音识别API
    """

    SLUG = "azure-asr"

    def __init__(self, secret_key, region, lang="zh-CN", **args):
        super(self.__class__, self).__init__()
        self.post_url = "https://<REGION_IDENTIFIER>.stt.speech.microsoft.com/speech/recognition/conversation/cognitiveservices/v1".replace(
            "<REGION_IDENTIFIER>", region
        )

        self.post_header = {
            "Ocp-Apim-Subscription-Key": secret_key,
            "Content-Type": "audio/wav; codecs=audio/pcm; samplerate=16000",
            "Accept": "application/json",
        }

        self.post_param = {"language": lang, "profanity": "raw"}
        self.sess = requests.session()

    @classmethod
    def get_config(cls):
        # Try to get azure_yuyin config from config
        return config.get("azure_yuyin", {})

    def transcribe(self, fp):
        # 识别本地文件
        pcm = utils.get_pcm_from_wav(fp)
        ret = self.sess.post(
            url=self.post_url,
            data=pcm,
            headers=self.post_header,
            params=self.post_param,
        )

        if ret.status_code == 200:
            res = ret.json()
            logger.info(f"{self.SLUG} 语音识别到了：{res['DisplayText']}")
            return "".join(res["DisplayText"])
        else:
            logger.info(f"{self.SLUG} 语音识别出错了: {res.text}")
            return ""


class BaiduASR(AbstractASR):
    """
    百度的语音识别API.
    dev_pid:
        - 1936: 普通话远场
        - 1536：普通话(支持简单的英文识别)
        - 1537：普通话(纯中文识别)
        - 1737：英语
        - 1637：粤语
        - 1837：四川话
    要使用本模块, 首先到 yuyin.baidu.com 注册一个开发者账号,
    之后创建一个新应用, 然后在应用管理的"查看key"中获得 API Key 和 Secret Key
    填入 config.xml 中.
    ...
        baidu_yuyin:
            appid: '9670645'
            api_key: 'qg4haN8b2bGvFtCbBGqhrmZy'
            secret_key: '585d4eccb50d306c401d7df138bb02e7'
        ...
    """

    SLUG = "baidu-asr"

    def __init__(self, appid, api_key, secret_key, dev_pid=1936, **args):
        super(self.__class__, self).__init__()
        if dev_pid != 80001:
            self.client = AipSpeech(appid, api_key, secret_key)
        else:
            self.client = BaiduSpeech.baiduSpeech(api_key, secret_key, dev_pid)
        self.dev_pid = dev_pid

    @classmethod
    def get_config(cls):
        # Try to get baidu_yuyin config from config
        return config.get("baidu_yuyin", {})

    def transcribe(self, fp):
        # 识别本地文件
        pcm = fp
        if isinstance(pcm, str):
            pcm = utils.get_pcm_from_wav(wav_path=pcm)
        res = self.client.asr(pcm, "pcm", 16000, {"dev_pid": self.dev_pid})
        res = res or dict(err_no=-1, err_msg='unknown')
        if res["err_no"] == 0:
            logger.info(f"{self.SLUG} 语音识别到了：{res['result']}")
            return "".join(res["result"])
        else:
            logger.info(f"{self.SLUG} 语音识别出错了: {res['err_msg']}")
            if res["err_msg"] == "request pv too much":
                logger.info("出现这个原因很可能是你的百度语音服务调用量超出限制，或未开通付费")
            return ""


class TencentASRListener(RecognizeListener):
    def __init__(self):
        self.words = []

    def on_recognition_start(self, ws, response):
        logger.debug("tencent asr start.")

    def on_recognition_complete(self, ws, response):
        logger.debug("tencent asr end.")

    def on_sentence_begin(self, ws, response):
        logger.debug("tencent asr sentence begin.")

    def on_sentence_temp(self, ws, response):
        logger.debug("tencent asr sentence temp: %s" % response["result"]["voice_text_str"])

    def on_sentence_end(self, ws, response):
        result = response["result"]["voice_text_str"]
        self.words.append(result)
        logger.debug("tencent asr sentence result: %s" % result)

    def on_fail(self, ws, response):
        logger.warning("tencent asr err.")


class TencentASR(AbstractASR):
    """
    腾讯的语音识别API.
    """

    SLUG = "tencent-asr"

    def __init__(self, appid, secretid, secret_key, engine_model_type=None, **kwargs):
        super(self.__class__, self).__init__()
        self.app_id = appid
        self.engine_model_type = engine_model_type or '16k_zh_en'
        # 1：pcm；4：speex(sp)；8：mp；12：wav；14：m4a；16：aac
        self.voice_format = 8
        self.time_wait = 0.04
        self.chunk_size = 2 * 16 * int(self.time_wait * 1000)
        self.credential = Credential(secret_id=secretid, secret_key=secret_key)

    @classmethod
    def get_config(cls):
        # Try to get tencent_yuyin config from config
        return config.get("tencent_yuyin", {})

    def transcribe(self, fp):
        mp3_path = fp
        if isinstance(mp3_path, str):
            mp3_path = utils.convert_wav_to_mp3(wav_path=mp3_path)
        # init
        recognizer = SpeechRecognizer(app_id=self.app_id, credential=self.credential,
                                      engine_model_type=self.engine_model_type)
        recognizer.set_voice_format(self.voice_format)
        # connect
        listener = TencentASRListener()
        recognizer.start(listener=listener)
        # send voice
        with open(file=mp3_path, mode='r+b') as f:
            recognizer.write(f.read(self.chunk_size))
            time.sleep(self.time_wait)
        recognizer.stop()
        # result
        text = "".join(listener.words)
        logger.info(f"{self.SLUG} 语音识别到了：{text}")
        return text


class XunfeiASR(AbstractASR):
    """
    科大讯飞的语音识别API.
    外网ip查询：https://ip.51240.com/
    """

    SLUG = "xunfei-asr"

    def __init__(self, appid, api_key, api_secret, **args):
        super(self.__class__, self).__init__()
        self.appid = appid
        self.api_key = api_key
        self.api_secret = api_secret

    @classmethod
    def get_config(cls):
        # Try to get xunfei_yuyin config from config
        return config.get("xunfei_yuyin", {})

    def transcribe(self, fp):
        return XunfeiSpeech.transcribe(fp, self.appid, self.api_key, self.api_secret)


class AliASR(AbstractASR):
    """
    阿里的语音识别API.
    """

    SLUG = "ali-asr"

    def __init__(self, appKey, token, **args):
        super(self.__class__, self).__init__()
        self.appKey, self.token = appKey, token

    @classmethod
    def get_config(cls):
        # Try to get ali_yuyin config from config
        return config.get("ali_yuyin", {})

    def transcribe(self, fp):
        result = AliSpeech.asr(self.appKey, self.token, fp)
        if result:
            logger.info(f"{self.SLUG} 语音识别到了：{result}")
            return result
        else:
            logger.critical(f"{self.SLUG} 语音识别出错了", stack_info=True)
            return ""


class WhisperASR(AbstractASR):
    """
    OpenAI 的 whisper 语音识别API
    """

    SLUG = "openai"

    def __init__(self, openai_api_key, **args):
        super(self.__class__, self).__init__()
        try:
            import openai

            self.openai = openai
            self.openai.api_key = openai_api_key
            print(openai_api_key)
        except Exception:
            logger.critical("OpenAI 初始化失败，请升级 Python 版本至 > 3.6")

    @classmethod
    def get_config(cls):
        return config.get("openai", {})

    def transcribe(self, fp):
        if self.openai:
            try:
                with open(fp, "rb") as f:
                    result = self.openai.Audio.transcribe("whisper-1", f)
                    if result:
                        logger.info(f"{self.SLUG} 语音识别到了：{result.text}")
                        return result.text
            except Exception:
                logger.critical(f"{self.SLUG} 语音识别出错了", stack_info=True)
                return ""
        logger.critical(f"{self.SLUG} 语音识别出错了", stack_info=True)
        return ""


class FunASR(AbstractASR):
    """
    达摩院FunASR实时语音转写服务软件包
    """

    SLUG = "fun-asr"

    def __init__(self, inference_type, model_dir, **args):
        super(self.__class__, self).__init__()
        self.engine = FunASREngine.funASREngine(inference_type, model_dir)

    @classmethod
    def get_config(cls):
        return config.get("fun_asr", {})

    def transcribe(self, fp):
        result = self.engine(fp)
        if result:
            logger.info(f"{self.SLUG} 语音识别到了：{result}")
            return result
        else:
            logger.critical(f"{self.SLUG} 语音识别出错了", stack_info=True)
            return ""


def get_engine_by_slug(slug=None):
    """
    Returns:
        An ASR Engine implementation available on the current platform

    Raises:
        ValueError if no speaker implementation is supported on this platform
    """

    if not slug or type(slug) is not str:
        raise TypeError("无效的 ASR slug '%s'", slug)

    selected_engines = list(
        filter(
            lambda engine: hasattr(engine, "SLUG") and engine.SLUG == slug,
            get_engines(),
        )
    )

    if len(selected_engines) == 0:
        raise ValueError(f"错误：找不到名为 {slug} 的 ASR 引擎")
    else:
        if len(selected_engines) > 1:
            logger.warning(f"注意: 有多个 ASR 名称与指定的引擎名 {slug} 匹配")
        engine = selected_engines[0]
        logger.info(f"使用 {engine.SLUG} ASR 引擎")
        return engine.get_instance()


def get_engines():
    def get_subclasses(cls):
        subclasses = set()
        for subclass in cls.__subclasses__():
            subclasses.add(subclass)
            subclasses.update(get_subclasses(subclass))
        return subclasses

    return [
        engine
        for engine in list(get_subclasses(AbstractASR))
        if hasattr(engine, "SLUG") and engine.SLUG
    ]
