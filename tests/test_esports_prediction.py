# -*- coding: utf-8 -*-
import asyncio
import copy
import datetime
import threading
import time
import unittest
from types import SimpleNamespace

import esports_feature
from main import PointSystemPlugin, REGISTERED_COMMAND_NAMES
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
    async def test_bet_without_arguments_returns_usage_help(self):
        plugin = build_plugin()

        reply = await anext(plugin.esports_bet(FakeEvent("/竞猜")))

        self.assertIn("赛事竞猜使用方法", reply)
        self.assertIn("/今日赛事", reply)
        self.assertIn("/竞猜 L001 TES 100", reply)
        self.assertIn("/改选 L001 BLG", reply)
        self.assertIn("/撤销竞猜 L001", reply)
        self.assertIn("/签到", reply)
        self.assertNotIn("/群聊签到", reply)
        self.assertIn("/我的积分", reply)
        self.assertIn("/积分榜", reply)
        self.assertIn("/积分规则", reply)

    async def test_esports_prediction_help_is_registered_as_a_chat_command(self):
        self.assertIn("赛事竞猜", REGISTERED_COMMAND_NAMES)
        self.assertTrue(hasattr(PointSystemPlugin, "esports_help_command"))

    async def test_sign_in_command_uses_short_name(self):
        self.assertIn("签到", REGISTERED_COMMAND_NAMES)
        self.assertNotIn("群聊签到", REGISTERED_COMMAND_NAMES)

    async def test_today_matches_use_readable_multiline_blocks(self):
        plugin = build_plugin()
        match = add_future_match(plugin)

        reply = await anext(plugin.esports_matches(FakeEvent("/今日赛事")))

        self.assertIn(f"【{match['display_id']}｜LoL】\n", reply)
        self.assertIn("时间：", reply)
        self.assertIn("\n赛事：LPL 测试赛", reply)
        self.assertIn("\n对阵：BLG ", reply)
        self.assertIn(" vs TES ", reply)
        self.assertIn("\n\n竞猜：/竞猜 ", reply)
        self.assertNotIn("赛事详情", reply)

    async def test_today_matches_reserve_space_for_each_available_game(self):
        plugin = build_plugin()
        plugin._utcnow = lambda: datetime.datetime(
            2026, 8, 28, 0, 0, tzinfo=datetime.timezone.utc
        )
        lol_matches = [
            plugin._create_manual_match_locked(
                "lol",
                "LPL",
                f"LPL-A{index}",
                f"LPL-B{index}",
                f"2026-08-29 {10 + index:02d}:00",
            )
            for index in range(10)
        ]
        valorant_match = plugin._create_manual_match_locked(
            "valorant", "VCT", "EDG", "PRX", "2026-08-30 20:00"
        )

        reply = await anext(plugin.esports_matches(FakeEvent("/今日赛事")))

        self.assertIn("（10 场）", reply)
        self.assertIn(valorant_match["display_id"], reply)
        self.assertIn("｜VALORANT】", reply)
        self.assertNotIn(lol_matches[-1]["display_id"], reply)

    async def test_match_detail_is_not_registered_as_a_chat_command(self):
        self.assertNotIn("赛事详情", REGISTERED_COMMAND_NAMES)
        self.assertFalse(hasattr(PointSystemPlugin, "esports_match_detail_command"))

    async def test_recent_results_show_finished_match_score_and_winner(self):
        plugin = build_plugin()
        plugin._utcnow = lambda: datetime.datetime(
            2026, 8, 28, 12, 0, tzinfo=datetime.timezone.utc
        )
        match = plugin._create_manual_match_locked(
            "valorant", "VCT Pacific", "EDward Gaming", "Paper Rex", "2026-08-28 18:00"
        )
        match["teams"][0]["code"] = "EDG"
        match["teams"][1]["code"] = "PRX"
        match["teams"][0]["score"] = 2
        match["teams"][1]["score"] = 1
        match["winner_id"] = match["teams"][0]["id"]
        match["status"] = "settled"
        match["end_time"] = "2026-08-28T11:30:00+00:00"
        match["settled_at"] = "2026-08-28T11:31:00+00:00"

        reply = await anext(plugin.esports_results(FakeEvent("/比赛结果")))

        self.assertIn(f"【{match['display_id']}｜VALORANT】", reply)
        self.assertIn("对阵：EDG 2 : 1 PRX", reply)
        self.assertIn("赛果：EDG 获胜", reply)

    async def test_recent_result_commands_are_registered(self):
        self.assertIn("比赛结果", REGISTERED_COMMAND_NAMES)
        self.assertIn("赛事结果", REGISTERED_COMMAND_NAMES)

    async def test_match_ids_are_short_and_legacy_ids_remain_resolvable(self):
        plugin = build_plugin()
        first = plugin._create_manual_match_locked(
            "lol", "LPL", "BLG", "TES", "2026-09-01 19:00"
        )
        second = plugin._create_manual_match_locked(
            "lol", "LCK", "GEN", "T1", "2026-09-02 19:00"
        )
        raw = copy.deepcopy(plugin.data["esports"])
        raw.pop("display_sequences", None)
        first_raw = raw["matches"][first["id"]]
        second_raw = raw["matches"][second["id"]]
        first_raw["display_id"] = "L1629296"
        second_raw["display_id"] = "L1643087"

        normalized = plugin._normalize_esports_store(raw)
        plugin.data["esports"] = normalized

        self.assertEqual(normalized["matches"][first["id"]]["display_id"], "L001")
        self.assertEqual(normalized["matches"][second["id"]]["display_id"], "L002")
        self.assertIs(
            plugin._resolve_match_locked("L1629296"),
            normalized["matches"][first["id"]],
        )
        self.assertIs(
            plugin._resolve_match_locked("L1643087"),
            normalized["matches"][second["id"]],
        )
        next_lol = plugin._create_manual_match_locked(
            "lol", "LPL", "AL", "WBG", "2026-09-03 19:00"
        )
        first_valorant = plugin._create_manual_match_locked(
            "valorant", "VCT", "EDG", "PRX", "2026-09-04 19:00"
        )
        self.assertEqual(next_lol["display_id"], "L003")
        self.assertEqual(first_valorant["display_id"], "V001")

    async def test_today_matches_are_supplemented_with_nearby_open_matches(self):
        plugin = build_plugin()
        plugin._utcnow = lambda: datetime.datetime(
            2026, 8, 26, 8, 0, tzinfo=datetime.timezone.utc
        )
        today_match = plugin._create_manual_match_locked(
            "lol", "LPL", "BLG", "TES", "2026-08-26 20:00"
        )
        next_match = plugin._create_manual_match_locked(
            "lol", "LCK", "GEN", "T1", "2026-08-27 20:00"
        )

        reply = await anext(plugin.esports_matches(FakeEvent("/今日赛事")))

        self.assertIn(today_match["display_id"], reply)
        self.assertIn(next_match["display_id"], reply)
        self.assertIn("今日及近期可竞猜赛事", reply)

    async def test_tier_one_allowlist_is_game_specific(self):
        plugin = build_plugin()
        accepted = [
            {"game": "lol", "_filter_text": "lpl 2026 split 3 playoffs"},
            {"game": "lol", "_filter_text": "league of legends pro league 2026"},
            {"game": "lol", "_filter_text": "lck 2026 cup"},
            {"game": "lol", "_filter_text": "league of legends champions korea 2026"},
            {"game": "lol", "_filter_text": "first stand 2026"},
            {"game": "lol", "_filter_text": "mid-season invitational 2026"},
            {"game": "lol", "_filter_text": "2026 season world championship"},
            {"game": "valorant", "_filter_text": "vct 2026 americas stage 2"},
            {"game": "valorant", "_filter_text": "valorant champions tour 2026 emea kickoff"},
            {"game": "valorant", "_filter_text": "vct 2026 pacific stage 1"},
            {"game": "valorant", "_filter_text": "vct 2026 china stage 2"},
            {"game": "valorant", "_filter_text": "vct 2026 masters london"},
            {"game": "valorant", "_filter_text": "valorant champions 2026"},
            {
                "game": "valorant",
                "_filter_text": "valorant champions tour 2026 regular season",
            },
            {"game": "valorant", "_filter_text": "vct 2026 regular season"},
            {
                "game": "valorant",
                "_filter_text": "vct 2026 stage 2 pacific",
            },
        ]
        rejected = [
            {"game": "lol", "_filter_text": "lck challengers league 2026"},
            {"game": "lol", "_filter_text": "lck cl 2026 summer"},
            {"game": "lol", "_filter_text": "lec 2026 summer"},
            {"game": "valorant", "_filter_text": "vct challengers 2026 japan"},
            {"game": "valorant", "_filter_text": "vct ascension 2026 pacific"},
            {"game": "valorant", "_filter_text": "valorant game changers 2026 china"},
        ]

        self.assertTrue(all(plugin._is_tier_one_match(match) for match in accepted))
        self.assertFalse(any(plugin._is_tier_one_match(match) for match in rejected))

    async def test_tier_one_league_discovery_excludes_lower_tiers(self):
        plugin = build_plugin()
        accepted = [
            ("lol", {"name": "LPL", "slug": "lpl"}),
            ("lol", {"name": "LCK", "slug": "lck"}),
            ("lol", {"name": "Mid-Season Invitational", "slug": "msi"}),
            ("lol", {"name": "World Championship", "slug": "worlds"}),
            (
                "valorant",
                {"name": "Valorant Champions Tour 2026", "slug": "vct-2026"},
            ),
            ("valorant", {"name": "Valorant Masters", "slug": "masters"}),
        ]
        rejected = [
            ("lol", {"name": "LEC", "slug": "lec"}),
            (
                "lol",
                {"name": "LCK Challengers League", "slug": "lck-challengers"},
            ),
            (
                "valorant",
                {"name": "VCT Challengers Japan", "slug": "vct-challengers-japan"},
            ),
            (
                "valorant",
                {"name": "Valorant Game Changers", "slug": "game-changers"},
            ),
        ]

        self.assertTrue(
            all(plugin._is_tier_one_league(game, league) for game, league in accepted)
        )
        self.assertFalse(
            any(plugin._is_tier_one_league(game, league) for game, league in rejected)
        )

    async def test_match_display_and_bet_prefer_official_team_code(self):
        plugin = build_plugin()
        match = add_future_match(plugin)
        match["teams"][0]["name"] = "Top Esports"
        match["teams"][0]["code"] = "TES"
        match["teams"][1]["name"] = "Bilibili Gaming"
        match["teams"][1]["code"] = "BLG"

        line = plugin._format_match_line_locked(match)
        self.assertIn("TES", line)
        self.assertIn("BLG", line)
        self.assertNotIn("Top Esports", line)
        self.assertIs(plugin._resolve_match_team(match, "TES"), match["teams"][0])
        self.assertIs(
            plugin._resolve_match_team(match, "Top Esports"), match["teams"][0]
        )

        reply = await anext(
            plugin.esports_bet(FakeEvent(f"/竞猜 {match['display_id']} TES 100"))
        )
        bet = next(iter(plugin.data["esports"]["bets"].values()))
        self.assertEqual(bet["team_name"], "TES")
        self.assertIn("｜TES｜", reply)
        self.assertNotIn("Top Esports", reply)
        self.assertIn(
            f"赛事竞猜：{match['display_id']} TES",
            plugin.data["point_transactions"][-1]["source"],
        )

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
        self.assertEqual(
            [item["delta"] for item in plugin.data["point_transactions"]],
            [-200, 200],
        )

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

    async def test_model_odds_follow_elo_strength(self):
        plugin = build_plugin()
        match = add_future_match(plugin)
        first = plugin._get_team_rating_locked("lol", match["teams"][0])
        second = plugin._get_team_rating_locked("lol", match["teams"][1])
        first.update({"rating": 1700.0, "games": 12})
        second.update({"rating": 1500.0, "games": 12})

        plugin._calculate_match_odds_locked(match)

        first_id = match["teams"][0]["id"]
        second_id = match["teams"][1]["id"]
        self.assertGreater(match["probabilities"][first_id], 0.7)
        self.assertAlmostEqual(
            match["probabilities"][first_id]
            + match["probabilities"][second_id],
            1.0,
        )
        self.assertLess(match["odds"][first_id], match["odds"][second_id])

    async def test_model_shrinks_small_samples_toward_even(self):
        plugin = build_plugin()
        match = add_future_match(plugin)
        first = plugin._get_team_rating_locked("lol", match["teams"][0])
        second = plugin._get_team_rating_locked("lol", match["teams"][1])
        first.update({"rating": 1900.0, "games": 0})
        second.update({"rating": 1500.0, "games": 0})

        plugin._calculate_match_odds_locked(match)
        first_id = match["teams"][0]["id"]
        cold_probability = match["probabilities"][first_id]
        first["games"] = 12
        second["games"] = 12
        plugin._calculate_match_odds_locked(match)

        self.assertGreater(cold_probability, 0.5)
        self.assertLess(cold_probability, 0.6)
        self.assertGreater(match["probabilities"][first_id], 0.85)

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

    async def test_provider_filters_matches_by_allowed_league_ids(self):
        provider = PandaScoreProvider("test-token")
        captured = {}

        def fake_get(_path, params):
            captured.update(params)
            return []

        provider._get_json_sync = fake_get
        await provider.fetch_matches(
            "lol", "upcoming", league_ids=("9001", "9002")
        )

        self.assertEqual(captured["filter[league_id]"], "9001,9002")

    async def test_provider_discovers_valorant_leagues_with_targeted_searches(self):
        provider = PandaScoreProvider("test-token")
        searches = []

        def fake_get(_path, params):
            searches.append(params.get("search[name]"))
            return []

        provider._get_json_sync = fake_get
        await provider.fetch_leagues("valorant")

        self.assertIn("VCT", searches)
        self.assertIn("Valorant Champions Tour", searches)
        self.assertNotIn(None, searches)

    async def test_provider_discovers_leagues_concurrently(self):
        provider = PandaScoreProvider("test-token")
        lock = threading.Lock()
        active = 0
        max_active = 0

        def fake_get(_path, _params):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return []

        provider._get_json_sync = fake_get
        await provider.fetch_leagues("valorant", pages=1)

        self.assertGreater(max_active, 1)


