#!/usr/bin/env python3
"""Plan topics and write six-image WeChat posts from source documents."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_API_URL = "https://api.apimart.ai/v1/chat/completions"
DEFAULT_MODEL = "gpt-5.4"
SUPPORTED_SUFFIXES = {".pdf", ".md", ".markdown", ".txt"}
PUBLIC_TITLE_PREFIX = "每天介绍一个 AI 产品："
MAX_PUBLIC_TITLE_LENGTH = 32


def load_env(path: Path | None) -> None:
    if path is None or not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def compact_spaces(value: str) -> str:
    return " ".join(value.replace("\x00", " ").split())


def safe_slug(value: str, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return (slug[:48] or fallback).strip("_")


def read_pdf(path: Path) -> list[dict[str, Any]]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise SystemExit("PDF input requires pypdf: python -m pip install pypdf") from exc
    reader = PdfReader(str(path))
    chunks = []
    for page_number, page in enumerate(reader.pages, start=1):
        text = compact_spaces(page.extract_text() or "")
        if text:
            chunks.append(
                {
                    "ref": len(chunks) + 1,
                    "source": str(path.resolve()),
                    "location": f"page {page_number}",
                    "text": text[:6000],
                }
            )
    return chunks


def read_text(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8-sig")
    headings = list(re.finditer(r"(?m)^#{1,4}\s+(.+?)\s*$", text))
    chunks: list[dict[str, Any]] = []
    if headings:
        for index, match in enumerate(headings):
            end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
            body = compact_spaces(text[match.end() : end])
            if body:
                chunks.append(
                    {
                        "ref": 0,
                        "source": str(path.resolve()),
                        "location": match.group(1).strip(),
                        "text": body[:6000],
                    }
                )
    else:
        for offset in range(0, len(text), 5000):
            body = compact_spaces(text[offset : offset + 5000])
            if body:
                chunks.append(
                    {
                        "ref": 0,
                        "source": str(path.resolve()),
                        "location": f"characters {offset + 1}-{offset + len(body)}",
                        "text": body,
                    }
                )
    return chunks


def extract_sources(paths: list[Path]) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for path in paths:
        resolved = path.resolve()
        if not resolved.is_file():
            raise SystemExit(f"Source file does not exist: {resolved}")
        if resolved.suffix.lower() not in SUPPORTED_SUFFIXES:
            raise SystemExit(f"Unsupported source type: {resolved.suffix}")
        chunks.extend(read_pdf(resolved) if resolved.suffix.lower() == ".pdf" else read_text(resolved))
    for ref, chunk in enumerate(chunks, start=1):
        chunk["ref"] = ref
        chunk["sha256"] = hashlib.sha256(chunk["text"].encode("utf-8")).hexdigest()
    if not chunks:
        raise SystemExit("No readable source text was extracted.")
    return chunks


def api_chat(system_prompt: str, user_prompt: str, max_tokens: int) -> dict[str, Any]:
    api_key = (
        os.environ.get("CONTENT_API_KEY", "").strip()
        or os.environ.get("APIMART_API_KEY", "").strip()
    )
    if not api_key:
        raise SystemExit("Set CONTENT_API_KEY or APIMART_API_KEY in the local env file.")
    api_url = os.environ.get("CONTENT_API_URL", DEFAULT_API_URL).strip() or DEFAULT_API_URL
    model = os.environ.get("CONTENT_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "temperature": 0.7,
        "max_tokens": max_tokens,
    }
    request = urllib.request.Request(
        api_url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Content API returned HTTP {exc.code}: {detail}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Content API network request failed: {type(exc.reason).__name__}") from None
    data = payload.get("data", payload)
    raw = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not raw:
        raise RuntimeError("Content API returned an empty response.")
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise RuntimeError("Content API response did not contain a JSON object.")
    return json.loads(text[start : end + 1])


def source_catalog(chunks: list[dict[str, Any]]) -> str:
    lines = []
    for chunk in chunks:
        lines.append(
            f"[REF {chunk['ref']}] {Path(chunk['source']).name} / {chunk['location']}\n"
            f"{chunk['text'][:900]}"
        )
    return "\n\n".join(lines)[:50000]


def validate_topics(payload: dict[str, Any], count: int, max_ref: int) -> list[dict[str, Any]]:
    topics = payload.get("topics")
    if not isinstance(topics, list) or len(topics) != count:
        raise ValueError(f"Expected exactly {count} topics.")
    seen_titles: set[str] = set()
    seen_slugs: set[str] = set()
    validated: list[dict[str, Any]] = []
    for position, item in enumerate(topics, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Topic {position} must be an object.")
        title = str(item.get("title") or "").strip()
        compact_title = str(item.get("compact_title") or "").strip()
        slug = safe_slug(str(item.get("slug") or ""), f"topic_{position:02d}")
        refs = sorted({int(ref) for ref in item.get("source_refs") or [] if str(ref).isdigit()})
        if not title or not compact_title:
            raise ValueError(f"Topic {position} is missing title or compact_title.")
        if title != compact_title:
            raise ValueError(
                f"Topic {position} title and compact_title must be identical public titles."
            )
        if not title.startswith(PUBLIC_TITLE_PREFIX) or not title[len(PUBLIC_TITLE_PREFIX):].strip():
            raise ValueError(
                f"Topic {position} title must use {PUBLIC_TITLE_PREFIX}{{产品名}}."
            )
        if len(title) > MAX_PUBLIC_TITLE_LENGTH:
            raise ValueError(f"Topic {position} public title exceeds 32 characters.")
        title_key = re.sub(r"\W+", "", title.lower())
        if title_key in seen_titles or slug in seen_slugs:
            raise ValueError(f"Topic {position} duplicates an earlier topic.")
        if not refs or any(ref < 1 or ref > max_ref for ref in refs):
            raise ValueError(f"Topic {position} has invalid source_refs.")
        seen_titles.add(title_key)
        seen_slugs.add(slug)
        validated.append(
            {
                "index": position,
                "slug": slug,
                "title": title,
                "compact_title": compact_title,
                "pain_point": str(item.get("pain_point") or "").strip(),
                "promise": str(item.get("promise") or "").strip(),
                "deliverable": str(item.get("deliverable") or "").strip(),
                "source_refs": refs,
                "next_hook": str(item.get("next_hook") or "").strip(),
            }
        )
    return validated


def plan_topics(
    chunks: list[dict[str, Any]],
    count: int,
    audience: str,
    positioning: str,
) -> list[dict[str, Any]]:
    system_prompt = """You are a Chinese WeChat content strategist for a white-background
