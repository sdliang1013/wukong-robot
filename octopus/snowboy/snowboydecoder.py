#!/usr/bin/env python

import collections
import os
import pyaudio
import threading
import time
import wave
from contextlib import contextmanager
from ctypes import CFUNCTYPE, c_char_p, c_int, cdll

from octopus import resource_file
from octopus.robot import constants, log
from octopus.robot import utils
from octopus.snowboy import snowboydetect

logger = log.getLogger("snowboy")

RESOURCE_FILE = resource_file("common.res")
DETECT_DING = resource_file("ding.wav")
DETECT_DONG = resource_file("dong.wav")


def py_error_handler(filename, line, function, err, fmt):
    pass


ERROR_HANDLER_FUNC = CFUNCTYPE(None, c_char_p, c_int, c_char_p, c_int, c_char_p)

c_error_handler = ERROR_HANDLER_FUNC(py_error_handler)


@contextmanager
def no_alsa_error():
    try:
        asound = cdll.LoadLibrary("libasound.so")
        asound.snd_lib_error_set_handler(c_error_handler)
        yield
        asound.snd_lib_error_set_handler(None)
    except:
        yield
        pass


class RingBuffer(object):
    """Ring buffer to hold audio from PortAudio"""

    def __init__(self, size=4096):
        self._buf = collections.deque(maxlen=size)

    def extend(self, data):
        """Adds data to the end of buffer"""
        self._buf.extend(data)

    def get(self):
        """Retrieves data from the beginning of buffer and clears it"""
        tmp = bytes(bytearray(self._buf))
        self._buf.clear()
        return tmp


def play_audio_file(fname=DETECT_DING):
    """Simple callback function to play a wave file. By default it plays
    a Ding sound.

    :param str fname: wave file name
    :return: None
    """
    ding_wav = wave.open(fname, "rb")
    ding_data = ding_wav.readframes(ding_wav.getnframes())
    with no_alsa_error():
        audio = pyaudio.PyAudio()
    stream_out = audio.open(
        format=audio.get_format_from_width(ding_wav.getsampwidth()),
        channels=ding_wav.getnchannels(),
        rate=ding_wav.getframerate(),
        input=False,
        output=True,
    )
    stream_out.start_stream()
    stream_out.write(ding_data)
    time.sleep(0.2)
    stream_out.stop_stream()
    stream_out.close()
    audio.terminate()


class ActiveListener(object):
    """Active Listening with VAD"""

    def __init__(self, decoder_model, resource=RESOURCE_FILE):
        logger.debug("activeListen __init__()")
        self.recordedData = []
        model_str = ",".join(decoder_model)
        self.detector = snowboydetect.SnowboyDetect(
            resource_filename=resource.encode(), model_str=model_str.encode()
        )
        self.ring_buffer = RingBuffer(
            self.detector.NumChannels() * self.detector.SampleRate() * 5
        )
        self.listening = threading.Event()

    def listen(
        self,
        interrupt_check=lambda: False,
        interval_time=0.03,
        silent_count_threshold=15,
        recording_threshold=100,
    ):
        """
        :param interrupt_check: a function that returns True if the main loop
                                needs to stop.
        :param silent_count_threshold: indicates how long silence must be heard
                                       to mark the end of a phrase that is
                                       being recorded.
        :param float interval_time: how much time in second every loop waits.
        :param recording_threshold: limits the maximum length of a recording.
        :return: recorded file path
        """
        logger.debug("activeListen listen()")

        self.set_listen()

        def audio_callback(in_data, frame_count, time_info, status):
            self.ring_buffer.extend(in_data)
            play_data = chr(0) * len(in_data)
            return play_data, pyaudio.paContinue

        with no_alsa_error():
            audio = pyaudio.PyAudio()

        logger.debug("opening audio stream")

        try:
            stream_in = audio.open(
                input=True,
                output=False,
                format=audio.get_format_from_width(self.detector.BitsPerSample() / 8),
                channels=self.detector.NumChannels(),
                rate=self.detector.SampleRate(),
                frames_per_buffer=2048,
                stream_callback=audio_callback,
            )
        except Exception as e:
            logger.critical(e, stack_info=True)
            return

        logger.debug("audio stream opened")

        if interrupt_check():
            logger.debug("detect voice interrupt.")
            return

        # 初始化数据
        silent_count = 0
        voice_count = 0
        recording_count = 0
        voice_file = None
        self.recordedData.clear()

        logger.debug("begin activeListen loop")

        while self.is_listening():

            if interrupt_check():
                logger.debug("detect voice break")
                break
            data = self.ring_buffer.get()
            if len(data) == 0:
                time.sleep(interval_time)
                continue

            status = self.detector.RunDetection(data)
            if status == -1:
                logger.warning("Error initializing streams or reading audio data")

            # todo 通过手工提交判断结束
            # 通过停止说话, 自动结束 ---
            stop_recording = False
            if recording_count > recording_threshold:
                stop_recording = True
            elif voice_count and status == -2:  # silence found
                if silent_count > silent_count_threshold:
                    stop_recording = True
                else:
                    silent_count += 1
            elif status == 0:  # voice found
                silent_count = 0
                voice_count += 1
            # 通过停止说话, 自动结束 ---
            # 结束录音
            if stop_recording == True:
                voice_file = self.saveMessage(
                    voice_data=self.recordedData,
                    samp_width=audio.get_sample_size(
                        audio.get_format_from_width(self.detector.BitsPerSample() / 8)
                    ),
                )
                break
            # 忽略开头的静音
            if voice_count:
                self.recordedData.append(data)
            recording_count += 1

        # 关闭
        stream_in.stop_stream()
        stream_in.close()
        audio.terminate()

        logger.debug("finished.")
        return voice_file

    def saveMessage(self, voice_data, samp_width):
        """
        Save the message stored in data to a timestamped file.
        """
        filename = os.path.join(
            constants.TEMP_PATH, "output" + str(int(time.time())) + ".wav"
        )

        # use wave to save data
        with wave.open(filename, "wb") as wf:
            wf.setnchannels(self.detector.NumChannels())
            wf.setsampwidth(samp_width)
            wf.setframerate(self.detector.SampleRate())
            wf.writeframes(b"".join(voice_data))

        logger.debug("finished saving: " + filename)

        return filename

    def set_listen(self):
        self.listening.set()

    def clear_listen(self):
        self.listening.clear()

    def is_listening(self):
        return self.listening.is_set()


