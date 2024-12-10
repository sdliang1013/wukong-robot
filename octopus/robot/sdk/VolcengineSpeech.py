import gzip
import json
import threading
import time
import uuid

from websocket import WebSocketApp

from octopus.robot import log

logger = log.getLogger(__name__)

cluster = "volc.bigasr.sauc.duration"
# cluster = "volc.bigasr.sauc.concurrent"

PROTOCOL_VERSION = 0b0001
DEFAULT_HEADER_SIZE = 0b0001

# Message Type:
FULL_CLIENT_REQUEST = 0b0001
AUDIO_ONLY_REQUEST = 0b0010
FULL_SERVER_RESPONSE = 0b1001
SERVER_ACK = 0b1011
SERVER_ERROR_RESPONSE = 0b1111

# Message Type Specific Flags
REQ_FULL_OR_NO_FINAL = 0b0000  # no check sequence
REQ_SEQUENCE = 0b0001  # check sequence
RESP_FULL_OR_NO_FINAL = 0b0001
REQ_FINAL = 0b0010
RESP_FINAL = 0b0011

# Message Serialization
NO_SERIALIZATION = 0b0000
JSON = 0b0001

# Message Compression
NO_COMPRESSION = 0b0000
GZIP = 0b0001

# ws status
NOTOPEN = 0
STARTED = 1
OPENED = 2
FINAL = 3
ERROR = 4
CLOSED = 5


def generate_header(
    message_type=FULL_CLIENT_REQUEST,
    message_type_specific_flags=REQ_FULL_OR_NO_FINAL,
    serial_method=JSON,
    compression_type=GZIP,
    reserved_data=0x00,
):
    """
    protocol_version(4 bits), header_size(4 bits),
    message_type(4 bits), message_type_specific_flags(4 bits)
    serialization_method(4 bits) message_compression(4 bits)
    reserved （8bits) 保留字段
    """
    header = bytearray()
    header_size = 1
    header.append((PROTOCOL_VERSION << 4) | header_size)
    header.append((message_type << 4) | message_type_specific_flags)
    header.append((serial_method << 4) | compression_type)
    header.append(reserved_data)
    return header


def generate_before_payload(sequence: int):
    """请求体前的内容: 序号"""
    before_payload = bytearray()
    before_payload.extend(sequence.to_bytes(4, "big", signed=True))  # sequence
    return before_payload


def parse_response(res) -> dict:
    """
    protocol_version(4 bits), header_size(4 bits),
    message_type(4 bits), message_type_specific_flags(4 bits)
    serialization_method(4 bits) message_compression(4 bits)
    reserved （8bits) 保留字段
    header_extensions 扩展头(大小等于 8 * 4 * (header_size - 1) )
    payload 类似与http 请求体
    """
    protocol_version = res[0] >> 4
    header_size = res[0] & 0x0F
    message_type = res[1] >> 4
    message_type_specific_flags = res[1] & 0x0F
    serialization_method = res[2] >> 4
    message_compression = res[2] & 0x0F
    reserved = res[3]
    header_extensions = res[4 : header_size * 4]
    payload = res[header_size * 4 :]
    result = {
        "is_last_package": False,
    }
    payload_msg = None
    payload_size = 0
    if message_type_specific_flags & 0x01:
        # receive frame with sequence
        seq = int.from_bytes(payload[:4], "big", signed=True)
        result["payload_sequence"] = seq
        payload = payload[4:]

    if message_type_specific_flags & 0x02:
        # receive last package
        result["is_last_package"] = True

    if message_type == FULL_SERVER_RESPONSE:
        payload_size = int.from_bytes(payload[:4], "big", signed=True)
        payload_msg = payload[4:]
    elif message_type == SERVER_ACK:
        seq = int.from_bytes(payload[:4], "big", signed=True)
        result["seq"] = seq
        if len(payload) >= 8:
            payload_size = int.from_bytes(payload[4:8], "big", signed=False)
            payload_msg = payload[8:]
    elif message_type == SERVER_ERROR_RESPONSE:
        code = int.from_bytes(payload[:4], "big", signed=False)
        result["code"] = code
        payload_size = int.from_bytes(payload[4:8], "big", signed=False)
        payload_msg = payload[8:]
    if payload_msg is None:
        return result
    if message_compression == GZIP:
        payload_msg = gzip.decompress(payload_msg)
    if serialization_method == JSON:
        payload_msg = json.loads(str(payload_msg, "utf-8"))
    elif serialization_method != NO_SERIALIZATION:
        payload_msg = str(payload_msg, "utf-8")
    result["payload_msg"] = payload_msg
    result["payload_size"] = payload_size
    return result


