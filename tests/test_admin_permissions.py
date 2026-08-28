# -*- coding: utf-8 -*-
import unittest
from types import SimpleNamespace

from main import PointSystemPlugin


class FakeEvent:
    def __init__(self, sender_id, raw_user_id=None):
        self._sender_id = sender_id
        self.message_obj = SimpleNamespace(
            sender=SimpleNamespace(user_id=raw_user_id)
        )

    def get_sender_id(self):
        return self._sender_id


def build_plugin(admin_ids):
    plugin = object.__new__(PointSystemPlugin)
    plugin.config = {"admin_settings": {"points_admin_ids": admin_ids}}
    return plugin


class AdminPermissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_openid_admin_is_not_discarded_as_non_numeric(self):
        openid = "B5138C3BBF9FC0FCEEB211E54B91FF9C"
        plugin = build_plugin([openid])

        error = await plugin._ensure_points_admin(FakeEvent(openid))

        self.assertIsNone(error)

    async def test_admin_sender_id_is_normalized_before_comparison(self):
        plugin = build_plugin(["123456789"])

        error = await plugin._ensure_points_admin(FakeEvent(" 123456789 "))

        self.assertIsNone(error)

    async def test_admin_can_match_raw_message_user_id(self):
        plugin = build_plugin(["123456789"])
        event = FakeEvent("aiocqhttp:FriendMessage:123456789", 123456789)

        error = await plugin._ensure_points_admin(event)

        self.assertIsNone(error)

    async def test_permission_error_shows_detected_sender_id(self):
        plugin = build_plugin(["987654321"])

        error = await plugin._ensure_points_admin(FakeEvent("123456789"))

        self.assertIn("123456789", error)


if __name__ == "__main__":
    unittest.main()
