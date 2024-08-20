import os
import yaml
import json
import time
import base64
import random
import hashlib
import asyncio
import requests
import markdown
import threading
import subprocess
import tornado.web
import tornado.ioloop
import tornado.options
import tornado.httpserver

from tornado.websocket import WebSocketHandler
from urllib.parse import unquote

from robot.Sender import ExtWebSocketHandler, ACTION_ROBOT_SPEAK, StatusData
from robot.sdk.History import History
from robot import config, utils, logging, Updater, constants
from tools import make_json, solr_tools

logger = logging.getLogger(__name__)

conversation, wukong = None, None
commiting = False

suggestions = [
    "现在几点",
    "你吃饭了吗",
    "上海的天气",
    "写一首关于大海的诗",
    "来玩成语接龙",
    "我有多少邮件",
    "你叫什么名字",
    "讲个笑话",
]


def uri_base(prefix: str) -> str:
    return prefix
    # base = config.get(item="/server/path", default="/sdl-robot")
    # return rf"{base}{prefix}"


def api_base(prefix: str) -> str:
    return prefix
    # base = config.get(item="/server/path", default="/sdl-robot")
    # return rf"{base}/api{prefix}"


class BaseHandler(tornado.web.RequestHandler):
    def isValidated(self):
        if not self.get_secure_cookie("validation"):
            return False
        return str(
            self.get_secure_cookie(name="validation"), encoding="utf-8"
        ) == config.get("/server/validate", "")

    def validate(self, validation):
        if validation and '"' in validation:
            validation = validation.replace('"', "")
        return validation == config.get("/server/validate", "") or validation == str(
            self.get_cookie("validation")
        )


class MainHandler(BaseHandler):
    def get(self):
        global conversation, wukong, suggestions
        if not self.isValidated():
            self.redirect(uri_base("/login"))
            return
        if conversation:
            # info = Updater.fetch()
            info = {}
            suggestion = random.choice(suggestions)
            notices = None
            if "notices" in info:
                notices = info["notices"]
            self.render(
                "index.html",
                update_info=info,
                suggestion=suggestion,
                notices=notices,
                location=self.request.host,
            )
        else:
            self.render("index.html")


class MessageUpdatesHandler(BaseHandler):
    """Long-polling request for new messages.

    Waits until new messages are available before returning anything.
    """

    async def post(self):
        if not self.validate(validation=self.get_argument("validate", default=None)):
            res = {"code": 1, "message": "illegal visit"}
            self.write(json.dumps(res))
        else:
            cursor = self.get_argument("cursor", None)
            history = History()
            messages = history.get_messages_since(cursor)
            while not messages:
                # Save the Future returned here so we can cancel it in
                # on_connection_close.
                self.wait_future = history.cond.wait(timeout=1)
                try:
                    await self.wait_future
                except asyncio.CancelledError:
                    return
                messages = history.get_messages_since(cursor)
            if self.request.connection.stream.closed():
                return
            res = {"code": 0, "message": "ok", "history": json.dumps(messages)}
            self.write(json.dumps(res))
        self.finish()

    def on_connection_close(self):
        self.wait_future.cancel()


"""
负责跟前端通信，把机器人的响应内容传输给前端
"""


class ChatWebSocketHandler(WebSocketHandler, BaseHandler):
    clients = set()

    def open(self):
        self.clients.add(self)

    def on_close(self):
        self.clients.remove(self)

    def send_response(self, msg, uuid, plugin=""):
        response = {
            "action": "new_message",
            "type": 1,
            "text": msg,
            "uuid": uuid,
            "plugin": plugin,
        }
        self.write_message(json.dumps(response))


