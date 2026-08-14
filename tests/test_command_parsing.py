# -*- coding: utf-8 -*-
import unittest
from types import SimpleNamespace

from main import PointSystemPlugin


class FakeEvent:
    def __init__(self, message_str):
        self.message_str = message_str
        self.message_obj = SimpleNamespace(message=[])


class CommandParsingTests(unittest.TestCase):
    def setUp(self):
        self.plugin = object.__new__(PointSystemPlugin)

    def test_exchange_name_ignores_trailing_message_id(self):
        event = FakeEvent("/兑换 111 [MSG_ID:6010388791]")

        self.assertEqual(self.plugin._get_command_args(event), "111")

    def test_manual_points_amount_ignores_trailing_message_id(self):
        event = FakeEvent("#给积分 123456 100 [MSG_ID:6010388792]")

        self.assertEqual(
            self.plugin._parse_manual_points_args(event),
            ("123456", 100),
        )

    def test_multiple_message_ids_are_removed_only_from_the_end(self):
        event = FakeEvent(
            "/兑换 礼包 [MSG_ID:6010388793] [msg_id: 6010388794]"
        )

        self.assertEqual(self.plugin._get_command_args(event), "礼包")


if __name__ == "__main__":
    unittest.main()
