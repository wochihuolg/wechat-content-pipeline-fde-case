#!/usr/bin/env python3
"""Generate images from the two Markdown prompt formats used by _skill-gen."""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from io import BytesIO
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_ENV_FILE = SCRIPT_DIR / ".env"
DEFAULT_API_BASE = "https://api.apimart.ai"
DEFAULT_MODEL = "gpt-image-2"

# Product requirements: these values are intentionally not CLI-overridable.
IMAGE_PROFILES = {
    "wechat": {
        "prompt_file": "公众号文章配图中文指令.md",
        "output_dir": "公众号配图",
        "size": "2:1",
        "resolution": "2k",
        "heading": re.compile(r"(?m)^##\s+配图(\d+)(?:：([^\r\n]+))?\s*$"),
    },
    "sticker": {
        "prompt_file": "贴图指令.md",
        "output_dir": "贴图",
        "size": "3:4",
        "resolution": "2k",
        "heading": re.compile(r"(?m)^###\s+图片(\d+)\s*$"),
    },
}
WECHAT_DEFAULT_PIXELS = (1080, 558)
WECHAT_ALLOWED_PIXELS = {(1080, 558), (1080, 546)}

PRINT_LOCK = threading.Lock()


@dataclass(frozen=True)
class ImageTask:
    article_dir: Path
    profile: str
    index: int
    label: str
    prompt: str
    output_dir: Path
    size: str
    resolution: str
    target_pixels: Optional[Tuple[int, int]] = None

    @property
    def prompt_hash(self) -> str:
        return hashlib.sha256(self.prompt.encode("utf-8")).hexdigest()

    @property
    def key(self) -> str:
        return f"{self.article_dir.resolve()}::{self.profile}::{self.index:02d}"


def log(message: str) -> None:
    with PRINT_LOCK:
        try:
            print(message, flush=True)
        except OSError:
            # A detached/background process may lose its console handle on Windows.
            pass


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def sanitize_label(value: str) -> str:
    value = value.strip().replace("`", "") or "图片"
    value = re.sub(r"[<>:\"/\\|?*]+", "_", value)
    value = re.sub(r"\s+", "_", value)
    return value[:48].strip("._") or "图片"


def parse_fields(block: str) -> Dict[str, str]:
    fields: Dict[str, List[str]] = {}
    current: Optional[str] = None
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line or line == "---":
            continue
        match = re.match(r"^([^：]{1,30})：(.*)$", line)
        if match:
            current = match.group(1).strip()
            fields.setdefault(current, []).append(match.group(2).strip())
        elif current:
            fields[current].append(line)
    return {key: " ".join(part for part in values if part).strip() for key, values in fields.items()}


def extract_article_title(text: str) -> str:
    match = re.search(r"(?m)^文章：(.+?)\s*$", text)
    if match:
        return match.group(1).strip()
    match = re.search(r"(?m)^#\s+(.+?)\s*$", text)
    return match.group(1).strip() if match else "未识别文章"


def split_sections(text: str, heading: re.Pattern[str]) -> Iterable[Tuple[re.Match[str], str]]:
    matches = list(heading.finditer(text))
    for position, match in enumerate(matches):
        end = matches[position + 1].start() if position + 1 < len(matches) else len(text)
        yield match, text[match.end() : end].strip()


def build_wechat_prompt(title: str, index: int, label: str, fields: Dict[str, str]) -> str:
    parts = [
        f"为公众号文章《{title}》生成第 {index} 张横版编辑配图。",
        "固定规格：2:1 横版构图，2K 高清。",
        "这是一张编辑配图，不是真实软件运行截图。",
    ]
    for key in ("点题目标", "中文指令", "无字底图指令", "后期排字指令"):
        if fields.get(key):
            parts.append(f"{key}：{fields[key]}")
    if not any(fields.get(key) for key in ("中文指令", "无字底图指令", "后期排字指令")):
        for key, value in fields.items():
            if key not in {"对应位置", "配图类型"} and value:
                parts.append(f"{key}：{value}")
    if fields.get("避免"):
        parts.append(f"避免：{fields['避免']}")
    parts.append(f"配图角色：{label or '编辑配图'}。主体与标题在手机端必须清晰可读。")
    return "\n".join(parts)


