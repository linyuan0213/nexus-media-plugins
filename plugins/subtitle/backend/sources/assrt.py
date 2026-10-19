"""Assrt 字幕源（搜索/详情/下载/解压）."""

from __future__ import annotations

import io
import posixpath
import zipfile

import httpx

try:
    import rarfile
except ImportError:  # pragma: no cover - optional dependency
    rarfile = None

from ..models import Candidate, SearchQuery
from ..naming import canonical_language, parse_language_and_flags, subtitle_extensions
from .base import SourceError

_API = "https://api.assrt.net/v1"
_MAX_ENTRIES = 200
_MAX_UNCOMPRESSED = 200 * 1024 * 1024
_TEXT_FORMATS = {".srt", ".ass", ".ssa", ".vtt", ".sup"}


class AssrtSource:
    name = "assrt"

    def __init__(self, token: str = "", timeout: int = 20):
        self._token = token.strip()
        self._timeout = max(5, int(timeout))

    def is_available(self) -> bool:
        return bool(self._token)

    def search(self, query: SearchQuery) -> list[Candidate]:
        if not self.is_available():
            return []
        try:
            with httpx.Client(timeout=self._timeout) as client:
                res = client.get(
                    f"{_API}/sub/search",
                    params={"q": query.title, "cnt": 15, "token": self._token},
                )
                res.raise_for_status()
                payload = res.json()
                if int(payload.get("status") or 0) != 0:
                    raise SourceError(f"Assrt 搜索失败: status={payload.get('status')}")
        except SourceError:
            raise
        except Exception as e:  # noqa: BLE001
            raise SourceError(f"Assrt 搜索失败: {e}") from e
        subs = ((payload.get("sub") or {}).get("subs") or [])
        result = []
        for sub in subs:
            try:
                language = _language_from_sub(sub)
                name = str(sub.get("native_name") or sub.get("videoname") or query.title)
                result.append(
                    Candidate(
                        source=self.name,
                        title=name,
                        language=language,
                        format=_format_from_subtype(str(sub.get("subtype") or ""), name),
                        url="",
                        downloads=int(sub.get("downloadcount") or 0),
                        payload={"id": sub.get("id")},
                    )
                )
            except (TypeError, ValueError):
                continue
        return result

    def download(self, candidate: Candidate) -> bytes:
        sub_id = (candidate.payload or {}).get("id")
        if not sub_id:
            raise SourceError("Assrt 候选缺少字幕 ID")
        try:
            with httpx.Client(timeout=self._timeout) as client:
                detail = client.get(
                    f"{_API}/sub/detail",
                    params={"id": sub_id, "token": self._token},
                )
                detail.raise_for_status()
                data = detail.json()
                if int(data.get("status") or 0) != 0:
                    raise SourceError(f"Assrt 详情失败: status={data.get('status')}")
                sub_info = ((data.get("sub") or {}).get("subs") or [{}])[0]
                filelist = sub_info.get("filelist") or []
                candidate.language = _language_from_sub(sub_info, candidate.language)
                if isinstance(filelist, list) and filelist:
                    chosen = _choose_file(filelist, candidate.language)
                    url = str(chosen.get("url") or "")
                    file_name = _file_name(chosen)
                else:
                    # 单文件字幕没有 filelist，直接使用详情里的 filename/url
                    url = str(sub_info.get("url") or "")
                    file_name = str(sub_info.get("filename") or sub_info.get("title") or "")
                if not url:
                    raise SourceError("Assrt 字幕文件缺少下载地址")
                response = client.get(url, follow_redirects=True)
                response.raise_for_status()
                content = response.content
                candidate.format = _format_from_name(file_name) or candidate.format
        except SourceError:
            raise
        except Exception as e:  # noqa: BLE001
            raise SourceError(f"Assrt 下载失败: {e}") from e
        payload, ext = _extract(content, candidate.language, candidate.format)
        candidate.format = ext.lstrip(".") or candidate.format
        return payload


def _choose_file(filelist: list[dict], language: str) -> dict:
    def score(item: dict) -> int:
        name = _file_name(item)
        detected, _, _ = parse_language_and_flags(name)
        ext = posixpath.splitext(name)[1].lower()
        value = {"srt": 3, "ass": 4, "ssa": 2, "vtt": 1}.get(ext.lstrip("."), 0)
        if detected == language:
            value += 10
        return value

    return max(filelist, key=score)


