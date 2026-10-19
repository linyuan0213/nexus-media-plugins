"""字幕语言、标记与命名规则."""

from __future__ import annotations

import posixpath
import re
from typing import Iterable

LANGUAGES = {
    "zh": "zh-Hans",
    "zho": "zh-Hans",
    "chi": "zh-Hans",
    "zh-cn": "zh-Hans",
    "zh-sg": "zh-Hans",
    "zh-hans": "zh-Hans",
    "chs": "zh-Hans",
    "sc": "zh-Hans",
    "简体": "zh-Hans",
    "简中": "zh-Hans",
    "zh-tw": "zh-Hant",
    "zh-hk": "zh-Hant",
    "zh-hant": "zh-Hant",
    "cht": "zh-Hant",
    "tc": "zh-Hant",
    "繁体": "zh-Hant",
    "繁中": "zh-Hant",
    "en": "en",
    "eng": "en",
    "english": "en",
    "英语": "en",
    "ja": "ja",
    "jpn": "ja",
    "japanese": "ja",
    "日语": "ja",
    "ko": "ko",
    "kor": "ko",
    "korean": "ko",
    "韩语": "ko",
    "mul": "mul",
    "multi": "mul",
    "und": "und",
    "unknown": "und",
}

FLAGS = {
    "forced": "forced",
    "foreign": "forced",
    "sdh": "sdh",
    "cc": "cc",
    "commentary": "commentary",
    "comment": "commentary",
    "default": "default",
    "bilingual": "bilingual",
    "双语": "bilingual",
    "中英": "bilingual",
    "multi": "bilingual",
}
FLAG_ORDER = ("forced", "sdh", "cc", "commentary", "default", "bilingual")

_NEXUS = {
    "zh-Hans": ".chi.zh-cn",
    "zh-Hant": ".zh-tw",
    "en": ".eng",
    "ja": ".jpn",
    "ko": ".kor",
    "mul": ".mul",
    "und": ".und",
}
_BCP47 = {
    "zh-Hans": ".zh-Hans",
    "zh-Hant": ".zh-Hant",
    "en": ".en",
    "ja": ".ja",
    "ko": ".ko",
    "mul": ".mul",
    "und": ".und",
}
_ISO639 = {
    "zh-Hans": ".zh",
    "zh-Hant": ".zh",
    "en": ".en",
    "ja": ".ja",
    "ko": ".ko",
    "mul": ".mul",
    "und": ".und",
}


def canonical_language(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "und"
    text = raw.lower().replace("_", "-")
    if any(sep in text for sep in ("&", "+", "/")) and len([p for p in re.split(r"[&+/]", text) if p]) > 1:
        return "mul"
    if text in ("双语", "中英", "bilingual"):
        return "mul"
    return LANGUAGES.get(text, LANGUAGES.get(text.split("-")[0], "und"))


def normalize_flags(values: Iterable[str] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        key = str(value).strip().lower()
        flag = FLAGS.get(key)
        if flag and flag not in result:
            result.append(flag)
    return [flag for flag in FLAG_ORDER if flag in result]


def language_suffix(language: str, style: str = "nexus") -> str:
    lang = canonical_language(language)
    table = {"bcp47": _BCP47, "iso639": _ISO639}.get(style, _NEXUS)
    return table.get(lang, ".und")


def build_subtitle_path(
    video_path: str,
    language: str,
    flags: Iterable[str] | None,
    ext: str,
    style: str = "nexus",
) -> str:
    base = posixpath.splitext(str(video_path).replace("\\", "/"))[0]
    extension = ext if str(ext).startswith(".") else f".{ext}"
    suffix = language_suffix(language, style)
    for flag in normalize_flags(flags):
        suffix += f".{flag}"
    return f"{base}{suffix}{extension.lower()}"


def parse_language_and_flags(filename: str) -> tuple[str, list[str], str]:
    """从字幕文件名解析语言/标记，返回 (language, flags, 去掉标记后的基名)."""
    stem, _ = posixpath.splitext(posixpath.basename(str(filename).replace("\\", "/")))
    tokens = [token for token in re.split(r"[.\s_-]+", stem) if token]
    language = "und"
    flags: list[str] = []
    kept: list[str] = []
    for token in tokens:
        lower = token.lower()
        if lower in ("双语", "中英") or any(sep in lower for sep in ("&", "+", "/")):
            language = "mul"
            if "bilingual" not in flags:
                flags.append("bilingual")
            continue
        if lower in LANGUAGES:
            language = canonical_language(token)
            continue
        if lower in FLAGS:
            flag = FLAGS[lower]
            if flag not in flags:
                flags.append(flag)
            continue
        kept.append(token)
    return language, normalize_flags(flags), ".".join(kept)


def subtitle_extensions() -> set[str]:
    return {".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx", ".sup"}
