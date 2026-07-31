#!/usr/bin/env python3
"""Save validated image posts as WeChat Official Account newspic drafts."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import mimetypes
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests


API_ROOT = "https://api.weixin.qq.com"
TOKEN_ERRORS = {40001, 40014, 42001}
IMAGE_EXTENSIONS = {".bmp", ".png", ".jpg", ".jpeg", ".gif"}
MAX_IMAGE_BYTES = 10 * 1024 * 1024
SHANGHAI = ZoneInfo("Asia/Shanghai")
TITLE_IGNORED_CHARACTERS = "​‌‍⁠﻿"
UNICODE_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")


class WechatApiError(RuntimeError):
    """A sanitized WeChat API error that never includes credentials."""

    def __init__(self, operation: str, payload: dict[str, Any]) -> None:
        self.operation = operation
        self.errcode = int(payload.get("errcode", -1))
        self.errmsg = str(payload.get("errmsg", "unknown error"))
        if self.errcode == 40164:
            self.errmsg = "current egress IP is not in the official account whitelist"
        super().__init__(f"{operation} failed: [{self.errcode}] {self.errmsg}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--results", type=Path)
    parser.add_argument("--media-cache", type=Path)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--end", type=int, default=10_000)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--request-delay", type=float, default=0.2)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-verify", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    return parser.parse_args()


def now_iso() -> str:
    return datetime.now(SHANGHAI).isoformat(timespec="seconds")


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(path)


def load_env_file(path: Path | None) -> None:
    if path is None:
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"").strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def result_map(payload: Any) -> dict[str, dict[str, Any]]:
    items = payload.get("items", []) if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise ValueError("results must be a list or an object containing items")
    mapped: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict) or not item.get("article_dir"):
            raise ValueError("each result needs article_dir")
        mapped[str(item["article_dir"])] = item
    return mapped


def cache_map(payload: Any) -> dict[str, dict[str, Any]]:
    if not payload:
        return {}
    if not isinstance(payload, dict):
        raise ValueError("media cache must be a JSON object")
    entries = payload.get("media_by_sha256", {})
    if not isinstance(entries, dict):
        raise ValueError("media cache media_by_sha256 must be an object")
    return entries


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_title(value: Any) -> str:
    """Normalize harmless transformations made by the WeChat draft API."""
    title = str(value or "")
    title = UNICODE_ESCAPE.sub(lambda match: chr(int(match.group(1), 16)), title)
    for _ in range(3):
        decoded = html.unescape(title)
        if decoded == title:
            break
        title = decoded
    title = title.translate({ord(character): None for character in TITLE_IGNORED_CHARACTERS})
    return re.sub(r"\s+", " ", title.replace("\xa0", " ")).strip()


def title_key(value: Any) -> str:
    return re.sub(r"\s+", "", normalize_title(value))


def validate_item(item: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    label = str(item.get("article_dir", "unknown"))
    title = item.get("draft_title")
    content = item.get("description")
    paths = item.get("image_paths")
    if not isinstance(title, str) or not title.strip():
        errors.append(f"{label}: missing draft_title")
    elif len(title) > 32:
        errors.append(f"{label}: title exceeds 32 characters")
    if not isinstance(content, str):
        errors.append(f"{label}: description must be text")
    elif len(content.encode("utf-8")) > 2048:
        errors.append(f"{label}: description exceeds the 2 KiB newspic content limit")
    if not isinstance(paths, list) or len(paths) != 6:
        errors.append(f"{label}: expected exactly six image_paths")
        return errors
    for raw_path in paths:
        path = Path(raw_path)
        if not path.is_file():
            errors.append(f"{label}: missing image {path}")
        elif path.suffix.lower() not in IMAGE_EXTENSIONS:
            errors.append(f"{label}: unsupported image type {path.suffix}")
        elif path.stat().st_size > MAX_IMAGE_BYTES:
            errors.append(f"{label}: image exceeds 10 MiB: {path.name}")
    return errors


class WechatClient:
    def __init__(self, access_token: str | None, app_id: str | None, app_secret: str | None) -> None:
        if not access_token and not (app_id and app_secret):
            raise ValueError(
                "Set WECHAT_ACCESS_TOKEN or both WECHAT_APP_ID and WECHAT_APP_SECRET"
            )
        self._access_token = access_token
        self._app_id = app_id
        self._app_secret = app_secret
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "codex-wechat-newspic-drafts/1.0"})

    def token(self, force_refresh: bool = False) -> str:
        if self._access_token and not force_refresh:
            return self._access_token
        if not self._app_id or not self._app_secret:
            if self._access_token:
                return self._access_token
            raise ValueError("AppID/AppSecret are required to refresh the access token")
        try:
            response = self._session.get(
                f"{API_ROOT}/cgi-bin/token",
                params={
                    "grant_type": "client_credential",
                    "appid": self._app_id,
                    "secret": self._app_secret,
                },
                timeout=30,
            )
        except requests.RequestException as exc:
            raise RuntimeError(
                f"get access token network request failed: {type(exc).__name__}"
            ) from None
        response.raise_for_status()
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise WechatApiError("get access token", payload)
        self._access_token = str(token)
        return self._access_token

    def _json_call(
        self,
        operation: str,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        for attempt in range(2):
            try:
                request_kwargs: dict[str, Any] = {}
                if json_body is not None:
                    request_kwargs["data"] = json.dumps(
                        json_body, ensure_ascii=False
                    ).encode("utf-8")
                    request_kwargs["headers"] = {
                        "Content-Type": "application/json; charset=utf-8"
                    }
                response = self._session.request(
                    method,
                    f"{API_ROOT}{path}",
                    params={"access_token": self.token(force_refresh=attempt == 1)},
                    timeout=60,
                    **request_kwargs,
                )
            except requests.RequestException as exc:
                raise RuntimeError(
                    f"{operation} network request failed: {type(exc).__name__}"
                ) from None
            response.raise_for_status()
            response.encoding = "utf-8"
            payload = response.json()
            errcode = int(payload.get("errcode", 0))
            if errcode == 0:
                return payload
            if errcode in TOKEN_ERRORS and attempt == 0 and self._app_id and self._app_secret:
                continue
            raise WechatApiError(operation, payload)
        raise RuntimeError(f"{operation} failed after token refresh")

    def upload_permanent_image(self, path: Path) -> dict[str, Any]:
        for attempt in range(2):
            mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            try:
                with path.open("rb") as stream:
                    response = self._session.post(
                        f"{API_ROOT}/cgi-bin/material/add_material",
                        params={
                            "access_token": self.token(force_refresh=attempt == 1),
                            "type": "image",
                        },
                        files={"media": (path.name, stream, mime_type)},
                        timeout=120,
                    )
            except requests.RequestException as exc:
                raise RuntimeError(
                    f"upload {path.name} network request failed: {type(exc).__name__}"
                ) from None
            response.raise_for_status()
            payload = response.json()
            errcode = int(payload.get("errcode", 0))
            if errcode == 0 and payload.get("media_id"):
                return payload
            if errcode in TOKEN_ERRORS and attempt == 0 and self._app_id and self._app_secret:
                continue
            raise WechatApiError(f"upload {path.name}", payload)
        raise RuntimeError(f"upload {path.name} failed after token refresh")

    @staticmethod
    def newspic_article(item: dict[str, Any], media_ids: list[str]) -> dict[str, Any]:
        return {
            "article_type": "newspic",
            "title": item["draft_title"],
            "content": item["description"],
            "need_open_comment": 1,
            "only_fans_can_comment": 0,
            "image_info": {
                "image_list": [
                    {"image_media_id": media_id} for media_id in media_ids
                ]
            },
        }

    def add_newspic_draft(self, item: dict[str, Any], media_ids: list[str]) -> str:
        payload = {"articles": [self.newspic_article(item, media_ids)]}
        response = self._json_call("add newspic draft", "POST", "/cgi-bin/draft/add", json_body=payload)
        media_id = response.get("media_id")
        if not media_id:
            raise WechatApiError("add newspic draft", response)
        return str(media_id)

    def update_newspic_draft(
        self, draft_media_id: str, item: dict[str, Any], media_ids: list[str]
    ) -> None:
        self._json_call(
            "update newspic draft",
            "POST",
            "/cgi-bin/draft/update",
            json_body={
                "media_id": draft_media_id,
                "index": 0,
                "articles": self.newspic_article(item, media_ids),
            },
        )

    def get_draft(self, draft_media_id: str) -> dict[str, Any]:
        return self._json_call(
            "get draft",
            "POST",
            "/cgi-bin/draft/get",
            json_body={"media_id": draft_media_id},
        )

    @staticmethod
    def draft_articles(payload: dict[str, Any]) -> list[dict[str, Any]]:
        articles = payload.get("news_item") or payload.get("articles") or []
        return articles if isinstance(articles, list) else []

    def verify_draft(self, draft_media_id: str, expected_title: str) -> None:
        payload = self.get_draft(draft_media_id)
        articles = self.draft_articles(payload)
        if not articles:
            raise RuntimeError("draft verification returned no articles")
        article = articles[0]
        if UNICODE_ESCAPE.search(str(article.get("title") or "")):
            raise RuntimeError("draft verification found a literal Unicode escape in title")
        if UNICODE_ESCAPE.search(str(article.get("content") or "")):
            raise RuntimeError("draft verification found a literal Unicode escape in content")
        if title_key(article.get("title")) != title_key(expected_title):
            raise RuntimeError("draft verification title mismatch")
        article_type = article.get("article_type")
        if article_type not in (None, "newspic"):
            raise RuntimeError(f"draft verification type mismatch: {article_type}")

    def find_recent_draft(self, expected_title: str, count: int = 20) -> str | None:
        payload = self._json_call(
            "list drafts",
            "POST",
            "/cgi-bin/draft/batchget",
            json_body={"offset": 0, "count": count, "no_content": 0},
        )
        expected = title_key(expected_title)
        matches: list[tuple[int, str]] = []
        for item in payload.get("item") or []:
            if not isinstance(item, dict) or not item.get("media_id"):
                continue
            content = item.get("content") if isinstance(item.get("content"), dict) else {}
            articles = self.draft_articles(content)
            if articles and title_key(articles[0].get("title")) == expected:
                matches.append((int(item.get("update_time") or 0), str(item["media_id"])))
        if not matches:
            return None
        matches.sort(reverse=True)
        return matches[0][1]


def main() -> int:
    args = parse_args()
    manifest_path = args.manifest.resolve()
    manifest = read_json(manifest_path, {})
    if not isinstance(manifest, dict) or not isinstance(manifest.get("items"), list):
        raise ValueError("manifest must be an object containing items")
    if manifest.get("publish_allowed") is not False:
        raise ValueError("manifest must explicitly set publish_allowed to false")

    series_dir = Path(manifest.get("series_dir") or manifest_path.parent).resolve()
    results_path = (args.results or series_dir / "公众号贴图草稿结果.json").resolve()
    cache_path = (args.media_cache or series_dir / "公众号永久素材缓存.json").resolve()
    results = result_map(read_json(results_path, {"items": []}))
    media_cache = cache_map(read_json(cache_path, {}))

    selected: list[dict[str, Any]] = []
    for item in manifest["items"]:
        index = int(item["index"])
        if not args.start <= index <= args.end:
            continue
        prior = results.get(str(item["article_dir"]), {})
        if prior.get("status") == "saved" and (
            prior.get("appmsgid") or prior.get("draft_media_id")
        ):
            continue
        selected.append(item)
    if args.limit is not None:
        selected = selected[: args.limit]

    validation_errors = [error for item in selected for error in validate_item(item)]
    if validation_errors:
        raise SystemExit("Validation failed:\n- " + "\n- ".join(validation_errors))

    cache_hits = 0
    image_count = 0
    for item in selected:
        for raw_path in item["image_paths"]:
            image_count += 1
            if file_sha256(Path(raw_path)) in media_cache:
                cache_hits += 1
    print(
        f"Ready: {len(selected)} drafts, {image_count} images, "
        f"{cache_hits} cached permanent materials"
    )
    if args.dry_run or not selected:
        return 0

    load_env_file(args.env_file.resolve() if args.env_file else None)
    client = WechatClient(
        os.environ.get("WECHAT_ACCESS_TOKEN"),
        os.environ.get("WECHAT_APP_ID") or os.environ.get("WECHAT_APPID"),
        os.environ.get("WECHAT_APP_SECRET") or os.environ.get("WECHAT_APPSECRET"),
    )

    saved = 0
    failed = 0
    for position, item in enumerate(selected, start=1):
        label = str(item["article_dir"])
        try:
            previous = results.get(label, {})
            draft_media_id = previous.get("draft_media_id")
            recovered = False
            if draft_media_id and previous.get("status") in {
                "created_unverified",
                "needs_update",
            }:
                update_media_ids: list[str] = []
                for raw_path in item["image_paths"]:
                    cached = media_cache.get(file_sha256(Path(raw_path)))
                    if not cached or not cached.get("media_id"):
                        raise RuntimeError(
                            "existing draft is missing cached image materials; stopped before update"
                        )
                    update_media_ids.append(str(cached["media_id"]))
                client.update_newspic_draft(
                    str(draft_media_id), item, update_media_ids
                )
                recovered = True
            if not draft_media_id and previous.get("status") == "failed":
                draft_media_id = client.find_recent_draft(str(item["draft_title"]))
                if not draft_media_id:
                    raise RuntimeError(
                        "could not recover the previously created draft; stopped to avoid a duplicate"
                    )
                recovered_media_ids: list[str] = []
                for raw_path in item["image_paths"]:
                    cached = media_cache.get(file_sha256(Path(raw_path)))
                    if not cached or not cached.get("media_id"):
                        raise RuntimeError(
                            "recovered draft is missing cached image materials; stopped before update"
                        )
                    recovered_media_ids.append(str(cached["media_id"]))
                client.update_newspic_draft(
                    str(draft_media_id), item, recovered_media_ids
                )
                recovered = True
            if draft_media_id:
                if not args.no_verify:
                    client.verify_draft(str(draft_media_id), str(item["draft_title"]))
                results[label] = {
                    "article_dir": label,
                    "draft_title": item["draft_title"],
                    "appmsgid": previous.get("appmsgid"),
                    "draft_media_id": str(draft_media_id),
                    "save_method": "official_newspic_api",
                    "saved_at": now_iso(),
                    "status": "saved",
                    "error": None,
                }
                saved += 1
                action = "recovered" if recovered else "verified"
                print(f"[{position}/{len(selected)}] {action} {label}")
                continue

            media_ids: list[str] = []
            for raw_path in item["image_paths"]:
                path = Path(raw_path).resolve()
                digest = file_sha256(path)
                cached = media_cache.get(digest)
                if cached and cached.get("media_id"):
                    media_ids.append(str(cached["media_id"]))
                    continue
                response = client.upload_permanent_image(path)
                media_id = str(response["media_id"])
                media_cache[digest] = {
                    "media_id": media_id,
                    "url": response.get("url"),
                    "file_name": path.name,
                    "source_path": str(path),
                    "uploaded_at": now_iso(),
                }
                media_ids.append(media_id)
                atomic_write_json(
                    cache_path,
                    {"schema_version": 1, "media_by_sha256": media_cache},
                )
                if args.request_delay > 0:
                    time.sleep(args.request_delay)

            draft_media_id = client.add_newspic_draft(item, media_ids)
            results[label] = {
                "article_dir": label,
                "draft_title": item["draft_title"],
                "appmsgid": previous.get("appmsgid"),
                "draft_media_id": draft_media_id,
                "save_method": "official_newspic_api",
                "saved_at": None,
                "status": "created_unverified",
                "error": None,
            }
            ordered_results = [
                results[manifest_item["article_dir"]]
                for manifest_item in manifest["items"]
                if manifest_item["article_dir"] in results
            ]
            atomic_write_json(results_path, {"items": ordered_results})
            if not args.no_verify:
                client.verify_draft(draft_media_id, str(item["draft_title"]))
            results[label] = {
                "article_dir": label,
                "draft_title": item["draft_title"],
                "appmsgid": previous.get("appmsgid"),
                "draft_media_id": draft_media_id,
                "save_method": "official_newspic_api",
                "saved_at": now_iso(),
                "status": "saved",
                "error": None,
            }
            saved += 1
            print(f"[{position}/{len(selected)}] saved {label}")
        except Exception as exc:  # Record enough context to resume without leaking secrets.
            existing_draft_media_id = (
                str(draft_media_id) if draft_media_id else results.get(label, {}).get("draft_media_id")
            )
            results[label] = {
                "article_dir": label,
                "draft_title": item.get("draft_title"),
                "appmsgid": results.get(label, {}).get("appmsgid"),
                "draft_media_id": existing_draft_media_id,
                "save_method": "official_newspic_api",
                "saved_at": None,
                "status": "needs_update" if existing_draft_media_id else "failed",
                "error": str(exc),
            }
            failed += 1
            print(f"[{position}/{len(selected)}] failed {label}: {exc}")
            if not args.continue_on_error:
                ordered_results = [
                    results[item["article_dir"]]
                    for item in manifest["items"]
                    if item["article_dir"] in results
                ]
                atomic_write_json(results_path, {"items": ordered_results})
                print("Stopped after the first failure; rerun to resume.")
                return 1
        finally:
            ordered_results = [
                results[manifest_item["article_dir"]]
                for manifest_item in manifest["items"]
                if manifest_item["article_dir"] in results
            ]
            atomic_write_json(results_path, {"items": ordered_results})
        if args.request_delay > 0:
            time.sleep(args.request_delay)

    print(f"Finished: saved={saved}, failed={failed}, published=0")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
