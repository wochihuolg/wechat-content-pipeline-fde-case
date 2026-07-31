from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skill" / "wechat-white-minimal-article" / "scripts"
FULL_TITLE = "每天介绍一个 AI 产品：Gemini Notebook"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


PLAN = load_module("white_minimal_plan", SCRIPTS / "plan_and_write.py")


def topic_payload(title: str = FULL_TITLE) -> dict:
    return {
        "topics": [
            {
                "title": title,
                "compact_title": title,
                "slug": "gemini_notebook",
                "source_refs": [1],
                "pain_point": "资料多而散",
                "promise": "围绕自有来源研究",
                "deliverable": "来源包清单",
                "next_hook": "Perplexity",
            }
        ]
    }


class TitleContractTests(unittest.TestCase):
    def test_complete_28_character_title_is_accepted(self):
        topics = PLAN.validate_topics(topic_payload(), 1, 1)
        self.assertEqual(topics[0]["title"], FULL_TITLE)
        self.assertEqual(topics[0]["compact_title"], FULL_TITLE)

    def test_shortened_title_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "must use"):
            PLAN.validate_topics(topic_payload("AI产品：Gemini Notebook"), 1, 1)

    def test_title_and_compact_title_must_match(self):
        payload = topic_payload()
        payload["topics"][0]["compact_title"] = "AI产品：Gemini Notebook"
        with self.assertRaisesRegex(ValueError, "must be identical"):
            PLAN.validate_topics(payload, 1, 1)


class ManifestAndAuditTests(unittest.TestCase):
    def make_fixture(self, root: Path) -> Path:
        series = root / "series"
        article = series / "01_gemini_notebook"
        image_dir = article / "generated_images" / "贴图"
        image_dir.mkdir(parents=True)
        for name in ("来源卡.md", "公众号终稿_图片标注版.md"):
            (article / name).write_text("# test\n", encoding="utf-8")
        lines = [
            "# 白底极简 AI 产品拆解贴图指令",
            "",
            f"文章：{FULL_TITLE}",
            "",
            "## 公众号标题",
            FULL_TITLE,
            "",
            "## 公众号摘要",
            "让 AI 围绕自己提供的来源研究，并保留可回看的引用。",
            "",
        ]
        for index in range(1, 7):
            lines.extend([f"### 图片{index}", "文字内容：test", ""])
            (image_dir / f"{index:02d}.png").write_bytes(b"test")
        (article / "贴图指令.md").write_text("\n".join(lines), encoding="utf-8")
        (series / "topic_plan.json").write_text(
            json.dumps(
                {
                    "topics": [
                        {
                            "index": 1,
                            "slug": "gemini_notebook",
                            "working_title": FULL_TITLE,
                            "compact_title": FULL_TITLE,
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (series / "公众号贴图短标题.json").write_text(
            json.dumps({"01_gemini_notebook": FULL_TITLE}, ensure_ascii=False),
            encoding="utf-8",
        )
        return series

    def test_manifest_accepts_full_title_and_audit_passes(self):
        with tempfile.TemporaryDirectory() as temporary:
            series = self.make_fixture(Path(temporary))
            manifest_path = series / "公众号贴图草稿清单.json"
            build = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "build_manifest.py"),
                    "--series-dir",
                    str(series),
                    "--title-map",
                    str(series / "公众号贴图短标题.json"),
                    "--output",
                    str(manifest_path),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["items"][0]["draft_title"], FULL_TITLE)

            manifest["items"][0].update(
                {
                    "status": "saved",
                    "draft_media_id": "unique-media-id",
                    "save_method": "official_newspic_api",
                }
            )
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False),
                encoding="utf-8",
            )
            (series / "公众号贴图草稿结果.json").write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "article_dir": "01_gemini_notebook",
                                "draft_title": FULL_TITLE,
                                "status": "saved",
                                "draft_media_id": "unique-media-id",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            audit = subprocess.run(
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
        self.assertEqual(audit.returncode, 0, audit.stdout + audit.stderr)
        self.assertEqual(json.loads(audit.stdout)["status"], "passed")

    def test_manifest_rejects_title_map_mismatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            series = self.make_fixture(Path(temporary))
            (series / "公众号贴图短标题.json").write_text(
                json.dumps(
                    {"01_gemini_notebook": "AI产品：Gemini Notebook"},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "build_manifest.py"),
                    "--series-dir",
                    str(series),
                    "--title-map",
                    str(series / "公众号贴图短标题.json"),
                    "--output",
                    str(series / "公众号贴图草稿清单.json"),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("title map does not match", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
