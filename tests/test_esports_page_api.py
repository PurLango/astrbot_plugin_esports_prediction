# -*- coding: utf-8 -*-
import asyncio
import unittest

import esports_page_api
from esports_page_api import EsportsPredictionPageApi
from main import PointSystemPlugin


class FakeRequest:
    def __init__(self, payload=None):
        self._payload = payload if payload is not None else {}

    async def json(self, default=None):
        return self._payload


def build_plugin():
    plugin = object.__new__(PointSystemPlugin)
    plugin.config = {
        "points_name": "积分",
        "esports_prediction_settings": {
            "enabled": True,
            "sync_enabled": True,
            "timezone_offset_hours": 8,
        },
    }
    plugin.data = plugin._new_store()
    plugin._data_lock = asyncio.Lock()

    async def save_data():
        return True

    plugin._save_data_locked = save_data
    return plugin


def add_candidate(plugin, match_id="pandascore:lol:9001", dismissed=False):
    store = plugin._get_esports_store()
    store["candidates"][match_id] = {
        "id": match_id,
        "display_id": "",
        "source": "pandascore",
        "source_id": match_id.rsplit(":", 1)[-1],
        "game": "lol",
        "competition": "LJL 2026 Summer",
        "stage": "",
        "name": "Alpha vs Beta",
        "start_time": "2026-08-28T12:00:00Z",
        "status": "not_started",
        "teams": [
            {"id": f"{match_id}:team1", "name": "Alpha", "code": "ALP", "image_url": "", "score": 0},
            {"id": f"{match_id}:team2", "name": "Beta", "code": "BTA", "image_url": "", "score": 0},
        ],
        "winner_id": "",
        "odds": {},
        "probabilities": {},
        "odds_locked": False,
        "visible": True,
        "settled_at": "",
        "created_at": "",
        "updated_at": "",
        "dismissed": dismissed,
        "first_seen_at": "",
    }
    return store["candidates"][match_id]


class EsportsPageApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.plugin = build_plugin()
        self.api = EsportsPredictionPageApi(self.plugin)
        self._original_request = esports_page_api.request

    def tearDown(self):
        esports_page_api.request = self._original_request

    def _set_payload(self, payload):
        esports_page_api.request = FakeRequest(payload)

    async def test_overview_lists_candidates_with_summary(self):
        add_candidate(self.plugin, "pandascore:lol:9001")
        add_candidate(self.plugin, "pandascore:lol:9002", dismissed=True)

        data = await self.api.overview()

        self.assertTrue(data["ok"])
        self.assertEqual(data["summary"]["candidate_count"], 1)
        self.assertEqual(data["summary"]["dismissed_count"], 1)
        self.assertEqual(len(data["candidates"]), 2)
        self.assertEqual(data["candidates"][0]["teams"][0]["name"], "Alpha")
        self.assertFalse(data["candidates"][0]["dismissed"])
        self.assertTrue(data["candidates"][1]["dismissed"])

    async def test_candidate_include_promotes_match(self):
        add_candidate(self.plugin)

        self._set_payload({"action": "include", "match_ids": ["pandascore:lol:9001"]})
        result = await self.api.candidate_action()

        self.assertTrue(result["ok"])
        store = self.plugin._get_esports_store()
        self.assertNotIn("pandascore:lol:9001", store["candidates"])
        match = store["matches"]["pandascore:lol:9001"]
        self.assertTrue(match["display_id"])
        self.assertEqual(len(match["odds"]), 2)
        self.assertIn("已加入竞猜 1 场", result["message"])

    async def test_candidate_dismiss_and_restore(self):
        add_candidate(self.plugin)
        candidate_id = "pandascore:lol:9001"

        self._set_payload({"action": "dismiss", "match_ids": [candidate_id]})
        result = await self.api.candidate_action()
        self.assertTrue(result["ok"])
        self.assertTrue(
            self.plugin._get_esports_store()["candidates"][candidate_id]["dismissed"]
        )

        self._set_payload({"action": "restore", "match_ids": [candidate_id]})
        result = await self.api.candidate_action()
        self.assertTrue(result["ok"])
        self.assertFalse(
            self.plugin._get_esports_store()["candidates"][candidate_id]["dismissed"]
        )

    async def test_candidate_action_rejects_unknown_match(self):
        self._set_payload({"action": "include", "match_ids": ["missing"]})
        result = await self.api.candidate_action()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "未找到所选比赛。")

    async def test_candidate_action_rejects_empty_selection(self):
        self._set_payload({"action": "include", "match_ids": []})
        result = await self.api.candidate_action()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "请选择候选比赛。")

    async def test_candidate_action_rejects_unknown_action(self):
        self._set_payload({"action": "explode", "match_ids": ["pandascore:lol:9001"]})
        result = await self.api.candidate_action()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "不支持的操作。")


if __name__ == "__main__":
    unittest.main()