def build_sticker_prompt(title: str, index: int, fields: Dict[str, str]) -> str:
    parts = [
        f"为公众号文章《{title}》生成第 {index} 张竖版贴图。",
        "固定规格：3:4 竖版构图，2K 高清。",
        "这是设计排版成图，图中文字和视觉元素应直接按以下要求呈现。",
    ]
    for key in ("定位", "布局描述", "文字内容", "视觉元素", "配色建议", "避免元素"):
        if fields.get(key):
            parts.append(f"{key}：{fields[key]}")
    parts.append("确保手机缩略图可读，主体、品牌热词和点题结论不互相遮挡。")
    return "\n".join(parts)


def extract_wechat_pixels(block: str) -> Tuple[int, int]:
    match = re.search(r"尺寸\s*[：:]\s*(\d+)\s*[x×]\s*(\d+)", block, re.IGNORECASE)
    pixels = (int(match.group(1)), int(match.group(2))) if match else WECHAT_DEFAULT_PIXELS
    if pixels not in WECHAT_ALLOWED_PIXELS:
        allowed = ", ".join(f"{width}x{height}" for width, height in sorted(WECHAT_ALLOWED_PIXELS))
        raise ValueError(f"公众号配图尺寸 {pixels[0]}x{pixels[1]} 不在固定白名单中：{allowed}")
    return pixels


def parse_prompt_file(article_dir: Path, profile: str) -> List[ImageTask]:
    config = IMAGE_PROFILES[profile]
    prompt_path = article_dir / str(config["prompt_file"])
    if not prompt_path.exists():
        return []
    text = prompt_path.read_text(encoding="utf-8-sig")
    title = extract_article_title(text)
    tasks: List[ImageTask] = []
    for match, block in split_sections(text, config["heading"]):
        index = int(match.group(1))
        fields = parse_fields(block)
        if profile == "wechat":
            label = (match.group(2) or fields.get("配图类型") or f"配图{index}").strip()
            prompt = build_wechat_prompt(title, index, label, fields)
            target_pixels: Optional[Tuple[int, int]] = extract_wechat_pixels(block)
        else:
            label = fields.get("定位", f"图片{index}")
            prompt = build_sticker_prompt(title, index, fields)
            target_pixels = None
        tasks.append(
            ImageTask(
                article_dir=article_dir,
                profile=profile,
                index=index,
                label=label,
                prompt=prompt,
                output_dir=article_dir / "generated_images" / str(config["output_dir"]),
                size=str(config["size"]),
                resolution=str(config["resolution"]),
                target_pixels=target_pixels,
            )
        )
    return tasks


def discover_article_dirs(root: Path, profiles: Iterable[str]) -> List[Path]:
    prompt_names = {str(IMAGE_PROFILES[name]["prompt_file"]) for name in profiles}
    directories = {
        path.parent.resolve()
        for name in prompt_names
        for path in root.rglob(name)
        if "generated_images" not in path.parts
    }
    return sorted(directories, key=lambda path: str(path).lower())


class Manifest:
    def __init__(self, path: Path, metadata: Dict[str, object]) -> None:
        self.path = path
        self.lock = threading.Lock()
        if path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self.data = {}
        else:
            self.data = {}
        self.data.setdefault("version", 1)
        self.data.setdefault("tasks", {})
        self.data.update(metadata)

    @contextlib.contextmanager
    def process_lock(self):
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def read_disk(self) -> Dict[str, object]:
        if not self.path.exists():
            return dict(self.data)
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else dict(self.data)
        except (json.JSONDecodeError, OSError):
            return dict(self.data)

    def get(self, key: str) -> Dict[str, object]:
        with self.lock:
            with self.process_lock():
                self.data = self.read_disk()
            value = self.data.get("tasks", {}).get(key, {})
            return dict(value) if isinstance(value, dict) else {}

    def update(self, key: str, values: Dict[str, object]) -> None:
        with self.lock:
            with self.process_lock():
                data = self.read_disk()
                data.setdefault("version", self.data.get("version", 1))
                for metadata_key, metadata_value in self.data.items():
                    if metadata_key not in {"tasks", "updated_at"}:
                        data.setdefault(metadata_key, metadata_value)
                tasks = data.setdefault("tasks", {})
                current = tasks.setdefault(key, {})
                current.update(values)
                data["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temp_path = self.path.with_suffix(
                    self.path.suffix + f".{os.getpid()}.{threading.get_ident()}.tmp"
                )
                temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                temp_path.replace(self.path)
                self.data = data


