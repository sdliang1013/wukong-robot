# -*- coding: utf-8 -*-
# author: wzpan
# 写诗

from octopus.robot import log

from octopus.robot.sdk.AbstractPlugin import AbstractPlugin

INTENT = "BUILT_POEM"

logger = log.getLogger(__name__)


class Plugin(AbstractPlugin):
    SLUG = "poem"

    def handle(self, text, parsed):
        try:
            responds = self.nlu.getSay(parsed, INTENT)
            self.say(responds, cache=True)
        except Exception as e:
            logger.error(e, stack_info=True)
            self.say("抱歉，写诗插件出问题了，请稍后再试", cache=True)

    def isValid(self, text, parsed):
        return self.nlu.hasIntent(parsed, INTENT) and "写" in text and "诗" in text
