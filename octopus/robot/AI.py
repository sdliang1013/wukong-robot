# -*- coding: utf-8 -*-
import json
import os
import random
import requests
from abc import ABCMeta, abstractmethod
from uuid import getnode as get_mac

from octopus.robot import log, config, utils
from octopus.robot.sdk import unit

logger = log.getLogger(__name__)


class AbstractRobot(object):
    __metaclass__ = ABCMeta

    @classmethod
    def get_instance(cls):
        profile = cls.get_config()
        instance = cls(**profile)
        return instance

    def __init__(self, **kwargs):
        pass

    def support_stream(self):
        return False

    @abstractmethod
    def chat(self, texts, parsed, **kwargs):
        pass

    @abstractmethod
    def stream_chat(self, texts, **kwargs):
        pass

    @abstractmethod
    def feedback(self, chat_id, **kwargs):
        pass


class TulingRobot(AbstractRobot):
    SLUG = "tuling"

    def __init__(self, tuling_key):
        """
        图灵机器人
        """
        super(self.__class__, self).__init__()
        self.tuling_key = tuling_key

    @classmethod
    def get_config(cls):
        return config.get("tuling", {})

    def chat(self, texts, parsed=None):
        """
        使用图灵机器人聊天

        Arguments:
        texts -- user input, typically speech, to be parsed by a module
        """
        msg = "".join(texts)
        msg = utils.stripEndPunc(msg)
        try:
            url = "http://openapi.turingapi.com/openapi/api/v2"
            userid = str(get_mac())[:32]
            body = {
                "perception": {"inputText": {"text": msg}},
                "userInfo": {"apiKey": self.tuling_key, "userId": userid},
            }
            r = requests.post(url, json=body)
            respond = json.loads(r.text)
            result = ""
            if "results" in respond:
                for res in respond["results"]:
                    result += "\n".join(res["values"].values())
            else:
                result = "图灵机器人服务异常，请联系作者"
            logger.debug(f"{self.SLUG} 回答：{result}")
            return result
        except Exception:
            logger.critical(
                "Tuling robot failed to response for %r", msg, exc_info=True
            )
            return "抱歉, 图灵机器人服务回答失败"


class UnitRobot(AbstractRobot):
    SLUG = "unit"

    def __init__(self):
        """
        百度UNIT机器人
        """
        super(self.__class__, self).__init__()

    @classmethod
    def get_config(cls):
        return {}

    def chat(self, texts, parsed):
        """
        使用百度UNIT机器人聊天

        Arguments:
        texts -- user input, typically speech, to be parsed by a module
        """
        msg = "".join(texts)
        msg = utils.stripEndPunc(msg)
        try:
            result = unit.getSay(parsed)
            logger.debug("{} 回答：{}".format(self.SLUG, result))
            return result
        except Exception:
            logger.critical("UNIT robot failed to response for %r", msg, exc_info=True)
            return "抱歉, 百度UNIT服务回答失败"


class BingRobot(AbstractRobot):
    SLUG = "bing"

    def __init__(self, prefix, proxy, mode):
        """
        bing
        """
        super(self.__class__, self).__init__()
        self.prefix = prefix
        self.proxy = proxy
        self.mode = mode

    @classmethod
    def get_config(cls):
        return config.get("bing", {})

    def chat(self, texts, parsed):
        """

        Arguments:
        texts -- user input, typically speech, to be parsed by a module
        """
        msg = "".join(texts)
        msg = utils.stripEndPunc(msg)
        try:
            import asyncio, json
            from EdgeGPT.EdgeGPT import Chatbot, ConversationStyle

            async def query_bing():
                # Passing cookies is "optional"
                bot = await Chatbot.create(proxy=self.proxy)
                m2s = {
                    "creative": ConversationStyle.creative,
                    "balanced": ConversationStyle.balanced,
                    "precise": ConversationStyle.precise,
                }
                response = await bot.ask(
                    prompt=self.prefix + "\n" + msg,
                    conversation_style=m2s[self.mode],
                    simplify_response=True,
                )
                # print(json.dumps(response, indent=2)) # Returns
                return response["text"]
                await bot.close()

            result = asyncio.run(query_bing())

            logger.debug("{} 回答：{}".format(self.SLUG, result))
            return result
        except Exception:
            logger.critical("bing robot failed to response for %r", msg, exc_info=True)
            return "抱歉, bing回答失败"