minimalist AI-product breakdown column. Build non-duplicative, source-grounded topics for native
six-image posts. Independently synthesize ideas; do not copy source wording, layouts, screenshots,
watermarks, or unsupported claims. Return JSON only."""
    schema = {
        "topics": [
            {
                "index": 1,
                "slug": "lowercase_english_slug",
                "title": "每天介绍一个 AI 产品：产品名",
                "compact_title": "每天介绍一个 AI 产品：产品名",
                "pain_point": "one concrete reader pain",
                "promise": "one promise only",
                "deliverable": "one reusable card/checklist",
                "source_refs": [1],
                "next_hook": "what the next post can open",
            }
        ]
    }
    prompt = f"""Create exactly {count} topics.

Account positioning: {positioning}
Audience: {audience}

Rules:
- Each topic must solve one distinct problem and cite 1-5 REF numbers.
- Favor concrete mistakes, decisions, checklists, and before/after judgment.
- Ensure the topic can support one cover plus five useful content images.
- title and compact_title must be exactly the same complete title in the form 每天介绍一个 AI 产品：产品名.
- Preserve the official product spelling and capitalization.
- The complete title must be at most 32 characters; never shorten it to AI产品：产品名.
- Do not claim that a tool was tested unless the source includes real evidence.

Return this shape:
{json.dumps(schema, ensure_ascii=False)}

Source catalog:
{source_catalog(chunks)}"""
    return validate_topics(api_chat(system_prompt, prompt, 12000), count, len(chunks))


def render_topic_matrix(topics: list[dict[str, Any]]) -> str:
    lines = [
        "# 选题矩阵",
        "",
        f"生成时间：{datetime.now().astimezone().isoformat(timespec='seconds')}",
        "",
        "| # | 选题 | 核心痛点 | 单一承诺 | 交付物 | 来源 |",
        "|---:|---|---|---|---|---|",
    ]
    for item in topics:
        refs = ", ".join(str(ref) for ref in item["source_refs"])
        values = [
            f"{item['index']:02d}",
            item["title"],
            item["pain_point"],
            item["promise"],
            item["deliverable"],
            refs,
        ]
        lines.append("| " + " | ".join(value.replace("|", "/") for value in values) + " |")
    return "\n".join(lines) + "\n"


def validate_article(payload: dict[str, Any]) -> dict[str, Any]:
    sections = payload.get("sections")
    images = payload.get("images")
    if not isinstance(sections, list) or len(sections) != 3:
        raise ValueError("Article must contain exactly three sections.")
    if not isinstance(images, list) or len(images) != 6:
        raise ValueError("Article must contain exactly six images.")
    for index, image in enumerate(images, start=1):
        if not isinstance(image, dict) or not image.get("text"):
            raise ValueError(f"Image {index} is missing display text.")
    return payload


def write_article(
    topic: dict[str, Any],
    chunks: list[dict[str, Any]],
    audience: str,
    positioning: str,
) -> dict[str, Any]:
    selected = [chunk for chunk in chunks if chunk["ref"] in topic["source_refs"]]
    evidence = "\n\n".join(
        f"[REF {chunk['ref']}] {Path(chunk['source']).name} / {chunk['location']}\n{chunk['text'][:2200]}"
        for chunk in selected
    )[:14000]
    system_prompt = """You are a Chinese WeChat writer and editorial designer for a white-background
