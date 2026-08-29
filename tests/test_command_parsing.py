# -*- coding: utf-8 -*-
import unittest
from types import SimpleNamespace

from astrbot.api.message_components import At

from main import PointSystemPlugin


class FakeEvent:
    def __init__(self, message_str, message=None, self_id="", raw_message=None):
        self.message_str = message_str
        self.message_obj = SimpleNamespace(
            message=message or [],
            raw_message=raw_message,
        )
        self._self_id = self_id

    def get_self_id(self):
        return self._self_id


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

    def test_manual_points_skips_bot_mention_before_target_user(self):
        bot_mention = At()
        bot_mention.qq = "46070199"
        target_mention = At()
        target_mention.qq = "123456789"
        event = FakeEvent(
            "/给积分 @BinLG 200",
            message=[bot_mention, target_mention],
            self_id="46070199",
        )

        self.assertEqual(
            self.plugin._parse_manual_points_args(event),
            ("123456789", 200),
        )

    def test_manual_points_prefers_qq_official_member_openid(self):
        bot_mention = At()
        bot_mention.qq = "BOT_OPENID"
        raw_message = SimpleNamespace(
            mentions=[
                SimpleNamespace(
                    id="BOT_OPENID",
                    member_openid="",
                    user_openid="",
                    is_you=True,
                ),
                SimpleNamespace(
                    id="46070199",
                    member_openid="TARGET_MEMBER_OPENID",
                    user_openid="",
                    is_you=False,
                ),
            ]
        )
        event = FakeEvent(
            "/给积分 <@46070199> 200",
            message=[bot_mention],
            self_id="BOT_OPENID",
            raw_message=raw_message,
        )

        self.assertEqual(
            self.plugin._parse_manual_points_args(event),
            ("TARGET_MEMBER_OPENID", 200),
        )

    def test_multiple_message_ids_are_removed_only_from_the_end(self):
        event = FakeEvent(
            "/兑换 礼包 [MSG_ID:6010388793] [msg_id: 6010388794]"
        )

        self.assertEqual(self.plugin._get_command_args(event), "礼包")


if __name__ == "__main__":
    unittest.main()
