# -*- coding: utf-8 -*-
import math
from typing import Any

from octopus.robot import utils


def serialize(obj: Any) -> Any:
    """序列化py对象"""
    if obj is None:
        return None
    if isinstance(obj, list):
        return list(map(serialize, obj))
    if isinstance(obj, set):
        return set(map(serialize, obj))
    if hasattr(obj, "serialize"):
        return obj.serialize()
    return obj


class Paginate:
    """分页信息"""

    def __init__(self, page: int, paginate_by: int, total: int = None):
        self.page = page
        self.paginate_by = paginate_by
        self.total = total or 0

    @property
    def total_page(self):
        return math.ceil(self.total / self.paginate_by)

    def set_total(self, total: int):
        self.total = total

    def serialize(self) -> dict:
        return {
            "page": self.page,
            "paginate_by": self.paginate_by,
            "total": self.total,
            "total_page": self.total_page,
        }


class Page:
    """分页数据"""

    def __init__(self, paginate: Paginate, content: list):
        self.paginate = paginate
        self.content = content

    def serialize(self) -> dict:
        return {
            "paginate": self.paginate.serialize(),
            "content": list(map(serialize, self.content)),
        }


class Response:

    def __init__(self, code: int, message: str, data: Any):
        self.code = code
        self.message = message
        self.data = data

    def is_success(self) -> bool:
        return self.code == 0

    def serialize(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "data": serialize(self.data),
        }

    @classmethod
    def ok(cls, data=None):
        return Response(code=0, message="success", data=data)

    @classmethod
    def error(cls, code: int = -1, message="error", data=None):
        return Response(code=code, message=message, data=data)


class TxtEntity:
    def __init__(self, split=None, cols=None, file=None, encoding=None):
        """
        @param split 分隔符
        @param cols 列头
        @param file 文件
        @param encoding 编码
        """
        self.split = split or ","
        self.cols = cols
        self.data = []
        # 加载数据
        if file:
            self.load(file=file, encoding=encoding)

    def load(self, file: str, encoding=None):
        utils.each_line(func=self._load_data, file=file, encoding=encoding or "utf8")

    def serialize(self) -> list:
        if not self.cols:
            return self.data
        return list(map(self._to_dict, self.data))

    def _load_data(self, line: str, idx: int):
        if idx == 0 and line[0] == "#":  # 加载列头(idx==0 and 以#字符开头)
            self.cols = line[1:].split(self.split)
        else:  # 加载数据
            self.data.append(line.strip())

    def _to_dict(self, row: str) -> dict:
        row_ary = row.split(self.split)
        row_len = len(row_ary)
        row_data = {}
        for i, col in enumerate(self.cols):
            row_data.update({col: row_len > i and row_ary[i] or None})
        return row_data
