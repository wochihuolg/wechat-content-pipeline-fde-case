#!/usr/bin/env python3
"""Audit local content, image, manifest, and WeChat draft state."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


ARTICLE_DIR_RE = re.compile(r"^\d{2}_")
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
REQUIRED_FILES = ("来源卡.md", "公众号终稿_图片标注版.md", "贴图指令.md")


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--series-dir", required=True, type=Path)
    parser.add_argument(
        "--allow-local-only",
        action="store_true",
        help="Pass before drafts exist; still require complete article and image outputs.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    series_dir = args.series_dir.resolve()
    plan = read_json(series_dir / "topic_plan.json", {})
    topics = plan.get("topics") if isinstance(plan, dict) else []
    article_dirs = sorted(
        path for path in series_dir.iterdir() if path.is_dir() and ARTICLE_DIR_RE.match(path.name)
    )
    errors: list[str] = []

    if not topics:
        errors.append("missing or empty topic_plan.json")
    elif len(topics) != len(article_dirs):
        errors.append(f"topic/article count mismatch: {len(topics)} != {len(article_dirs)}")

    for article_dir in article_dirs:
        for name in REQUIRED_FILES:
            if not (article_dir / name).is_file():
                errors.append(f"{article_dir.name}: missing {name}")
        instruction = article_dir / "贴图指令.md"
        if instruction.exists():
            text = instruction.read_text(encoding="utf-8-sig")
            image_sections = len(re.findall(r"(?m)^###\s+图片\d+\s*$", text))
            if image_sections != 6:
                errors.append(f"{article_dir.name}: expected 6 image instructions, found {image_sections}")
        image_dir = article_dir / "generated_images" / "贴图"
        images = (
            [path for path in image_dir.iterdir() if path.suffix.lower() in IMAGE_EXTENSIONS]
            if image_dir.exists()
            else []
        )
        if len(images) != 6:
            errors.append(f"{article_dir.name}: expected 6 generated images, found {len(images)}")

    results_path = series_dir / "公众号贴图草稿结果.json"
    results_payload = read_json(results_path, {})
    results = results_payload.get("items", []) if isinstance(results_payload, dict) else results_payload
    results = results if isinstance(results, list) else []
    ids = [
        f"app:{item['appmsgid']}" if item.get("appmsgid") else f"media:{item['draft_media_id']}"
        for item in results
        if item.get("appmsgid") or item.get("draft_media_id")
    ]
    saved = sum(item.get("status") == "saved" for item in results)
    failed = sum(item.get("status") == "failed" for item in results)
    needs_update = sum(item.get("status") == "needs_update" for item in results)

    if not args.allow_local_only:
        if len(results) != len(article_dirs):
            errors.append(f"draft/article count mismatch: {len(results)} != {len(article_dirs)}")
        if saved != len(article_dirs):
            errors.append(f"saved draft count mismatch: {saved} != {len(article_dirs)}")
        if failed or needs_update:
            errors.append(f"draft errors remain: failed={failed}, needs_update={needs_update}")
        if len(ids) != len(set(ids)) or len(ids) != len(article_dirs):
            errors.append("draft identifiers are missing or not unique")

    summary = {
        "topics": len(topics),
        "articles": len(article_dirs),
        "expected_images": len(article_dirs) * 6,
        "saved_drafts": saved,
        "failed": failed,
        "needs_update": needs_update,
        "unique_draft_ids": len(set(ids)),
        "published": 0,
        "status": "failed" if errors else "passed",
        "errors": errors,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
