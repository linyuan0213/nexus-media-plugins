"""检测媒体库文件是否已有中文字幕（外挂/软字幕/硬字幕）."""

from __future__ import annotations

import base64
import io
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from app.infrastructure.ffmpeg import FfmpegProcessor
from app.infrastructure.ocr.core.task import RecognitionTask, TaskType
from app.infrastructure.ocr.recognizer import OcrRecognizer

from .naming import parse_language_and_flags, subtitle_extensions
from .placer import Placer

_CJK_PATTERN = r"[\u3400-\u9fff]"
_HARD_NAME_PATTERN = (
    r"(?i)(中字|中英|简繁|简体|繁体|中文|双语|内嵌|硬字幕|\bchs\b|\bcht\b|\bchi\b|\bzho\b|\bzh\b)"
)
_CHINESE_LANGS = {"zh-Hans", "zh-Hant", "mul"}
_CHINESE_TEXT = (
    "中",
    "简",
    "繁",
    "中文",
    "汉语",
    "普通话",
    "粤语",
    "双语",
    "中英",
    "chinese",
    "mandarin",
    "cantonese",
)


@dataclass
class SubtitlePresence:
    found: bool
    kind: str = "none"  # external/soft/hard_filename/hard_ocr
    details: list[str] = field(default_factory=list)


def detect_chinese_subtitles(backend, video_path: str, config: dict) -> SubtitlePresence:
    if check_external_subtitles(backend, video_path):
        return SubtitlePresence(True, "external")
    if check_soft_subtitles(video_path, backend):
        return SubtitlePresence(True, "soft")
    mode = str(config.get("hard_sub_detection") or "filename")
    if mode == "off":
        return SubtitlePresence(False)
    if check_hard_subtitle_filename(video_path):
        return SubtitlePresence(True, "hard_filename")
    if mode == "ocr" and check_hard_subtitles_by_ocr(video_path):
        return SubtitlePresence(True, "hard_ocr")
    return SubtitlePresence(False)


def check_external_subtitles(backend, video_path: str) -> bool:
    for path in Placer.list_subtitles(backend, video_path):
        if os.path.splitext(path)[1].lower() not in subtitle_extensions():
            continue
        language, _, _ = parse_language_and_flags(os.path.basename(path))
        if language in _CHINESE_LANGS:
            return True
        if _has_chinese_content(backend, path):
            return True
    return False


def check_soft_subtitles(video_path: str, backend=None) -> bool:
    if not shutil.which("ffprobe"):
        return False
    if os.path.isfile(video_path):
        return _probe_soft(video_path)
    if backend is not None:
        return _probe_remote_header(backend, video_path)
    return False


def _probe_remote_header(backend, video_path: str) -> bool:
    try:
        info = backend.stat(video_path)
        if info is None or info.is_dir:
            return False
        with tempfile.NamedTemporaryFile(
            suffix=os.path.splitext(video_path)[1] or ".mkv", delete=False
        ) as tmp:
            stream = backend.read_stream(video_path)
            try:
                remaining = min(int(info.size), 16 * 1024 * 1024)
                while remaining > 0:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    tmp.write(chunk)
                    remaining -= len(chunk)
            finally:
                stream.close()
            temp_path = tmp.name
        try:
            return _probe_soft(temp_path)
        finally:
            Path(temp_path).unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        return False


def _probe_soft(video_path: str) -> bool:
    try:
        data = FfmpegProcessor.get_video_metadata(video_path) or {}
    except Exception:  # noqa: BLE001
        return False
    for stream in data.get("streams") or []:
        tags = stream.get("tags") or {}
        values = [tags.get("language"), tags.get("title"), stream.get("codec_name")]
        text = " ".join(str(value or "") for value in values)
        code = str(tags.get("language") or "").lower().replace("_", "-")
        if code in {"zh", "chi", "zho", "zh-cn", "zh-tw", "zh-hans", "zh-hant", "chs", "cht"}:
            return True
        if any(key in text.lower() for key in _CHINESE_TEXT):
            return True
    return False


def check_hard_subtitle_filename(video_path: str) -> bool:
    return bool(re.search(_HARD_NAME_PATTERN, os.path.basename(video_path)))


def check_hard_subtitles_by_ocr(video_path: str) -> bool:
    if not os.path.isfile(video_path) or not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        return False
    try:
        duration = _duration(video_path)
        if not duration:
            return False
        recognizer = OcrRecognizer()
        with tempfile.TemporaryDirectory(prefix="subtitle-hard-") as tmp:
            for index, ratio in enumerate((0.2, 0.4, 0.6, 0.8)):
                target = os.path.join(tmp, f"frame-{index}.png")
                if not FfmpegProcessor.extract_frame(
                    video_path, target, max(0.0, duration * ratio)
                ):
                    continue
                with Image.open(target) as frame:
                    frame = frame.convert("RGB")
                    variants = [frame]
                    width, height = frame.size
                    variants.append(frame.crop((0, int(height * 0.3), width, height)))
                    for image in variants:
                        buffer = io.BytesIO()
                        image.save(buffer, format="PNG")
                        image_b64 = base64.b64encode(buffer.getvalue()).decode()
                        result = recognizer.recognize(
                            RecognitionTask(task_type=TaskType.TEXT_OCR, image_b64=image_b64)
                        )
                        if result.text and re.search(_CJK_PATTERN, result.text):
                            return True
    except Exception:  # noqa: BLE001
        return False
    return False


def _duration(video_path: str) -> float:
    try:
        data = FfmpegProcessor.get_video_metadata(video_path) or {}
        value = (data.get("format") or {}).get("duration")
        if value is None:
            for stream in data.get("streams") or []:
                value = stream.get("duration")
                if value is not None:
                    break
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _has_chinese_content(backend, path: str) -> bool:
    if os.path.splitext(path)[1].lower() not in {".srt", ".ass", ".ssa", ".vtt"}:
        return False
    try:
        stream = backend.read_stream(path)
        try:
            sample = stream.read(8192)
        finally:
            stream.close()
        text = sample.decode("utf-8", errors="ignore") + sample.decode("gb18030", errors="ignore")
        return bool(re.search(_CJK_PATTERN, text))
    except Exception:  # noqa: BLE001
        return False
