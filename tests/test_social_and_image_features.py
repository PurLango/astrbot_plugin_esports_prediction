# -*- coding: utf-8 -*-
import asyncio
import base64
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from astrbot.api.message_components import At, Plain
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)

from image_generation_feature import ImageGenerationError
from main import PointSystemPlugin, REGISTERED_COMMAND_NAMES


class FakeEvent:
    def __init__(
        self,
        message_str,
        *,
        user_id="sender",
        group_id="100",
        message=None,
        self_id="bot",
    ):
        self.message_str = message_str
        self._user_id = user_id
        self._group_id = group_id
        self._self_id = self_id
        self.message_obj = SimpleNamespace(message=message or [], raw_message=None)

    def get_sender_id(self):
        return self._user_id

    def get_group_id(self):
        return self._group_id

    def get_self_id(self):
        return self._self_id

    def get_sender_name(self):
        return "发送者"

    def plain_result(self, text):
        return text

    def chain_result(self, chain):
        return chain


class FakeTitleEvent(FakeEvent, AiocqhttpMessageEvent):
    pass


def build_plugin(*, points=100, config=None, save_result=True, user_id="sender"):
    plugin = object.__new__(PointSystemPlugin)
    plugin.config = {"points_name": "积分", **(config or {})}
    plugin.data = plugin._new_store()
    plugin.data["users"][user_id] = plugin._normalize_user_record(
        {"points": points}
    )
    plugin._data_lock = asyncio.Lock()
    plugin._active_image_generation_users = set()

    async def save_data():
        return save_result

    plugin._save_data_locked = save_data
    plugin._refresh_negative_titles_for_user = AsyncMock()
    return plugin


class SocialPointFeatureTests(unittest.IsolatedAsyncioTestCase):
    async def test_title_exchange_can_target_mentioned_member(self):
        plugin = build_plugin(
            points=100,
            user_id="10001",
            config={"exchange_settings": {"title_cost": 30}},
        )
        target = At()
        target.qq = "20002"
        event = FakeTitleEvent(
            "/兑换头衔 @目标 荣耀王者",
            user_id="10001",
            group_id="30003",
            message=[Plain("/兑换头衔 "), target, Plain(" 荣耀王者")],
        )
        event.bot = SimpleNamespace(set_group_special_title=AsyncMock())

        reply = await anext(plugin.exchange_title(event))

        event.bot.set_group_special_title.assert_awaited_once_with(
            group_id=30003,
            user_id=20002,
            special_title="荣耀王者",
            duration=-1,
        )
        self.assertEqual(plugin.data["users"]["10001"]["points"], 70)
        self.assertIn("指定用户的群头衔", reply)

    async def test_title_exchange_without_mention_still_targets_sender(self):
        plugin = build_plugin(
            points=100,
            user_id="10001",
            config={"exchange_settings": {"title_cost": 30}},
        )
        event = FakeTitleEvent(
            "/兑换头衔 荣耀王者",
            user_id="10001",
            group_id="30003",
        )
        event.bot = SimpleNamespace(set_group_special_title=AsyncMock())

        reply = await anext(plugin.exchange_title(event))

        event.bot.set_group_special_title.assert_awaited_once_with(
            group_id=30003,
            user_id=10001,
            special_title="荣耀王者",
            duration=-1,
        )
        self.assertIn("您的群头衔", reply)

    async def test_transfer_moves_points_atomically_and_records_both_sides(self):
        plugin = build_plugin(points=100)
        target = At()
        target.qq = "target"
        event = FakeEvent("/转让 @目标 30", message=[target])

        reply = await anext(plugin.transfer_points(event))

        self.assertIn("【积分转让成功】", reply)
        self.assertEqual(plugin.data["users"]["sender"]["points"], 70)
        self.assertEqual(plugin.data["users"]["target"]["points"], 30)
        transactions = plugin.data["point_transactions"]
        self.assertEqual([item["delta"] for item in transactions], [-30, 30])
        self.assertIn("转让给 target", transactions[0]["source"])
        self.assertIn("收到 sender 转让", transactions[1]["source"])

    async def test_transfer_rejects_self_and_insufficient_balance(self):
        plugin = build_plugin(points=20)
        self_target = At()
        self_target.qq = "sender"

        reply = await anext(
            plugin.transfer_points(
                FakeEvent("/转让 @自己 10", message=[self_target])
            )
        )
        self.assertIn("不能", reply)

        other = At()
        other.qq = "target"
        reply = await anext(
            plugin.transfer_points(
                FakeEvent("/转让 @目标 30", message=[other])
            )
        )
        self.assertIn("积分不足", reply)
        self.assertEqual(plugin.data["users"]["sender"]["points"], 20)

    async def test_any_member_can_fund_a_red_packet_with_own_points(self):
        plugin = build_plugin(
            points=50,
            config={
                "red_packet_settings": {
                    "enabled": True,
                    "max_total_points": 100,
                    "max_count": 10,
                    "expire_minutes": 60,
                }
            },
        )

        reply = await anext(
            plugin.create_red_packet(FakeEvent("/发红包 固定 10 2"))
        )

        self.assertIn("【积分红包已发出】", reply)
        self.assertIn("余额：30 积分", reply)
        self.assertEqual(plugin.data["users"]["sender"]["points"], 30)
        self.assertEqual(plugin.data["red_packets"][0]["total_points"], 20)
        self.assertEqual(
            plugin.data["point_transactions"][-1]["source"], "发出积分红包"
        )

    async def test_red_packet_is_not_created_when_sender_cannot_afford_it(self):
        plugin = build_plugin(
            points=10,
            config={"red_packet_settings": {"enabled": True}},
        )

        reply = await anext(
            plugin.create_red_packet(FakeEvent("/发红包 固定 10 2"))
        )

        self.assertIn("积分不足", reply)
        self.assertEqual(plugin.data["red_packets"], [])
        self.assertEqual(plugin.data["users"]["sender"]["points"], 10)

    async def test_red_packet_save_failure_rolls_back_balance_and_packet(self):
        plugin = build_plugin(
            points=50,
            config={"red_packet_settings": {"enabled": True}},
            save_result=False,
        )

        reply = await anext(
            plugin.create_red_packet(FakeEvent("/发红包 固定 10 2"))
        )

        self.assertIn("没有扣除积分", reply)
        self.assertEqual(plugin.data["red_packets"], [])
        self.assertEqual(plugin.data["users"]["sender"]["points"], 50)
        self.assertEqual(plugin.data["point_transactions"], [])

    async def test_new_commands_are_registered(self):
        self.assertIn("生图", REGISTERED_COMMAND_NAMES)
        self.assertIn("转让", REGISTERED_COMMAND_NAMES)
        self.assertIn("转让积分", REGISTERED_COMMAND_NAMES)


class ImageGenerationFeatureTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def image_config(**overrides):
        return {
            "enabled": True,
            "api_url": "https://image.example/v1",
            "api_key": "secret",
            "model": "image-model",
            "cost": 40,
            "daily_limit": 2,
            "size": "1024x1024",
            "timeout_seconds": 30,
            "max_prompt_length": 100,
            **overrides,
        }

    async def test_successful_generation_charges_points_and_returns_image(self):
        plugin = build_plugin(
            points=100,
            config={"image_generation_settings": self.image_config()},
        )
        plugin._request_image_generation = AsyncMock(
            return_value={"kind": "url", "value": "https://image.example/a.png"}
        )

        result = await anext(plugin.image_generation_command(FakeEvent("/生图 星空")))

        self.assertEqual(plugin.data["users"]["sender"]["points"], 60)
        self.assertEqual(
            plugin.data["users"]["sender"]["daily_image_generation_times"], 1
        )
        self.assertIn("AI 生图完成", result[0].text)
        self.assertEqual(result[1].url, "https://image.example/a.png")

    async def test_failed_generation_refunds_points_and_daily_quota(self):
        plugin = build_plugin(
            points=100,
            config={"image_generation_settings": self.image_config()},
        )
        plugin._request_image_generation = AsyncMock(
            side_effect=ImageGenerationError("接口拒绝请求")
        )

        reply = await anext(plugin.image_generation_command(FakeEvent("/生图 星空")))

        user = plugin.data["users"]["sender"]
        self.assertIn("已退还 40 积分", reply)
        self.assertEqual(user["points"], 100)
        self.assertEqual(user["daily_image_generation_times"], 0)
        self.assertEqual(user["image_generation_count"], 0)
        self.assertEqual(
            [item["delta"] for item in plugin.data["point_transactions"]],
            [-40, 40],
        )

    async def test_same_user_cannot_start_two_generations_at_once(self):
        plugin = build_plugin(
            points=120,
            config={
                "image_generation_settings": self.image_config(daily_limit=3)
            },
        )
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_generation(_settings, _prompt):
            started.set()
            await release.wait()
            return {"kind": "url", "value": "https://image.example/a.png"}

        plugin._request_image_generation = AsyncMock(side_effect=slow_generation)

        async def collect(message):
            return [
                result
                async for result in plugin.image_generation_command(
                    FakeEvent(message)
                )
            ]

        first_task = asyncio.create_task(collect("/生图 星空"))
        await started.wait()

        duplicate = await collect("/生图 星空")
        self.assertIn("已有一个生图请求正在处理中", duplicate[0])
        self.assertEqual(plugin.data["users"]["sender"]["points"], 80)

        release.set()
        await first_task
        await collect("/生图 海洋")

        self.assertEqual(plugin._request_image_generation.await_count, 2)
        self.assertEqual(plugin.data["users"]["sender"]["points"], 40)

    async def test_image_api_result_supports_url_and_base64(self):
        plugin = build_plugin()
        encoded = base64.b64encode(b"image-bytes").decode("ascii")

        self.assertEqual(
            plugin._resolve_image_generation_api_url("https://example.com/v1"),
            "https://example.com/v1/images/generations",
        )
        self.assertEqual(
            plugin._extract_image_generation_result(
                {"data": [{"url": "https://example.com/result.png"}]}
            )["kind"],
            "url",
        )
        self.assertEqual(
            plugin._extract_image_generation_result(
                {"data": [{"b64_json": encoded}]}
            ),
            {"kind": "base64", "value": encoded},
        )

    async def test_image_api_request_uses_openai_compatible_payload(self):
        plugin = build_plugin(
            config={"image_generation_settings": self.image_config()}
        )
        encoded = base64.b64encode(b"image-bytes").decode("ascii")
        with patch("image_generation_feature.urllib.request.urlopen") as opener:
            opener.return_value.__enter__.return_value.read.return_value = json.dumps(
                {"data": [{"b64_json": encoded}]}
            ).encode("utf-8")

            result = plugin._request_image_generation_sync(
                plugin._get_image_generation_settings(), "一只猫"
            )

        request = opener.call_args.args[0]
        request_body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(
            request.full_url,
            "https://image.example/v1/images/generations",
        )
        self.assertEqual(request.get_header("Authorization"), "Bearer secret")
        self.assertEqual(
            request_body,
            {
                "prompt": "一只猫",
                "n": 1,
                "model": "image-model",
                "size": "1024x1024",
            },
        )
        self.assertEqual(result, {"kind": "base64", "value": encoded})


if __name__ == "__main__":
    unittest.main()
