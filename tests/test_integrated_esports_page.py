# -*- coding: utf-8 -*-
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class IntegratedEsportsPageTests(unittest.TestCase):
    def test_metadata_exposes_only_the_integrated_operations_console(self):
        metadata = (ROOT / "metadata.yaml").read_text(encoding="utf-8")
        self.assertIn("title: 积分运营台", metadata)
        self.assertNotIn("name: 竞猜管理", metadata)

    def test_operations_console_contains_esports_workspace(self):
        html = (ROOT / "pages" / "兑换管理" / "index.html").read_text(
            encoding="utf-8"
        )
        self.assertIn('data-page="esports"', html)
        self.assertIn('id="esportsPage"', html)
        self.assertIn('id="esportsMatchRows"', html)
        self.assertIn('id="esportsBetRows"', html)
        self.assertIn('data-esports-game-filter="lol"', html)
        self.assertIn('data-esports-game-filter="valorant"', html)
        self.assertNotIn("候选比赛", html)
        script = (ROOT / "pages" / "兑换管理" / "app.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("match.betting_open", script)
        self.assertNotIn("candidates/action", script)

    def test_legacy_standalone_esports_page_is_removed(self):
        self.assertFalse((ROOT / "pages" / "竞猜管理" / "index.html").exists())

    def test_operations_console_contains_personal_lottery_prize_editor(self):
        script = (ROOT / "pages" / "兑换管理" / "app.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("lottery_settings.personal_prizes.first.label", script)
        self.assertIn("lottery_settings.personal_prizes.fifth.weight", script)
        self.assertIn("个人抽奖期望返还", script)


if __name__ == "__main__":
    unittest.main()