class AnyQRobot(AbstractRobot):
    SLUG = "anyq"

    def __init__(self, host, port, solr_port, threshold, secondary):
        """
        AnyQ机器人
        """
        super(self.__class__, self).__init__()
        self.host = host
        self.threshold = threshold
        self.port = port
        self.secondary = secondary

    @classmethod
    def get_config(cls):
        # Try to get anyq config from config
        return config.get("anyq", {})

    def chat(self, texts, parsed):
        """
        使用AnyQ机器人聊天

        Arguments:
        texts -- user input, typically speech, to be parsed by a module
        """
        msg = "".join(texts)
        msg = utils.stripEndPunc(msg)
        try:
            url = f"http://{self.host}:{self.port}/anyq?question={msg}"
            r = requests.get(url)
            respond = json.loads(r.text)
            logger.debug(f"anyq response: {respond}")
            if len(respond) > 0:
                # 有命中，进一步判断 confidence 是否达到要求
                confidence = respond[0]["confidence"]
                if confidence >= self.threshold:
                    # 命中该问题，返回回答
                    answer = respond[0]["answer"]
                    if utils.validjson(answer):
                        answer = random.choice(json.loads(answer))
                    logger.debug(f"{self.SLUG} 回答：{answer}")
                    return answer
            # 没有命中，走兜底
            if self.secondary != "null" and self.secondary:
                try:
                    ai = get_robot_by_slug(self.secondary)
                    return ai.chat(texts, parsed)
                except Exception:
                    logger.critical(
                        f"Secondary robot {self.secondary} failed to response for {msg}"
                    )
                    return get_unknown_response()
            else:
                return get_unknown_response()
        except Exception:
            logger.critical("AnyQ robot failed to response for %r", msg, exc_info=True)
            return "抱歉, AnyQ回答失败"