class ApiMartClient:
    def __init__(
        self,
        api_key: str,
        api_base: str,
        model: str,
        official_fallback: bool,
        max_attempts: int,
    ) -> None:
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.model = model
        self.official_fallback = official_fallback
        self.max_attempts = max_attempts

    def request_json(
        self, method: str, url: str, payload: Optional[Dict[str, object]] = None
    ) -> Dict[str, object]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        attempts = 3 if method == "GET" else 1
        last_error: Optional[Exception] = None
        for attempt in range(1, attempts + 1):
            request = urllib.request.Request(url, data=body, headers=headers, method=method)
            try:
                with urllib.request.urlopen(request, timeout=90) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                detail = error.read().decode("utf-8", errors="replace")
                last_error = RuntimeError(f"API HTTP {error.code}: {detail[:500]}")
                retryable = method == "GET" and (error.code == 429 or error.code >= 500)
                if not retryable or attempt == attempts:
                    raise last_error from error
            except urllib.error.URLError as error:
                last_error = RuntimeError(f"API 网络错误：{error.reason}")
                if method != "GET" or attempt == attempts:
                    raise last_error from error
            time.sleep(1.5 * attempt)
        raise last_error or RuntimeError("API 请求失败")

    def submit(self, task: ImageTask) -> str:
        payload: Dict[str, object] = {
            "model": self.model,
            "prompt": task.prompt,
            "n": 1,
            "size": task.size,
            "resolution": task.resolution,
        }
        if self.official_fallback:
            payload["official_fallback"] = True
        response = self.request_json("POST", f"{self.api_base}/v1/images/generations", payload)
        if response.get("error"):
            raise RuntimeError(f"API 提交失败：{response['error']}")
        data = response.get("data")
        first = data[0] if isinstance(data, list) and data else None
        task_id = first.get("task_id") if isinstance(first, dict) else None
        if not task_id:
            raise RuntimeError(f"API 返回缺少 task_id：{json.dumps(response, ensure_ascii=False)[:500]}")
        return str(task_id)

    def poll(self, task_id: str) -> Tuple[str, Dict[str, object]]:
        time.sleep(1.5)
        last_status = "submitted"
        for attempt in range(1, self.max_attempts + 1):
            response = self.request_json("GET", f"{self.api_base}/v1/tasks/{task_id}")
            task_data = response.get("data") if isinstance(response.get("data"), dict) else response
            status = str(task_data.get("status", "unknown"))
            last_status = status
            if status == "completed":
                result = task_data.get("result")
                images = result.get("images") if isinstance(result, dict) else None
                first = images[0] if isinstance(images, list) and images else None
                url = first.get("url") if isinstance(first, dict) else None
                if isinstance(url, list):
                    url = url[0] if url else None
                if not url:
                    raise RuntimeError("任务已完成，但返回中没有图片 URL")
                return str(url), task_data
            if status == "failed":
                raise RuntimeError(f"图片生成失败：{json.dumps(task_data.get('error'), ensure_ascii=False)}")
            time.sleep(self.poll_delay(attempt, status))
        raise RuntimeError(f"轮询超时，最后状态：{last_status}，task_id={task_id}")

    @staticmethod
    def poll_delay(attempt: int, status: str) -> float:
        if status in {"pending", "submitted"}:
            return 2.0 if attempt <= 10 else 3.0 if attempt <= 60 else 4.0
        if status == "processing":
            return 2.2 if attempt <= 20 else 2.8 if attempt <= 60 else 3.5
        return 2.5 if attempt <= 20 else 3.5


def extension_for(url: str, content_type: str) -> str:
    lowered = content_type.lower()
    if "jpeg" in lowered:
        return ".jpg"
    for extension in ("png", "webp", "gif"):
        if extension in lowered:
            return f".{extension}"
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    return suffix if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"} else ".png"


