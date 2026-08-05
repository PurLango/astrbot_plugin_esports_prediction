import asyncio
import unittest
from types import SimpleNamespace

from main import (
    PRIVATE_SEND_FAILED,
    PRIVATE_SEND_SUCCESS,
    PRIVATE_SEND_UNCERTAIN,
    MessageSession,
    MessageType,
    PointSystemPlugin,
)


class FakeContext:
    def __init__(self, send_result=True):
        self.send_result = send_result
        self.sent = []

    async def send_message(self, session, chain):
        self.sent.append((session, chain))
        if isinstance(self.send_result, BaseException):
            raise self.send_result
        return self.send_result


class FakeEvent:
    def __init__(self, group_id="456", private=False):
        self.bot = None
        self.message_str = "/兑换 群福利"
        self._group_id = group_id
        self._private = private
        self.session = MessageSession(
            platform_name="test-platform",
            message_type=(
                MessageType.FRIEND_MESSAGE if private else MessageType.GROUP_MESSAGE
            ),
            session_id="private-123" if private else str(group_id or "unknown"),
        )
        self.platform_meta = SimpleNamespace(id="test-platform")
        self.unified_msg_origin = str(self.session)

    def get_sender_id(self):
        return "123"

    def get_sender_name(self):
        return "测试用户"

    def get_group_id(self):
        return self._group_id

    def is_private_chat(self):
        return self._private

    def plain_result(self, text):
        return text


def build_plugin(send_result=True):
    plugin = object.__new__(PointSystemPlugin)
    plugin.context = FakeContext(send_result)
    plugin.config = {
        "points_name": "积分",
        "exchange_items": [
            {
                "name": "群福利",
                "enabled": True,
                "cost": 20,
                "contents": ["SECRET-001"],
                "private_only": True,
            }
        ],
        "exchange_scope": {"mode": "blacklist", "scope": []},
    }
    plugin.data = {
        "version": 9,
        "users": {"123": {"points": 100}},
        "groups": {},
        "exchange_redemptions": [],
        "private_message_targets": {
            "test-platform|123": "test-platform:FriendMessage:private-123"
        },
        "reset_generation": 0,
    }
    plugin._data_lock = asyncio.Lock()
    plugin._get_command_args = lambda event: "群福利"

    async def save_data():
        return True

    plugin._save_data_locked = save_data
    return plugin


class ExchangeDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_group_exchange_sends_content_privately(self):
        plugin = build_plugin(send_result=True)
        event = FakeEvent()

        replies = [reply async for reply in plugin.exchange_item(event)]

        self.assertEqual(plugin.data["users"]["123"]["points"], 80)
        self.assertEqual(len(plugin.data["exchange_redemptions"]), 1)
        self.assertEqual(len(plugin.context.sent), 1)
        private_session, private_chain = plugin.context.sent[0]
        self.assertEqual(private_session.session_id, "private-123")
        self.assertEqual(private_session.message_type.value, "FriendMessage")
        private_text = "".join(
            getattr(component, "text", "") for component in private_chain.chain
        )
        self.assertIn("SECRET-001", private_text)
        self.assertEqual(len(replies), 1)
        self.assertIn("已通过私聊发送", replies[0])
        self.assertNotIn("SECRET-001", replies[0])

    async def test_private_send_failure_refunds_points_and_stock(self):
        plugin = build_plugin(send_result=False)
        event = FakeEvent()

        replies = [reply async for reply in plugin.exchange_item(event)]

        self.assertEqual(plugin.data["users"]["123"]["points"], 100)
        self.assertEqual(plugin.data["exchange_redemptions"], [])
        self.assertEqual(len(replies), 1)
        self.assertIn("本次未扣除积分", replies[0])
        self.assertIn("未消耗库存", replies[0])
        self.assertNotIn("SECRET-001", replies[0])

    async def test_public_delivery_stays_in_the_current_chat(self):
        plugin = build_plugin(send_result=True)
        plugin.config["exchange_items"][0]["private_only"] = False
        event = FakeEvent()

        replies = [reply async for reply in plugin.exchange_item(event)]

        self.assertEqual(plugin.context.sent, [])
        self.assertEqual(plugin.data["users"]["123"]["points"], 80)
        self.assertEqual(len(plugin.data["exchange_redemptions"]), 1)
        self.assertEqual(len(replies), 1)
        self.assertIn("SECRET-001", replies[0])

    async def test_missing_private_route_refunds_without_sending(self):
        plugin = build_plugin(send_result=True)
        plugin.data["private_message_targets"] = {}
        event = FakeEvent()

        replies = [reply async for reply in plugin.exchange_item(event)]

        self.assertEqual(plugin.context.sent, [])
        self.assertEqual(plugin.data["users"]["123"]["points"], 100)
        self.assertEqual(plugin.data["exchange_redemptions"], [])
        self.assertIn("本次未扣除积分", replies[0])

    async def test_uncertain_send_keeps_reservation_for_manual_review(self):
        plugin = build_plugin(send_result=TimeoutError("request contained a secret"))
        event = FakeEvent()

        replies = [reply async for reply in plugin.exchange_item(event)]

        self.assertEqual(len(plugin.context.sent), 1)
        self.assertEqual(plugin.data["users"]["123"]["points"], 80)
        self.assertEqual(len(plugin.data["exchange_redemptions"]), 1)
        self.assertEqual(
            plugin.data["exchange_redemptions"][0]["delivery_status"], "uncertain"
        )
        self.assertIn("暂时无法确认", replies[0])
        self.assertNotIn("SECRET-001", replies[0])

    async def test_private_item_never_fails_open_when_group_id_is_missing(self):
        plugin = build_plugin(send_result=True)
        event = FakeEvent(group_id=None, private=False)

        replies = [reply async for reply in plugin.exchange_item(event)]

        self.assertEqual(len(plugin.context.sent), 1)
        self.assertIn("已通过私聊发送", replies[0])
        self.assertNotIn("SECRET-001", replies[0])

    async def test_unknown_template_placeholder_does_not_interrupt_delivery(self):
        plugin = build_plugin(send_result=True)
        plugin.config["exchange_items"][0]["private_only"] = False
        plugin.config["exchange_items"][0]["success_template"] = "状态：{0}"
        event = FakeEvent()

        replies = [reply async for reply in plugin.exchange_item(event)]

        self.assertEqual(plugin.data["users"]["123"]["points"], 80)
        self.assertIn("状态：{0}", replies[0])
        self.assertIn("SECRET-001", replies[0])

    async def test_delivery_content_keeps_template_like_text_unchanged(self):
        plugin = build_plugin(send_result=True)
        plugin.config["exchange_items"][0]["private_only"] = False
        plugin.config["exchange_items"][0]["contents"] = ["券面 {cost} {item}"]
        event = FakeEvent()

        replies = [reply async for reply in plugin.exchange_item(event)]

        self.assertIn("券面 {cost} {item}", replies[0])

    def test_private_send_result_classification_is_conservative(self):
        self.assertEqual(
            PointSystemPlugin._private_send_result_status(
                {"status": "ok", "retcode": 0}, "onebot_call_action"
            ),
            PRIVATE_SEND_SUCCESS,
        )
        self.assertEqual(
            PointSystemPlugin._private_send_result_status(
                {"status": "failed", "retcode": 100}, "onebot_call_action"
            ),
            PRIVATE_SEND_FAILED,
        )
        self.assertEqual(
            PointSystemPlugin._private_send_result_status(
                None, "onebot_send_private_msg"
            ),
            PRIVATE_SEND_UNCERTAIN,
        )

    async def test_private_message_records_real_session_target(self):
        plugin = build_plugin(send_result=True)
        plugin.data["private_message_targets"] = {}
        event = FakeEvent(group_id=None, private=True)

        await plugin.on_private_message_remember_target(event)

        self.assertEqual(
            plugin.data["private_message_targets"]["test-platform|123"],
            "test-platform:FriendMessage:private-123",
        )

    async def test_reset_generation_prevents_refund_into_new_account(self):
        plugin = build_plugin(send_result=True)
        plugin.data["users"]["123"]["points"] = 5
        plugin.data["reset_generation"] = 1
        redemption = {
            "redemption_id": "old-redemption",
            "content_hash": "a" * 64,
            "item_name": "群福利",
            "user_id": "123",
            "redeemed_at": "2026-08-05T03:00:00",
            "cost": 20,
            "delivery_status": "pending",
            "reset_generation": 0,
        }
        plugin.data["exchange_redemptions"] = [redemption]

        rolled_back = await plugin._rollback_failed_private_exchange(
            "123", 20, redemption
        )

        self.assertFalse(rolled_back)
        self.assertEqual(plugin.data["users"]["123"]["points"], 5)
        self.assertEqual(len(plugin.data["exchange_redemptions"]), 1)
        self.assertEqual(redemption["delivery_status"], "uncertain")


if __name__ == "__main__":
    unittest.main()
