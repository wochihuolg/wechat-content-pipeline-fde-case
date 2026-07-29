from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skill" / "wechat-content-pipeline" / "scripts"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


PLAN = load_module("plan_and_write", SCRIPTS / "plan_and_write.py")


class ContentContractTests(unittest.TestCase):
    def test_text_source_is_split_by_headings(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "source.md"
            path.write_text("# A\nFirst idea.\n## B\nSecond idea.\n", encoding="utf-8")
            chunks = PLAN.extract_sources([path])
        self.assertEqual([chunk["ref"] for chunk in chunks], [1, 2])
        self.assertEqual([chunk["location"] for chunk in chunks], ["A", "B"])
        self.assertTrue(all(len(chunk["sha256"]) == 64 for chunk in chunks))

    def test_duplicate_topic_is_rejected(self):
        payload = {
            "topics": [
                {
                    "title": "同一个选题",
                    "compact_title": "选题一",
                    "slug": "same",
                    "source_refs": [1],
                },
                {
                    "title": "同一个选题",
                    "compact_title": "选题二",
                    "slug": "other",
                    "source_refs": [1],
                },
            ]
        }
        with self.assertRaisesRegex(ValueError, "duplicates"):
            PLAN.validate_topics(payload, 2, 1)

    def test_six_image_contract_is_required(self):
        payload = {"sections": [{}, {}, {}], "images": [{"text": ["x"]}] * 5}
        with self.assertRaisesRegex(ValueError, "six images"):
            PLAN.validate_article(payload)


class AuditTests(unittest.TestCase):
    def test_complete_fixture_passes(self):
        with tempfile.TemporaryDirectory() as temporary:
            series = Path(temporary)
            (series / "topic_plan.json").write_text(
                json.dumps({"topics": [{"index": 1}]}, ensure_ascii=False),
                encoding="utf-8",
            )
            article = series / "01_example"
            image_dir = article / "generated_images" / "贴图"
            image_dir.mkdir(parents=True)
            for name in ("来源卡.md", "公众号终稿_图片标注版.md"):
                (article / name).write_text("# test\n", encoding="utf-8")
            instructions = ["# 贴图模式生图指令", ""]
            for index in range(1, 7):
                instructions.extend([f"### 图片{index}", "文字内容：test", ""])
                (image_dir / f"{index:02d}.png").write_bytes(b"test")
            (article / "贴图指令.md").write_text("\n".join(instructions), encoding="utf-8")
            (series / "公众号贴图草稿结果.json").write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "article_dir": "01_example",
                                "status": "saved",
                                "draft_media_id": "unique-media-id",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "audit_pipeline.py"),
                    "--series-dir",
                    str(series),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "passed")
        self.assertEqual(payload["published"], 0)


if __name__ == "__main__":
    unittest.main()