def download_image(url: str, task: ImageTask) -> Path:
    request = urllib.request.Request(url, headers={"User-Agent": "skill-gen-image-pipeline/1.0"})
    with urllib.request.urlopen(request, timeout=180) as response:
        content_type = response.headers.get("Content-Type", "")
        payload = response.read()
    if not payload:
        raise RuntimeError("图片下载结果为空")
    if content_type and not content_type.lower().startswith("image/"):
        raise RuntimeError(f"下载结果不是图片：{content_type}")
    task.output_dir.mkdir(parents=True, exist_ok=True)
    extension = extension_for(url, content_type)
    if task.target_pixels:
        extension = ".png"
    filename = f"{task.index:02d}_{sanitize_label(task.label)}{extension}"
    destination = task.output_dir / filename
    temp_path = destination.with_suffix(destination.suffix + ".tmp")
    if task.target_pixels:
        try:
            from PIL import Image, ImageOps
        except ImportError as error:
            raise RuntimeError("公众号图片定尺需要 Pillow，请运行：python -m pip install -r requirements-image.txt") from error
        with Image.open(BytesIO(payload)) as image:
            fitted = ImageOps.fit(image.convert("RGB"), task.target_pixels, method=Image.Resampling.LANCZOS)
            fitted.save(temp_path, format="PNG", optimize=True)
    else:
        temp_path.write_bytes(payload)
    temp_path.replace(destination)
    for old_file in task.output_dir.glob(f"{task.index:02d}_*.*"):
        if old_file != destination and not old_file.name.endswith(".tmp"):
            old_file.unlink()
    return destination


def existing_files(task: ImageTask) -> List[Path]:
    if not task.output_dir.exists():
        return []
    return [path for path in task.output_dir.glob(f"{task.index:02d}_*.*") if path.is_file()]


def validate_existing_image(task: ImageTask, path: Path) -> bool:
    try:
        from PIL import Image

        with Image.open(path) as image:
            width, height = image.size
        if task.target_pixels:
            return (width, height) == task.target_pixels
        return width * 4 == height * 3 and max(width, height) >= 2000
    except (ImportError, OSError):
        return False


def repair_manifest(tasks: List[ImageTask], manifest: Manifest) -> Tuple[int, int]:
    repaired = 0
    missing = 0
    for task in tasks:
        files = sorted(existing_files(task))
        valid_file = next((path for path in files if validate_existing_image(task, path)), None)
        if not valid_file:
            missing += 1
            continue
        manifest.update(
            task.key,
            {
                "article_dir": str(task.article_dir.resolve()),
                "profile": task.profile,
                "index": task.index,
                "label": task.label,
                "size": task.size,
                "resolution": task.resolution,
                "target_pixels": list(task.target_pixels) if task.target_pixels else None,
                "prompt_hash": task.prompt_hash,
                "prompt": task.prompt,
                "status": "completed",
                "output_file": str(valid_file.resolve()),
                "recovered_from_existing_file": True,
                "error": None,
            },
        )
        repaired += 1
    return repaired, missing


