# -*- coding: utf-8 -*-
import threading
from abc import ABCMeta, abstractmethod


from chatbot.robot import log
from chatbot.robot.compt import ThreadManager, Robot
from chatbot.robot.enums import AssistantEvent

logger = log.getLogger(__name__)

class AbstractAgent(object):
    __metaclass__ = ABCMeta

    @classmethod
    def get_instance(cls, **kwargs):
        instance = cls(**kwargs)
        return instance

    def __init__(self, **kwargs):
        pass

    @abstractmethod
    def start(self):
        pass

    @abstractmethod
    def stop(self):
        pass

    @abstractmethod
    def response(self, **kwargs):
        pass

    @abstractmethod
    def stop_response(self, **kwargs):
        pass


class ConversationAgent(AbstractAgent):
    SLUG = "conversation"
    
    def __init__(self, bot: Robot, conversation, **kwargs) -> None:
        super().__init__(**kwargs)
        self.bot = bot
        self.conversation = conversation
        self.running = threading.Event()
        self.interrupted = threading.Event()

    def start(self):
        self.running.set()

    def stop(self):
        self.running.clear()

    def response(self, query: str, **kwargs):
        # 响应
        self.interrupted.clear()
        ThreadManager.new(target=self._do_response, kwargs=dict(query=query)).start()

    def stop_response(self, req_id=None, manual=False, interrupt_time=None, **kwargs):
        # 停止响应
        self.interrupted.set()
        self.conversation.interrupt(req_id=req_id, manual=manual, interrupt_time=interrupt_time)

    def _do_response(self, query: str, **kwargs):
        try:
            self.conversation.do_response(query=query)
        finally:
            if not self.interrupted.is_set():
                self.bot.action(event=AssistantEvent.RESPONDED)



def get_agent_by_slug(slug, **kwargs) -> AbstractAgent:
    """
    Returns:
        A Agent implementation available on the current platform
    """
    if not slug or type(slug) is not str:
        raise TypeError("Invalid slug '%s'", slug)

    selects = list(
        filter(
            lambda _cls: hasattr(_cls, "SLUG") and _cls.SLUG == slug, get_agents()
        )
    )
    if len(selects) == 0:
        raise ValueError("No Agent found for slug '%s'" % slug)
    else:
        if len(selects) > 1:
            logger.warning(
                "WARNING: Multiple Agent found for slug '%s'. "
                + "This is most certainly a bug." % slug
            )
        select = selects[0]
        logger.info(f"使用 {select.SLUG} 智能体")
        return select.get_instance(**kwargs)


def get_agents():
    def get_subclasses(sub_cls):
        subclasses = set()
        for subclass in sub_cls.__subclasses__():
            subclasses.add(subclass)
            subclasses.update(get_subclasses(subclass))
        return subclasses

    return [
        _cls
        for _cls in list(get_subclasses(AbstractAgent))
        if hasattr(_cls, "SLUG") and _cls.SLUG
    ]
