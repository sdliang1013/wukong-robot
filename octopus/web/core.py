# -*- coding: utf-8 -*-
import json
from typing import List, Any, Tuple

import tornado.web

from octopus.robot import config
from octopus.schemas.core import Paginate, Response


class Route:
    def __init__(self, path: str, handler: Any, kwarg: dict = None):
        self.path = path
        self.handler = handler
        self.kwarg = kwarg


class BaseHandler(tornado.web.RequestHandler):

    def initialize(self, octopus=None, **kwargs):
        self.octopus = octopus

    def is_validated(self):
        valid_arg = self.get_argument("validate", default=None)
        valid_cookie = self.get_secure_cookie("validation")
        if not valid_arg and not valid_cookie:
            return False
        if valid_cookie:
            valid_cookie = str(valid_cookie, encoding="utf-8").replace('"', "")
        if valid_arg:
            valid_arg = valid_arg.replace('"', "")
        validate = config.get("/server/validate", "")
        return valid_arg == validate or valid_cookie == validate

    def validate(self, validation):
        if validation and '"' in validation:
            validation = validation.replace('"', "")
        return validation == config.get("/server/validate", "") or validation == str(
            self.get_cookie("validation")
        )

    def response(self, response: Response):
        self.write(json.dumps(response.serialize()))
        self.finish()

    def valid_to_login(self):
        if self.is_validated():
            return True
        self.redirect("/login")
        return False

    def valid_to_json(self):
        if self.is_validated():
            return True
        self.set_status(status_code=401)
        self.response(Response.error(code=1, message="illegal validation."))
        return False

    def get_body_json(self) -> dict:
        return json.loads(self.request.body)

    def get_body(self, cls: type):
        return cls(**self.get_body_json())


class ApiBaseHandler(BaseHandler):
    """API接口"""

    def get(self, action: str):
        """
        对应请求: GET
        对应子类方法: get_{action.replace('-', '_')}
        """
        self._do_service(method="get", action=action)

    def post(self, action: str):
        """
        对应请求: POST
        对应子类方法: post_{action.replace('-', '_')}
        """
        self._do_service(method="post", action=action)

    def put(self, action: str):
        """
        对应请求: PUT
        对应子类方法: put_{action.replace('-', '_')}
        """
        self._do_service(method="put", action=action)

    def delete(self, action: str):
        """
        对应请求: DELETE
        对应子类方法: delete_{action.replace('-', '_')}
        """
        self._do_service(method="delete", action=action)

    def get_paginate(self) -> Paginate:
        page = int(self.get_query_argument("page", "1"))
        paginate_by = int(self.get_query_argument("paginate_by", "20"))
        return Paginate(page=page, paginate_by=paginate_by)

    @classmethod
    def _reset_action(cls, method: str, action: str) -> Tuple[str, list]:
        args = []
        path = action
        # 参数处理
        if "/" in path:
            args = action.split("/")
            path = args.pop(-1)
        # 路径处理
        if "-" in path:
            path = path.replace("-", "_")
        return f"{method}_{path}", args

    def _resp_result(self, res: Any):
        if isinstance(res, Response):
            self.response(res)
        elif isinstance(res, BaseException):
            self.send_error(status_code=500, exc_info=res)
        else:
            self.response(Response.ok(res))

    def _do_service(self, method: str, action: str):
        """
        对应子类方法: {method}_{action}
        """
        if not self.valid_to_json():
            return
        func_name, args = self._reset_action(method=method, action=action)
        func = getattr(self, func_name, None)
        if not func:
            self.send_error(status_code=404)
            return
        res = func(*args)
        if res is None:
            return
        self._resp_result(res)


def api_base(prefix: str) -> str:
    base = config.get(item="/server/path", default="/chat-robot")
    return rf"{base}/api{prefix}"


def add_routes(app: tornado.web.Application, routes: List[Route], kwarg: dict = None):
    rules = []
    for route in routes:
        _kwarg = route.kwarg or dict()
        _kwarg.update(kwarg or dict())
        rules.append((route.path, route.handler, _kwarg))
    app.default_router.add_rules(rules)
