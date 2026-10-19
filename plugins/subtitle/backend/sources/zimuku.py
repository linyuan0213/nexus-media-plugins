"""Zimuku（字幕库）字幕源：网页搜索 + 云锁验证码 + 详情下载."""

from __future__ import annotations

import base64
import io
import posixpath
import re
import zipfile
from urllib.parse import quote, urljoin

import httpx
from bs4 import BeautifulSoup

try:
    import rarfile
except ImportError:  # pragma: no cover - optional dependency
    rarfile = None

from app.core.settings import settings
from app.infrastructure.ocr.recognizer import OcrRecognizer

from ..models import Candidate, SearchQuery
from ..naming import parse_language_and_flags
from .base import SourceError

_BASE = "https://zimuku.org"
_CAPTCHA_IMAGE_RE = re.compile(
    r"data:image/(?:bmp|png|jpeg);base64,([A-Za-z0-9+/=\s]+)", re.IGNORECASE
)
_CAPTCHA_LOCATION_RE = re.compile(
    r"self\.location\s*=\s*[\"']([^\"']+)[\"']\s*\+\s*stringToHex\(", re.IGNORECASE
)
_SEARCH_REDIRECT_RE = re.compile(r"url\s*=\s*'([^']*)'\s*\+\s*url", re.IGNORECASE)
_TEXT_FORMATS = {".srt", ".ass", ".ssa", ".vtt", ".sup"}
_MAX_ARCHIVE_MEMBERS = 300
_MAX_UNCOMPRESSED = 100 * 1024 * 1024


class ZimukuSource:
    name = "zimuku"

    def __init__(self, base_url: str = _BASE, timeout: int = 30):
        self._base = (base_url or _BASE).rstrip("/")
        self._timeout = max(10, int(timeout))
        self._client = httpx.Client(
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
            },
            follow_redirects=True,
            timeout=self._timeout,
        )
        self._ocr = OcrRecognizer()

    def is_available(self) -> bool:
        lab = settings.get("laboratory") or {}
        return bool(lab.get("ocr_enabled", True) and lab.get("ocr_server_host"))

    def close(self) -> None:
        self._client.close()

    # ---------- search ----------

    def search(self, query: SearchQuery) -> list[Candidate]:
        title = query.title or posixpath.splitext(posixpath.basename(query.video_path or ""))[0]
        if not title:
            return []
        queries = [f"{title} {query.year}" if query.year else title]
        if len(queries[0]) > len(title):
            queries.append(title)
        candidates: list[Candidate] = []
        for value in queries:
            response = self._request(self._search_url(value))
            if not response:
                continue
            found = self._parse_search(response.text)
            if found:
                candidates.extend(found)
                break
        return _dedupe(candidates)[:30]

    def _search_url(self, value: str) -> str:
        return f"{self._base}/search?q={quote(value)}&chost=zimuku.org"

    def _parse_search(self, html: str) -> list[Candidate]:
        soup = BeautifulSoup(html, "html.parser")
        candidates: list[Candidate] = []
        for item in soup.select("div.item"):
            link = item.select_one("p.tt.clearfix a[href]") or item.select_one("p.tt a[href]")
            if link is None:
                continue
            work_title = link.get_text(" ", strip=True)
            rows = item.select("div.sublist tbody tr")
            if rows:
                candidates.extend(self._parse_rows(rows, work_title))
                continue
            href = str(link.get("href") or "")
            if not href:
                continue
            page = self._request(urljoin(f"{self._base}/", href))
            if page:
                candidates.extend(
                    self._parse_rows(
                        BeautifulSoup(page.text, "html.parser").select("div.sublist tbody tr")
                        or BeautifulSoup(page.text, "html.parser").select("tbody tr"),
                        work_title,
                    )
                )
        return candidates

    def _parse_rows(self, rows, work_title: str) -> list[Candidate]:
        result = []
        for row in rows:
            detail = row.select_one("td.first a[href]") or row.select_one("a[href*='/detail/']")
            if detail is None:
                continue
            href = str(detail.get("href") or "")
            if "/detail/" not in href:
                continue
            text = " ".join(
                str(img.get("alt") or img.get("title") or img.get("src") or "")
                for img in row.select("img")
            )
            language = _language_from_text(text)
            if language is None:
                continue
            subtitle_title = str(detail.get("title") or detail.get_text(" ", strip=True)).strip()
            if subtitle_title.lower().endswith(".7z"):
                continue
            format_name = _format_from_text(f"{row.get_text(' ', strip=True)} {subtitle_title}")
            if format_name is None:
                continue
            result.append(
                Candidate(
                    source=self.name,
                    title=subtitle_title or work_title,
                    language=language,
                    format=format_name,
                    url=urljoin(f"{self._base}/", href),
                    payload={"detail_path": href, "work_title": work_title},
                )
            )
        return result

    # ---------- download ----------

    def download(self, candidate: Candidate) -> bytes:
        detail_url = candidate.url or urljoin(
            f"{self._base}/", str((candidate.payload or {}).get("detail_path") or "")
        )
        if not detail_url:
            raise SourceError("Zimuku 候选缺少详情地址")
        detail = self._request(detail_url)
        if not detail:
            raise SourceError("Zimuku 详情页打开失败")
        first = BeautifulSoup(detail.text, "html.parser").select_one("a#down1[href]")
        if first is None:
            raise SourceError("Zimuku 详情页缺少下载按钮")
        intermediate_url = urljoin(detail_url, str(first.get("href") or ""))
        intermediate = self._request(intermediate_url, headers={"Referer": detail_url})
        if not intermediate:
            raise SourceError("Zimuku 下载中转页打开失败")
        soup = BeautifulSoup(intermediate.text, "html.parser")
        link = soup.select_one("a[rel~=nofollow][href]") or soup.select_one("a[href*='/download']")
        if link is None:
            raise SourceError("Zimuku 下载页缺少最终链接")
        final_url = urljoin(intermediate_url, str(link.get("href") or ""))
        response = self._request(final_url, headers={"Referer": detail_url})
        if response is None or not response.content:
            raise SourceError("Zimuku 字幕下载为空")
        payload, ext = _extract(response.content, candidate.language, candidate.format)
        candidate.format = ext.lstrip(".") or candidate.format
        return payload

    # ---------- captcha / request ----------

    def _request(self, url: str, headers: dict | None = None):
        for _ in range(3):
            try:
                response = self._client.get(url, headers=headers)
            except httpx.HTTPError as e:
                raise SourceError(f"Zimuku 请求失败: {e}") from e
            content_type = (response.headers.get("Content-Type") or "").lower()
            html = response.text if response.status_code == 404 or "html" in content_type else ""
            image = _captcha_image(html)
            if image is None:
                try:
                    response.raise_for_status()
                except httpx.HTTPError as e:
                    raise SourceError(f"Zimuku 请求失败: {e}") from e
                return response
            try:
                answer = self._ocr.get_captcha_text(image_b64=image)
            except Exception as e:  # noqa: BLE001
                raise SourceError(f"Zimuku 验证码识别服务不可用: {e}") from e
            if not answer:
                raise SourceError("Zimuku 验证码识别失败（nexus-verify 不可用或未识别）")
            self._submit_captcha(response, answer)
        raise SourceError("Zimuku 验证码被拒绝")

    def _submit_captcha(self, response, answer: str) -> None:
        self._client.cookies.set("srcurl", _js_hex(str(response.url)))
        match = _CAPTCHA_LOCATION_RE.search(response.text or "")
        path = match.group(1) if match else "/?security_verify_img="
        verify_url = urljoin(f"{self._base}/", f"{path}{_js_hex(answer)}")
        self._client.get(verify_url, follow_redirects=False)