class OPENAIRobot(AbstractRobot):
    SLUG = "openai"

    def __init__(
        self,
        openai_api_key,
        model,
        provider,
        api_version,
        temperature,
        max_tokens,
        top_p,
        frequency_penalty,
        presence_penalty,
        stop_ai,
        prefix="",
        proxy="",
        api_base="",
    ):
        """
        OpenAI机器人
        """
        super(self.__class__, self).__init__()
        self.openai = None
        try:
            import openai

            self.openai = openai
            if not openai_api_key:
                openai_api_key = os.getenv("OPENAI_API_KEY")
            self.openai.api_key = openai_api_key
            if proxy:
                logger.info(f"{self.SLUG} 使用代理：{proxy}")
                self.openai.proxy = proxy
            else:
                self.openai.proxy = None

        except Exception:
            logger.critical("OpenAI 初始化失败，请升级 Python 版本至 > 3.6")
        self.model = model
        self.prefix = prefix
        self.provider = provider
        self.api_version = api_version
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.top_p = top_p
        self.frequency_penalty = frequency_penalty
        self.presence_penalty = presence_penalty
        self.stop_ai = stop_ai
        self.api_base = api_base if api_base else "https://api.openai.com/v1/chat"
        self.context = []

    @classmethod
    def get_config(cls):
        # Try to get anyq config from config
        return config.get("openai", {})

    def support_stream(self):
        return True

    def stream_chat(self, texts, **kwargs):
        """
        从ChatGPT API获取回复
        :return: 回复
        """

        msg = "".join(texts)
        msg = utils.stripEndPunc(msg)
        msg = self.prefix + msg  # 增加一段前缀
        logger.debug("msg: " + msg)
        self.context.append({"role": "user", "content": msg})

        header = {
            "Content-Type": "application/json",
            # "Authorization": "Bearer " + self.openai.api_key
        }
        if self.provider == "openai":
            header["Authorization"] = "Bearer " + self.openai.api_key
        elif self.provider == "azure":
            header["api-key"] = self.openai.api_key
        else:
            raise ValueError(
                "Please check your config file, OpenAiRobot's provider should be openai or azure."
            )

        data = {"model": self.model, "messages": self.context, "stream": True}
        logger.debug(f"使用模型：{self.model}，开始流式请求")
        url = self.api_base + "/completions"
        if self.provider == "azure":
            url = f"{self.api_base}/openai/deployments/{self.model}/chat/completions?api-version={self.api_version}"
        # 请求接收流式数据
        try:
            response = requests.request(
                "POST",
                url,
                headers=header,
                json=data,
                stream=True,
                proxies={"https": self.openai.proxy},
            )

            def generate():
                contants = []
                i = 0
                for line in response.iter_lines():
                    line_str = str(line, encoding="utf-8")
                    if line_str.startswith("data:") and line_str[5:]:
                        if line_str.startswith("data: [DONE]"):
                            break
                        line_json = json.loads(line_str[5:])
                        choices = line_json.get("choices", [])
                        if choices:
                            delta_content = (
                                choices[0].get("delta", {}).get("content", "")
                            )
                            i += 1
                            if i < 40:
                                logger.debug(delta_content)  # , end="")
                            elif i == 40:
                                logger.debug("......")
                            contants.append(delta_content)
                            yield delta_content
                    elif len(line_str.strip()) > 0:
                        logger.debug(line_str)
                        yield line_str
                self.context.append({"role": "assistant", "content": "".join(contants)})

        except Exception as e:
            ee = e

            def generate():
                yield "request error:\n" + str(ee)

        return generate

    def chat(self, texts, parsed):
        """
        使用OpenAI机器人聊天

        Arguments:
        texts -- user input, typically speech, to be parsed by a module
        """
        msg = "".join(texts)
        msg = utils.stripEndPunc(msg)
        msg = self.prefix + msg  # 增加一段前缀
        logger.debug("msg: " + msg)
        try:
            respond = ""
            self.context.append({"role": "user", "content": msg})
            if self.provider == "openai":
                response = self.openai.Completion.create(
                    model=self.model,
                    messages=self.context,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    top_p=self.top_p,
                    frequency_penalty=self.frequency_penalty,
                    presence_penalty=self.presence_penalty,
                    stop=self.stop_ai,
                    api_base=self.api_base,
                )
            else:
                from openai import AzureOpenAI

                client = AzureOpenAI(
                    azure_endpoint=self.api_base,
                    api_key=self.openai_api_key,
                    api_version=self.api_version,
                )
                response = client.chat.completions.create(
                    model=self.model, messages=self.context
                )
            message = response.choices[0].message
            respond = message.content
            self.context.append(message)
            return respond
        except self.openai.error.InvalidRequestError:
            logger.warning("token超出长度限制，丢弃历史会话")
            self.context = []
            return self.chat(texts, parsed)
        except Exception:
            logger.critical(
                "openai robot failed to response for %r", msg, exc_info=True
            )
            return "抱歉，OpenAI 回答失败"


class WenxinRobot(AbstractRobot):
    SLUG = "wenxin"

    def __init__(self, api_key, secret_key):
        """
        Wenxin机器人
        """
        super(self.__class__, self).__init__()
        self.api_key = api_key
        self.secret_key = secret_key

    @classmethod
    def get_config(cls):
        return config.get("wenxin", {})

    def chat(self, texts, _):
        """
        使用Wenxin机器人聊天

        Arguments:
        texts -- user input, typically speech, to be parsed by a module
        """
        msg = "".join(texts)
        msg = utils.stripEndPunc(msg)
        wenxinurl = f"https://aip.baidubce.com/oauth/2.0/token?client_id={self.api_key}&\
                    client_secret={self.secret_key}&grant_type=client_credentials"
        try:
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
            payload = json.dumps(
                {
                    "question": [
                        {
                            "role": "user",
                            "content": msg,
                        }
                    ]
                }
            )
            response = requests.request("POST", wenxinurl, headers=headers)
            logger.debug(f"wenxin response: {response}")
            return response.text

        except Exception:
            logger.critical(
                "Wenxin robot failed to response for %r", msg, exc_info=True
            )
            return "抱歉, Wenxin回答失败"