minimalist AI-product breakdown column. Independently rewrite source-grounded knowledge as a
concise, practical article plus a six-image native post.
Do not copy source sentences, screenshots, layouts, author marks, or watermarks. Do not fabricate
tests, data, links, product behavior, or current facts. Return JSON only."""
    schema = {
        "summary": "60-100 Chinese characters",
        "source_summary": "source-grounded synthesis",
        "opening": ["short paragraph"],
        "sections": [
            {
                "number": "01",
                "heading": "section heading",
                "paragraphs": ["short paragraph"],
                "action_card": ["optional checklist line"],
            }
        ],
        "closing": ["action sentence", "next-post hook"],
        "rewrite_boundaries": ["fact or copyright boundary"],
        "images": [
            {
                "position": "cover or content",
                "layout": "specific editorial layout",
                "text": ["exact display text"],
                "visual": "supporting visual elements",
                "color": "color direction",
                "avoid": "forbidden elements",
            }
        ],
    }
    prompt = f"""Write this post.

Account positioning: {positioning}
Audience: {audience}
Title: {topic['title']}
Public title: {topic['title']}
Pain point: {topic['pain_point']}
Single promise: {topic['promise']}
Reader deliverable: {topic['deliverable']}
Next hook: {topic['next_hook']}

Requirements:
- Use a concrete error or work scene in the opening.
- Use exactly 01/02/03 sections and short spoken Chinese paragraphs.
- Keep the article around 450-750 Chinese characters and 8-10 short paragraphs.
- Explain what the product is, what it is not, the old workflow pain, the input-to-output action chain,
  the author's judgment, suitable users, and one explicit unsuitable boundary.
- Give one copyable card or checklist.
- End with an action sentence and the supplied next hook.
- Create exactly six 3:4 image specifications: one high-click cover and five content images.
- Image 1 is the cover; images 2-6 are overview, problem/change, core capability, suitable users, and summary.
- Use white backgrounds, black/gray type, generous whitespace, and at most one low-saturation accent color per image.
- Keep display text concise and readable on a phone.
- Use source facts only as knowledge clues; independently rewrite all wording.

Return this shape:
{json.dumps(schema, ensure_ascii=False)}