class StreamLmClient:
    def __init__(self, app_id, token, **kwargs):
        """
        :param config: config
        """
        self.app_id = app_id
        self.token = token
        self.success_code = 1000  # success code, default is 1000
        self.seg_duration = int(kwargs.get("seg_duration", 100))
        self.uid = kwargs.get("uid", "test")
        self.format = kwargs.get("format", "wav")
        self.rate = kwargs.get("rate", 16000)
        self.bits = kwargs.get("bits", 16)
        self.channel = kwargs.get("channel", 1)
        self.codec = kwargs.get("codec", "raw")
        self.auth_method = kwargs.get("auth_method", "none")
        self.hot_words = kwargs.get("hot_words", None)
        self.streaming = kwargs.get("streaming", True)
        self.silence_threshold = kwargs.get("silence_threshold", 500)
        self.online_url = kwargs.get(
            "online_url", "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel"
        )
        self.offline_url = kwargs.get(
            "offline_url",
            "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream",
        )

    def conn_online(
        self, on_message, on_open=None, on_close=None, on_error=None
    ) -> WebSocketApp:
        return WebSocketApp(
            url=self.online_url,
            header=self.ws_header(str(uuid.uuid4())),
            on_message=on_message,
            on_open=on_open or self._on_open,
            on_error=on_error or self._on_error,
            on_close=on_close or self._on_close,
        )

    def conn_offline(
        self, on_message, on_open=None, on_close=None, on_error=None
    ) -> WebSocketApp:
        return WebSocketApp(
            url=self.offline_url,
            header=self.ws_header(str(uuid.uuid4())),
            on_message=on_message,
            on_open=on_open or self._on_open,
            on_error=on_error or self._on_error,
            on_close=on_close or self._on_close,
        )

    def ws_header(self, req_id) -> dict:
        return {
            "X-Api-Resource-Id": cluster,
            "X-Api-Access-Key": self.token,
            "X-Api-App-Key": self.app_id,
            "X-Api-Connect-Id": req_id,
        }

    def meta_payload(self):
        return {
            "user": {
                "uid": self.uid,
            },
            "audio": {
                "format": self.format,
                "sample_rate": self.rate,
                "bits": self.bits,
                "channel": self.channel,
                "codec": self.codec,
            },
            "request": {
                "model_name": "bigmodel",
                "enable_punc": True,
                "result_type": "single",
                # "vad_segment_duration": 800,
                "end_window_size": self.silence_threshold,
                "context": {"hotwords": [{"word": word} for word in self.hot_words]},
            },
        }

    def send_meta_info(self, ws: WebSocketApp, seq: int = None):
        specific_flags = REQ_FULL_OR_NO_FINAL
        if seq:
            specific_flags = REQ_SEQUENCE
        meta_params = self.meta_payload()
        payload_bytes = str.encode(json.dumps(meta_params))
        payload_bytes = gzip.compress(payload_bytes)
        # header
        meta_request = bytearray(
            generate_header(message_type_specific_flags=specific_flags)
        )
        if seq:
            meta_request.extend(generate_before_payload(sequence=seq))
        # payload
        meta_request.extend(
            (len(payload_bytes)).to_bytes(4, "big")
        )  # payload size(4 bytes)
        # req_str = ' '.join(format(byte, '02x') for byte in meta_request)
        # print(f"{time.time()}, seq", seq, "req", req_str)
        meta_request.extend(payload_bytes)
        # send
        ws.send_bytes(meta_request)

    def send_audio_data(
        self, ws: WebSocketApp, chunk_data: bytes, seq: int = None, is_final=False
    ):
        specific_flags = REQ_FULL_OR_NO_FINAL
        if seq:
            specific_flags = REQ_SEQUENCE
        if seq and is_final:
            specific_flags = RESP_FINAL
        payload_bytes = gzip.compress(chunk_data)
        # header
        audio_request = bytearray(
            generate_header(
                message_type=AUDIO_ONLY_REQUEST,
                message_type_specific_flags=specific_flags,
            )
        )
        if seq:
            audio_request.extend(generate_before_payload(sequence=seq))
        # payload
        audio_request.extend(
            (len(payload_bytes)).to_bytes(4, "big")
        )  # payload size(4 bytes)
        # req_str = ' '.join(format(byte, '02x') for byte in audio_request)
        # print("seq", seq, "req", req_str)
        audio_request.extend(payload_bytes)
        # send
        ws.send_bytes(audio_request)

    def chunk_size(self) -> int:
        """音频块数据大小(16/8=2)"""
        return int(self.rate * 2 * self.channel * self.seg_duration / 1000)

    def chunk_time(self, last_time: float) -> float:
        """音频块时长"""
        return max(0.0, (self.seg_duration / 1000.0 - (time.time() - last_time)))

    @classmethod
    def parse_message(cls, message: str) -> dict:
        return parse_response(message)

    @staticmethod
    def slice_data(data: bytes, chunk_size: int) -> (list, bool):
        data_len = len(data)
        offset = chunk_size
        while offset < data_len:
            yield data[offset - chunk_size : offset], False
            offset += chunk_size
        yield data[offset - chunk_size : data_len], True

    def _on_error(self, ws: WebSocketApp, error):
        logger.error("volcengine ws err, error={}, session_id={}".format(error, ws))

    def _on_close(self, ws: WebSocketApp, code, message):
        logger.info(
            "volcengine ws closed, close_status_code={} close_msg={}".format(
                code, message
            )
        )

    def _on_open(self, ws: WebSocketApp):
        logger.info("volcengine ws opened")
        self.send_meta_info(ws)


