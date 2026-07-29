#!/usr/bin/env python3
"""Build and validate a resumable WeChat sticker-draft manifest."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


ARTICLE_DIR_RE = re.compile(r"^\d{2}_")
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--series-dir", required=True, type=Path)
    parser.add_argument("--title-map", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--results",
        type=Path,
        help="Optional progress JSON; defaults to 公众号贴图草稿结果.json in the series directory",
    )
    return parser.parse_args()


def value_after_heading(text: str, heading: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() == heading:
            for value in lines[index + 1 :]:
                value = value.strip()
                if value:
                    return value
    raise ValueError(f"missing value after heading: {heading}")


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def result_index(results: Any) -> dict[str, dict[str, Any]]:
    if isinstance(results, dict) and "items" in results:
        results = results["items"]
    if not isinstance(results, list):
        raise ValueError("results JSON must be a list or an object containing an items list")
    indexed: dict[str, dict[str, Any]] = {}
    for item in results:
        if not isinstance(item, dict) or not item.get("article_dir"):
            raise ValueError("each result needs article_dir")
        indexed[str(item["article_dir"])] = item
    return indexed


def main() -> int:
    args = parse_args()
    series_dir = args.series_dir.resolve()
    title_map = read_json(args.title_map.resolve(), {})
    if not isinstance(title_map, dict):
        raise ValueError("title map must be a JSON object keyed by article directory")

    results_path = (args.results or series_dir / "公众号贴图草稿结果.json").resolve()
    results = result_index(read_json(results_path, []))
    errors: list[str] = []
    items: list[dict[str, Any]] = []

    article_dirs = sorted(
        path for path in series_dir.iterdir() if path.is_dir() and ARTICLE_DIR_RE.match(path.name)
    )
    for article_dir in article_dirs:
        instruction_path = article_dir / "贴图指令.md"
        if not instruction_path.exists():
            errors.append(f"{article_dir.name}: missing 贴图指令.md")
            continue

        try:
            text = instruction_path.read_text(encoding="utf-8-sig")
            original_title = value_after_heading(text, "## 公众号标题")
            description = value_after_heading(text, "## 公众号摘要")
        except (OSError, UnicodeError, ValueError) as exc:
            errors.append(f"{article_dir.name}: {exc}")
            continue

        draft_title = title_map.get(article_dir.name)
        if not isinstance(draft_title, str) or not draft_title.strip():
            errors.append(f"{article_dir.name}: missing compact title")
            continue
        draft_title = draft_title.strip()
        if len(draft_title) > 20:
            errors.append(f"{article_dir.name}: title has {len(draft_title)} characters")
        if len(description) > 1000:
            errors.append(f"{article_dir.name}: description has {len(description)} characters")

        image_dir = article_dir / "generated_images" / "贴图"
        images = (
            sorted(path.resolve() for path in image_dir.iterdir() if path.suffix.lower() in IMAGE_EXTENSIONS)
            if image_dir.exists()
            else []
        )
        if len(images) != 6:
            errors.append(f"{article_dir.name}: expected 6 images, found {len(images)}")

        progress = results.get(article_dir.name, {})
        items.append(
            {
                "index": int(article_dir.name[:2]),
                "article_dir": article_dir.name,
                "original_title": original_title,
                "draft_title": draft_title,
                "description": description,
                "image_paths": [str(path) for path in images],
                "status": progress.get("status", "pending"),
                "appmsgid": progress.get("appmsgid"),
                "draft_media_id": progress.get("draft_media_id"),
                "save_method": progress.get("save_method"),
                "saved_at": progress.get("saved_at"),
                "error": progress.get("error"),
            }
        )

    extra_titles = sorted(set(title_map) - {path.name for path in article_dirs})
    if extra_titles:
        errors.append("title map contains unknown directories: " + ", ".join(extra_titles))
    if errors:
        raise SystemExit("Manifest validation failed:\n- " + "\n- ".join(errors))

    manifest = {
        "schema_version": 1,
        "mode": "wechat_official_account_sticker_draft",
        "publish_allowed": False,
        "series_dir": str(series_dir),
        "count": len(items),
        "items": items,
    }
    args.output.resolve().write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Validated {len(items)} posts -> {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
