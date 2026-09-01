# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch

from lottery_feature import LotteryFeatureMixin


class FakeLotteryPlugin(LotteryFeatureMixin):
    def __init__(self, config=None):
        self.config = config or {}

    @staticmethod
    def _normalize_int(value, default, minimum=0):
        try:
            return max(minimum, int(value))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _normalize_float(value, default, minimum=0.0):
        try:
            return max(minimum, float(value))
        except (TypeError, ValueError):
            return default


class GroupLotteryDistributionTests(unittest.TestCase):
    def test_configured_weights_split_the_complete_pool(self):
        plugin = FakeLotteryPlugin()

        rewards = plugin._calculate_group_lottery_rewards(100, [1.0, 3.0])

        self.assertEqual(rewards, [25, 75])
        self.assertEqual(sum(rewards), 100)

    def test_random_distribution_uses_fresh_weights_and_keeps_pool_total(self):
        plugin = FakeLotteryPlugin()

        with patch("lottery_feature.random.random", side_effect=[0.2, 0.8]):
            rewards = plugin._calculate_random_group_lottery_rewards(100, 2)

        self.assertEqual(rewards, [20, 80])
        self.assertEqual(sum(rewards), 100)

    def test_lottery_settings_expose_distribution_mode(self):
        plugin = FakeLotteryPlugin(
            {
                "lottery_settings": {
                    "group_required_participants": 3,
                    "group_distribution_mode": "random",
                    "group_distribution_ratios": [1, 2, 3],
                }
            }
        )

        settings = plugin._get_lottery_settings()

        self.assertEqual(settings["group_distribution_mode"], "random")
        self.assertEqual(settings["group_distribution_ratios"], [1.0, 2.0, 3.0])


if __name__ == "__main__":
    unittest.main()
