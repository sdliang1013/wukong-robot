# -*- coding: utf-8 -*-
import os
import shutil

from octopus import APP_PATH

# Octopus main directory

LIB_PATH = os.path.join(APP_PATH, "robot")
PLUGIN_PATH = os.path.join(APP_PATH, "plugins")
RS_PATH = os.path.join(APP_PATH, "resources")
WWW_PATH = os.path.join(APP_PATH, "www")
DEFAULT_CONFIG_NAME = "default.yml"
CUSTOM_CONFIG_NAME = "config.yml"

CONFIG_PATH = os.path.expanduser(os.getenv("OCTOPUS_CONFIG", "~/.octopus"))
CONTRIB_PATH = os.path.join(CONFIG_PATH, "contrib")
CUSTOM_PATH = os.path.join(CONFIG_PATH, "custom")

DATA_PATH = os.getenv("OCTOPUS_DATA_DIR", os.path.join(CONFIG_PATH, "data"))
LOG_PATH = os.getenv("OCTOPUS_LOG_DIR", os.path.join(CONFIG_PATH, "log"))
TEMP_PATH = os.path.join(DATA_PATH, "temp")


def ensure_dir():
    """目录创建"""
    if not os.path.exists(DATA_PATH):
        os.makedirs(name=DATA_PATH, mode=0o755, exist_ok=True)
    if not os.path.exists(LOG_PATH):
        os.makedirs(name=LOG_PATH, mode=0o755, exist_ok=True)
    if not os.path.exists(TEMP_PATH):
        os.makedirs(name=TEMP_PATH, mode=0o755, exist_ok=True)


def getConfigPath():
    """
    获取配置文件的路径

    returns: 配置文件的存储路径
    """
    return os.path.join(CONFIG_PATH, CUSTOM_CONFIG_NAME)


def getQAPath():
    """
    获取QA数据集文件的路径

    returns: QA数据集文件的存储路径
    """
    qa_source = os.path.join(RS_PATH, "qa.csv")
    qa_dst = os.path.join(CONFIG_PATH, "qa.csv")
    if not os.path.exists(qa_dst):
        shutil.copyfile(qa_source, qa_dst)
    return qa_dst


def getConfigData(*fname):
    """
    获取配置目录下的指定文件的路径

    :param *fname: 指定文件名。如果传多个，则自动拼接
    :returns: 配置目录下的某个文件的存储路径
    """
    return os.path.join(CONFIG_PATH, *fname)


def getRS(*fname):
    """
    获取资源目录下指定文件的路径

    :param *fname: 指定文件名。如果传多个，则自动拼接
    :returns: 配置文件的存储路径
    """
    return os.path.join(RS_PATH, *fname)


def getDefaultConfigPath():
    return getRS(DEFAULT_CONFIG_NAME)


def newConfig():
    shutil.copyfile(getDefaultConfigPath(), getConfigPath())


def getHotwordModel(fname):
    if os.path.exists(getRS(fname)):
        return getRS(fname)
    else:
        return getConfigData(fname)


def getData(*fname):
    """
    获取数据目录下指定文件的路径

    :param *fname: 指定文件名。如果传多个，则自动拼接
    :returns: 配置文件的存储路径
    """
    return os.path.join(DATA_PATH, *fname)


ensure_dir()