class ChatHandler(BaseHandler):
    def onResp(self, msg, audio, plugin):
        logger.info(f"response msg: {msg}")
        res = {
            "code": 0,
            "message": "ok",
            "resp": msg,
            "audio": audio,
            "plugin": plugin,
        }
        try:
            self.write(json.dumps(res))
            self.flush()
        except:
            pass

    def onStream(self, message, uuid, action=None):
        logger.info(f"OnStream: {message}")
        # 通过 ChatWebSocketHandler 发送给前端
        for client in ExtWebSocketHandler.clients:
            logger.info(f"ClientOnStream: {client}")
            client.send_response(uuid=uuid, message=message, action=action)

    def post(self):
        global conversation
        if self.validate(self.get_argument("validate", default=None)):
            if self.get_argument("type") == "text":
                query = self.get_argument("query")
                uuid = self.get_argument("uuid")
                if query == "":
                    res = {"code": 1, "message": "query text is empty"}
                    self.write(json.dumps(res))
                else:
                    conversation.doResponse(
                        query,
                        uuid,
                        onSay=lambda msg, audio, plugin: self.onResp(
                            msg, audio, plugin
                        ),
                        onStream=lambda data, resp_uuid: self.onStream(
                            message=data, uuid=resp_uuid, action=ACTION_ROBOT_SPEAK
                        ),
                    )

            elif self.get_argument("type") == "voice":
                voice_data = self.get_argument("voice")
                tmpfile = utils.write_temp_file(base64.b64decode(voice_data), ".wav")
                fname, suffix = os.path.splitext(tmpfile)
                nfile = fname + "-16k" + suffix
                # downsampling
                soxCall = "sox " + tmpfile + " " + nfile + " rate 16k"
                subprocess.call([soxCall], shell=True, close_fds=True)
                utils.check_and_delete(tmpfile)
                conversation.doConverse(
                    nfile,
                    onSay=lambda msg, audio, plugin: self.onResp(msg, audio, plugin),
                    onStream=lambda data, resp_uuid: self.onStream(data, resp_uuid),
                )
            else:
                res = {"code": 1, "message": "illegal type"}
                self.write(json.dumps(res))
        else:
            res = {"code": 1, "message": "illegal visit"}
            self.write(json.dumps(res))
        self.finish()


class GetHistoryHandler(BaseHandler):
    def get(self):
        global conversation
        if not self.validate(self.get_argument("validate", default=None)):
            res = {"code": 1, "message": "illegal visit"}
            self.write(json.dumps(res))
        else:
            res = {
                "code": 0,
                "message": "ok",
                "history": json.dumps(conversation.getHistory().cache),
            }
            self.write(json.dumps(res))
        self.finish()


class GetLogHandler(BaseHandler):
    def get(self):
        if not self.validate(self.get_argument("validate", default=None)):
            res = {"code": 1, "message": "illegal visit"}
            self.write(json.dumps(res))
        else:
            lines = self.get_argument("lines", default=200)
            res = {"code": 0, "message": "ok", "log": logging.readLog(lines)}
            self.write(json.dumps(res))
        self.finish()


class LogPageHandler(BaseHandler):
    def get(self):
        if not self.isValidated():
            self.redirect(uri_base("/login"))
        else:
            self.render("log.html")


class OperateHandler(BaseHandler):
    def post(self):
        global wukong
        if self.validate(self.get_argument("validate", default=None)):
            type = self.get_argument("type")
            if type in ["restart", "0"]:
                res = {"code": 0, "message": "ok"}
                self.write(json.dumps(res))
                self.finish()
                time.sleep(3)
                wukong.restart()
            else:
                res = {"code": 1, "message": f"illegal type {type}"}
                self.write(json.dumps(res))
                self.finish()
        else:
            res = {"code": 1, "message": "illegal visit"}
            self.write(json.dumps(res))
            self.finish()


class ConfigPageHandler(BaseHandler):
    def get(self):
        if not self.isValidated():
            self.redirect(uri_base("/login"))
        else:
            self.render("config.html", sensitivity=config.get("sensitivity"))


class ConfigHandler(BaseHandler):
    def get(self):
        if not self.validate(self.get_argument("validate", default=None)):
            res = {"code": 1, "message": "illegal visit"}
            self.write(json.dumps(res))
        else:
            key = self.get_argument("key", default="")
            res = ""
            if key == "":
                res = {
                    "code": 0,
                    "message": "ok",
                    "config": config.getText(),
                    "sensitivity": config.get("sensitivity", 0.5),
                }
            else:
                res = {"code": 0, "message": "ok", "value": config.get(key)}
            self.write(json.dumps(res))
        self.finish()

    def post(self):
        if self.validate(self.get_argument("validate", default=None)):
            configStr = self.get_argument("config")
            try:
                cfg = unquote(configStr)
                yaml.safe_load(cfg)
                config.dump(cfg)
                res = {"code": 0, "message": "ok"}
                self.write(json.dumps(res))
            except:
                res = {"code": 1, "message": "YAML解析失败，请检查内容"}
                self.write(json.dumps(res))
        else:
            res = {"code": 1, "message": "illegal visit"}
            self.write(json.dumps(res))
        self.finish()


class DonateHandler(BaseHandler):
    def get(self):
        if not self.isValidated():
            self.redirect(uri_base("/login"))
            return
        r = requests.get(
            "https://raw.githubusercontent.com/wzpan/wukong-contrib/master/docs/donate.md"
        )
        content = markdown.markdown(
            r.text,
            extensions=["codehilite", "tables", "fenced_code", "meta", "nl2br", "toc"],
        )
        self.render("donate.html", content=content)


