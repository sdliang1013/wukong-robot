# -*- coding: utf-8 -*-

import os

from octopus.robot import constants, utils
from octopus.robot.sdk.AbstractPlugin import AbstractPlugin


class Plugin(AbstractPlugin):
    SLUG = "cleancache"

    def handle(self, text, parsed):
        temp = constants.TEMP_PATH
        for f in os.listdir(temp):
            if f != "DIR":
                utils.check_and_delete(os.path.join(temp, f))
        self.say("缓存目录已清空", cache=True)

    def isValid(self, text, parsed):
        return any(word in text.lower() for word in ["清除缓存", "清空缓存", "清缓存"])
