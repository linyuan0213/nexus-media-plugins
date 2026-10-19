"""字幕插件业务服务（供 API/hook/agent_tool 复用）."""

from __future__ import annotations

import base64
import posixpath
from typing import Any

import log
from app.media.parser import meta_info
from app.utils import PathUtils

from .config import library_roots, load_config
from .matcher import rank_candidates
from .media_locator import locate as locate_video
from .media_match import match_files
from .models import PlaceResult, SearchQuery, SyncAdjustment, UploadMapping
from .naming import canonical_language, parse_language_and_flags, subtitle_extensions
from .placer import Placer
from .sources.assrt import AssrtSource
from .sources.base import SourceError, SubtitleSource
from .sources.thunder import ThunderSource, compute_cid
from .sources.zimuku import ZimukuSource
from .storage import BackendResolver
from .store import PluginStore
from .subtitle_presence import detect_chinese_subtitles
from .sync import SyncUnavailable, detect_audio_offset, detect_reference_offset, sample_times


class SubtitleService:
    def __init__(self, ctx, app_context=None, mediaserver=None, media_service=None):
        self._ctx = ctx
        self._app_context = app_context
        self._mediaserver = mediaserver
        self._media_service = media_service
        self._resolver = BackendResolver(app_context) if app_context else None
        self._store = PluginStore(ctx.data_dir)
        self._placer = Placer()

    # ---------- helpers ----------

    def config(self) -> dict[str, Any]:
        return load_config(self._ctx)

    def _sources(self, config: dict[str, Any]) -> dict[str, SubtitleSource]:
        sources: dict[str, SubtitleSource] = {}
        enabled = config.get("sources") or []
        if "assrt" in enabled:
            sources["assrt"] = AssrtSource(config.get("assrt_token") or "", config.get("request_timeout") or 20)
        if "thunder" in enabled:
            sources["thunder"] = ThunderSource(bool(config.get("thunder_meta_search")))
        if "zimuku" in enabled:
            sources["zimuku"] = ZimukuSource(timeout=config.get("request_timeout") or 20)
        return sources

    def _query(self, payload: dict[str, Any], config: dict[str, Any]) -> SearchQuery:
        languages = payload.get("languages") or config.get("languages") or ["zh-Hans"]
        if isinstance(languages, str):
            languages = [languages]
        return SearchQuery(
            tmdb_id=str(payload.get("tmdb_id") or ""),
            title=str(payload.get("title") or ""),
            year=str(payload.get("year") or ""),
            media_type=str(payload.get("type") or payload.get("media_type") or "movie"),
            season=_int_or_none(payload.get("season")),
            episode=_int_or_none(payload.get("episode")),
            languages=[canonical_language(lang) for lang in languages],
            video_path=payload.get("video_path"),
        )

    def _query_from_event(self, info: dict[str, Any], config: dict[str, Any]) -> SearchQuery:
        return SearchQuery(
            tmdb_id=str(info.get("tmdb_id") or info.get("id") or ""),
            title=str(info.get("title") or ""),
            year=str(info.get("year") or ""),
            media_type=str(info.get("type") or info.get("media_type") or "movie"),
            season=_first_int(info.get("season")),
            episode=_first_int(info.get("episode")),
            languages=[canonical_language(lang) for lang in (config.get("languages") or ["zh-Hans"])],
            video_path=info.get("video_path"),
        )

    def _resolve(self, path: str):
        if self._resolver is None:
            raise SourceError("插件缺少 app_context，无法解析存储后端")
        return self._resolver.resolve(path)

    def _backend_for(self, path: str):
        resolved = self._resolve(path)
        if resolved is None:
            raise SourceError(f"路径不在媒体库配置中: {path}")
        return resolved[0], resolved[1]

    def _allowed(self, path: str, config: dict[str, Any], extra_dir: str | None = None) -> bool:
        roots = library_roots(config)
        if roots:
            return any(PathUtils.is_path_in_path(root, path) for root in roots)
        if self._resolver is not None:
            for root in self._resolver.roots():
                if PathUtils.is_path_in_path(root.path, path):
                    return True
        return bool(extra_dir and PathUtils.is_path_in_path(extra_dir, path))

    def _search_candidates(
        self,
        query: SearchQuery,
        config: dict[str, Any],
        sources: dict[str, SubtitleSource] | None = None,
    ) -> list:
        sources = sources or self._sources(config)
        if "thunder" in sources and query.video_path and not query.cid:
            query.cid = self._cid_for(query.video_path)
        candidates = []
        for source in sources.values():
            if not source.is_available():
                continue
            try:
                candidates.extend(source.search(query))
            except SourceError:
                continue
        return rank_candidates(query, candidates, int(config.get("min_score") or 60))

    def _refresh_path(self, video_path: str, media_type: str = "movie") -> None:
        if self._mediaserver is None:
            return
        parsed = meta_info(video_path)
        title = (parsed.title or parsed.get_name()) if parsed else posixpath.basename(video_path)
        year = str(parsed.year) if parsed and parsed.year else ""
        try:
            self._mediaserver.refresh_library_by_items(
                [{"file_path": video_path, "title": title, "year": year, "type": media_type}]
            )
        except Exception as e:  # noqa: BLE001
            log.debug(f"[Subtitle] 刷新媒体服务器失败: {e}")

    def _refresh_media_server(self, query: SearchQuery, _target_path: str | None) -> None:
        if self._mediaserver is None:
            return
        try:
            self._mediaserver.refresh_library_by_items(
                [
                    {
                        "file_path": query.video_path,
                        "title": query.title,
                        "year": query.year,
                        "type": query.media_type,
                    }
                ]
            )
        except Exception as e:  # noqa: BLE001
            log.debug(f"[Subtitle] 刷新媒体服务器失败: {e}")

    @staticmethod
    def _close_sources(sources: dict[str, SubtitleSource]) -> None:
        for source in sources.values():
            close = getattr(source, "close", None)
            if close:
                try:
                    close()
                except Exception:  # noqa: BLE001, S110
                    pass

    def _cid_for(self, path: str) -> str | None:
        try:
            backend, _root = self._backend_for(path)
            info = backend.stat(path)
            if info is None:
                return None
            key = f"cid:{path}:{int(info.size)}:{int(info.mtime or 0)}"
            cached = self._store.cache_get(key)
            if cached:
                return str(cached)
            cid = compute_cid(backend, path)
            if cid:
                self._store.cache_set(key, cid, ttl=86400)
            return cid
        except Exception:  # noqa: BLE001
            return None

    def _auto_sync(self, backend, video_path: str, content: bytes, ext: str, config: dict[str, Any]) -> SyncAdjustment:
        policy = str(config.get("sync_policy") or "off")
        if policy not in ("auto_reference", "auto_audio") or ext.lower() != ".srt":
            return SyncAdjustment()
        if policy == "auto_audio":
            try:
                adjustment = detect_audio_offset(video_path, content)
            except (OSError, SyncUnavailable):
                return SyncAdjustment()
        else:
            references = self._placer.list_subtitles(backend, video_path)
            if len(references) != 1:
                return SyncAdjustment()
            stream = backend.read_stream(references[0])
            try:
                ref = stream.read()
            finally:
                stream.close()
            try:
                adjustment = detect_reference_offset(content, ref)
            except (OSError, SyncUnavailable):
                return SyncAdjustment()
        if adjustment.confidence < float(config.get("sync_min_confidence") or 0.75):
            return SyncAdjustment()
        if abs(adjustment.offset_ms) > int(config.get("sync_max_offset_ms") or 60000):
            return SyncAdjustment()
        return adjustment

    # ---------- search / download ----------

    def search(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        config = self.config()
        query = self._query(payload, config)
        sources = self._sources(config)
        try:
            return [self._candidate_dict(item) for item in self._search_candidates(query, config, sources)]
        finally:
            self._close_sources(sources)

    def download(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        config = self.config()
        query = self._query(payload, config)
        if not query.video_path:
            video_path = self._locate_first(payload)
            if not video_path:
                raise SourceError("未找到目标视频，请先选择视频")
            query.video_path = video_path["video_path"]
        backend, root = self._backend_for(query.video_path)
        if not self._allowed(query.video_path, config, posixpath.dirname(query.video_path)):
            raise SourceError("目标视频不在媒体库白名单内")
        if not payload.get("force") and config.get("skip_if_chinese_subtitle", True):
            presence = detect_chinese_subtitles(backend, query.video_path, config)
            if presence.found:
                return [
                    PlaceResult(
                        success=True,
                        action="skipped",
                        backend_id=root.backend_id,
                        reason=f"chinese_subtitle_exists:{presence.kind}",
                    ).__dict__
                ]
        sources = self._sources(config)
        results = []
        try:
            ranked = self._search_candidates(query, config, sources)
            for language in query.languages:
                candidate = next((c for c in ranked if c.language == language), None)
                if not candidate:
                    results.append(
                        PlaceResult(
                            success=False,
                            action="skipped",
                            backend_id=root.backend_id,
                            language=language,
                            reason="no_candidate",
                        ).__dict__
                    )
                    continue
                source = sources.get(candidate.source)
                if source is None:
                    results.append(
                        PlaceResult(
                            success=False,
                            action="skipped",
                            backend_id=root.backend_id,
                            language=language,
                            reason="source_disabled",
                        ).__dict__
                    )
                    continue
                try:
                    content = source.download(candidate)
                    ext = f".{candidate.format}" if candidate.format else ".srt"
                    if ext.lower() in {".sub", ".idx"}:
                        raise SourceError("sub_idx_pair_required")
                    sync = self._auto_sync(backend, query.video_path, content, ext, config)
                    result = self._placer.place(
                        backend,
                        query.video_path,
                        content,
                        language=language,
                        flags=[],
                        ext=ext,
                        suffix_style=str(config.get("suffix_style") or "nexus"),
                        overwrite_policy=str(config.get("overwrite_policy") or "skip"),
                        sync=sync,
                    )
                    results.append(result.__dict__)
                    if result.success:
                        self._refresh_media_server(query, result.target_path)
                    self._store.add_history(
                        {
                            "title": query.title,
                            "tmdb_id": query.tmdb_id,
                            "language": language,
                            "source": candidate.source,
                            "action": result.action,
                            "target": result.target_path,
                            "result": result.success,
                        }
                    )
                except Exception as e:  # noqa: BLE001
                    log.warn(f"[Subtitle] 下载失败 {query.title}/{language}: {e}")
                    results.append(
                        PlaceResult(
                            success=False,
                            action="failed",
                            backend_id=root.backend_id,
                            language=language,
                            reason=str(e),
                        ).__dict__
                    )
                    self._store.add_history(
                        {
                            "title": query.title,
                            "tmdb_id": query.tmdb_id,
                            "language": language,
                            "source": candidate.source,
                            "action": "failed",
                            "target": "",
                            "result": False,
                            "reason": str(e),
                        }
                    )
        finally:
            self._close_sources(sources)
        return results

    def _locate_first(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        if not self._resolver or not self._mediaserver or not self._media_service:
            return None
        items = locate_video(
            media_server=self._mediaserver,
            media_service=self._media_service,
            resolver=self._resolver,
            tmdb_id=str(payload.get("tmdb_id") or ""),
            media_type=str(payload.get("type") or "movie"),
            season=_int_or_none(payload.get("season")),
            episode=_int_or_none(payload.get("episode")),
        )
        return items[0] if items else None

    # ---------- match / upload ----------

    def match(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        config = self.config()
        mode = str(payload.get("mode") or "filename")
        target_video = payload.get("target_video")
        target_dir = payload.get("target_dir")
        if target_video:
            target_dir = posixpath.dirname(str(target_video))
        if mode in ("filename", "directory") and not target_dir:
            raise SourceError("缺少 target_dir 或 target_video")
        backend = None
        root = None
        if target_dir:
            backend, root = self._backend_for(str(target_dir))
            if not self._allowed(str(target_dir), config):
                raise SourceError("目标目录不在媒体库白名单内")
        elif mode == "tmdb":
            roots = self._resolver.roots() if self._resolver else []
            if not roots:
                raise SourceError("没有可用的媒体库根目录")
            backend, root = roots[0].backend, roots[0]
        else:
            raise SourceError("缺少 target_dir 或 target_video")

        def tmdb_candidates(file_info: dict[str, Any]) -> list[dict[str, Any]]:
            if not self._mediaserver or not self._media_service:
                return []
            return locate_video(
                media_server=self._mediaserver,
                media_service=self._media_service,
                resolver=self._resolver,
                tmdb_id=str(payload.get("tmdb_id") or ""),
                media_type=str(payload.get("type") or "movie"),
                season=_int_or_none(file_info.get("season") or payload.get("season")),
                episode=_int_or_none(file_info.get("episode") or payload.get("episode")),
            )

        return match_files(
            backend,
            root.backend_id if root else str(payload.get("backend_id") or "local"),
            files=list(payload.get("files") or []),
            mode=mode,
            target_video=str(target_video) if target_video else None,
            target_dir=str(target_dir) if target_dir else None,
            tmdb_candidates=tmdb_candidates,
            recursive=True,
        )

    def upload(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        config = self.config()
        files = {str(item.get("filename")): item for item in payload.get("files") or []}
        results = []
        for raw in payload.get("mappings") or []:
            try:
                mapping = _mapping_from_raw(raw)
                file_info = files.get(mapping.subtitle_filename)
                if not file_info:
                    results.append({"success": False, "action": "failed", "reason": "missing_content"})
                    continue
                backend, root = self._backend_for(mapping.video_path)
                if not self._allowed(mapping.video_path, config, posixpath.dirname(mapping.video_path)):
                    results.append({"success": False, "action": "failed", "reason": "outside_whitelist"})
                    continue
                content = _decode_content(file_info.get("content") or "")
                if len(content) > 5 * 1024 * 1024:
                    results.append({"success": False, "action": "failed", "reason": "subtitle_too_large"})
                    continue
                ext = posixpath.splitext(mapping.subtitle_filename)[1] or ".srt"
                if ext.lower() in {".sub", ".idx"}:
                    results.append({"success": False, "action": "failed", "reason": "sub_idx_pair_required"})
                    continue
                result = self._placer.place(
                    backend,
                    mapping.video_path,
                    content,
                    language=mapping.language,
                    flags=mapping.flags,
                    ext=ext,
                    suffix_style=mapping.suffix_style or str(config.get("suffix_style") or "nexus"),
                    overwrite_policy=str(raw.get("overwrite_policy") or config.get("overwrite_policy") or "skip"),
                    sync=mapping.sync,
                    dry_run=bool(payload.get("dry_run", False)),
                )
                results.append(result.__dict__)
                if result.success and not payload.get("dry_run"):
                    self._refresh_path(mapping.video_path, root.media_type)
                self._store.add_history(
                    {
                        "title": posixpath.basename(mapping.video_path),
                        "language": mapping.language,
                        "source": "upload",
                        "action": result.action,
                        "target": result.target_path,
                        "result": result.success,
                    }
                )
            except Exception as e:  # noqa: BLE001
                results.append({"success": False, "action": "failed", "reason": str(e)})
        return results

    # ---------- rename / locate / sync / targets ----------

    def rename(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        config = self.config()
        dry_run = bool(payload.get("dry_run", True))
        results = []
        if str(payload.get("mode") or "manual") == "manual":
            for raw in payload.get("mappings") or []:
                try:
                    results.append(self._rename_one(raw, config, dry_run))
                except Exception as e:  # noqa: BLE001
                    results.append(
                        {
                            "old_path": str(raw.get("subtitle_path") or ""),
                            "success": False,
                            "action": "failed",
                            "reason": str(e),
                        }
                    )
            return results
        scope = payload.get("scope") or {}
        backend, root = self._backend_for(str(scope.get("path") or ""))
        if not self._allowed(str(scope.get("path") or ""), config):
            raise SourceError("扫描目录不在媒体库白名单内")
        for subtitle in _scan_subtitles(backend, str(scope.get("path") or ""), bool(scope.get("recursive", True))):
            language, flags, _ = parse_language_and_flags(subtitle)
            file_info = {
                "filename": posixpath.basename(subtitle),
                "language": language,
                "flags": flags,
                "type": scope.get("type") or root.media_type,
                "season": scope.get("season"),
                "episode": scope.get("episode"),
            }
            matches = match_files(
                backend,
                root.backend_id,
                files=[file_info],
                mode="filename",
                target_dir=posixpath.dirname(subtitle),
                recursive=True,
            )
            selected = (matches[0].get("selected") if matches else None)
            if not selected:
                results.append({"old_path": subtitle, "action": "needs_confirmation", "reason": "ambiguous"})
                continue
            raw = {
                "subtitle_path": subtitle,
                "video_path": selected["video_path"],
                "language": language,
                "flags": flags,
                "suffix_style": str(config.get("suffix_style") or "nexus"),
                "overwrite_policy": str(config.get("overwrite_policy") or "skip"),
            }
            results.append(self._rename_one(raw, config, dry_run))
        return results

    def _rename_one(self, raw: dict[str, Any], config: dict[str, Any], dry_run: bool) -> dict[str, Any]:
        subtitle_path = str(raw.get("subtitle_path") or "")
        video_path = str(raw.get("video_path") or "")
        backend, root = self._backend_for(video_path)
        if not self._allowed(subtitle_path, config, posixpath.dirname(video_path)):
            return {"old_path": subtitle_path, "success": False, "action": "failed", "reason": "outside_whitelist"}
        if not self._allowed(video_path, config, posixpath.dirname(video_path)):
            return {"old_path": subtitle_path, "success": False, "action": "failed", "reason": "outside_whitelist"}
        content = backend.read_stream(subtitle_path).read()
        ext = posixpath.splitext(subtitle_path)[1] or ".srt"
        target = self._placer.place(
            backend,
            video_path,
            content,
            language=str(raw.get("language") or "zh-Hans"),
            flags=raw.get("flags") or [],
            ext=ext,
            suffix_style=str(raw.get("suffix_style") or config.get("suffix_style") or "nexus"),
            overwrite_policy=str(raw.get("overwrite_policy") or config.get("overwrite_policy") or "skip"),
            sync=SyncAdjustment(),
            dry_run=dry_run,
        )
        base_result = {
            "old_path": subtitle_path,
            "new_path": target.target_path,
            "language": str(raw.get("language") or "zh-Hans"),
            "flags": raw.get("flags") or [],
            "action": target.action,
            "sync": {"method": target.sync_method, "offset_ms": target.offset_ms, "rate": target.rate},
        }
        if dry_run:
            return {**base_result, "success": True, "action": "dry_run"}
        if target.action in ("placed", "overwritten") and target.target_path and (
            posixpath.normpath(target.target_path) != posixpath.normpath(subtitle_path)
        ):
            backend.remove(subtitle_path)
        if target.success:
            self._refresh_path(video_path, root.media_type)
        return {**base_result, "success": target.success, "reason": target.reason}

    def locate(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        if not self._resolver or not self._mediaserver or not self._media_service:
            return []
        return locate_video(
            media_server=self._mediaserver,
            media_service=self._media_service,
            resolver=self._resolver,
            tmdb_id=str(payload.get("tmdb_id") or ""),
            media_type=str(payload.get("type") or "movie"),
            season=_int_or_none(payload.get("season")),
            episode=_int_or_none(payload.get("episode")),
        )

    def sync_detect(self, payload: dict[str, Any]) -> dict[str, Any]:
        backend, _ = self._backend_for(str(payload.get("subtitle_path") or ""))
        content = backend.read_stream(str(payload.get("subtitle_path") or "")).read()
        if str(payload.get("method") or "reference") == "audio":
            video_path = str(payload.get("video_path") or "")
            if not video_path:
                raise SourceError("音频对齐需要 video_path")
            adjustment = detect_audio_offset(video_path, content)
            return {
                "offset_ms": adjustment.offset_ms,
                "rate": adjustment.rate,
                "method": adjustment.method,
                "confidence": adjustment.confidence,
                "samples": sample_times(content),
            }
        reference = payload.get("reference_path")
        if not reference:
            raise SourceError("参考字幕不能为空")
        ref_content = backend.read_stream(str(reference)).read()
        adjustment = detect_reference_offset(content, ref_content)
        return {
            "offset_ms": adjustment.offset_ms,
            "rate": adjustment.rate,
            "method": adjustment.method,
            "confidence": adjustment.confidence,
            "samples": sample_times(content),
        }

    def list_targets(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        path = str(payload.get("path") or "")
        backend, _ = self._backend_for(path)
        result = []
        for info in backend.list_dir(path):
            result.append({"path": info.path, "name": posixpath.basename(info.path), "is_dir": info.is_dir})
        return result

    def history(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._store.history(
            page=int(payload.get("page") or 1),
            page_size=max(1, min(200, int(payload.get("page_size") or 50))),
        )

    def test_source(self) -> dict[str, Any]:
        config = self.config()
        sources = self._sources(config)
        if not sources:
            return {"ok": False, "message": "未启用字幕源"}
        try:
            available = [name for name, source in sources.items() if source.is_available()]
            return {"ok": bool(available), "message": ", ".join(available) or "没有可用源"}
        finally:
            self._close_sources(sources)

    def download_bytes(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        return self.download(payload)

    @staticmethod
    def _candidate_dict(candidate) -> dict[str, Any]:
        return {
            "source": candidate.source,
            "title": candidate.title,
            "language": candidate.language,
            "format": candidate.format,
            "score": candidate.score,
            "downloads": candidate.downloads,
            "payload": candidate.payload,
        }


def _mapping_from_raw(raw: dict[str, Any]) -> UploadMapping:
    data = dict(raw)
    sync = data.get("sync")
    if isinstance(sync, dict):
        data["sync"] = SyncAdjustment(**sync)
    return UploadMapping(**data)


def _scan_subtitles(backend, directory: str, recursive: bool) -> list[str]:
    result = []
    try:
        entries = list(backend.list_dir(directory))
    except Exception:  # noqa: BLE001
        return result
    for entry in entries:
        if entry.is_dir:
            if recursive:
                result.extend(_scan_subtitles(backend, entry.path, recursive))
            continue
        if posixpath.splitext(entry.path)[1].lower() in subtitle_extensions():
            result.append(entry.path)
    return result


def _decode_content(value: Any) -> bytes:
    text = str(value or "")
    if text.startswith("data:"):
        text = text.split(",", 1)[-1]
    try:
        return base64.b64decode(text, validate=True)
    except (ValueError, TypeError):
        return text.encode("utf-8")


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None and str(value).strip() != "" else None
    except (TypeError, ValueError):
        return None


def _first_int(value: Any) -> int | None:
    if isinstance(value, (list, tuple)):
        return _int_or_none(value[0]) if value else None
    return _int_or_none(value)