class QAHandler(BaseHandler):
    def get(self):
        if not self.isValidated():
            self.redirect(uri_base("/login"))
        else:
            content = ""
            with open(constants.getQAPath(), "r") as f:
                content = f.read()
            self.render("qa.html", content=content)

    def post(self):
        if self.validate(self.get_argument("validate", default=None)):
            qaStr = self.get_argument("qa")
            qaJson = os.path.join(constants.TEMP_PATH, "qa_json")
            try:
                make_json.convert(qaStr, qaJson)
                solr_tools.clear_documents(
                    config.get("/anyq/host", "0.0.0.0"),
                    "collection1",
                    config.get("/anyq/solr_port", "8900"),
                )
                solr_tools.upload_documents(
                    config.get("/anyq/host", "0.0.0.0"),
                    "collection1",
                    config.get("/anyq/solr_port", "8900"),
                    qaJson,
                    10,
                )
                with open(constants.getQAPath(), "w") as f:
                    f.write(qaStr)
                res = {"code": 0, "message": "ok"}
                self.write(json.dumps(res))
            except Exception as e:
                logger.error(e, stack_info=True)
                res = {"code": 1, "message": "提交失败，请检查内容"}
                self.write(json.dumps(res))
        else:
            res = {"code": 1, "message": "illegal visit"}
            self.write(json.dumps(res))
        self.finish()


class APIHandler(BaseHandler):
    def get(self):
        if not self.isValidated():
            self.redirect(uri_base("/login"))
        else:
            content = ""
            r = requests.get(
                "https://raw.githubusercontent.com/wzpan/wukong-contrib/master/docs/api.md"
            )
            content = markdown.markdown(
                r.text,
                extensions=[
                    "codehilite",
                    "tables",
                    "fenced_code",
                    "meta",
                    "nl2br",
                    "toc",
                ],
            )
            self.render("api.html", content=content)


class UpdateHandler(BaseHandler):
    def post(self):
        global wukong
        if self.validate(self.get_argument("validate", default=None)):
            if wukong.update():
                res = {"code": 0, "message": "ok"}
                self.write(json.dumps(res))
                self.finish()
                time.sleep(3)
                wukong.restart()
            else:
                res = {"code": 1, "message": "更新失败，请手动更新"}
                self.write(json.dumps(res))
        else:
            res = {"code": 1, "message": "illegal visit"}
            self.write(json.dumps(res))
        self.finish()


class LoginHandler(BaseHandler):
    def get(self):
        if self.isValidated():
            self.redirect(uri_base("/"))
        else:
            self.render("login.html", error=None)

    def post(self):
        if self.get_argument("username") == config.get(
            "/server/username"
        ) and hashlib.md5(
            self.get_argument("password").encode("utf-8")
        ).hexdigest() == config.get(
            "/server/validate"
        ):
            logger.info("login success")
            self.set_secure_cookie("validation", config.get("/server/validate"))
            self.redirect(uri_base("/"))
        else:
            self.render("login.html", error="登录失败")


class LogoutHandler(BaseHandler):
    def get(self):
        if self.isValidated():
            self.set_secure_cookie("validation", "")
        self.redirect(uri_base("/login"))


class ExtLoginHandler(BaseHandler):
    def get(self):
        if self.isValidated():
            self.write("已登录")
        else:
            self.set_status(status_code=401, reason="未登录.")

    def post(self):
        if self.get_argument("username") == config.get(
            "/server/username"
        ) and hashlib.md5(
            self.get_argument("password").encode("utf-8")
        ).hexdigest() == config.get(
            "/server/validate"
        ):
            logger.info("login success")
            self.set_secure_cookie("validation", config.get("/server/validate"))
            self.finish()
        else:
            self.set_status(status_code=401, reason="账号或密码错误.")
            self.write("账号或密码错误")
            self.finish()


class ExtLogoutHandler(BaseHandler):
    def get(self):
        if self.isValidated():
            self.set_secure_cookie("validation", "")
        self.write("已登出")


class DHPageHandler(BaseHandler):
    def get(self):
        if not self.isValidated():
            self.redirect(uri_base("/login"))
        else:
            self.render(
                template_name="digital-human.html",
                session_id=conversation.dh and conversation.dh.sessionId,
                play_stream_addr=conversation.dh and conversation.dh.play_stream_addr,
                cmd_status=conversation.dh and conversation.dh.get_cmd_status(),
            )


