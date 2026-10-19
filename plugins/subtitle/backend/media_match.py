"""手动上传字幕 → 视频候选匹配."""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from typing import Callable

from app.core.constants import RMT_MEDIAEXT
from app.domain.mediatypes import MediaType
from app.media.parser import meta_info

from .matcher import title_similarity
from .models import MatchCandidate
from .naming import canonical_language, normalize_flags, parse_language_and_flags


@dataclass
class VideoItem:
    path: str
    title: str
    year: str
    season: int | None
    episode: int | None


def match_files(
    backend,
    backend_id: str,
    *,
    files: list[dict],
    mode: str,
    target_video: str | None = None,
    target_dir: str | None = None,
    tmdb_candidates: Callable[[dict], list[dict]] | None = None,
    recursive: bool = True,
) -> list[dict]:
    videos = list_videos(backend, target_dir, recursive) if target_dir else []
    result = []
    for file_info in files:
        filename = str(file_info.get("filename") or "")
        language, flags, stripped = parse_language_and_flags(filename)
        language = canonical_language(file_info.get("language") or language)
        flags = normalize_flags(file_info.get("flags") or flags)
        candidates: list[MatchCandidate] = []
        if mode == "target_video" and target_video:
            candidates.append(MatchCandidate(target_video, backend_id, 100, "explicit"))
        elif mode == "tmdb" and tmdb_candidates:
            for item in tmdb_candidates(file_info):
                candidates.append(
                    MatchCandidate(
                        video_path=str(item.get("video_path") or ""),
                        backend_id=str(item.get("backend_id") or backend_id),
                        confidence=int(item.get("confidence") or 95),
                        reason="tmdb",
                        title=str(item.get("title") or ""),
                        year=item.get("year"),
                        season=item.get("season"),
                        episode=item.get("episode"),
                    )
                )
        else:
            candidates = _match_by_filename(
                videos, stripped, language, flags, file_info, backend_id=backend_id
            )
            if not candidates and len(videos) == 1:
                video = videos[0]
                candidates = [
                    MatchCandidate(
                        video.path,
                        backend_id,
                        70,
                        "directory_single",
                        video.title,
                        video.year,
                        video.season,
                        video.episode,
                    )
                ]
        candidates = [c for c in candidates if c.video_path]
        candidates.sort(key=lambda item: item.confidence, reverse=True)
        selected = None
        if candidates and candidates[0].confidence >= 80:
            top = candidates[0].confidence
            if len([c for c in candidates if c.confidence == top]) == 1:
                selected = {"video_path": candidates[0].video_path, "backend_id": candidates[0].backend_id}
        result.append(
            {
                "filename": filename,
                "language": language,
                "flags": flags,
                "candidates": [c.__dict__ for c in candidates],
                "selected": selected,
            }
        )
    return result


def list_videos(backend, directory: str, recursive: bool = True) -> list[VideoItem]:
    result: list[VideoItem] = []
    try:
        entries = list(backend.list_dir(directory))
    except Exception:  # noqa: BLE001
        return result
    for entry in entries:
        if entry.is_dir:
            if recursive:
                result.extend(list_videos(backend, entry.path, recursive))
            continue
        if posixpath.splitext(entry.path)[1].lower() not in RMT_MEDIAEXT:
            continue
        parsed = meta_info(entry.path)
        title = (parsed.title or parsed.get_name()) if parsed else ""
        if not title:
            continue
        result.append(
            VideoItem(
                path=entry.path,
                title=title,
                year=str(parsed.year or ""),
                season=parsed.begin_season,
                episode=parsed.begin_episode,
            )
        )
    return result


def _match_by_filename(
    videos: list[VideoItem],
    stripped: str,
    language: str,
    flags: list[str],
    file_info: dict,
    *,
    backend_id: str,
) -> list[MatchCandidate]:
    parsed = meta_info(stripped, mtype=MediaType.from_string(str(file_info.get("type") or "")))
    title = (parsed.title or parsed.get_name()) if parsed else ""
    if not title:
        return []
    season = file_info.get("season")
    if season is None:
        season = parsed.begin_season if parsed else None
    episode = file_info.get("episode")
    if episode is None:
        episode = parsed.begin_episode if parsed else None
    year = str(parsed.year) if parsed and parsed.year else ""
    matches: list[MatchCandidate] = []
    for video in videos:
        similarity = title_similarity(title, video.title)
        if similarity < 0.82:
            continue
        if year and video.year and abs(_to_int(year) - _to_int(video.year)) > 1:
            continue
        if season is not None and video.season != season:
            continue
        if episode is not None and video.episode != episode:
            continue
        confidence = 80 if similarity >= 0.9 else 70
        matches.append(
            MatchCandidate(
                video.path,
                backend_id,
                confidence,
                "filename",
                video.title,
                video.year,
                video.season,
                video.episode,
                flags,
            )
        )
    return matches


def detect_from_filename(filename: str) -> tuple[str, list[str]]:
    language, flags, _ = parse_language_and_flags(filename)
    return language, flags


def _to_int(value: str) -> int:
    try:
        return int(str(value)[:4])
    except (TypeError, ValueError):
        return 0


def parse_episode_from_filename(filename: str) -> tuple[int | None, int | None]:
    stem = posixpath.splitext(posixpath.basename(filename))[0]
    match = re.search(r"(?i)s(\d{1,3})[\s._-]*e(\d{1,4})", stem)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None, None