class HotwordDetector(object):
    """
    Snowboy decoder to detect whether a keyword specified by `decoder_model`
    exists in a microphone input stream.

    :param decoder_model: decoder model file path, a string or a list of strings
    :param resource: resource file path.
    :param sensitivity: decoder sensitivity, a float of a list of floats.
                              The bigger the value, the more senstive the
                              decoder. If an empty list is provided, then the
                              default sensitivity in the model will be used.
    :param audio_gain: multiply input volume by this factor.
    :param apply_frontend: applies the frontend processing algorithm if True.
    """

    def __init__(
        self,
        decoder_model,
        resource=RESOURCE_FILE,
        sensitivity=[],
        audio_gain=1,
        apply_frontend=False,
    ):

        self._running = False

        tm = type(decoder_model)
        ts = type(sensitivity)
        if tm is not list:
            decoder_model = [decoder_model]
        if ts is not list:
            sensitivity = [sensitivity]
        model_str = ",".join(decoder_model)

        self.detector = snowboydetect.SnowboyDetect(
            resource_filename=resource.encode(), model_str=model_str.encode()
        )
        self.detector.SetAudioGain(audio_gain)
        self.detector.ApplyFrontend(apply_frontend)
        self.num_hotwords = self.detector.NumHotwords()

        if len(decoder_model) > 1 and len(sensitivity) == 1:
            sensitivity = sensitivity * self.num_hotwords
        if len(sensitivity) != 0:
            assert self.num_hotwords == len(sensitivity), (
                "number of hotwords in decoder_model (%d) and sensitivity "
                "(%d) does not match" % (self.num_hotwords, len(sensitivity))
            )
        sensitivity_str = ",".join([str(t) for t in sensitivity])
        if len(sensitivity) != 0:
            self.detector.SetSensitivity(sensitivity_str.encode())

        self.ring_buffer = RingBuffer(
            self.detector.NumChannels() * self.detector.SampleRate() * 5
        )

    def start(
        self,
        detected_callback=play_audio_file,
        interrupt_check=lambda: False,
        interval_time=0.03,
        audio_recorder_callback=None,
        silent_count_threshold=15,
        recording_threshold=100,
    ):
        """
        Start the voice detector. For every `interval_time` second it checks the
        audio buffer for triggering keywords. If detected, then call
        corresponding function in `detected_callback`, which can be a single
        function (single model) or a list of callback functions (multiple
        models). Every loop it also calls `interrupt_check` -- if it returns
        True, then breaks from the loop and return.

        :param detected_callback: a function or list of functions. The number of
                                  items must match the number of models in
                                  `decoder_model`.
        :param interrupt_check: a function that returns True if the main loop
                                needs to stop.
        :param float interval_time: how much time in second every loop waits.
        :param audio_recorder_callback: if specified, this will be called after
                                        a keyword has been spoken and after the
                                        phrase immediately after the keyword has
                                        been recorded. The function will be
                                        passed the name of the file where the
                                        phrase was recorded.
        :param silent_count_threshold: indicates how long silence must be heard
                                       to mark the end of a phrase that is
                                       being recorded.
        :param recording_threshold: limits the maximum length of a recording.
        :return: None
        """
        self._running = True

        def audio_callback(in_data, frame_count, time_info, status):
            if utils.isRecordable():
                self.ring_buffer.extend(in_data)
                play_data = chr(0) * len(in_data)
            else:
                play_data = chr(0)
            return play_data, pyaudio.paContinue

        with no_alsa_error():
            self.audio = pyaudio.PyAudio()
        self.stream_in = self.audio.open(
            input=True,
            output=False,
            format=self.audio.get_format_from_width(self.detector.BitsPerSample() / 8),
            channels=self.detector.NumChannels(),
            rate=self.detector.SampleRate(),
            frames_per_buffer=2048,
            stream_callback=audio_callback,
        )

        if interrupt_check():
            logger.debug("detect voice return")
            return

        tc = type(detected_callback)
        if tc is not list:
            detected_callback = [detected_callback]
        if len(detected_callback) == 1 and self.num_hotwords > 1:
            detected_callback *= self.num_hotwords

        assert self.num_hotwords == len(detected_callback), (
            "Error: hotwords in your models (%d) do not match the number of "
            "callbacks (%d)" % (self.num_hotwords, len(detected_callback))
        )

        logger.debug("detecting...")

        state = "PASSIVE"
        silentCount = 0
        voice_count = 0
        recordingCount = 0
        while self._running is True:
            if interrupt_check():
                logger.debug("detect voice break")
                break
            data = self.ring_buffer.get()
            if len(data) == 0:
                time.sleep(interval_time)
                continue

            status = self.detector.RunDetection(data)
            if status == -1:
                logger.warning("Error initializing streams or reading audio data")

            # small state machine to handle recording of phrase after keyword
            if state == "PASSIVE":
                if status > 0:  # key word found

                    self.recordedData = []
                    self.recordedData.append(data)
                    silentCount = 0
                    recordingCount = 0
                    voice_count = 0
                    message = "Keyword " + str(status) + " detected at time: "
                    message += time.strftime(
                        "%Y-%m-%d %H:%M:%S", time.localtime(time.time())
                    )
                    logger.info(message)
                    callback = detected_callback[status - 1]
                    callback and callback()

                    if (
                        audio_recorder_callback
                        and status == 1
                        and utils.is_proper_time()
                    ):
                        state = "ACTIVE"
                    continue

            elif state == "ACTIVE":
                stopRecording = False
                if recordingCount > recording_threshold:
                    stopRecording = True
                elif voice_count and status == -2:  # silence found
                    if silentCount > silent_count_threshold:
                        stopRecording = True
                    else:
                        silentCount += 1
                elif status == 0:  # voice found
                    silentCount = 0
                    voice_count += 1

                if stopRecording == True:
                    fname = self.saveMessage()
                    audio_recorder_callback(fname)
                    state = "PASSIVE"
                    continue

                recordingCount += 1
                self.recordedData.append(data)

        logger.debug("finished.")

    def saveMessage(self):
        """
        Save the message stored in self.recordedData to a timestamped file.
        """
        filename = os.path.join(
            constants.TEMP_PATH, "output" + str(int(time.time())) + ".wav"
        )
        data = b"".join(self.recordedData)

        # use wave to save data
        wf = wave.open(filename, "wb")
        wf.setnchannels(self.detector.NumChannels())
        wf.setsampwidth(
            self.audio.get_sample_size(
                self.audio.get_format_from_width(self.detector.BitsPerSample() / 8)
            )
        )
        wf.setframerate(self.detector.SampleRate())
        wf.writeframes(data)
        wf.close()
        logger.debug("finished saving: " + filename)
        return filename

    def terminate(self):
        """
        Terminate audio stream. Users can call start() again to detect.
        :return: None
        """
        if self._running:
            self.stream_in.stop_stream()
            self.stream_in.close()
            self.audio.terminate()
            self._running = False
