# -*- coding: utf-8 -*-
import asyncio
import datetime
import unittest
from types import SimpleNamespace

import esports_feature
from main import PointSystemPlugin
from esports_provider import PandaScoreProvider


class FakeEvent:
    def __init__(self, message, group_id="100", user_id="123"):
        self.message_str = message
        self.message_obj = SimpleNamespace(message=[])
        self._group_id = group_id
        self._user_id = user_id

    def get_sender_id(self):
        return self._user_id

    def get_sender_name(self):
        return "测试用户"

    def get_group_id(self):
        return self._group_id

    def plain_result(self, text):
        return text


def build_plugin():
    plugin = object.__new__(PointSystemPlugin)
    plugin.config = {
        "points_name": "积分",
        "esports_prediction_settings": {
            "enabled": True,
            "min_bet": 10,
            "max_bet": 10000,
            "switch_deadline_minutes": 60,
            "close_before_minutes": 30,
            "timezone_offset_hours": 8,
        },
    }
    plugin.data = plugin._new_store()
    plugin.data["users"]["123"] = plugin._normalize_user_record({"points": 1000})
    plugin._data_lock = asyncio.Lock()

    async def save_data():
        return True

    plugin._save_data_locked = save_data
    return plugin


def add_future_match(plugin, hours=3):
    local_tz = datetime.timezone(datetime.timedelta(hours=8))
    local_start = (
        datetime.datetime.now(local_tz) + datetime.timedelta(hours=hours)
    ).strftime("%Y-%m-%d %H:%M")
    return plugin._create_manual_match_locked(
        "lol", "LPL 测试赛", "BLG", "TES", local_start
    )


class EsportsPredictionTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_user_bet_is_merged_across_groups(self):
        plugin = build_plugin()
        match = add_future_match(plugin)

        first = FakeEvent(f"/竞猜 {match['display_id']} 1 100", group_id="100")
        second = FakeEvent(f"/竞猜 {match['display_id']} 1 50", group_id="200")
        first_replies = [item async for item in plugin.esports_bet(first)]
        second_replies = [item async for item in plugin.esports_bet(second)]

        bets = plugin.data["esports"]["bets"]
        self.assertEqual(len(bets), 1)
        bet = next(iter(bets.values()))
        self.assertEqual(bet["amount"], 150)
        self.assertEqual(plugin.data["users"]["123"]["points"], 850)
        self.assertIn("下注成功", first_replies[0])
        self.assertIn("累计 150", second_replies[0])

    async def test_switch_then_withdraw_refunds_full_stake(self):
        plugin = build_plugin()
        match = add_future_match(plugin)
        await anext(plugin.esports_bet(FakeEvent(f"/竞猜 {match['display_id']} 1 200")))

        switched = await anext(
            plugin.esports_switch_bet(FakeEvent(f"/改选 {match['display_id']} 2"))
        )
        bet = next(iter(plugin.data["esports"]["bets"].values()))
        self.assertEqual(bet["team_id"], match["teams"][1]["id"])
        self.assertIn("已改选", switched)

        cancelled = await anext(
            plugin.esports_cancel_bet(FakeEvent(f"/撤单 {match['display_id']}"))
        )
        self.assertEqual(bet["status"], "withdrawn")
        self.assertEqual(plugin.data["users"]["123"]["points"], 1000)
        self.assertIn("撤单成功", cancelled)

    async def test_settlement_pays_locked_multiplier(self):
        plugin = build_plugin()
        match = add_future_match(plugin)
        await anext(plugin.esports_bet(FakeEvent(f"/竞猜 {match['display_id']} 1 100")))
        bet = next(iter(plugin.data["esports"]["bets"].values()))
        expected = int(bet["amount"] * bet["odds"])

        match["winner_id"] = match["teams"][0]["id"]
        match["status"] = "finished"
        settled, winners, paid = plugin._settle_match_locked(match)

        self.assertEqual((settled, winners, paid), (1, 1, expected))
        self.assertEqual(bet["status"], "won")
        self.assertEqual(plugin.data["users"]["123"]["points"], 900 + expected)

    async def test_abnormal_match_refunds_all_pending_bets(self):
        plugin = build_plugin()
        match = add_future_match(plugin)
        await anext(plugin.esports_bet(FakeEvent(f"/竞猜 {match['display_id']} 2 100")))

        match["status"] = "postponed"
        settled, refunded = plugin._settle_ready_matches_locked()
        bet = next(iter(plugin.data["esports"]["bets"].values()))

        self.assertEqual((settled, refunded), (0, 1))
        self.assertEqual(bet["status"], "refunded")
        self.assertEqual(plugin.data["users"]["123"]["points"], 1000)

    async def test_odds_are_not_recalculated_after_first_bet(self):
        plugin = build_plugin()
        match = add_future_match(plugin)
        await anext(plugin.esports_bet(FakeEvent(f"/竞猜 {match['display_id']} 1 100")))
        original_odds = dict(match["odds"])

        rating = plugin._get_team_rating_locked("lol", match["teams"][0])
        rating["rating"] = 2200
        incoming = dict(match)
        incoming["odds"] = {}
        incoming["probabilities"] = {}
        incoming["updated_at"] = plugin._utcnow().isoformat(timespec="seconds")
        plugin._upsert_synced_match_locked(incoming)

        stored = plugin.data["esports"]["matches"][match["id"]]
        self.assertEqual(stored["odds"], original_odds)

    async def test_competition_filter_supports_exclusions(self):
        plugin = build_plugin()
        settings = plugin._get_esports_settings()
        settings["tracked_competitions"] = ["lck", "!challengers"]

        self.assertTrue(
            plugin._is_tracked_match({"_filter_text": "lck 2026 season"}, settings)
        )
        self.assertFalse(
            plugin._is_tracked_match(
                {"_filter_text": "lck challengers league 2026"}, settings
            )
        )

    async def test_provider_stops_pagination_after_short_page(self):
        provider = PandaScoreProvider("test-token")
        calls = []

        def fake_get(_path, params):
            calls.append(params["page[number]"])
            return [{"id": index} for index in range(100 if len(calls) == 1 else 2)]

        provider._get_json_sync = fake_get
        result = await provider.fetch_matches("lol", "past", pages=3)

        self.assertEqual(calls, [1, 2])
        self.assertEqual(len(result), 102)