def _captcha_image(html: str) -> str | None:
    match = _CAPTCHA_IMAGE_RE.search(html or "")
    return base64.b64encode(base64.b64decode(re.sub(r"\s+", "", match.group(1)))).decode() if match else None


def _js_hex(value: str) -> str:
    return "".join(format(ord(char), "x") for char in value)


def _language_from_text(text: str) -> str | None:
    value = str(text or "")
    if "双语" in value or "中英" in value:
        return "mul"
    if ("简" in value or "繁" in value or "chs" in value.lower() or "cht" in value.lower()) and (
        "英" in value or "eng" in value.lower() or "日" in value or "韩" in value
    ):
        return "mul"
    if "简" in value or "chs" in value.lower():
        return "zh-Hans"
    if "繁" in value or "cht" in value.lower():
        return "zh-Hant"
    if "英" in value or "eng" in value.lower():
        return "en"
    if "日" in value:
        return "ja"
    if "韩" in value:
        return "ko"
    return None


def _format_from_text(text: str) -> str | None:
    value = str(text or "").lower()
    for fmt in ("ass", "ssa", "srt", "vtt"):
        if fmt in value:
            return fmt
    return None


def _dedupe(candidates: list[Candidate]) -> list[Candidate]:
    result: dict[tuple[str, str], Candidate] = {}
    for candidate in candidates:
        key = (candidate.url, candidate.language)
        result.setdefault(key, candidate)
    return list(result.values())


def _extract(content: bytes, language: str, fallback_format: str = "srt") -> tuple[bytes, str]:
    if content[:6] == b"7z\xbc\xaf\x27\x1c":
        raise SourceError("Zimuku 7z 压缩包暂不支持")
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
            if len(files) > _MAX_ARCHIVE_MEMBERS:
                raise SourceError("Zimuku 压缩包条目过多")
            if sum(info.file_size for info in files) > _MAX_UNCOMPRESSED:
                raise SourceError("Zimuku 压缩包解压后过大")
            files = [info for info in files if _safe(info.filename) and not _symlink(info)]
            chosen = _best(files, language)
            if chosen is None:
                raise SourceError("Zimuku 压缩包内没有字幕")
            return zf.read(chosen), posixpath.splitext(chosen.filename)[1].lower()
    except SourceError:
        raise
    except (OSError, zipfile.BadZipFile) as e:
        raise SourceError(f"Zimuku ZIP 解压失败: {e}") from e


def _extract_rar(content: bytes, language: str) -> tuple[bytes, str]:
    if rarfile is None:
        raise SourceError("rarfile 未安装或系统缺少 unrar")
    try:
        with rarfile.RarFile(io.BytesIO(content)) as rf:
            files = [info for info in rf.infolist() if not info.is_dir() and _safe(info.filename)]
            chosen = _best(files, language)
            if chosen is None:
                raise SourceError("Zimuku 压缩包内没有字幕")
            return rf.read(chosen), posixpath.splitext(chosen.filename)[1].lower()
    except SourceError:
        raise
    except Exception as e:  # noqa: BLE001
        raise SourceError(f"Zimuku RAR 解压失败: {e}") from e


def _best(files, language: str):
    usable = [
        info for info in files
        if posixpath.splitext(info.filename)[1].lower() in _TEXT_FORMATS
    ]
    if not usable:
        return None
    return max(
        usable,
        key=lambda info: (
            parse_language_and_flags(info.filename)[0] == language,
            posixpath.splitext(info.filename)[1].lower() in {".ass", ".srt"},
        ),
    )


def _safe(name: str) -> bool:
    value = name.replace("\\", "/")
    return not value.startswith("/") and ".." not in value.split("/")


def _symlink(info) -> bool:
    mode = (getattr(info, "external_attr", 0) >> 16) & 0o170000
    return mode == 0o120000
