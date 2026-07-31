#!/usr/bin/env python3
"""Orchestrate the source-to-WeChat-draft workflow with explicit safety gates."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent


def run(arguments: list[str]) -> None:
    command = [sys.executable, *arguments]
    print("+ " + subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, check=True)


def content_args(args: argparse.Namespace, stage: str) -> list[str]:
    command = [
        str(SCRIPT_DIR / "plan_and_write.py"),
        "--stage",
        stage,
        "--series-dir",
        str(args.series_dir),
        "--count",
        str(args.count),
        "--audience",
        args.audience,
        "--positioning",
        args.positioning,
    ]
    for source in args.source or []:
        command.extend(["--source", str(source)])
    if args.env_file:
        command.extend(["--env-file", str(args.env_file)])
    if args.limit:
        command.extend(["--limit", str(args.limit)])
    if args.force:
        command.append("--force")
    if args.dry_run:
        command.append("--dry-run")
    return command


def run_images(args: argparse.Namespace) -> None:
    command = [
        str(SCRIPT_DIR / "generate_prompt_images.py"),
        "--batch-dir",
        str(args.series_dir),
        "--type",
        "sticker",
    ]
    if args.env_file:
        command.extend(["--env-file", str(args.env_file)])
    if args.limit:
        command.extend(["--limit", str(args.limit)])
    if args.force:
        command.append("--force")
    if not args.execute_images:
        command.append("--dry-run")
    run(command)


def run_drafts(args: argparse.Namespace) -> None:
    series_dir = args.series_dir.resolve()
    title_map = series_dir / "公众号贴图短标题.json"
    manifest = series_dir / "公众号贴图草稿清单.json"
    run(
        [
            str(SCRIPT_DIR / "build_manifest.py"),
            "--series-dir",
            str(series_dir),
            "--title-map",
            str(title_map),
            "--output",
            str(manifest),
        ]
    )
    command = [
        str(SCRIPT_DIR / "official_newspic_drafts.py"),
        "--manifest",
        str(manifest),
    ]
    if args.env_file:
        command.extend(["--env-file", str(args.env_file)])
    if args.limit:
        command.extend(["--limit", str(args.limit)])
    if not args.save_drafts:
        command.append("--dry-run")
    run(command)


def run_audit(args: argparse.Namespace) -> None:
    run([str(SCRIPT_DIR / "audit_pipeline.py"), "--series-dir", str(args.series_dir)])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "stage",
        choices=("topics", "content", "images", "drafts", "audit", "all"),
    )
    parser.add_argument("--source", type=Path, action="append")
    parser.add_argument("--series-dir", type=Path, required=True)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--audience", default="想快速判断 AI 产品是否适合自己工作的中文读者")
    parser.add_argument("--positioning", default="白底极简 AI 产品拆解：讲清它是什么、解决什么、适合谁和不适合什么")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--execute-images",
        action="store_true",
        help="Call the paid image API. Without this flag the image stage is a dry-run.",
    )
    parser.add_argument(
        "--save-drafts",
        action="store_true",
        help="Create WeChat drafts. Without this flag the draft stage is a dry-run.",
    )
    return parser.parse_args()


def require_sources(args: argparse.Namespace) -> None:
    if not args.source:
        raise SystemExit(f"Stage {args.stage} requires at least one --source.")


def main() -> int:
    args = parse_args()
    args.series_dir = args.series_dir.resolve()
    if args.env_file:
        args.env_file = args.env_file.resolve()

    if args.stage == "topics":
        require_sources(args)
        run(content_args(args, "plan"))
    elif args.stage == "content":
        require_sources(args)
        run(content_args(args, "write"))
    elif args.stage == "images":
        run_images(args)
    elif args.stage == "drafts":
        run_drafts(args)
    elif args.stage == "audit":
        run_audit(args)
    else:
        require_sources(args)
        run(content_args(args, "all"))
        run_images(args)
        if args.execute_images:
            run_drafts(args)
            if args.save_drafts:
                run_audit(args)
        else:
            print(
                "Stopped after image dry-run. Rerun with --execute-images after review; "
                "add --save-drafts only after account authorization."
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
