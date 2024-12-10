# -*- coding: utf-8 -*-
import enum

class AssistantStatus(str, enum.Enum):
    """状态"""
    DEFAULT = "等待"  # 默认(等待唤醒)
    LISTEN = "聆听"  # 聆听
    RECOGNIZE = "识别"  # 识别
    RESPONSE = "回答"  # 回答


class AssistantEvent(str, enum.Enum):
    """事件"""
    DETECTED = "检测到关键字"  # 检测到关键字
    LISTENED = "聆听结束"  # 聆听结束
    RECOGNIZED = "识别结束"  # 识别结束
    RESPONDED = "回答结束"  # 回答结束

    CTRL_WAKEUP = "直接提问"  # 直接唤醒(停止检测)
    CTRL_COMMIT_LISTEN = "直接提交"  # 中断聆听(提交回答)
    CTRL_STOP_RESP = "停止回答"  # 中断回答
    CTRL_ASK_AGAIN = "再次提问"  # 再次提问(中断回答,直接聆听)
    CTRL_QUERY = "提交问题"  # 手动提交问题