class FakeSyncProvider:
    def __init__(self, token, **kwargs):
        pass

    async def fetch_matches(self, game, state, **kwargs):
        if game != "lol" or state != "upcoming":
            return []
        return [
            {
                "id": 555001,
                "status": "not_started",
                "begin_at": "2026-08-28T12:00:00Z",
                "opponents": [{"id": 101, "name": "Alpha"}, {"id": 102, "name": "Beta"}],
                "league": {"name": "LPL", "slug": "lpl"},
                "serie": {"name": "Summer 2026"},
            },
            {
                "id": 555002,
                "status": "not_started",
                "begin_at": "2026-08-28T13:00:00Z",
                "opponents": [{"id": 201, "name": "Gamma"}, {"id": 202, "name": "Delta"}],
                "league": {"name": "LJL", "slug": "ljl"},
                "serie": {"name": "Summer 2026"},
            },
            {
                "id": 555003,
                "status": "not_started",
                "begin_at": "2026-08-28T14:00:00Z",
                "opponents": [{"id": 301, "name": "Epsilon"}, {"id": 302, "name": "Zeta"}],
                "league": {"name": "LCK Challengers", "slug": "lck-challengers"},
                "serie": {"name": "Summer 2026"},
            },
        ]


class EsportsCandidateSyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_sync_routes_untracked_matches_to_candidates(self):
        plugin = build_plugin()
        original = esports_feature.PandaScoreProvider
        esports_feature.PandaScoreProvider = FakeSyncProvider
        try:
            result = await plugin._sync_esports_once("测试")
        finally:
            esports_feature.PandaScoreProvider = original

        store = plugin._get_esports_store()
        self.assertIn("pandascore:lol:555001", store["matches"])
        self.assertNotIn("pandascore:lol:555002", store["matches"])
        self.assertIn("pandascore:lol:555002", store["candidates"])
        self.assertIn("pandascore:lol:555003", store["candidates"])
        self.assertFalse(store["candidates"]["pandascore:lol:555002"]["dismissed"])
        self.assertEqual(result["candidates"], 2)

    async def test_sync_keeps_dismissed_flag_on_candidates(self):
        plugin = build_plugin()
        store = plugin._get_esports_store()
        store["candidates"]["pandascore:lol:555002"] = {
            "id": "pandascore:lol:555002",
            "display_id": "",
            "source": "pandascore",
            "source_id": "555002",
            "game": "lol",
            "competition": "LJL 2026 Summer",
            "stage": "",
            "name": "Gamma vs Delta",
            "start_time": "2026-08-28T13:00:00Z",
            "status": "not_started",
            "teams": [
                {"id": "555002:team1", "name": "Gamma", "code": "", "image_url": "", "score": 0},
                {"id": "555002:team2", "name": "Delta", "code": "", "image_url": "", "score": 0},
            ],
            "winner_id": "",
            "odds": {},
            "probabilities": {},
            "odds_locked": False,
            "visible": True,
            "settled_at": "",
            "created_at": "",
            "updated_at": "",
            "dismissed": True,
            "first_seen_at": "2026-08-25T00:00:00+00:00",
        }
        original = esports_feature.PandaScoreProvider
        esports_feature.PandaScoreProvider = FakeSyncProvider
        try:
            await plugin._sync_esports_once("测试")
        finally:
            esports_feature.PandaScoreProvider = original

        candidate = plugin._get_esports_store()["candidates"]["pandascore:lol:555002"]
        self.assertTrue(candidate["dismissed"])
        self.assertEqual(candidate["first_seen_at"], "2026-08-25T00:00:00+00:00")

    async def test_include_candidate_promotes_match_with_odds(self):
        plugin = build_plugin()
        store = plugin._get_esports_store()
        store["candidates"]["pandascore:lol:555002"] = {
            "id": "pandascore:lol:555002",
            "display_id": "",
            "source": "pandascore",
            "source_id": "555002",
            "game": "lol",
            "competition": "LJL 2026 Summer",
            "stage": "",
            "name": "Gamma vs Delta",
            "start_time": "2026-08-28T13:00:00Z",
            "status": "not_started",
            "teams": [
                {"id": "555002:team1", "name": "Gamma", "code": "", "image_url": "", "score": 0},
                {"id": "555002:team2", "name": "Delta", "code": "", "image_url": "", "score": 0},
            ],
            "winner_id": "",
            "odds": {},
            "probabilities": {},
            "odds_locked": False,
            "visible": True,
            "settled_at": "",
            "created_at": "",
            "updated_at": "",
            "dismissed": False,
            "first_seen_at": "",
        }

        match = plugin._include_candidate_locked("pandascore:lol:555002")

        self.assertIsNotNone(match)
        self.assertIn("pandascore:lol:555002", store["matches"])
        self.assertNotIn("pandascore:lol:555002", store["candidates"])
        self.assertTrue(match["display_id"])
        self.assertEqual(len(match["odds"]), 2)
        self.assertNotIn("dismissed", match)
        self.assertNotIn("first_seen_at", match)

    async def test_sync_removes_expired_candidates(self):
        plugin = build_plugin()
        store = plugin._get_esports_store()
        stale_start = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=5)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        store["candidates"]["pandascore:lol:424242"] = {
            "id": "pandascore:lol:424242",
            "display_id": "",
            "source": "pandascore",
            "source_id": "424242",
            "game": "lol",
            "competition": "LJL 2026 Summer",
            "stage": "",
            "name": "Old vs Older",
            "start_time": stale_start,
            "status": "not_started",
            "teams": [
                {"id": "424242:team1", "name": "Old", "code": "", "image_url": "", "score": 0},
                {"id": "424242:team2", "name": "Older", "code": "", "image_url": "", "score": 0},
            ],
            "winner_id": "",
            "odds": {},
            "probabilities": {},
            "odds_locked": False,
            "visible": True,
            "settled_at": "",
            "created_at": "",
            "updated_at": "",
            "dismissed": False,
            "first_seen_at": "",
        }
        original = esports_feature.PandaScoreProvider
        esports_feature.PandaScoreProvider = FakeSyncProvider
        try:
            await plugin._sync_esports_once("测试")
        finally:
            esports_feature.PandaScoreProvider = original

        self.assertNotIn("pandascore:lol:424242", store["candidates"])


if __name__ == "__main__":
    unittest.main()
