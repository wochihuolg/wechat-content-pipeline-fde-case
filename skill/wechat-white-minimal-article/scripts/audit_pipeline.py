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
PUBLIC_TITLE_PREFIX = "每天介绍一个 AI 产品："
MAX_PUBLIC_TITLE_LENGTH = 32


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def value_after_heading(text: str, heading: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() == heading:
            for value in lines[index + 1:]:
                value = value.strip()
                if value:
                    return value
    raise ValueError(f"missing {heading}")


def instruction_title(text: str) -> str:
    try:
        return value_after_heading(text, "## 公众号标题")
    except ValueError:
        for line in text.splitlines():
            if line.startswith("文章："):
                return line.removeprefix("文章：").strip()
        raise


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
    topic_by_slug = {
        str(item.get("slug")): item
        for item in topics
        if isinstance(item, dict) and item.get("slug")
    }
    title_map = read_json(series_dir / "公众号贴图短标题.json", {})
    article_dirs = sorted(
        path for path in series_dir.iterdir() if path.is_dir() and ARTICLE_DIR_RE.match(path.name)
    )
    manifest_path = series_dir / "公众号贴图草稿清单.json"
    manifest_payload = read_json(manifest_path, {})
    manifest_items = (
        manifest_payload.get("items", [])
        if isinstance(manifest_payload, dict)
        else []
    )
    manifest_by_article = {
        str(item.get("article_dir")): item
        for item in manifest_items
        if isinstance(item, dict) and item.get("article_dir")
    }
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
        article_title = None
        if instruction.exists():
            text = instruction.read_text(encoding="utf-8-sig")
            image_sections = len(re.findall(r"(?m)^###\s+图片\d+\s*$", text))
            if image_sections != 6:
                errors.append(f"{article_dir.name}: expected 6 image instructions, found {image_sections}")
            try:
                article_title = instruction_title(text)
                if (
                    not article_title.startswith(PUBLIC_TITLE_PREFIX)
                    or not article_title[len(PUBLIC_TITLE_PREFIX):].strip()
                ):
                    errors.append(f"{article_dir.name}: title does not use the complete column formula")
                if len(article_title) > MAX_PUBLIC_TITLE_LENGTH:
                    errors.append(f"{article_dir.name}: title exceeds 32 characters")
            except ValueError as exc:
                errors.append(f"{article_dir.name}: {exc}")
        topic = topic_by_slug.get(article_dir.name[3:])
        if topic and article_title:
            planned_title = topic.get("title") or topic.get("working_title")
            if planned_title != article_title or topic.get("compact_title") != article_title:
                errors.append(f"{article_dir.name}: topic plan title mismatch")
        if isinstance(title_map, dict) and article_title:
            if title_map.get(article_dir.name) != article_title:
                errors.append(f"{article_dir.name}: title map mismatch")
        manifest_item = manifest_by_article.get(article_dir.name)
        if manifest_item and article_title:
            if (
                manifest_item.get("original_title") != article_title
                or manifest_item.get("draft_title") != article_title
            ):
                errors.append(f"{article_dir.name}: draft manifest title mismatch")
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
    article_titles = {}
    for article_dir in article_dirs:
        instruction = article_dir / "贴图指令.md"
        if instruction.exists():
            try:
                article_titles[article_dir.name] = instruction_title(
                    instruction.read_text(encoding="utf-8-sig")
                )
            except ValueError:
                pass
    ids = [
        f"app:{item['appmsgid']}" if item.get("appmsgid") else f"media:{item['draft_media_id']}"
        for item in results
        if item.get("appmsgid") or item.get("draft_media_id")
    ]
    saved = sum(item.get("status") == "saved" for item in results)
    failed = sum(item.get("status") == "failed" for item in results)
    needs_update = sum(item.get("status") == "needs_update" for item in results)
    for item in results:
        label = str(item.get("article_dir") or "")
        expected_title = article_titles.get(label)
        if expected_title and item.get("draft_title") != expected_title:
            errors.append(f"{label}: draft result title mismatch")

    if not args.allow_local_only:
        if len(manifest_items) != len(article_dirs):
            errors.append(f"manifest/article count mismatch: {len(manifest_items)} != {len(article_dirs)}")
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
