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

    def test_operations_console_contains_candidate_workspace(self):
        html = (ROOT / "pages" / "兑换管理" / "index.html").read_text(
            encoding="utf-8"
        )
        self.assertIn('id="esportsCandidateRows"', html)
        self.assertIn('id="esportsCandidateAll"', html)
        self.assertIn('id="esportsIncludeSelected"', html)
        script = (ROOT / "pages" / "兑换管理" / "app.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("candidates/action", script)
        self.assertIn("renderEsportsCandidates", script)

    def test_legacy_standalone_esports_page_is_removed(self):
        self.assertFalse((ROOT / "pages" / "竞猜管理" / "index.html").exists())


if __name__ == "__main__":
    unittest.main()
