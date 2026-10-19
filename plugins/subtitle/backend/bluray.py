"""蓝光目录主视频定位."""

from __future__ import annotations

import posixpath


def is_bluray_directory(backend, directory: str) -> bool:
    """目录是否包含 BDMV（蓝光原盘）."""
    try:
        for entry in backend.list_dir(directory):
            if entry.is_dir and posixpath.basename(entry.path).lower() == "bdmv":
                return True
    except Exception:  # noqa: BLE001
        return False
    return False


def find_bluray_main(backend, directory: str) -> str | None:
    """在蓝光目录中找主视频：优先 BDMV/STREAM 下最大的 m2ts."""
    candidates = []
    stream_dir = posixpath.join(directory, "BDMV", "STREAM")
    for target in (stream_dir, directory):
        try:
            entries = list(backend.list_dir(target))
        except Exception:  # noqa: BLE001
            continue
        for entry in entries:
            if entry.is_dir:
                continue
            ext = posixpath.splitext(entry.path)[1].lower()
            if ext in {".m2ts", ".mts", ".mkv", ".mp4", ".iso"}:
                candidates.append((int(getattr(entry, "size", 0) or 0), entry.path))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]
