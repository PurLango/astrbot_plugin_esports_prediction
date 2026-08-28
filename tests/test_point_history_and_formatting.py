# -*- coding: utf-8 -*-
import asyncio
import unittest
from types import SimpleNamespace

from main import PointSystemPlugin, REGISTERED_COMMAND_NAMES


class FakeEvent:
    def __init__(self, user_id="123"):
        self._user_id = user_id
        self.message_obj = SimpleNamespace(message=[])

    def get_sender_id(self):
        return self._user_id

    def plain_result(self, text):
        return text


def build_plugin():
    plugin = object.__new__(PointSystemPlugin)
    plugin.config = {"points_name": "积分"}
    plugin.data = plugin._new_store()
    plugin.data["users"]["123"] = plugin._normalize_user_record({"points": 70})
    plugin._data_lock = asyncio.Lock()

    async def save_data():
        return True

    plugin._save_data_locked = save_data
    return plugin


class PointHistoryAndFormattingTests(unittest.IsolatedAsyncioTestCase):
    async def test_plain_result_preserves_structured_line_breaks(self):
        plugin = build_plugin()

        reply = plugin._plain_result(FakeEvent(), "【积分榜】\n1. A\n2. B")

        self.assertEqual(reply, "【积分榜】\n1. A\n2. B")

    async def test_point_history_shows_latest_five_changes_and_sources(self):
        plugin = build_plugin()
        for index in range(1, 7):
            plugin._record_point_transaction_locked(
                "123", index, f"来源{index}", balance=64 + index
            )

        reply = await anext(plugin.point_history(FakeEvent()))

        self.assertIn("【最近积分记录】", reply)
        self.assertIn("+6 积分｜来源6", reply)
        self.assertIn("+2 积分｜来源2", reply)
        self.assertNotIn("来源1", reply)
        self.assertEqual(reply.count("余额 "), 5)

    async def test_point_history_commands_are_registered(self):
        self.assertIn("积分记录", REGISTERED_COMMAND_NAMES)
        self.assertIn("积分明细", REGISTERED_COMMAND_NAMES)


if __name__ == "__main__":
    unittest.main()
