"""插件数据目录 JSON 存储（历史/指纹/缓存）."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from typing import Any


class PluginStore:
    def __init__(self, data_dir: str, max_fingerprints: int = 5000):
        self._dir = data_dir
        self._max_fingerprints = max_fingerprints
        self._lock = threading.RLock()
        os.makedirs(self._dir, exist_ok=True)

    def _path(self, name: str) -> str:
        return os.path.join(self._dir, name)

    def _load(self, name: str, default: Any) -> Any:
        path = self._path(name)
        if not os.path.isfile(path):
            return default
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return default

    def _save(self, name: str, data: Any) -> None:
        path = self._path(name)
        tmp = f"{path}.{uuid.uuid4().hex}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    def add_history(self, item: dict[str, Any], limit: int = 200) -> None:
        with self._lock:
            history = self._load("history.json", [])
            history.insert(0, {**item, "time": time.strftime("%Y-%m-%d %H:%M:%S")})
            self._save("history.json", history[:limit])

    def history(self, page: int = 1, page_size: int = 50) -> dict[str, Any]:
        with self._lock:
            history = self._load("history.json", [])
        total = len(history)
        start = max(0, (max(1, page) - 1) * page_size)
        return {"items": history[start : start + page_size], "total": total}

    def is_duplicate(self, fingerprint: str) -> bool:
        with self._lock:
            values = self._load("fingerprints.json", [])
        return fingerprint in values

    def add_fingerprint(self, fingerprint: str) -> None:
        with self._lock:
            values = self._load("fingerprints.json", [])
            if fingerprint in values:
                return
            values.append(fingerprint)
            self._save("fingerprints.json", values[-self._max_fingerprints :])

    def save_job(self, job: dict[str, Any]) -> None:
        with self._lock:
            jobs = self._load("jobs.json", {})
            jobs[str(job["id"])] = job
            items = sorted(jobs.values(), key=lambda item: item.get("created_at", ""), reverse=True)[:50]
            self._save("jobs.json", {str(item["id"]): item for item in items})

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            return (self._load("jobs.json", {}) or {}).get(str(job_id))

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            return list((self._load("jobs.json", {}) or {}).values())

    def cache_get(self, key: str) -> Any:
        with self._lock:
            cache = self._load("cache.json", {})
        entry = cache.get(key)
        if not entry or entry.get("expires", 0) < time.time():
            return None
        return entry.get("value")

    def cache_set(self, key: str, value: Any, ttl: int = 3600) -> None:
        with self._lock:
            cache = self._load("cache.json", {})
            cache[key] = {"value": value, "expires": time.time() + ttl}
            self._save("cache.json", cache)