class DHApiHandler(BaseHandler):
    def get(self, action: str):
        if not self.isValidated():
            self.redirect(uri_base("/login"))
            return
        if "session-list" == action:
            self.get_session_list()
        elif "session-status" == action:
            self.get_session_status()

    def post(self, action: str):
        if "session-create" == action:
            self.create_session()
        elif "session-open" == action:
            self.open_session()
        elif "session-close" == action:
            self.close_session()
        elif "create-cmd" == action:
            self.create_cmd()

    def get_session_list(self):
        func = self.get_func("list_session")
        if func:
            self.write_data(data=func())

    def get_session_status(self):
        func = self.get_func("get_session_status")
        if func:
            self.write_data(data=func())

    def create_session(self):
        func = self.get_func("create_session")
        if func:
            self.write_data(data=func())

    def open_session(self):
        func = self.get_func("start_session")
        if func:
            self.write_data(data=func())

    def close_session(self):
        func = self.get_func("close_all_session")
        if func:
            self.write_data(data=func())

    def create_cmd(self):
        func = self.get_func("create_cmd_channel")
        if func:
            self.write_data(data=func())

    def get_func(self, name: str):
        func = conversation.dh and getattr(conversation.dh, name, None)
        if not func:
            self.write_data(code=-1, message=f"找不到方法: {name}")
        return func

    def write_data(self, data=None, code: int = 0, message: str = "success"):
        self.write(json.dumps(obj={"code": code, "message": message, "data": data}))
        self.finish()


settings = {
    "cookie_secret": config.get(
        "/server/cookie_secret", "__GENERATE_YOUR_OWN_RANDOM_VALUE_HERE__"
    ),
    "template_path": os.path.join(constants.APP_PATH, "server/templates"),
    "static_path": os.path.join(constants.APP_PATH, "server/static"),
    "static_url_prefix": uri_base("/static/"),
    "login_url": uri_base("/login"),
    "debug": False,
}

application = tornado.web.Application(
    [
        (uri_base(r"/"), MainHandler),
        (uri_base(r"/login"), LoginHandler),
        (uri_base(r"/logout"), LogoutHandler),
        (api_base(r"/history"), GetHistoryHandler),
        (api_base(r"/chat"), ChatHandler),
        # (api_base(r"/websocket"), ChatWebSocketHandler),
        (api_base(r"/chat/updates"), MessageUpdatesHandler),
        (api_base(r"/config"), ConfigHandler),
        (api_base(r"/configpage"), ConfigPageHandler),
        (api_base(r"/operate"), OperateHandler),
        (api_base(r"/logpage"), LogPageHandler),
        (api_base(r"/log"), GetLogHandler),
        (api_base(r"/api"), APIHandler),
        (api_base(r"/qa"), QAHandler),
        (api_base(r"/upgrade"), UpdateHandler),
        (api_base(r"/donate"), DonateHandler),
        # 废弃老接口
        (api_base(r"/getlog"), GetLogHandler),
        (api_base(r"/gethistory"), GetHistoryHandler),
        (api_base(r"/getconfig"), ConfigHandler),
        # 自定义
        (api_base(r"/websocket"), ExtWebSocketHandler),
        (api_base(r"/digital-human"), DHPageHandler),
        (api_base(r"/dh/(.*)"), DHApiHandler),
        (api_base(r"/ext/login"), ExtLoginHandler),
        (api_base(r"/ext/logout"), ExtLogoutHandler),
        (api_base(r"/websocket"), ExtWebSocketHandler),
        (api_base(r"/chat"), ChatHandler),
        (
            uri_base(r"/photo/(.+\.(?:png|jpg|jpeg|bmp|gif|JPG|PNG|JPEG|BMP|GIF))"),
            tornado.web.StaticFileHandler,
            {"path": config.get("/camera/dest_path", "server/static")},
        ),
        (
            uri_base(r"/audio/(.+\.(?:mp3|wav|pcm))"),
            tornado.web.StaticFileHandler,
            {"path": constants.TEMP_PATH},
        ),
        (
            uri_base(r"/static/(.*)"),
            tornado.web.StaticFileHandler,
            {"path": "server/static"},
        ),
    ],
    **settings,
)


def start_server(con, wk):
    global conversation, wukong
    conversation = con
    wukong = wk
    if config.get("/server/enable", False):
        port = config.get("/server/port", "5001")
        try:
            asyncio.set_event_loop(asyncio.new_event_loop())
            application.listen(int(port))
            tornado.ioloop.IOLoop.instance().start()
        except Exception as e:
            logger.critical(f"服务器启动失败: {e}", stack_info=True)


def run(conversation, wukong, debug=False):
    settings["debug"] = debug
    t = threading.Thread(target=lambda: start_server(conversation, wukong))
    t.start()
