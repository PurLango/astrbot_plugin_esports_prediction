"""Test-only AstrBot compatibility stubs for running this repository standalone."""

from __future__ import annotations

import enum
import sys
import types


try:
    import astrbot  # type: ignore  # noqa: F401
except ModuleNotFoundError:
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    event = types.ModuleType("astrbot.api.event")
    components = types.ModuleType("astrbot.api.message_components")
    star = types.ModuleType("astrbot.api.star")
    web = types.ModuleType("astrbot.api.web")

    class Logger:
        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

    class DummyFilter:
        class EventMessageType:
            PRIVATE_MESSAGE = "private"
            GROUP_MESSAGE = "group"

        @staticmethod
        def command(*args, **kwargs):
            return lambda func: func

        @staticmethod
        def event_message_type(*args, **kwargs):
            return lambda func: func

    class AstrMessageEvent:
        pass

    class MessageChain:
        def __init__(self, chain=None):
            self.chain = chain or []

    class Component:
        def __init__(self, *args, **kwargs):
            self.text = kwargs.get("text", args[0] if args else "")

    class Context:
        pass

    class Star:
        def __init__(self, context=None):
            self.context = context

    class StarTools:
        @staticmethod
        def get_data_dir(_name):
            return "."

    def register(*args, **kwargs):
        return lambda cls: cls

    class MessageType(enum.Enum):
        FRIEND_MESSAGE = "FriendMessage"
        GROUP_MESSAGE = "GroupMessage"

    class MessageSession:
        def __init__(self, platform_name="", message_type=None, session_id=""):
            self.platform_name = platform_name
            self.platform_id = platform_name
            self.message_type = message_type
            self.session_id = session_id

        def __str__(self):
            value = getattr(self.message_type, "value", self.message_type)
            return f"{self.platform_name}:{value}:{self.session_id}"

        @classmethod
        def from_str(cls, value):
            platform, message_type, session_id = str(value).split(":", 2)
            selected = next(
                (item for item in MessageType if item.value == message_type),
                MessageType.FRIEND_MESSAGE,
            )
            return cls(platform, selected, session_id)

    class AiocqhttpMessageEvent(AstrMessageEvent):
        pass

    class Request:
        async def json(self, default=None):
            return default

    api.logger = Logger()
    event.AstrMessageEvent = AstrMessageEvent
    event.MessageChain = MessageChain
    event.filter = DummyFilter()
    components.At = type("At", (Component,), {})
    components.Plain = type("Plain", (Component,), {})
    components.Reply = type("Reply", (Component,), {})
    star.Context = Context
    star.Star = Star
    star.StarTools = StarTools
    star.register = register
    web.request = Request()

    module_map = {
        "astrbot": astrbot,
        "astrbot.api": api,
        "astrbot.api.event": event,
        "astrbot.api.message_components": components,
        "astrbot.api.star": star,
        "astrbot.api.web": web,
    }
    for name in (
        "astrbot.core",
        "astrbot.core.platform",
        "astrbot.core.platform.sources",
        "astrbot.core.platform.sources.aiocqhttp",
    ):
        module_map[name] = types.ModuleType(name)
    message_session = types.ModuleType("astrbot.core.platform.message_session")
    message_session.MessageSession = MessageSession
    message_type = types.ModuleType("astrbot.core.platform.message_type")
    message_type.MessageType = MessageType
    aiocqhttp_event = types.ModuleType(
        "astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event"
    )
    aiocqhttp_event.AiocqhttpMessageEvent = AiocqhttpMessageEvent
    module_map[message_session.__name__] = message_session
    module_map[message_type.__name__] = message_type
    module_map[aiocqhttp_event.__name__] = aiocqhttp_event
    sys.modules.update(module_map)
