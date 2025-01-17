# -*- coding: utf-8 -*-
# author: wzpan
# 回声

from octopus.robot import log

from octopus.robot.sdk.AbstractPlugin import AbstractPlugin

logger = log.getLogger(__name__)


class Plugin(AbstractPlugin):
    def handle(self, text, parsed):
        text = text.lower().replace("echo", "").replace("传话", "")
        self.say(text, cache=False)

    def isValid(self, text, parsed):
        return any(word in text.lower() for word in ["echo", "传话"])
