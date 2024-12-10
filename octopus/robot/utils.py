# -*- coding: utf-8 -*-

import _thread as thread
import hashlib
import json
import os
import platform
import re
import shutil
import smtplib
import subprocess
import tempfile
import time
import wave
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import yaml
from pydub import AudioSegment
from pytz import timezone

from octopus.robot import constants, config
from octopus.robot import log

logger = log.getLogger(__name__)

do_not_bother = False
is_recordable = True

system = platform.system()


def is_windows():
    return system == "Windows"


def is_linux():
    return system == "Linux"


def sendEmail(
        SUBJECT, BODY, ATTACH_LIST, TO, FROM, SENDER, PASSWORD, SMTP_SERVER, SMTP_PORT
):
    """
    发送邮件

    :param SUBJECT: 邮件标题
    :param BODY: 邮件正文
    :param ATTACH_LIST: 附件
    :param TO: 收件人
    :param FROM: 发件人
    :param SENDER: 发件人信息
    :param PASSWORD: 密码
    :param SMTP_SERVER: smtp 服务器
    :param SMTP_PORT: smtp 端口号
    :returns: True: 发送成功; False: 发送失败
    """
    txt = MIMEText(BODY.encode("utf-8"), "html", "utf-8")
    msg = MIMEMultipart()
    msg.attach(txt)

    for attach in ATTACH_LIST:
        try:
            att = MIMEText(open(attach, "rb").read(), "base64", "utf-8")
            filename = os.path.basename(attach)
            att["Content-Type"] = "application/octet-stream"
            att["Content-Disposition"] = 'attachment; filename="%s"' % filename
            msg.attach(att)
        except Exception:
            logger.error(f"附件 {attach} 发送失败！", stack_info=True)
            continue

    msg["From"] = SENDER
    msg["To"] = TO
    msg["Subject"] = SUBJECT

    try:
        session = smtplib.SMTP(SMTP_SERVER)
        session.connect(SMTP_SERVER, SMTP_PORT)
        session.starttls()
        session.login(FROM, PASSWORD)
        session.sendmail(SENDER, TO, msg.as_string())
        session.close()
        return True
    except Exception as e:
        logger.error(e, stack_info=True)
        return False


def emailUser(SUBJECT="", BODY="", ATTACH_LIST=[]):
    """
    给用户发送邮件

    :param SUBJECT: subject line of the email
    :param BODY: body text of the email
    :returns: True: 发送成功; False: 发送失败
    """
    # add footer
    if BODY:
        BODY = "%s，<br><br>这是您要的内容：<br>%s<br>" % (config["first_name"], BODY)

    recipient = config.get("/email/address", "")
    robot_name = config.get("robot_name_cn", "octopus-robot")
    recipient = robot_name + " <%s>" % recipient
    user = config.get("/email/address", "")
    password = config.get("/email/password", "")
    server = config.get("/email/smtp_server", "")
    port = config.get("/email/smtp_port", "")

    if not recipient or not user or not password or not server or not port:
        return False
    try:
        sendEmail(
            SUBJECT, BODY, ATTACH_LIST, user, user, recipient, password, server, port
        )
        return True
    except Exception as e:
        logger.error(e, stack_info=True)
        return False


def get_file_content(filePath, flag="rb"):
    """
    读取文件内容并返回

    :param filePath: 文件路径
    :returns: 文件内容
    :raises IOError: 读取失败则抛出 IOError
    """
    with open(filePath, flag) as fp:
        return fp.read()


def check_and_delete(fp, wait=0):
    """
    检查并删除文件/文件夹

    :param fp: 文件路径
    """

    def run():
        if wait > 0:
            time.sleep(wait)
        if fp and isinstance(fp, str) and os.path.exists(fp):
            if os.path.isfile(fp):
                os.remove(fp)
            else:
                shutil.rmtree(fp)

    thread.start_new_thread(run, ())


def write_temp_file(data, suffix, mode="w+b"):
    """
    写入临时文件

    :param data: 数据
    :param suffix: 后缀名
    :param mode: 写入模式，默认为 w+b
    :returns: 文件保存后的路径
    """
    with tempfile.NamedTemporaryFile(mode=mode, suffix=suffix, delete=False) as f:
        f.write(data)
        tmpfile = f.name
    return tmpfile


def get_pcm_from_wav(wav_path):
    """
    从 wav 文件中读取 pcm

    :param wav_path: wav 文件路径
    :returns: pcm 数据
    """
    wav = wave.open(wav_path, "rb")
    return wav.readframes(wav.getnframes())


def convert_wav_to_mp3(wav_path):
    """
    将 wav 文件转成 mp3

    :param wav_path: wav 文件路径
    :returns: mp3 文件路径
    """
    if not os.path.exists(wav_path):
        logger.critical(f"文件错误 {wav_path}", stack_info=True)
        return None
    mp3_path = wav_path.replace(".wav", ".mp3")
    AudioSegment.from_wav(wav_path).export(mp3_path, format="mp3")
    return mp3_path


def convert_mp3_to_wav(mp3_path):
    """
    将 mp3 文件转成 wav

    :param mp3_path: mp3 文件路径
    :returns: wav 文件路径
    """
    target = mp3_path.replace(".mp3", ".wav")
    if not os.path.exists(mp3_path):
        logger.critical(f"文件错误 {mp3_path}", stack_info=True)
        return None
    AudioSegment.from_mp3(mp3_path).export(target, format="wav")
    return target


