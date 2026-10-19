"""字幕落盘、命名与冲突处理."""

from __future__ import annotations

import io
import threading
import uuid
from pathlib import Path

from .models import PlaceResult, SyncAdjustment
from .naming import build_subtitle_path, subtitle_extensions
from .sync import SyncUnavailable, apply_sync

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


class Placer:
    def place(
        self,
        backend,
        video_path: str,
        content: bytes,
        *,
        language: str,
        flags: list[str] | None,
        ext: str,
        suffix_style: str = "nexus",
        overwrite_policy: str = "skip",
        sync: SyncAdjustment | None = None,
        dry_run: bool = False,
    ) -> PlaceResult:
        target = build_subtitle_path(video_path, language, flags, ext, suffix_style)
        if dry_run:
            return PlaceResult(
                success=True,
                action="dry_run",
                language=language,
                target_path=target,
            )
        if backend.exists(target):
            if overwrite_policy == "skip":
                return PlaceResult(
                    success=True,
                    action="skipped",
                    language=language,
                    target_path=target,
                    reason="already_exists",
                )
            if overwrite_policy == "suffix":
                target = _next_available(backend, target)
        adjustment = sync or SyncAdjustment()
        payload = _to_utf8(content, ext)
        if ext.lower() in {".srt", ".ass", ".ssa", ".vtt"}:
            if not payload.strip():
                raise ValueError("字幕内容为空")
            if b"<html" in payload[:512].lower() or payload[:15].lower().startswith(b"<!doctype html"):
                raise ValueError("返回内容不是字幕")
        try:
            if not adjustment.is_noop():
                payload = apply_sync(payload, ext, adjustment)
        except SyncUnavailable:
            adjustment = SyncAdjustment()

        lock = _lock_for(target)
        with lock:
            temp = f"{target}.tmp-{uuid.uuid4().hex}"
            backend.mkdir(str(Path(target).parent), parents=True)
            backend.write_stream(temp, io.BytesIO(payload), size=len(payload))
            try:
                if overwrite_policy == "overwrite" and backend.exists(target):
                    backend.remove(target)
                backend.move(temp, target)
            finally:
                if backend.exists(temp):
                    try:
                        backend.remove(temp)
                    except Exception:  # noqa: BLE001, S110
                        pass
        action = "overwritten" if overwrite_policy == "overwrite" else "placed"
        return PlaceResult(
            success=True,
            action=action,
            language=language,
            target_path=target,
            sync_method=adjustment.method if not adjustment.is_noop() else "none",
            offset_ms=adjustment.offset_ms,
            rate=adjustment.rate,
            sync_confidence=adjustment.confidence,
        )

    @staticmethod
    def list_subtitles(backend, video_path: str) -> list[str]:
        base = str(video_path).rsplit(".", 1)[0]
        parent = str(Path(video_path).parent)
        result = []
        try:
            for info in backend.list_dir(parent):
                if info.is_dir:
                    continue
                name = Path(info.path).name
                stem, ext = Path(name).stem, Path(name).suffix.lower()
                if ext in subtitle_extensions() and (stem == Path(base).name or stem.startswith(Path(base).name + ".")):
                    result.append(info.path)
        except Exception:  # noqa: BLE001
            return []
        return result


def _to_utf8(content: bytes, ext: str) -> bytes:
    if ext.lower() not in {".srt", ".ass", ".ssa", ".vtt"}:
        return content
    for encoding in ("utf-8-sig", "gb18030", "big5"):
        try:
            return content.decode(encoding).encode("utf-8")
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace").encode("utf-8")


def _next_available(backend, target: str) -> str:
    stem, ext = str(Path(target).with_suffix("")), Path(target).suffix
    index = 1
    candidate = f"{stem}.{index}{ext}"
    while backend.exists(candidate):
        index += 1
        candidate = f"{stem}.{index}{ext}"
    return candidate


def _lock_for(key: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())
