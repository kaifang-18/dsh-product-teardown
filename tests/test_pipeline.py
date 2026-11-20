"""离线冒烟测试：不访问网络，只处理仓库内的固定样例网页。"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "product-teardown" / "scripts"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample-listing.html"
URL = "https://detail.tmall.com/item.htm?id=123456789"


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *args],
        capture_output=True,
        text=True,
        check=True,
    )


class ExtractHtmlTest(unittest.TestCase):
    def setUp(self) -> None:
        result = run(str(SCRIPTS / "extract_html.py"), str(FIXTURE), "--url", URL)
        self.data = json.loads(result.stdout)

    def test_listing_fields(self) -> None:
        self.assertEqual(self.data["title"], "测试商品标题｜200x230cm 纯棉四件套")
        self.assertEqual(self.data["shop"], "测试旗舰店")
        self.assertEqual(self.data["price"], "¥ 199.00")
        self.assertEqual(self.data["access_state"], "ok")

    def test_images_and_specs(self) -> None:
        self.assertEqual(len(self.data["main_images"]), 2)
        self.assertEqual(len(self.data["detail_images"]), 1)
        self.assertEqual(self.data["spec_groups"][0]["name"], "颜色")
        self.assertTrue(self.data["spec_groups"][0]["options"][1]["disabled"])
        self.assertTrue(self.data["spec_groups"][1]["options"][0]["selected"])
        self.assertEqual(len(self.data["attributes"]), 2)


class CollectTest(unittest.TestCase):
    def test_builds_evidence_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "evidence"
            run(
                str(SCRIPTS / "collect.py"),
                "--url",
                URL,
                "--part",
                f"auto={FIXTURE}",
                "--out",
                str(out),
            )
            evidence = json.loads((out / "商品证据.json").read_text(encoding="utf-8"))
            self.assertEqual(evidence["item_id"], "123456789")
            self.assertEqual(evidence["access_state"], "ok")
            self.assertEqual(len(evidence["main_images"]), 2)
            self.assertTrue((out / "商品证据.md").is_file())
            self.assertIn("测试商品标题", (out / "商品证据.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
