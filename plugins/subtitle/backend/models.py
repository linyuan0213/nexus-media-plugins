"""字幕插件内部数据模型."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SearchQuery:
    tmdb_id: str | None = None
    title: str = ""
    year: str | None = None
    media_type: str = "movie"
    season: int | None = None
    episode: int | None = None
    languages: list[str] = field(default_factory=lambda: ["zh-Hans"])
    video_path: str | None = None
    backend_id: str | None = None
    cid: str | None = None


@dataclass
class Candidate:
    source: str
    title: str
    language: str
    format: str
    url: str
    score: int = 0
    downloads: int = 0
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class SyncAdjustment:
    offset_ms: int = 0
    rate: float = 1.0
    method: str = "manual"
    confidence: float = 0.0

    def is_noop(self) -> bool:
        return self.offset_ms == 0 and self.rate == 1.0


@dataclass
class PlaceResult:
    success: bool
    action: str
    backend_id: str = "local"
    language: str = ""
    target_path: str | None = None
    reason: str = ""
    sync_method: str = "none"
    offset_ms: int = 0
    rate: float = 1.0
    sync_confidence: float = 0.0


@dataclass
class SubtitleInput:
    filename: str
    content: bytes
    language: str | None = None
    detected_language: str | None = None
    flags: list[str] = field(default_factory=list)
    confidence: int = 0
    season: int | None = None
    episode: int | None = None


@dataclass
class MatchCandidate:
    video_path: str
    backend_id: str = "local"
    confidence: int = 0
    reason: str = ""
    title: str = ""
    year: str | None = None
    season: int | None = None
    episode: int | None = None
    flags: list[str] = field(default_factory=list)


@dataclass
class UploadMapping:
    subtitle_filename: str
    video_path: str
    backend_id: str = "local"
    language: str = "zh-Hans"
    flags: list[str] = field(default_factory=list)
    season: int | None = None
    episode: int | None = None
    suffix_style: str = "nexus"
    template: str | None = None
    sync: SyncAdjustment = field(default_factory=SyncAdjustment)


@dataclass
class RenameRule:
    mode: str = "manual"
    backend_id: str = "local"
    target_dir: str | None = None
    target_video: str | None = None
    tmdb_id: str | None = None
    media_type: str = "movie"
    season: int | None = None
    episode: int | None = None
    template: str | None = None
    language: str = "zh-Hans"
    flags: list[str] = field(default_factory=list)
    suffix_style: str = "nexus"
    overwrite_policy: str = "skip"
    recursive: bool = True
    sync: SyncAdjustment = field(default_factory=SyncAdjustment)
