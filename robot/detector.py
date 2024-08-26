# -*- coding: UTF-8 -*-
import os
import threading
import time

from snowboy import snowboydecoder
from robot import config, logging, constants

logger = logging.getLogger(__name__)


class Detector:

    def __init__(self) -> None:
        self.detector = None
        self.recorder = None
        self.porcupine = None
        self.listen_kw = threading.Event()

    def detect(self, wukong):
        """
        唤醒热词监听器，支持 snowboy 和 porcupine 两大引擎
        """
        self.listen_kw.set()
        if config.get("detector", "snowboy") == "porcupine":
            self.run_porcupine(wukong=wukong)
        else:
            self.run_snowboy(wukong=wukong)

    def run_porcupine(self, wukong):
        logger.info("使用 porcupine 进行离线唤醒")

        import pvporcupine
        from pvrecorder import PvRecorder

        access_key = config.get("/porcupine/access_key")
        keyword_paths = config.get("/porcupine/keyword_paths")
        keywords = config.get("/porcupine/keywords", ["porcupine"])
        if keyword_paths:
            self.porcupine = pvporcupine.create(
                access_key=access_key,
                model_path=os.path.join(
                    os.path.dirname(__file__),
                    os.path.pardir,
                    "static/porcupine_params.pv",
                ),
                keyword_paths=[constants.getConfigData(kw) for kw in keyword_paths],
                sensitivities=[config.get("sensitivity", 0.5)] * len(keyword_paths),
            )
        else:
            self.porcupine = pvporcupine.create(
                access_key=access_key,
                keywords=keywords,
                sensitivities=[config.get("sensitivity", 0.5)] * len(keywords),
            )

        self.recorder = PvRecorder(
            device_index=-1, frame_length=self.porcupine.frame_length
        )
        self.recorder.start()

        try:
            while True:
                # 关键字处理
                self.check_kw(keyword_paths=keyword_paths, keywords=keywords)
                # 聆听
                self.wakeup(wukong=wukong)
        except pvporcupine.PorcupineActivationError as e:
            logger.error("[Porcupine] AccessKey activation error", stack_info=True)
            raise e
        except pvporcupine.PorcupineActivationLimitError as e:
            logger.error(
                f"[Porcupine] AccessKey {access_key} has reached it's temporary device limit",
                stack_info=True,
            )
            raise e
        except pvporcupine.PorcupineActivationRefusedError as e:
            logger.error(
                "[Porcupine] AccessKey '%s' refused" % access_key, stack_info=True
            )
            raise e
        except pvporcupine.PorcupineActivationThrottledError as e:
            logger.error(
                "[Porcupine] AccessKey '%s' has been throttled" % access_key,
                stack_info=True,
            )
            raise e
        except pvporcupine.PorcupineError as e:
            logger.error("[Porcupine] 初始化 Porcupine 失败", stack_info=True)
            raise e
        except KeyboardInterrupt:
            logger.info(msg="Stopping ...")
        finally:
            self.porcupine and self.porcupine.delete()
            self.recorder and self.recorder.delete()

    def run_snowboy(self, wukong):
        logger.info("使用 snowboy 进行离线唤醒")
        self.terminate()
        models = constants.getHotwordModel(config.get("hotword", "wukong.pmdl"))
        self.detector = snowboydecoder.HotwordDetector(
            models, sensitivity=config.get("sensitivity", 0.5)
        )
        # main loop
        try:
            callbacks = wukong._detected_callback
            self.detector.start(
                detected_callback=callbacks,
                audio_recorder_callback=wukong.conversation.converse,
                interrupt_check=wukong._interrupt_callback,
                silent_count_threshold=config.get("silent_threshold", 15),
                recording_threshold=config.get("recording_timeout", 5) * 4,
                sleep_time=0.03,
            )
            self.terminate()
        except Exception as e:
            logger.critical(f"离线唤醒机制初始化失败：{e}", stack_info=True)

    def terminate(self):
        self.detector and self.detector.terminate()

    def check_kw(self, keyword_paths, keywords):
        while self.listen_kw.is_set():
            pcm = self.recorder.read()

            result = self.porcupine.process(pcm=pcm)
            if result < 0:
                continue
            kw = keyword_paths[result] if keyword_paths else keywords[result]
            # 清除标记
            self.listen_kw.clear()
            logger.info(
                "[porcupine] Keyword {} Detected at time {}".format(
                    kw,
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time())),
                )
            )
        self.listen_kw.set()

    def wakeup(self, wukong):
        self.recorder.stop()
        try:
            # 唤醒回调
            wukong.detected_callback(False)
            # 中断
            wukong.conversation.interrupt()
            # 聆听
            query = wukong.conversation.activeListen()
            # 响应
            thread_resp = threading.Thread(
                target=wukong.conversation.doResponse, kwargs=dict(query=query)
            )
            thread_resp.start()
        except:
            logger.critical("数字人走神了.", exc_info=True)
        finally:
            self.recorder.start()

    def set_kw(self):
        self.listen_kw.set()

    def clear_kw(self):
        self.listen_kw.clear()
