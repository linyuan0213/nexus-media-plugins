"""按 TMDB 定位媒体库视频路径."""

from __future__ import annotations

import posixpath

from app.db.repositories.media_sync_repo_adapter import MediaSyncRepositoryAdapter
from app.domain.mediatypes import MediaType

from .media_match import list_videos


def locate(
    *,
    media_server,
    media_service,
    resolver,
    tmdb_id: str,
    media_type: str = "movie",
    season: int | None = None,
    episode: int | None = None,
) -> list[dict]:
    mtype = MediaType.from_string(str(media_type))
    if mtype == MediaType.UNKNOWN:
        return []
    try:
        info = media_service.get_tmdb_info(mtype, int(tmdb_id), append_to_response="all") or {}
    except Exception:  # noqa: BLE001
        return []
    title = str(info.get("title") or info.get("name") or "")
    year = str(info.get("release_date") or info.get("first_air_date") or "")[:4]
    if not title:
        return []

    server_type = getattr(media_server, "_server_type", None)
    if server_type:
        try:
            item = MediaSyncRepositoryAdapter().query(server_type, title, year, str(tmdb_id))
            if item is not None:
                path = str(getattr(item, "PATH", "") or "")
                if path:
                    found = _from_path(resolver, path, mtype, season, episode, confidence=95)
                    if found:
                        return found
        except Exception:  # noqa: BLE001, S110
            pass

    # 回退：扫描已配置的媒体库根目录
    found: list[dict] = []
    for root in resolver.roots():
        if mtype == MediaType.MOVIE and root.media_type not in ("movie",):
            continue
        if mtype in (MediaType.TV, MediaType.ANIME) and root.media_type not in ("tv", "anime"):
            continue
        for video in list_videos(root.backend, root.path, recursive=True):
            if not _title_matches(video.title, title):
                continue
            if year and video.year and abs(_to_int(year) - _to_int(video.year)) > 1:
                continue
            if mtype != MediaType.MOVIE and (video.season != season or video.episode != episode):
                continue
            found.append(
                {
                    "video_path": video.path,
                    "backend_id": root.backend_id,
                    "title": video.title,
                    "year": video.year,
                    "season": video.season,
                    "episode": video.episode,
                    "confidence": 75,
                }
            )
    return found


def _from_path(
    resolver,
    path: str,
    mtype: MediaType,
    season: int | None,
    episode: int | None,
    confidence: int,
) -> list[dict]:
    resolved = resolver.resolve(path)
    if resolved:
        backend, root = resolved
        if mtype == MediaType.MOVIE and posixpath.splitext(path)[1]:
            return [{"video_path": path, "backend_id": root.backend_id, "confidence": confidence}]
        for video in list_videos(backend, path, recursive=True):
            if mtype != MediaType.MOVIE and (video.season != season or video.episode != episode):
                continue
            return [
                {
                    "video_path": video.path,
                    "backend_id": root.backend_id,
                    "title": video.title,
                    "year": video.year,
                    "season": video.season,
                    "episode": video.episode,
                    "confidence": confidence,
                }
            ]
    return []


def _to_int(value: str) -> int:
    try:
        return int(str(value)[:4])
    except (TypeError, ValueError):
        return 0


def _title_matches(left: str, right: str) -> bool:
    a = "".join(str(left).lower().split())
    b = "".join(str(right).lower().split())
    return bool(a and b and (a in b or b in a))