class FakeSyncProvider:
    cancel_existing = False

    def __init__(self, token, **kwargs):
        pass

    async def fetch_leagues(self, game, **kwargs):
        if game == "lol":
            return [
                {"id": 9001, "name": "LPL", "slug": "lpl"},
                {"id": 9002, "name": "LCK", "slug": "lck"},
                {
                    "id": 9999,
                    "name": "LCK Challengers League",
                    "slug": "lck-challengers",
                },
            ]
        return []

    async def fetch_matches(self, game, state, **kwargs):
        if game != "lol":
            return []
        if state == "past":
            return [
                {
                    "id": 555004,
                    "status": "finished",
                    "begin_at": "2026-08-25T12:00:00Z",
                    "end_at": "2026-08-25T13:00:00Z",
                    "winner_id": 401,
                    "opponents": [
                        {"id": 401, "name": "Ended Alpha", "acronym": "EA"},
                        {"id": 402, "name": "Ended Beta", "acronym": "EB"},
                    ],
                    "league": {"name": "LCK", "slug": "lck"},
                    "serie": {"name": "Summer 2026"},
                },
                {
                    "id": 555005,
                    "status": "finished",
                    "begin_at": "2026-08-23T12:00:00Z",
                    "end_at": "2026-08-23T13:00:00Z",
                    "winner_id": 501,
                    "opponents": [
                        {"id": 501, "name": "Old Alpha", "acronym": "OA"},
                        {"id": 502, "name": "Old Beta", "acronym": "OB"},
                    ],
                    "league": {"name": "LPL", "slug": "lpl"},
                    "serie": {"name": "Summer 2026"},
                },
                {
                    "id": 555006,
                    "status": "canceled",
                    "begin_at": "2026-08-27T10:00:00Z",
                    "opponents": [
                        {"id": 601, "name": "Canceled Alpha"},
                        {"id": 602, "name": "Canceled Beta"},
                    ],
                    "league": {"name": "LPL", "slug": "lpl"},
                    "serie": {"name": "Summer 2026"},
                },
            ]
        if state != "upcoming":
            return []
        return [
            {
                "id": 555001,
                "status": "canceled" if self.cancel_existing else "not_started",
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
                "league": {"name": "LCK", "slug": "lck"},
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


class TargetedHistoryProvider:
    calls = []

    def __init__(self, token, **kwargs):
        pass

    async def fetch_leagues(self, game, **kwargs):
        if game == "lol":
            return [{"id": 9001, "name": "LPL", "slug": "lpl"}]
        return []

    async def fetch_matches(self, game, state, **kwargs):
        league_ids = tuple(str(item) for item in kwargs.get("league_ids", []))
        self.calls.append((game, state, league_ids))
        if game != "lol":
            return []
        if state == "upcoming":
            return [
                {
                    "id": 880001,
                    "status": "not_started",
                    "begin_at": "2026-08-28T12:00:00Z",
                    "opponents": [
                        {"id": 101, "name": "Strong", "acronym": "STR"},
                        {"id": 102, "name": "Weak", "acronym": "WEK"},
                    ],
                    "league": {"id": 9001, "name": "LPL", "slug": "lpl"},
                    "serie": {"name": "Summer 2026"},
                }
            ]
        if state == "past" and league_ids == ("9001",):
            return [
                {
                    "id": 880100 + index,
                    "status": "finished",
                    "begin_at": f"2026-08-{10 + index:02d}T12:00:00Z",
                    "end_at": f"2026-08-{10 + index:02d}T13:00:00Z",
                    "winner_id": 101,
                    "opponents": [
                        {"id": 101, "name": "Strong", "acronym": "STR"},
                        {"id": 102, "name": "Weak", "acronym": "WEK"},
                    ],
                    "league": {"id": 9001, "name": "LPL", "slug": "lpl"},
                    "serie": {"name": "Summer 2026"},
                }
                for index in range(8)
            ]
        return []


class EsportsSyncFilterTests(unittest.IsolatedAsyncioTestCase):
    async def test_vct_sync_scans_beyond_first_two_upcoming_pages_and_reports_counts(self):
        class DeepVctProvider:
            calls = []

            def __init__(self, token, **kwargs):
                pass

            async def fetch_leagues(self, game, **kwargs):
                if game == "lol":
                    return [{"id": 9001, "name": "LPL", "slug": "lpl"}]
                return []

            async def fetch_matches(self, game, state, **kwargs):
                self.calls.append((game, state, kwargs.get("pages")))
                if game != "valorant" or state != "upcoming" or kwargs.get("pages", 0) < 3:
                    return []
                return [
                    {
                        "id": 790001,
                        "status": "not_started",
                        "begin_at": "2026-08-29T12:00:00Z",
                        "opponents": [
                            {"opponent": {"id": 791, "name": "EDward Gaming", "acronym": "EDG"}},
                            {"opponent": {"id": 792, "name": "Paper Rex", "acronym": "PRX"}},
                        ],
                        "league": {
                            "id": 7900,
                            "name": "Valorant Champions Tour 2026",
                            "slug": "valorant-champions-tour-2026",
                        },
                        "serie": {"name": "Champions"},
                    }
                ]

        plugin = build_plugin()
        plugin._utcnow = lambda: datetime.datetime(
            2026, 8, 28, 0, 0, tzinfo=datetime.timezone.utc
        )
        original = esports_feature.PandaScoreProvider
        esports_feature.PandaScoreProvider = DeepVctProvider
        DeepVctProvider.calls = []
        try:
            result = await plugin._sync_esports_once("深页 VCT 同步")
        finally:
            esports_feature.PandaScoreProvider = original

        self.assertIn(("valorant", "upcoming", 5), DeepVctProvider.calls)
        self.assertIn("pandascore:valorant:790001", plugin._get_esports_store()["matches"])
        self.assertIn("VALORANT 原始 1/收录 1", result["summary"])

    async def test_sync_fetches_vct_matches_without_league_catalog_ids(self):
        class VctWithoutCatalogProvider:
            calls = []

            def __init__(self, token, **kwargs):
                pass

            async def fetch_leagues(self, game, **kwargs):
                self.calls.append((game, "leagues", ()))
                if game == "lol":
                    return [{"id": 9001, "name": "LPL", "slug": "lpl"}]
                return []

            async def fetch_matches(self, game, state, **kwargs):
                league_ids = tuple(str(item) for item in kwargs.get("league_ids", []))
                self.calls.append((game, state, league_ids))
                if game != "valorant" or state != "upcoming" or league_ids:
                    return []
                return [
                    {
                        "id": 780001,
                        "status": "not_started",
                        "begin_at": "2026-08-29T12:00:00Z",
                        "opponents": [
                            {"opponent": {"id": 781, "name": "Paper Rex", "acronym": "PRX"}},
                            {"opponent": {"id": 782, "name": "Rex Regum Qeon", "acronym": "RRQ"}},
                        ],
                        "league": {"id": 7800, "name": "VCT 2026 Pacific", "slug": "vct-2026-pacific"},
                        "serie": {"name": "Stage 2"},
                    }
                ]

        plugin = build_plugin()
        plugin._utcnow = lambda: datetime.datetime(
            2026, 8, 28, 0, 0, tzinfo=datetime.timezone.utc
        )
        original = esports_feature.PandaScoreProvider
        esports_feature.PandaScoreProvider = VctWithoutCatalogProvider
        VctWithoutCatalogProvider.calls = []
        try:
            await plugin._sync_esports_once("无目录 VCT 同步")
        finally:
            esports_feature.PandaScoreProvider = original

        self.assertIn(("valorant", "upcoming", ()), VctWithoutCatalogProvider.calls)
        self.assertIn(
            "pandascore:valorant:780001",
            plugin._get_esports_store()["matches"],
        )

    async def test_sync_ignores_stale_valorant_league_cache_and_keeps_vct_match(self):
        class CurrentVctProvider:
            calls = []

            def __init__(self, token, **kwargs):
                pass

            async def fetch_leagues(self, game, **kwargs):
                self.calls.append((game, "leagues", ()))
                if game == "valorant":
                    return [
                        {
                            "id": 7701,
                            "name": "Valorant Champions Tour 2026",
                            "slug": "valorant-champions-tour-2026",
                        }
                    ]
                return [{"id": 9001, "name": "LPL", "slug": "lpl"}]

            async def fetch_matches(self, game, state, **kwargs):
                league_ids = tuple(
                    str(item) for item in kwargs.get("league_ids", [])
                )
                self.calls.append((game, state, league_ids))
                if (
                    game != "valorant"
                    or state != "upcoming"
                    or league_ids
                ):
                    return []
                return [
                    {
                        "id": 770001,
                        "status": "not_started",
                        "begin_at": "2026-08-29T12:00:00Z",
                        "opponents": [
                            {"id": 701, "name": "Rex Regum Qeon", "acronym": "RRQ"},
                            {"id": 702, "name": "Paper Rex", "acronym": "PRX"},
                        ],
                        "league": {
                            "id": 7701,
                            "name": "Valorant Champions Tour 2026",
                            "slug": "valorant-champions-tour-2026",
                        },
                        "serie": {"name": "2026: Stage 2 - Pacific"},
                    }
                ]

        plugin = build_plugin()
        plugin._utcnow = lambda: datetime.datetime(
            2026, 8, 28, 0, 0, tzinfo=datetime.timezone.utc
        )
        plugin._get_esports_store()["tier_one_league_ids"]["valorant"] = ["7600"]
        original = esports_feature.PandaScoreProvider
        esports_feature.PandaScoreProvider = CurrentVctProvider
        CurrentVctProvider.calls = []
        try:
            await plugin._sync_esports_once("刷新 VCT 联赛")
            league_calls_after_first_sync = sum(
                1 for call in CurrentVctProvider.calls if call[1] == "leagues"
            )
            await plugin._sync_esports_once("再次同步")
        finally:
            esports_feature.PandaScoreProvider = original

        self.assertNotIn(("valorant", "leagues", ()), CurrentVctProvider.calls)
        self.assertIn(
            "pandascore:valorant:770001",
            plugin._get_esports_store()["matches"],
        )
        self.assertEqual(
            sum(1 for call in CurrentVctProvider.calls if call[1] == "leagues"),
            league_calls_after_first_sync,
        )

    async def test_sync_fetches_target_league_history_before_pricing(self):
        plugin = build_plugin()
        plugin._utcnow = lambda: datetime.datetime(
            2026, 8, 26, 8, 0, tzinfo=datetime.timezone.utc
        )
        original = esports_feature.PandaScoreProvider
        esports_feature.PandaScoreProvider = TargetedHistoryProvider
        TargetedHistoryProvider.calls = []
        try:
            await plugin._sync_esports_once("测试历史赔率")
        finally:
            esports_feature.PandaScoreProvider = original

        self.assertIn(("lol", "past", ("9001",)), TargetedHistoryProvider.calls)
        self.assertTrue(
            all(call[2] for call in TargetedHistoryProvider.calls if call[0] == "lol")
        )
        match = plugin._get_esports_store()["matches"]["pandascore:lol:880001"]
        first_id = match["teams"][0]["id"]
        second_id = match["teams"][1]["id"]
        self.assertGreater(match["probabilities"][first_id], 0.5)
        self.assertNotEqual(match["odds"][first_id], match["odds"][second_id])

    async def test_sync_stores_only_filtered_matches_and_recent_results(self):
        plugin = build_plugin()
        plugin._utcnow = lambda: datetime.datetime(
            2026, 8, 26, 8, 0, tzinfo=datetime.timezone.utc
        )
        original = esports_feature.PandaScoreProvider
        esports_feature.PandaScoreProvider = FakeSyncProvider
        try:
            result = await plugin._sync_esports_once("测试")
        finally:
            esports_feature.PandaScoreProvider = original

        store = plugin._get_esports_store()
        self.assertIn("pandascore:lol:555001", store["matches"])
        self.assertIn("pandascore:lol:555002", store["matches"])
        self.assertIn("pandascore:lol:555004", store["matches"])
        self.assertNotIn("pandascore:lol:555003", store["matches"])
        self.assertNotIn("pandascore:lol:555005", store["matches"])
        self.assertNotIn("pandascore:lol:555006", store["matches"])
        self.assertNotIn("candidates", store)
        self.assertEqual(result["ignored"], 3)

    async def test_match_canceled_after_being_listed_is_retained_and_refunded(self):
        plugin = build_plugin()
        plugin._utcnow = lambda: datetime.datetime(
            2026, 8, 26, 8, 0, tzinfo=datetime.timezone.utc
        )
        original = esports_feature.PandaScoreProvider
        esports_feature.PandaScoreProvider = FakeSyncProvider
        FakeSyncProvider.cancel_existing = False
        try:
            await plugin._sync_esports_once("首次同步")
            match = plugin._get_esports_store()["matches"]["pandascore:lol:555001"]
            await anext(
                plugin.esports_bet(
                    FakeEvent(f"/竞猜 {match['display_id']} 1 100")
                )
            )
            FakeSyncProvider.cancel_existing = True
            await plugin._sync_esports_once("取消同步")
        finally:
            FakeSyncProvider.cancel_existing = False
            esports_feature.PandaScoreProvider = original

        store = plugin._get_esports_store()
        match = store["matches"]["pandascore:lol:555001"]
        bet = store["bets"][plugin._bet_key(match["id"], "123")]
        self.assertEqual(match["status"], "refunded")
        self.assertEqual(bet["status"], "refunded")
        self.assertEqual(plugin.data["users"]["123"]["points"], 1000)


if __name__ == "__main__":
    unittest.main()
