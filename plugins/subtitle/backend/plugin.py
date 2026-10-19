"""字幕插件入口：hook、API、Agent 工具与线程池."""

from __future__ import annotations

import posixpath
from concurrent.futures import ThreadPoolExecutor

import log

from .bluray import find_bluray_main, is_bluray_directory
from .config import load_config
from .library_scan import LibraryScanService
from .service import SubtitleService


class SubtitlePlugin:
    def __init__(
        self,
        context,
        mediaserver=None,
        media_service=None,
        filetransfer=None,
        event_bus=None,
        app_context=None,
    ):
        self._ctx = context
        self._mediaserver = mediaserver
        self._media_service = media_service
        self._filetransfer = filetransfer
        self._event_bus = event_bus
        self._app_context = app_context
        self._service = SubtitleService(
            context,
            app_context=app_context,
            mediaserver=mediaserver,
            media_service=media_service,
        )
        self._library: LibraryScanService | None = None
        self._executor: ThreadPoolExecutor | None = None

    def on_enable(self) -> None:
        config = load_config(self._ctx)
        workers = max(1, int(config.get("max_workers") or 2))
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="subtitle")
        self._library = LibraryScanService(
            self._service,
            self._service._resolver,
            self._service._store,
            self._executor,
        )
        self._register_apis()
        self._schedule_auto_scan()

    def on_disable(self) -> None:
        self._ctx.remove_schedule("library_scan")
        self._ctx.unregister_all_apis()
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    def on_hook(self, event: str, data: dict) -> None:
        if event == "plugin.config_changed":
            if data.get("plugin_id") in (None, self._ctx.plugin_id):
                self._schedule_auto_scan()
            return
        if event == "subtitle.download":
            if data.get("bluray") or not data.get("file"):
                return
            if self._executor is not None:
                self._executor.submit(self._auto_download, dict(data))
            return
        if event == "media.transfer_finished":
            target = str(data.get("target_path") or "")
            if not target or not self._service._resolver:
                return
            try:
                backend, _root = self._service._backend_for(target)
                info = backend.stat(target)
            except Exception:  # noqa: BLE001
                return
            if info is None or not info.is_dir:
                return
            if not is_bluray_directory(backend, target):
                return
            if self._executor is not None:
                self._executor.submit(self._auto_bluray, dict(data), target)

    def agent_tool(self, name: str, arguments: dict):
        try:
            if name == "subtitle_search":
                return {"success": True, "data": self._service.search(arguments or {})}
            if name == "subtitle_download":
                return {"success": True, "data": self._service.download(arguments or {})}
            return {"success": False, "error": f"未知工具: {name}"}
        except Exception as e:  # noqa: BLE001
            log.error(f"[Subtitle] Agent 工具 {name} 失败: {e}")
            return {"success": False, "error": str(e)}

    # ---------- hook task ----------

    def _auto_download(self, data: dict) -> None:
        config = load_config(self._ctx)
        if not config.get("auto_on_transfer"):
            return
        self._auto_for_path(data, str(data.get("file") or ""), config)

    def _auto_bluray(self, data: dict, directory: str) -> None:
        config = load_config(self._ctx)
        if not config.get("auto_on_transfer"):
            return
        try:
            backend, _root = self._service._backend_for(directory)
        except Exception:  # noqa: BLE001
            return
        main = find_bluray_main(backend, directory)
        if not main:
            self._service._store.add_history(
                {"action": "skipped", "reason": "bluray_main_not_found", "target": directory}
            )
            return
        self._auto_for_path(data, main, config)

    def _auto_for_path(self, data: dict, path: str, config: dict) -> None:
        info = data.get("media_info") or {}
        query = self._service._query_from_event(info, config)
        query.video_path = path
        try:
            _backend, root = self._service._backend_for(query.video_path)
        except Exception as e:  # noqa: BLE001
            self._service._store.add_history(
                {"action": "skipped", "reason": f"backend_unavailable: {e}", "target": query.video_path}
            )
            return
        if not self._service._allowed(query.video_path, config, posixpath.dirname(query.video_path)):
            self._service._store.add_history(
                {"action": "skipped", "reason": "outside_whitelist", "target": query.video_path}
            )
            return
        payload = {
            "video_path": query.video_path,
            "title": query.title,
            "year": query.year,
            "type": query.media_type,
            "season": query.season,
            "episode": query.episode,
            "languages": query.languages,
        }
        try:
            results = self._service.download(payload)
        except Exception as e:  # noqa: BLE001
            log.warn(f"[Subtitle] 自动下载失败 {query.title}: {e}")
            self._service._store.add_history(
                {"title": query.title, "action": "failed", "reason": str(e), "target": query.video_path}
            )
            return
        for result in results:
            language = str(result.get("language") or "")
            if result.get("action") not in ("placed", "overwritten"):
                continue
            fingerprint = (
                f"{query.tmdb_id}:{query.media_type}:{query.season}:{query.episode}:"
                f"{language}:{root.backend_id}"
            )
            self._service._store.add_fingerprint(fingerprint)
            self._notify(query.title, language, result.get("target_path"))

    def _schedule_auto_scan(self) -> None:
        config = load_config(self._ctx)
        if not config.get("auto_scan_enabled") or not config.get("auto_scan_cron"):
            return
        self._ctx.remove_schedule("library_scan")
        try:
            self._ctx.schedule_cron(
                "library_scan",
                self._scheduled_scan,
                cron=str(config.get("auto_scan_cron")),
            )
        except Exception as e:  # noqa: BLE001
            log.warn(f"[Subtitle] 注册全量扫描定时任务失败: {e}")

    def _scheduled_scan(self) -> None:
        if self._library is None:
            return
        try:
            self._library.start({"dry_run": False})
            self._ctx.notify("字幕全量任务已启动", "定时扫描媒体库缺失字幕")
        except Exception as e:  # noqa: BLE001
            log.warn(f"[Subtitle] 定时全量扫描失败: {e}")

    def _notify(self, title: str, language: str, target: str | None) -> None:
        try:
            self._ctx.notify(f"字幕已下载：{title}", f"{language} → {target or ''}")
        except Exception:  # noqa: BLE001, S110
            pass

    # ---------- APIs ----------

    def _register_apis(self) -> None:
        self._ctx.register_api("search", self._api_search)
        self._ctx.register_api("match", self._api_match)
        self._ctx.register_api("download", self._api_download)
        self._ctx.register_api("upload", self._api_upload)
        self._ctx.register_api("sync_detect", self._api_sync_detect)
        self._ctx.register_api("locate", self._api_locate)
        self._ctx.register_api("rename", self._api_rename)
        self._ctx.register_api("list_targets", self._api_list_targets)
        self._ctx.register_api("history", self._api_history, methods=("GET",))
        self._ctx.register_api("test_source", self._api_test_source, methods=("GET",))
        self._ctx.register_api("library/scan", self._api_library_scan)
        self._ctx.register_api("library/job", self._api_library_job, methods=("GET",))
        self._ctx.register_api("library/cancel", self._api_library_cancel)

    def _api_search(self, params: dict):
        return self._service.search(params or {})

    def _api_match(self, params: dict):
        return self._service.match(params or {})

    def _api_download(self, params: dict):
        return self._service.download(params or {})

    def _api_upload(self, params: dict):
        return self._service.upload(params or {})

    def _api_sync_detect(self, params: dict):
        return self._service.sync_detect(params or {})

    def _api_locate(self, params: dict):
        return self._service.locate(params or {})

    def _api_rename(self, params: dict):
        return self._service.rename(params or {})

    def _api_list_targets(self, params: dict):
        return self._service.list_targets(params or {})

    def _api_history(self, params: dict):
        return self._service.history(params or {})

    def _api_test_source(self, _params: dict):
        return self._service.test_source()

    def _api_library_scan(self, params: dict):
        if self._library is None:
            raise RuntimeError("插件尚未启用")
        return self._library.start(params or {})

    def _api_library_job(self, params: dict):
        if self._library is None:
            raise RuntimeError("插件尚未启用")
        job = self._library.get(str((params or {}).get("job_id") or ""))
        if job is None:
            raise RuntimeError("任务不存在")
        return job

    def _api_library_cancel(self, params: dict):
        if self._library is None:
            raise RuntimeError("插件尚未启用")
        return self._library.cancel(str((params or {}).get("job_id") or ""))