class TongyiRobot(AbstractRobot):
    """
    usage:
    pip install dashscope
    echo "export DASHSCOPE_API_KEY=YOUR_KEY" >> /.bashrc
    """

    SLUG = "tongyi"

    def __init__(self, api_key):
        """
        Tongyi机器人
        """
        super(self.__class__, self).__init__()
        self.api_key = api_key

    @classmethod
    def get_config(cls):
        return config.get("tongyi", {})

    def chat(self, texts, _):
        """
        使用Tongyi机器人聊天

        Arguments:
        texts -- user input, typically speech, to be parsed by a module
        """
        msg = "".join(texts)
        msg = utils.stripEndPunc(msg)
        msg = [{"role": "user", "content": msg}]
        try:
            response = dashscope.Generation.call(
                model="qwen1.5-72b-chat",
                messages=msg,
                result_format="message",  # set the result to be "message" format.
            )
            logger.debug(f"tongyi response: {response}")
            return response["output"]["choices"][0]["message"]["content"]

        except Exception:
            logger.critical(
                "Tongyi robot failed to response for %r", msg, exc_info=True
            )
            return "抱歉, Tongyi回答失败"


class FastGPTRobot(AbstractRobot):
    SLUG = "fastgpt"

    def __init__(
        self,
        api_key,
        prefix="",
        proxy="",
        api_base="",
        app_id="",
    ):
        """
        FastGPT机器人
        """
        super(FastGPTRobot, self).__init__()
        if not api_key:
            api_key = os.getenv("FASTGPT_API_KEY")
        self.api_key = api_key
        self.proxy = proxy
        self.prefix = prefix
        self.api_base = api_base if api_base else "https://api.tryfastgpt.ai"
        self.app_id = app_id
        self.context = []
        if proxy:
            logger.info(f"{self.SLUG} 使用代理：{proxy}")

    @classmethod
    def get_config(cls):
        # Try to get anyq config from config
        return config.get("fastgpt", {})

    def support_stream(self):
        return True

    def stream_chat(
        self, texts, chat_id=None, data_id=None, response_id=None, vars=None, **kwargs
    ):
        """
        从FastGPT API获取回复
        :return: 回复
        """
        header = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self.api_key,
        }
        msg = "".join(texts)
        msg = utils.stripEndPunc(msg)
        msg = self.prefix + msg  # 增加一段前缀
        logger.info("msg: %s", msg)

        dict_msg = {"role": "user", "content": msg}
        if data_id:  # req数据ID
            dict_msg.update(dataId=data_id)
        self.context.append(dict_msg)

        data = {"messages": self.context, "stream": True}
        if self.app_id:
            data.update(appId=self.app_id)
        if chat_id:  # 会话ID
            data.update(chatId=chat_id)
        if response_id:  # resp数据ID
            data.update(responseChatItemId=response_id)
        if vars:  # 变量
            data.update(variables=vars)

        logger.debug("使用 FastGPT 开始流式请求")
        url = self.api_base + "/api/v1/chat/completions"
        # 请求接收流式数据
        try:
            response = requests.request(
                method="POST",
                url=url,
                headers=header,
                json=data,
                stream=True,
                proxies={"https": self.proxy},
            )

            def generate():
                contants = []
                i = 0
                for line in response.iter_lines():
                    line_str = str(line, encoding="utf-8")
                    if line_str.startswith("data:") and line_str[5:]:
                        if line_str.startswith("data: [DONE]"):
                            break
                        line_json = json.loads(line_str[5:])
                        choices = line_json.get("choices", [])
                        if not choices:
                            continue
                        delta_content = choices[0].get("delta", {}).get("content", "")
                        i += 1
                        if i < 40:
                            logger.debug(delta_content)  # , end="")
                        elif i == 40:
                            logger.debug("......")
                        contants.append(delta_content)
                        yield delta_content
                    elif len(line_str.strip()) > 0:
                        logger.debug(line_str)
                        yield line_str
                self.context.append({"role": "assistant", "content": "".join(contants)})

        except Exception as e:
            ee = e

            def generate():
                yield "request error:\n" + str(ee)

        return generate

    def chat(
        self,
        texts,
        parsed,
        chat_id=None,
        data_id=None,
        response_id=None,
        vars=None,
        **kwargs,
    ):
        """
        使用FastGPT机器人聊天

        Arguments:
        texts -- user input, typically speech, to be parsed by a module
        """
        header = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self.api_key,
        }
        msg = "".join(texts)
        msg = utils.stripEndPunc(msg)
        msg = self.prefix + msg  # 增加一段前缀
        logger.info("msg: " + msg)

        dict_msg = {"role": "user", "content": msg}
        if data_id:  # req数据ID
            dict_msg.update(dataId=data_id)
        self.context.append(dict_msg)

        data = {"messages": self.context, "stream": False}
        if self.app_id:
            data.update(appId=self.app_id)
        if chat_id:  # 会话ID
            data.update(chatId=chat_id)
        if response_id:  # resp数据ID
            data.update(responseChatItemId=response_id)
        if vars:  # 变量
            data.update(variables=vars)

        logger.info(f"使用 FastGPT 开始请求")
        url = self.api_base + "/api/v1/chat/completions"
        # 请求接收流式数据
        try:
            response = requests.request(
                method="POST",
                url=url,
                headers=header,
                json=data,
                proxies={"https": self.proxy},
            )
            response.raise_for_status()
            resp_json = response.json()

            contents = []
            # 提取choices的内容
            choices = resp_json.get("choices", [])
            for choice in choices:
                msg_txt = choice.get("message", {}).get("content", "")
                if msg_txt:
                    contents.append(msg_txt)
            content = "".join(contents)
            self.context.append({"role": "assistant", "content": content})
            return content
        except Exception as e:
            logger.critical("FastGPT failed to response for %r", str(e), exc_info=True)
            return "request error:\n" + str(e)

    def feedback(self, chat_id, data_id, is_good=True, opinion=None, **kwargs):
        """意见反馈"""
        header = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self.api_key,
        }

        # v4.8.11之前
        # data = {"chatId": chat_id, "chatItemId": data_id,}
        # v4.8.11之后
        data = {
            "chatId": chat_id,
            "dataId": data_id,
        }
        if self.app_id:
            data.update(appId=self.app_id)
        if is_good:
            data.update(userGoodFeedback="yes")
        else:
            data.update(userBadFeedback="yes")
        url = self.api_base + "/api/core/chat/feedback/updateUserFeedback"
        # 请求数据
        try:
            response = requests.request(
                method="POST",
                url=url,
                headers=header,
                json=data,
                proxies={"https": self.proxy},
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.critical("FastGPT failed to response for %r", str(e), exc_info=True)
            return "抱歉, FastGPT反馈服务失败"


def get_unknown_response():
    """
    不知道怎么回答的情况下的答复

    :returns: 表示不知道的答复
    """
    results = [
        "抱歉，我不会这个呢",
        "我不会这个呢",
        "我还不会这个呢",
        "我还没学会这个呢",
        "对不起，你说的这个，我还不会",
    ]
    return random.choice(results)


def get_robot_by_slug(slug):
    """
    Returns:
        A robot implementation available on the current platform
    """
    if not slug or type(slug) is not str:
        raise TypeError("Invalid slug '%s'", slug)

    selected_robots = list(
        filter(
            lambda robot: hasattr(robot, "SLUG") and robot.SLUG == slug, get_robots()
        )
    )
    if len(selected_robots) == 0:
        raise ValueError("No robot found for slug '%s'" % slug)
    else:
        if len(selected_robots) > 1:
            logger.warning(
                "WARNING: Multiple robots found for slug '%s'. "
                + "This is most certainly a bug." % slug
            )
        robot = selected_robots[0]
        logger.info(f"使用 {robot.SLUG} 对话机器人")
        return robot.get_instance()


def get_robots():
    def get_subclasses(cls):
        subclasses = set()
        for subclass in cls.__subclasses__():
            subclasses.add(subclass)
            subclasses.update(get_subclasses(subclass))
        return subclasses

    return [
        robot
        for robot in list(get_subclasses(AbstractRobot))
        if hasattr(robot, "SLUG") and robot.SLUG
    ]
