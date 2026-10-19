"""字幕源协议."""

from __future__ import annotations

from typing import Protocol

from ..models import Candidate, SearchQuery


class SourceError(RuntimeError):
    pass


class SubtitleSource(Protocol):
    name: str

    def is_available(self) -> bool: ...

    def search(self, query: SearchQuery) -> list[Candidate]: ...

    def download(self, candidate: Candidate) -> bytes: ...
