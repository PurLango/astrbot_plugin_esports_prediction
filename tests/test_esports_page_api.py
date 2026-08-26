# -*- coding: utf-8 -*-
import asyncio
import datetime
import unittest

from esports_page_api import EsportsPredictionPageApi
from main import PointSystemPlugin


FIXED_NOW = datetime.datetime(2026, 8, 26, 8, 0, tzinfo=datetime.timezone.utc)


def build_plugin():
    plugin = object.__new__(PointSystemPlugin)
    plugin.config = {
        "points_name": "积分",
        "esports_prediction_settings": {
            "enabled": True,
            "sync_enabled": True,
            "timezone_offset_hours": 8,
            "close_before_minutes": 30,
        },
    }
    plugin.data = plugin._new_store()
    plugin._data_lock = asyncio.Lock()
    plugin._utcnow = lambda: FIXED_NOW

    async def save_data():
        return True

    plugin._save_data_locked = save_data
    return plugin


class EsportsPageApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.plugin = build_plugin()
        self.api = EsportsPredictionPageApi(self.plugin)

    async def test_overview_marks_matches_that_are_open_for_betting(self):
        match = self.plugin._create_manual_match_locked(
            "lol", "LPL", "Bilibili Gaming", "Top Esports", "2026-08-26 20:00"
        )
        match["teams"][0]["code"] = "BLG"
        match["teams"][1]["code"] = "TES"

        data = await self.api.overview()

        self.assertTrue(data["ok"])
        self.assertNotIn("candidates", data)
        self.assertEqual(data["summary"]["open_match_count"], 1)
        self.assertTrue(data["matches"][0]["betting_open"])
        self.assertEqual(data["matches"][0]["teams"][1]["code"], "TES")

    async def test_overview_keeps_finished_results_for_only_one_day(self):
        recent = self.plugin._create_manual_match_locked(
            "lol", "LCK", "GEN", "T1", "2026-08-25 18:00"
        )
        recent["status"] = "settled"
        recent["end_time"] = "2026-08-24T10:00:00+00:00"
        recent["settled_at"] = "2026-08-25T10:05:00+00:00"
        old = self.plugin._create_manual_match_locked(
            "valorant", "VCT Pacific", "PRX", "T1", "2026-08-24 12:00"
        )
        old["status"] = "settled"
        old["end_time"] = "2026-08-24T04:00:00+00:00"
        old["settled_at"] = "2026-08-24T04:05:00+00:00"

        data = await self.api.overview()

        ids = {item["id"] for item in data["matches"]}
        self.assertIn(recent["id"], ids)
        self.assertNotIn(old["id"], ids)


if __name__ == "__main__":
    unittest.main()