def clean():
    """清理垃圾数据"""
    temp = constants.TEMP_PATH
    temp_files = os.listdir(temp)
    for f in temp_files:
        if os.path.isfile(os.path.join(temp, f)) and re.match(
                r"output[\d]*\.wav", os.path.basename(f)
        ):
            os.remove(os.path.join(temp, f))


def setRecordable(value):
    """设置是否可以开始录制语音"""
    global is_recordable
    is_recordable = value


def isRecordable():
    """是否可以开始录制语音"""
    global is_recordable
    return is_recordable


def is_proper_time():
    """是否合适时间"""
    global do_not_bother
    if do_not_bother == True:
        return False
    if not config.has("do_not_bother"):
        return True
    bother_profile = config.get("do_not_bother")
    if not bother_profile["enable"]:
        return True
    if "since" not in bother_profile or "till" not in bother_profile:
        return True
    since = bother_profile["since"]
    till = bother_profile["till"]
    current = time.localtime(time.time()).tm_hour
    if till > since:
        return current not in range(since, till)
    else:
        return not (current in range(since, 25) or current in range(-1, till))


def get_do_not_bother_on_hotword():
    """打开勿扰模式唤醒词"""
    return config.get("/do_not_bother/on_hotword", "悟空别吵.pmdl")


def get_do_not_bother_off_hotword():
    """关闭勿扰模式唤醒词"""
    return config.get("/do_not_bother/off_hotword", "悟空醒醒.pmdl")


def getTimezone():
    """获取时区"""
    return timezone(config.get("timezone", "HKT"))


def getTimemStap():
    """获取时间戳"""
    return str(time.time()).replace(".", "")


def voice_cache_name(msg, ext):
    md5 = hashlib.md5(msg.encode("utf-8")).hexdigest()
    return os.path.join(constants.TEMP_PATH, md5 + ext)


def get_voice_cache(msg):
    """获取缓存的语音"""
    cache_paths = [
        voice_cache_name(msg=msg, ext=ext) for ext in [".mp3", ".wav"]
    ]
    return next((path for path in cache_paths if os.path.exists(path)), None)


def save_voice_cache(msg, ext, data, mode='w+b'):
    """获取缓存的语音"""
    target = voice_cache_name(msg=msg, ext=ext)
    with open(file=target, mode=mode) as f:
        f.write(data)
    return target


def clear_voice_cache(file, days):
    """清理最近未使用的缓存"""

    def run(*args):
        subprocess.run(
            'find . -name "%s" -atime +%d -exec rm {} \\;' % (file, days),
            cwd=constants.TEMP_PATH,
            shell=True,
        )

    thread.start_new_thread(run, ())


def validyaml(filename):
    """
    校验 YAML 格式是否正确

    :param filename: yaml文件路径
    :returns: True: 正确; False: 不正确
    """
    try:
        with open(filename) as f:
            str = f.read()
            yaml.safe_load(str)
            return True
    except Exception:
        return False


def validjson(s):
    """
    校验某个 JSON 字符串是否正确

    :param s: JOSN字符串
    :returns: True: 正确; False: 不正确
    """
    try:
        json.loads(s)
        return True
    except Exception:
        return False


chinese_char_pattern = re.compile(r'[\u4e00-\u9fff]+')
punc_cn = ['。', '？', '！', '；', '：', '、', '?', ';', '，', ',', "\n"]
punc_en = ['.', '?', '!', ';', ':', '，', ',', "\n"]


# whether contain chinese character
def contains_chinese(text):
    return bool(chinese_char_pattern.search(text))


def getPunctuations(text):
    if contains_chinese(text):
        return punc_cn
    return punc_en


def startPunc(s: str):
    """
    字符串末尾是标点
    """
    return s and s[0] in getPunctuations(s)


def endPunc(s: str):
    """
    字符串末尾是标点
    """
    return s and s[-1] in getPunctuations(s)


def stripEndPunc(s: str):
    """
    移除字符串末尾的标点
    """
    if endPunc(s):
        return s[:-1]
    return s


def stripStartPunc(s: str):
    """
    移除字符串开头的标点
    """
    if startPunc(s):
        return s[1:]
    return s


def split_paragraph(text: str, token_min_n=8, comma_split=True) -> list:
    lang_cn = contains_chinese(text=text)

    def calc_utt_length(_text: str):
        if lang_cn:
            return len(_text)
        else:
            return len(_text.encode("utf8"))

    if lang_cn:
        punc = punc_cn
    else:
        punc = punc_en
    if comma_split:
        punc.extend(['，', ','])
    # 按标点分割
    st = 0
    txt_list = []
    for i, c in enumerate(text):
        if c in punc:
            # 长度不足
            if calc_utt_length(text[st: i]) < token_min_n:
                continue
            if text[st: i]:
                txt_list.append(text[st: i] + c)
            st = i + 1
    if st < len(text):
        txt_list.append(text[st:])

    return txt_list


def wait_for(condition, wait_once: float = 1, limit: int = None, ) -> bool:
    count = 0
    while True:
        if condition():
            return True
        if limit:
            count += 1
            if count > limit:
                return False
        time.sleep(wait_once)


def page_list(data: list, page: int, paginate_by: int) -> list:
    """从数组中取分页数据"""
    if not data:
        return data
    start = min(paginate_by * (page - 1), len(data) - 1)
    end = min(paginate_by * page, len(data) - 1)
    return data[start:end]


def each_line(func, file: str, encoding=None):
    """
    @param func(line, index)
    @param file 文件路径
    @param encoding 文件编码
    """
    idx = 0
    with open(file=file, mode="r", encoding=encoding) as f:
        line = f.readline()
        while line:
            func(line, idx)
            line = f.readline()
            idx += 1
