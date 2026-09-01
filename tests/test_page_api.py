# -*- coding: utf-8 -*-
import asyncio
import datetime
import hashlib
import unittest

from page_api import PointSystemPageApi


class FakeConfig(dict):
    def save_config(self):
        return None


class FakePlugin:
    def __init__(self):
        self.config = FakeConfig(
            {
                "points_name": "星币",
                "exchange_items": [
                    {
                        "__template_key": "default",
                        "name": "礼包",
                        "enabled": True,
                        "cost": 100,
                        "contents": ["CODE-1", "CODE-2"],
                        "private_only": True,
                        "success_template": "{content}",
                    }
                ],
                "exchange_scope": {"mode": "blacklist", "scope": []},
                "lottery_settings": {
                    "enabled": True,
                    "default_mode": "personal",
                    "personal_prizes": {"first": {"weight": 1.0}},
                },
            }
        )
        self.data = {
            "users": {
                "1": {"points": 120, "streak": 3, "last_sign_in": "2026-08-09"},
                "2": {"points": -20, "streak": 0, "last_sign_in": ""},
            },
            "groups": {
                "100": {
                    "members": {
                        "1": {"display_name": "用户一", "updated_at": "2026-08-09T00:00:00"},
                        "2": {"display_name": "用户二", "updated_at": "2026-08-09T00:00:00"},
                    }
                },
                "200": {
                    "members": {
                        "2": {"display_name": "用户二", "updated_at": "2026-08-09T00:00:00"},
                    }
                }
            },
            "exchange_redemptions": [],
            "point_snapshots": [],
        }
        self._data_lock = asyncio.Lock()

    def _get_points_name(self):
        return self.config["points_name"]

    def _get_exchange_items(self):
        return [
            {key: value for key, value in item.items() if key != "__template_key"}
            for item in self.config["exchange_items"]
        ]

    @staticmethod
    def _exchange_content_fingerprint(content):
        return hashlib.sha256(content.encode("utf-8")).hexdigest()


class PageApiTests(unittest.TestCase):
    def setUp(self):
        self.plugin = FakePlugin()
        self.api = PointSystemPageApi(self.plugin)

    def test_overview_includes_global_point_dashboard(self):
        data = self.api._overview_locked()

        self.assertEqual(data["dashboard"]["summary"]["total_points"], 100)
        self.assertEqual(data["dashboard"]["summary"]["positive_points"], 120)
        self.assertEqual(data["dashboard"]["summary"]["debt_points"], 20)
        self.assertEqual(data["dashboard"]["summary"]["user_count"], 2)
        self.assertEqual(data["dashboard"]["groups"][0]["group_id"], "100")
        self.assertEqual(len(data["dashboard"]["daily"]), 7)

    def test_dashboard_can_filter_by_group(self):
        dashboard = self.api._dashboard_view([], group_id="200")

        self.assertEqual(dashboard["scope"]["group_id"], "200")
        self.assertEqual(dashboard["summary"]["total_points"], -20)
        self.assertEqual(dashboard["summary"]["user_count"], 1)
        self.assertEqual(dashboard["leaderboard"][0]["user_id"], "2")
        self.assertEqual(len(dashboard["group_options"]), 2)

    def test_point_history_supports_global_and_group_totals(self):
        now = datetime.datetime.now().replace(second=0, microsecond=0)
        self.plugin.data["point_snapshots"] = [
            {
                "captured_at": (now - datetime.timedelta(hours=3)).isoformat(),
                "total_points": 80,
                "user_count": 2,
                "groups": {"200": {"total_points": -30, "user_count": 1}},
            },
            {
                "captured_at": (now - datetime.timedelta(hours=1)).isoformat(),
                "total_points": 100,
                "user_count": 2,
                "groups": {"200": {"total_points": -20, "user_count": 1}},
            },
        ]

        global_history = self.api._point_history_view(history_range="24h")
        group_history = self.api._point_history_view("200", "24h")

        self.assertEqual(global_history["total_delta"], 20)
        self.assertEqual(group_history["total_delta"], 10)
        self.assertEqual(group_history["points"][-1]["total_points"], -20)

    def test_exchange_page_save_keeps_official_template_metadata(self):
        items, error = self.api._validate_items(
            [{"name": "新礼包", "cost": 20, "contents": ["NEW-CODE"]}]
        )

        self.assertEqual(error, "")
        self.assertEqual(items[0]["__template_key"], "default")

    def test_common_settings_include_complete_personal_lottery_prizes(self):
        settings, error = self.api._validate_settings(self.api._settings_view())

        self.assertEqual(error, "")
        prizes = settings["lottery_settings"]["personal_prizes"]
        self.assertEqual(prizes["first"]["weight"], 1.0)
        self.assertEqual(prizes["fifth"]["label"], "五等奖")
        self.assertEqual(len(prizes), 5)
        self.assertEqual(
            settings["lottery_settings"]["group_distribution_mode"],
            "configured",
        )
        self.assertEqual(
            settings["lottery_settings"]["group_distribution_ratios"],
            [1.0, 9.0, 20.0, 25.0, 35.0],
        )

    def test_personal_lottery_prizes_validate_ranges_and_weights(self):
        settings = self.api._settings_view()
        prizes = settings["lottery_settings"]["personal_prizes"]
        prizes["first"].update(
            {
                "label": "特等奖",
                "min_points": 120,
                "max_points": 150,
                "weight": 1.5,
            }
        )

        validated, error = self.api._validate_settings(settings)

        self.assertEqual(error, "")
        self.assertEqual(
            validated["lottery_settings"]["personal_prizes"]["first"],
            {
                "label": "特等奖",
                "min_points": 120,
                "max_points": 150,
                "weight": 1.5,
            },
        )

        prizes["first"]["min_points"] = 151
        _, error = self.api._validate_settings(settings)
        self.assertIn("下限不能大于上限", error)

        prizes["first"]["min_points"] = 120
        for prize in prizes.values():
            prize["weight"] = 0
        _, error = self.api._validate_settings(settings)
        self.assertIn("至少一个奖项权重", error)

    def test_group_lottery_configured_weights_match_participant_count(self):
        settings = self.api._settings_view()
        settings["lottery_settings"].update(
            {
                "group_required_participants": 3,
                "group_distribution_mode": "configured",
                "group_distribution_ratios": ["1", "1", "3"],
            }
        )

        validated, error = self.api._validate_settings(settings)

        self.assertEqual(error, "")
        self.assertEqual(
            validated["lottery_settings"]["group_distribution_ratios"],
            [1.0, 1.0, 3.0],
        )

        settings["lottery_settings"]["group_distribution_ratios"] = ["1", "3"]
        _, error = self.api._validate_settings(settings)
        self.assertIn("需要填写 3 个奖励权重", error)

    def test_group_lottery_random_mode_accepts_empty_weights(self):
        settings = self.api._settings_view()
        settings["lottery_settings"].update(
            {
                "group_distribution_mode": "random",
                "group_distribution_ratios": [],
            }
        )

        validated, error = self.api._validate_settings(settings)

        self.assertEqual(error, "")
        self.assertEqual(
            validated["lottery_settings"]["group_distribution_ratios"], []
        )


if __name__ == "__main__":
    unittest.main()
