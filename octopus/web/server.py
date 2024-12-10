# -*- coding: utf-8 -*-
import asyncio
import os

import tornado.httpserver
import tornado.ioloop
import tornado.options
import tornado.web

from octopus.robot import config, log, constants
from octopus.robot.Sender import ExtWebSocketHandler
from octopus.robot.compt import ThreadManager
from octopus.web.apis import (
    DigitalHumanHandler,
    ExtLoginHandler,
    ExtLogoutHandler,
    DetectorHandler,
    HandControlHandler,
    TuningControlHandler,
    ChatApiHandler,
    NavigationHandler,
)
from octopus.web.core import api_base, Route, add_routes
from octopus.web.pages import (
    MainHandler,
    LoginHandler,
    LogoutHandler,
    GetHistoryHandler,
    ChatHandler,
    MessageUpdatesHandler,
    ConfigHandler,
    ConfigPageHandler,
    OperateHandler,
    LogPageHandler,
    GetLogHandler,
    APIHandler,
    QAHandler,
    UpdateHandler,
    DonateHandler,
    DHPageHandler,
)

logger = log.getLogger(__name__)

# api接口
route_apis = [
    Route(path=api_base(r"/chat/(.*)"), handler=ChatApiHandler),
    Route(path=api_base(r"/websocket"), handler=ExtWebSocketHandler),
    Route(path=api_base(r"/dh/(.*)"), handler=DigitalHumanHandler),
    Route(path=api_base(r"/ext/login"), handler=ExtLoginHandler),
    Route(path=api_base(r"/ext/logout"), handler=ExtLogoutHandler),
    Route(path=api_base(r"/detect/(.*)"), handler=DetectorHandler),
    Route(path=api_base(r"/ctl/(.*)"), handler=HandControlHandler),
    Route(path=api_base(r"/tuning/(.*)"), handler=TuningControlHandler),
    Route(path=api_base(r"/navi/(.*)"), handler=NavigationHandler),
]

# 页面
route_pages = [
    Route(path=r"/", handler=MainHandler),
    Route(path=r"/login", handler=LoginHandler),
    Route(path=r"/logout", handler=LogoutHandler),
    Route(path=r"/history", handler=GetHistoryHandler),
    Route(path=r"/chat", handler=ChatHandler),
    # Route(path=r"/websocket", handler=ChatWebSocketHandler),
    Route(path=r"/chat/updates", handler=MessageUpdatesHandler),
    Route(path=r"/config", handler=ConfigHandler),
    Route(path=r"/configpage", handler=ConfigPageHandler),
    Route(path=r"/operate", handler=OperateHandler),
    Route(path=r"/logpage", handler=LogPageHandler),
    Route(path=r"/log", handler=GetLogHandler),
    Route(path=r"/api", handler=APIHandler),
    Route(path=r"/qa", handler=QAHandler),
    Route(path=r"/upgrade", handler=UpdateHandler),
    Route(path=r"/donate", handler=DonateHandler),
    # 废弃老接口
    Route(path=r"/getlog", handler=GetLogHandler),
    Route(path=r"/gethistory", handler=GetHistoryHandler),
    Route(path=r"/getconfig", handler=ConfigHandler),
    # 自定义
    Route(path=r"/digital-human", handler=DHPageHandler),
    Route(path=r"/websocket", handler=ExtWebSocketHandler),
]

# 静态资源
route_statics = [
    Route(
        path=r"/photo/(.+\.(?:png|jpg|jpeg|bmp|gif|JPG|PNG|JPEG|BMP|GIF))",
        handler=tornado.web.StaticFileHandler,
        kwarg={
            "path": config.get(
                "/camera/dest_path", os.path.join(constants.WWW_PATH, "static")
            )
        },
    ),
    Route(
        path=r"/audio/(.+\.(?:mp3|wav|pcm))",
        handler=tornado.web.StaticFileHandler,
        kwarg={"path": constants.TEMP_PATH},
    ),
    Route(
        path=r"/static/(.*)",
        handler=tornado.web.StaticFileHandler,
        kwarg={"path": os.path.join(constants.WWW_PATH, "static")},
    ),
]

settings = {
    "cookie_secret": config.get(
        "/server/cookie_secret", "__GENERATE_YOUR_OWN_RANDOM_VALUE_HERE__"
    ),
    "template_path": os.path.join(constants.WWW_PATH, "templates"),
    "static_path": os.path.join(constants.WWW_PATH, "static"),
    "login_url": "/login",
    "debug": False,
}

application = tornado.web.Application(
    **settings,
)


def start_server(octopus):
    if not config.get("/server/enable", False):
        return
    try:
        port = config.get("/server/port", "5001")
        # 页面路由
        add_routes(app=application, routes=route_pages, kwarg=dict(octopus=octopus))
        # 静态资源路由
        add_routes(app=application, routes=route_statics)
        # API路由
        add_routes(app=application, routes=route_apis, kwarg=dict(octopus=octopus))
        asyncio.set_event_loop(asyncio.new_event_loop())
        application.listen(int(port))
        tornado.ioloop.IOLoop.instance().start()
    except Exception as e:
        logger.critical("服务器启动失败: %s", str(e), stack_info=True)


def run(octopus, debug=False):
    settings["debug"] = debug
    t = ThreadManager.new(target=lambda: start_server(octopus=octopus))
    t.start()
