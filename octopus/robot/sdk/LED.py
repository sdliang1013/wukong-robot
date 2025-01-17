import _thread as thread
from octopus.robot import config, log
from octopus.robot.drivers.AIY import AIY

logger = log.getLogger(__name__)

aiy = AIY()


def wakeup():
    if config.get("/LED/enable", False):
        if config.get("/LED/type") == "aiy":
            thread.start_new_thread(aiy.wakeup, ())
        elif config.get("/LED/type") == "respeaker":
            from octopus.robot.drivers.pixels import pixels

            pixels.wakeup()
        else:
            logger.error("错误：不支持的灯光类型", stack_info=True)


def think():
    if config.get("/LED/enable", False):
        if config.get("/LED/type") == "aiy":
            thread.start_new_thread(aiy.think, ())
        elif config.get("/LED/type") == "respeaker":
            from octopus.robot.drivers.pixels import pixels

            pixels.think()
        else:
            logger.error("错误：不支持的灯光类型", stack_info=True)


def off():
    if config.get("/LED/enable", False):
        if config.get("/LED/type") == "aiy":
            thread.start_new_thread(aiy.off, ())
        elif config.get("/LED/type") == "respeaker":
            from octopus.robot.drivers.pixels import pixels

            pixels.off()
        else:
            logger.error("错误：不支持的灯光类型", stack_info=True)
