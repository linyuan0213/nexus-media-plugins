"""媒体库全量字幕扫描/补全任务."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from app.utils import PathUtils

from .media_match import list_videos
from .sources.base import SourceError
from .subtitle_presence import detect_chinese_subtitles


@dataclass
class LibraryJob:
    id: str
    status: str = "queued"
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))
    started_at: str | None = None
    finished_at: str | None = None
    total: int = 0
    done: int = 0
    skipped: int = 0
    failed: int = 0
    current: str = ""
    dry_run: bool = False
    items: list[dict] = field(default_factory=list)
    results: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total": self.total,
            "done": self.done,
            "skipped": self.skipped,
            "failed": self.failed,
            "current": self.current,
            "dry_run": self.dry_run,
            "results": self.results[:200],
        }


class LibraryScanService:
    def __init__(self, subtitle_service, resolver, store, executor):
        self._service = subtitle_service
        self._resolver = resolver
        self._store = store
        self._executor = executor
        self._active_job_id: str | None = None
        self.recover_interrupted()

    def recover_interrupted(self) -> None:
        for job in self._store.list_jobs():
            if job.get("status") in ("queued", "running"):
                job["status"] = "interrupted"
                job["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                job["current"] = ""
                self._store.save_job(job)

    def active(self) -> dict | None:
        if self._active_job_id:
            job = self._get(self._active_job_id)
            if job and job.get("status") in ("queued", "running"):
                return {key: value for key, value in job.items() if key != "items"}
            self._active_job_id = None
        for job in self._store.list_jobs():
            if job.get("status") in ("queued", "running"):
                return {key: value for key, value in job.items() if key != "items"}
        return None

    def start(self, payload: dict) -> dict:
        active = self.active()
        if active:
            return active
        roots = self._select_roots(payload)
        if not roots:
            raise SourceError("没有可扫描的媒体库根目录")
        media_types = set(payload.get("media_types") or ["movie", "tv", "anime"])
        videos: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for root in roots:
            if root.media_type not in media_types:
                continue
            for video in list_videos(root.backend, root.path, recursive=True):
                key = (root.backend_id, video.path)
                if key in seen:
                    continue
                seen.add(key)
                videos.append(
                    {
                        "video_path": video.path,
                        "title": video.title,
                        "year": video.year,
                        "season": video.season,
                        "episode": video.episode,
                        "type": root.media_type,
                        "backend_id": root.backend_id,
                    }
                )
                limit = int(payload.get("limit") or 0)
                if limit and len(videos) >= limit:
                    break
            limit = int(payload.get("limit") or 0)
            if limit and len(videos) >= limit:
                break
        job = LibraryJob(
            id=uuid.uuid4().hex,
            total=len(videos),
            dry_run=bool(payload.get("dry_run", False)),
            items=videos,
        )
        self._store.save_job(job.to_dict() | {"items": job.items})
        self._active_job_id = job.id
        self._executor.submit(self.run, job.id)
        return job.to_dict()

    def run(self, job_id: str) -> None:
        job = self._get(job_id)
        if not job or job.get("status") == "cancelled":
            return
        job["status"] = "running"
        job["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._save(job)
        config = self._service.config()
        languages = list(config.get("languages") or ["zh-Hans"])
        logger = self._service._ctx
        total = job.get("total") or 0
        try:
            for index, item in enumerate(job.get("items") or [], start=1):
                current = self._get(job_id) or {}
                if current.get("status") == "cancelled":
                    job["status"] = "cancelled"
                    break
                job["current"] = item.get("video_path") or ""
                self._save(job)
                logger.info(f"全量字幕 {index}/{total} 开始: {job['current']}")
                item_started = time.monotonic()
                result = self._one(item, job.get("dry_run", False), languages, config)
                elapsed = time.monotonic() - item_started
                logger.info(
                    f"全量字幕 {index}/{total} {result.get('status')} "
                    f"({elapsed:.1f}s): {job['current']}"
                )
                job.setdefault("results", []).insert(0, result)
                job["results"] = job["results"][:200]
                status = result.get("status")
                if status == "done":
                    job["done"] += 1
                elif status == "skipped":
                    job["skipped"] += 1
                else:
                    job["failed"] += 1
                self._save(job)
                delay = float(config.get("library_delay_seconds") or 0)
                if delay > 0:
                    time.sleep(delay)
            if job.get("status") != "cancelled":
                job["status"] = "completed"
        except Exception as e:  # noqa: BLE001
            job["status"] = "failed"
            job.setdefault("results", []).insert(
                0, {"video_path": job.get("current"), "status": "failed", "reason": str(e)}
            )
        finally:
            job["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            job["current"] = ""
            self._save(job)
            if self._active_job_id == job.get("id"):
                self._active_job_id = None

    def cancel(self, job_id: str) -> dict:
        job = self._get(job_id)
        if not job:
            raise SourceError("任务不存在")
        if job.get("status") in ("queued", "running"):
            job["status"] = "cancelled"
            job["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self._save(job)
        return {key: value for key, value in job.items() if key != "items"}

    def get(self, job_id: str) -> dict | None:
        job = self._get(job_id)
        if not job:
            return None
        return {key: value for key, value in job.items() if key != "items"}

    def _one(self, item: dict, dry_run: bool, languages: list[str], config: dict) -> dict:
        path = item["video_path"]
        if dry_run:
            backend, _root = self._service._backend_for(path)
            presence = detect_chinese_subtitles(backend, path, config)
            return {
                "video_path": path,
                "status": "skipped" if presence.found else "done",
                "action": "exists" if presence.found else "would_download",
                "reason": presence.kind if presence.found else "",
            }
        try:
            results = self._service.download(
                {
                    "video_path": path,
                    "title": item.get("title"),
                    "year": item.get("year"),
                    "type": item.get("type"),
                    "season": item.get("season"),
                    "episode": item.get("episode"),
                    "languages": languages,
                }
            )
        except Exception as e:  # noqa: BLE001
            return {"video_path": path, "status": "failed", "reason": str(e)}
        if not results:
            return {"video_path": path, "status": "skipped", "reason": "no_candidate"}
        placed = [r for r in results if r.get("action") in ("placed", "overwritten")]
        if placed:
            return {
                "video_path": path,
                "status": "done",
                "action": "downloaded",
                "targets": [r.get("target_path") for r in placed],
            }
        return {
            "video_path": path,
            "status": "skipped",
            "action": results[0].get("action"),
            "reason": results[0].get("reason"),
        }

    def _select_roots(self, payload: dict) -> list:
        roots = self._resolver.roots() if self._resolver else []
        prefixes = [str(item) for item in (payload.get("roots") or []) if item]
        if not prefixes:
            return roots
        return [
            root
            for root in roots
            if any(
                PathUtils.is_path_in_path(prefix, root.path)
                or PathUtils.is_path_in_path(root.path, prefix)
                for prefix in prefixes
            )
        ]

    def _get(self, job_id: str) -> dict | None:
        return self._store.get_job(job_id)

    def _save(self, job: dict) -> None:
        self._store.save_job(job)