Evidence:
{evidence}"""
    return validate_article(api_chat(system_prompt, prompt, 12000))


def render_article_md(topic: dict[str, Any], payload: dict[str, Any]) -> str:
    lines = [f"# {topic['title']}", "", f"> {payload['summary']}", ""]
    lines.extend(str(value).strip() for value in payload.get("opening", []) if str(value).strip())
    lines.append("")
    for section in payload["sections"]:
        lines.extend(
            [
                f"## {section.get('number', '')} {section.get('heading', '')}".rstrip(),
                "",
            ]
        )
        lines.extend(
            str(value).strip() for value in section.get("paragraphs", []) if str(value).strip()
        )
        card = section.get("action_card") or []
        if isinstance(card, str):
            card = [card]
        if card:
            lines.extend(["", "> 可直接使用："])
            lines.extend(f"> - {str(value).strip()}" for value in card if str(value).strip())
        lines.append("")
    lines.extend(str(value).strip() for value in payload.get("closing", []) if str(value).strip())
    return "\n\n".join(line for line in lines if line is not None).replace("\n\n\n", "\n\n").strip() + "\n"


def render_sticker_md(topic: dict[str, Any], payload: dict[str, Any]) -> str:
    lines = [
        "# 贴图模式生图指令",
        "",
        f"文章：{topic['title']}",
        "",
        "## 公众号标题",
        topic["title"],
        "",
        "## 公众号摘要",
        payload["summary"],
        "",
        "## 贴图图片（共6张）",
        "",
    ]
    for index, image in enumerate(payload["images"], start=1):
        text = image.get("text") or []
        if isinstance(text, list):
            text = " / ".join(str(value).strip() for value in text if str(value).strip())
        lines.extend(
            [
                f"### 图片{index}",
                f"定位：{image.get('position', '')}",
                f"布局描述：{image.get('layout', '')}",
                f"文字内容：{text}",
                f"视觉元素：{image.get('visual', '')}",
                f"配色建议：{image.get('color', '')}",
                f"避免元素：{image.get('avoid', '')}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def render_source_card(topic: dict[str, Any], chunks: list[dict[str, Any]], payload: dict[str, Any]) -> str:
    indexed = {chunk["ref"]: chunk for chunk in chunks}
    lines = [
        "# 来源卡",
        "",
        f"- 选题：{topic['title']}",
        f"- 核心承诺：{topic['promise']}",
        f"- 来源索引：{', '.join(str(ref) for ref in topic['source_refs'])}",
        "",
        "## 来源位置",
        "",
    ]
    for ref in topic["source_refs"]:
        chunk = indexed[ref]
        lines.append(f"- REF {ref}: `{chunk['source']}` / {chunk['location']}")
    lines.extend(["", "## 独立提炼", "", str(payload.get("source_summary") or ""), "", "## 发布前核对", ""])
    boundaries = payload.get("rewrite_boundaries") or []
    lines.extend(f"- {value}" for value in boundaries)
    lines.extend(
        [
            "- 核对产品、价格、模型、入口和权限等时效信息。",
            "- 不使用原图、原截图、原排版或第三方水印。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("plan", "write", "all"), required=True)
    parser.add_argument("--source", type=Path, action="append", required=True)
    parser.add_argument("--series-dir", type=Path, required=True)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--audience", default="希望用 AI 改进真实工作流程的中文读者")
    parser.add_argument("--positioning", default="AI 提效实战：讲清问题、判断和可复制动作")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = parse_args()
    if args.count < 1 or args.count > 100:
        raise SystemExit("--count must be between 1 and 100.")
    load_env(args.env_file.resolve() if args.env_file else None)
    chunks = extract_sources(args.source)
    series_dir = args.series_dir.resolve()
    plan_path = series_dir / "topic_plan.json"

    if args.dry_run:
        print(
            f"DRY-RUN: stage={args.stage}, sources={len(args.source)}, "
            f"chunks={len(chunks)}, requested_topics={args.count}"
        )
        if args.stage in {"write", "all"} and plan_path.exists():
            plan = json.loads(plan_path.read_text(encoding="utf-8-sig"))
            print(f"DRY-RUN: would write {len(plan.get('topics') or [])} article directories")
        return 0

    if args.stage in {"plan", "all"}:
        topics = plan_topics(chunks, args.count, args.audience, args.positioning)
        series_dir.mkdir(parents=True, exist_ok=True)
        write_json(
            series_dir / "source_index.json",
            {"schema_version": 1, "chunks": chunks},
        )
        write_json(
            plan_path,
            {
                "schema_version": 1,
                "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "audience": args.audience,
                "positioning": args.positioning,
                "topics": topics,
            },
        )
        (series_dir / "选题矩阵.md").write_text(render_topic_matrix(topics), encoding="utf-8")
        print(f"Planned {len(topics)} topics -> {plan_path}")

    if args.stage in {"write", "all"}:
        if not plan_path.exists():
            raise SystemExit(f"Missing topic plan: {plan_path}")
        plan = json.loads(plan_path.read_text(encoding="utf-8-sig"))
        topics = plan.get("topics") or []
        if args.limit > 0:
            topics = topics[: args.limit]
        title_map: dict[str, str] = {}
        title_map_path = series_dir / "公众号贴图短标题.json"
        if title_map_path.exists():
            title_map = json.loads(title_map_path.read_text(encoding="utf-8-sig"))
        completed = 0
        for topic in topics:
            article_dir = series_dir / f"{int(topic['index']):02d}_{topic['slug']}"
            required = [
                article_dir / "来源卡.md",
                article_dir / "公众号终稿_图片标注版.md",
                article_dir / "贴图指令.md",
            ]
            if not args.force and all(path.exists() for path in required):
                title_map[article_dir.name] = topic["compact_title"]
                print(f"[skip] {article_dir.name}")
                completed += 1
                continue
            payload = write_article(topic, chunks, args.audience, args.positioning)
            article_dir.mkdir(parents=True, exist_ok=True)
            (article_dir / "来源卡.md").write_text(
                render_source_card(topic, chunks, payload), encoding="utf-8"
            )
            (article_dir / "公众号终稿_图片标注版.md").write_text(
                render_article_md(topic, payload), encoding="utf-8"
            )
            (article_dir / "贴图指令.md").write_text(
                render_sticker_md(topic, payload), encoding="utf-8"
            )
            title_map[article_dir.name] = topic["compact_title"]
            write_json(title_map_path, title_map)
            completed += 1
            print(f"[{completed}/{len(topics)}] wrote {article_dir.name}")
        print(f"Wrote or recovered {completed} article directories.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