def run_task(task: ImageTask, client: ApiMartClient, manifest: Manifest) -> bool:
    base_record = {
        "article_dir": str(task.article_dir.resolve()),
        "profile": task.profile,
        "index": task.index,
        "label": task.label,
        "size": task.size,
        "resolution": task.resolution,
        "target_pixels": list(task.target_pixels) if task.target_pixels else None,
        "prompt_hash": task.prompt_hash,
        "prompt": task.prompt,
    }
    manifest.update(
        task.key,
        {
            **base_record,
            "status": "submitting",
            "task_id": None,
            "image_url": None,
            "output_file": None,
            "error": None,
        },
    )
    try:
        task_id = client.submit(task)
        manifest.update(task.key, {"status": "submitted", "task_id": task_id})
        image_url, task_data = client.poll(task_id)
        destination = download_image(image_url, task)
        manifest.update(
            task.key,
            {
                "status": "completed",
                "task_id": task_id,
                "image_url": image_url,
                "output_file": str(destination.resolve()),
                "completed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "cost": task_data.get("cost"),
            },
        )
        log(f"[完成] {task.article_dir.name} / {task.profile} / {task.index:02d} -> {destination.name}")
        return True
    except Exception as error:  # noqa: BLE001 - preserve per-task failures in the manifest.
        manifest.update(task.key, {**base_record, "status": "failed", "error": str(error)})
        log(f"[失败] {task.article_dir.name} / {task.profile} / {task.index:02d}: {error}")
        return False


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从公众号配图/贴图 Markdown 指令自动生成图片。")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--batch-dir", type=Path, help="包含多个文章目录的批次目录")
    target.add_argument("--article-dir", type=Path, help="单篇文章目录")
    parser.add_argument("--type", choices=("all", "wechat", "sticker"), default="all")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--concurrency", type=int, default=None, help="并发任务数，默认读取环境变量，最大 3")
    parser.add_argument("--max-attempts", type=int, default=240)
    parser.add_argument("--dry-run", action="store_true", help="只解析并展示任务，不调用 API")
    parser.add_argument("--repair-manifest", action="store_true", help="根据已落盘图片修复记录，不调用 API")
    parser.add_argument("--force", action="store_true", help="即使已有同编号图片也重新生成")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 个任务，0 表示不限制")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = parse_args(argv)
    load_env(args.env_file.resolve())
    profiles = ["wechat", "sticker"] if args.type == "all" else [args.type]
    root = (args.batch_dir or args.article_dir).resolve()
    if not root.is_dir():
        raise SystemExit(f"目标目录不存在：{root}")
    article_dirs = [root] if args.article_dir else discover_article_dirs(root, profiles)
    tasks = [task for article_dir in article_dirs for profile in profiles for task in parse_prompt_file(article_dir, profile)]
    profile_order = {"wechat": 0, "sticker": 1}
    tasks.sort(key=lambda task: (str(task.article_dir).lower(), profile_order[task.profile], task.index))
    if args.limit > 0:
        tasks = tasks[: args.limit]
    if not tasks:
        raise SystemExit("没有解析到图片任务，请检查 MD 文件名和标题格式。")

    log("固定图片参数：公众号配图=2:1/2K，贴图=3:4/2K")
    log(f"解析到 {len(article_dirs)} 个文章目录、{len(tasks)} 个图片任务。")
    if args.dry_run:
        for task in tasks:
            pixel_label = (
                f" / {task.target_pixels[0]}x{task.target_pixels[1]}"
                if task.target_pixels
                else " / API 2K 原图"
            )
            log(
                f"[DRY-RUN] {task.article_dir.name} / {task.profile} / {task.index:02d} "
                f"/ {task.size} / {task.resolution}{pixel_label}"
            )
        return 0
    api_base = os.getenv("APIMART_API_BASE", DEFAULT_API_BASE).strip() or DEFAULT_API_BASE
    model = os.getenv("APIMART_IMAGE_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    concurrency = args.concurrency or int(os.getenv("APIMART_CONCURRENCY", "2"))
    concurrency = max(1, min(3, concurrency))
    manifest_root = root if args.batch_dir else root.parent
    manifest = Manifest(
        manifest_root / "image_generation_manifest.json",
        {
            "api_base": api_base,
            "model": model,
            "profiles": {
                "wechat": {"size": "2:1", "resolution": "2k"},
                "sticker": {"size": "3:4", "resolution": "2k"},
            },
        },
    )
    if args.repair_manifest:
        repaired, missing = repair_manifest(tasks, manifest)
        log(f"manifest 修复完成：已恢复 {repaired}，缺少或尺寸不符 {missing}。")
        return 1 if missing else 0

    api_key = os.getenv("APIMART_API_KEY", "").strip()
    if not api_key:
        raise SystemExit(f"缺少 APIMART_API_KEY，请填写：{args.env_file.resolve()}")
    client = ApiMartClient(
        api_key=api_key,
        api_base=api_base,
        model=model,
        official_fallback=env_bool("APIMART_OFFICIAL_FALLBACK"),
        max_attempts=args.max_attempts,
    )

    runnable: List[ImageTask] = []
    skipped = 0
    for task in tasks:
        record = manifest.get(task.key)
        files = existing_files(task)
        recorded_output = Path(str(record.get("output_file", ""))) if record.get("output_file") else None
        recovered = (
            record.get("prompt_hash") == task.prompt_hash
            and recorded_output is not None
            and recorded_output.is_file()
        )
        if recovered and record.get("status") != "completed":
            manifest.update(
                task.key,
                {
                    "status": "completed",
                    "recovered_from_existing_file": True,
                    "error": None,
                },
            )
        is_current = (
            (record.get("status") == "completed" or recovered)
            and record.get("prompt_hash") == task.prompt_hash
            and files
        )
        if not args.force and (is_current or (files and not record)):
            skipped += 1
            log(f"[跳过] {task.article_dir.name} / {task.profile} / {task.index:02d} 已有图片")
            continue
        runnable.append(task)

    if not runnable:
        log(f"无需生成：全部 {skipped} 个任务已有结果。")
        return 0

    log(f"开始调用 APIMart：待生成 {len(runnable)}，跳过 {skipped}，并发 {concurrency}。")
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        results = list(executor.map(lambda task: run_task(task, client, manifest), runnable))
    succeeded = sum(1 for result in results if result)
    failed = len(results) - succeeded
    log(f"生成结束：成功 {succeeded}，失败 {failed}，跳过 {skipped}。")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