def _extract(content: bytes, language: str, fallback_format: str = "srt") -> tuple[bytes, str]:
    if content[:4] == b"PK\x03\x04":
        return _extract_zip(content, language)
    if content[:4] == b"Rar!":
        return _extract_rar(content, language)
    ext = f".{fallback_format.lstrip('.')}" if fallback_format else ".srt"
    return content, ext


def _extract_zip(content: bytes, language: str) -> tuple[bytes, str]:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            files = [info for info in zf.infolist() if not info.is_dir()]
            if len(files) > _MAX_ENTRIES:
                raise SourceError("压缩包条目过多")
            total = sum(info.file_size for info in files)
            if total > _MAX_UNCOMPRESSED:
                raise SourceError("压缩包解压后过大")
            files = [info for info in files if _safe_name(info.filename)]
            files = [info for info in files if not _is_symlink(info)]
            if not files:
                raise SourceError("压缩包内没有字幕文件")
            chosen = max(files, key=lambda info: _file_score(info.filename, language))
            ext = posixpath.splitext(chosen.filename)[1].lower()
            if ext not in _TEXT_FORMATS:
                raise SourceError("压缩包内没有可用字幕")
            return zf.read(chosen), ext
    except SourceError:
        raise
    except (OSError, zipfile.BadZipFile) as e:
        raise SourceError(f"ZIP 解压失败: {e}") from e


def _extract_rar(content: bytes, language: str) -> tuple[bytes, str]:
    if rarfile is None:
        raise SourceError("rarfile 未安装或系统缺少 unrar")
    try:
        with rarfile.RarFile(io.BytesIO(content)) as rf:
            files = [info for info in rf.infolist() if not info.is_dir()]
            files = [info for info in files if _safe_name(info.filename)]
            files = [info for info in files if not _is_symlink(info)]
            if not files:
                raise SourceError("压缩包内没有字幕文件")
            chosen = max(files, key=lambda info: _file_score(info.filename, language))
            ext = posixpath.splitext(chosen.filename)[1].lower()
            if ext not in _TEXT_FORMATS:
                raise SourceError("压缩包内没有可用字幕")
            return rf.read(chosen), ext
    except SourceError:
        raise
    except Exception as e:  # noqa: BLE001
        raise SourceError(f"RAR 解压失败: {e}") from e


def _file_score(name: str, language: str) -> int:
    ext = posixpath.splitext(name)[1].lower()
    detected, _, _ = parse_language_and_flags(name)
    score = {"srt": 3, "ass": 4, "ssa": 2, "vtt": 1}.get(ext.lstrip("."), 0)
    if detected == language:
        score += 10
    return score


def _safe_name(name: str) -> bool:
    normalized = name.replace("\\", "/")
    return not normalized.startswith("/") and ".." not in normalized.split("/")


def _is_symlink(info) -> bool:
    mode = (getattr(info, "external_attr", 0) >> 16) & 0o170000
    return mode == 0o120000


def _file_name(item: dict) -> str:
    return str(item.get("f") or item.get("name") or item.get("filename") or "")


def _format_from_name(name: str) -> str:
    ext = posixpath.splitext(name)[1].lower()
    return ext.lstrip(".") if ext in subtitle_extensions() else "srt"


def _format_from_subtype(subtype: str, name: str) -> str:
    text = subtype.lower()
    for fmt in ("ass", "ssa", "srt", "vtt", "sup", "sub"):
        if fmt in text:
            return fmt
    return _format_from_name(name)


def _language_from_sub(sub: dict, fallback: str = "und") -> str:
    lang = sub.get("lang") or {}
    if isinstance(lang, dict):
        desc = str(lang.get("desc") or "")
        langlist = lang.get("langlist") or {}
        if "双语" in desc or langlist.get("langdou"):
            return "mul"
        if "简" in desc or langlist.get("langchs"):
            return "zh-Hans"
        if "繁" in desc or langlist.get("langcht"):
            return "zh-Hant"
        if "英" in desc or langlist.get("langeng"):
            return "en"
        if "日" in desc or langlist.get("langjpn"):
            return "ja"
        if "韩" in desc or langlist.get("langkor"):
            return "ko"
        return canonical_language(desc)
    return canonical_language(str(lang or fallback))