def _record_microphone(chunk_size):
    import pyaudio

    FORMAT = pyaudio.paInt16
    CHANNELS = 1
    RATE = 16000
    # FRAMES = int(RATE / 1000 * chunk_size)
    FRAMES = 4096

    p = pyaudio.PyAudio()

    stream = p.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=RATE,
        input=True,
        frames_per_buffer=FRAMES,
    )
    while True:
        yield stream.read(chunk_size)


def _on_ws_message(ws, message):
    result = parse_response(message)
    logger.info("res: %s", result)


if __name__ == "__main__":
    import ssl

    print("测试流式")
    vol_client = StreamLmClient(
        app_id="9990660497",
        token="MjIYJv3ynYokL_4VIyr-RIpNL0lqBdkY",
        format="pcm",
        seg_duration=200,
    )
    vol_ws = vol_client.conn_offline(on_message=_on_ws_message)
    # volce.send_meta_info(ws=ws)
    t = threading.Thread(
        target=vol_ws.run_forever,
        kwargs=dict(
            ping_interval=20,
            skip_utf8_validation=True,
            sslopt=dict(cert_reqs=ssl.CERT_NONE),
        ),
    )
    t.start()
    time.sleep(1)
    for chunk in _record_microphone(vol_client.chunk_size()):
        start = time.time()
        vol_client.send_audio_data(ws=vol_ws, chunk_data=chunk)
        time.sleep(vol_client.chunk_time(last_time=start))
