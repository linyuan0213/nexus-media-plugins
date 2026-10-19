"""Thunder 影音字幕源（CID 匹配 + 名称兜底）."""

from __future__ import annotations

import hashlib
import os
import posixpath

import httpx

from ..models import Candidate, SearchQuery
from ..naming import canonical_language
from .base import SourceError

_API = "https://api-shoulei-ssl.xunlei.com/oracle/subtitle"
_CHUNK = 0x5000
_TOTAL = 0xF000
_FORMATS = {"srt", "ass", "ssa", "vtt"}


class ThunderSource:
    name = "thunder"

    def __init__(self, meta_search: bool = False, timeout: int = 20):
        self._meta_search = meta_search
        self._timeout = max(5, int(timeout))

    def is_available(self) -> bool:
        return True

    def search(self, query: SearchQuery) -> list[Candidate]:
        name = (
            posixpath.basename(query.video_path.replace("\\", "/"))
            if query.video_path
            else query.title
        )
        if not name:
            return []
        cid = query.cid or _local_cid(query.video_path)
        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.get(_API, params={"name": name})
                response.raise_for_status()
                payload = response.json()
        except Exception as e:  # noqa: BLE001
            raise SourceError(f"Thunder 搜索失败: {e}") from e
        return self._parse_payload(payload, cid)

    def _parse_payload(self, payload: dict, cid: str | None = None) -> list[Candidate]:
        if int(payload.get("code") or 0) != 0:
            return []
        items = payload.get("data") or []
        seen: set[str] = set()
        result: list[Candidate] = []
        for item in items:
            url = str(item.get("url") or "").strip()
            ext = str(item.get("ext") or "").strip().lower().lstrip(".")
            if not url or ext not in _FORMATS or url in seen:
                continue
            seen.add(url)
            languages = item.get("languages") or []
            lang = _language_from_thunder(languages)
            result.append(
                Candidate(
                    source=self.name,
                    title=str(item.get("name") or ""),
                    language=lang,
                    format=ext,
                    url=url,
                    score=int(item.get("score") or 0),
                    downloads=int(item.get("score") or 0),
                    payload={
                        "url": url,
                        "cid": item.get("cid"),
                        "gcid": item.get("gcid"),
                        "match": bool(cid and str(item.get("cid") or "").lower() == cid.lower()),
                    },
                )
            )
        result.sort(
            key=lambda c: (
                bool(c.payload.get("match")),
                c.language == "zh-Hans",
                c.score,
            ),
            reverse=True,
        )
        return result[:20]

    def download(self, candidate: Candidate) -> bytes:
        url = str(candidate.url or (candidate.payload or {}).get("url") or "")
        if not url:
            raise SourceError("Thunder 字幕缺少下载地址")
        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.get(url, follow_redirects=True)
                response.raise_for_status()
                return response.content
        except Exception as e:  # noqa: BLE001
            raise SourceError(f"Thunder 下载失败: {e}") from e


def compute_cid(backend, path: str) -> str | None:
    """按迅雷 CID 规则计算：首 20KB + 1/3 处 20KB + 末 20KB 的 SHA1；小文件整文件."""
    try:
        info = backend.stat(path)
        if info is None:
            return None
        stream = backend.read_stream(path)
        try:
            return _cid_from_stream(stream, int(info.size))
        finally:
            stream.close()
    except Exception:  # noqa: BLE001
        return _local_cid(path)


def _local_cid(path: str | None) -> str | None:
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as f:
            return _cid_from_stream(f, os.path.getsize(path))
    except OSError:
        return None


def _cid_from_stream(stream, size: int) -> str:
    if size < _TOTAL:
        data = _read_exact(stream, size)
        return hashlib.sha1(data).hexdigest().upper()
    first = _read_exact(stream, _CHUNK)
    stream.seek(size // 3)
    middle = _read_exact(stream, _CHUNK)
    stream.seek(size - _CHUNK)
    last = _read_exact(stream, _CHUNK)
    return hashlib.sha1(first + middle + last).hexdigest().upper()


def _read_exact(stream, count: int) -> bytes:
    chunks = []
    remaining = count
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _language_from_thunder(languages) -> str:
    if isinstance(languages, str):
        text = languages
    else:
        text = " ".join(str(item) for item in (languages or []))
    if "简体" in text or "简" in text or "zh-CN" in text or "chi" in text:
        return "zh-Hans"
    if "繁体" in text or "繁" in text or "zh-TW" in text:
        return "zh-Hant"
    if "英" in text or "eng" in text.lower():
        return "en"
    if "日" in text:
        return "ja"
    if "韩" in text:
        return "ko"
    if "," in text or "双语" in text:
        return "mul"
    return "zh-Hans" if not text else canonical_language(text)
