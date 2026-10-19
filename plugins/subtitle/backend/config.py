"""插件配置默认值与规范化."""

from __future__ import annotations

from typing import Any

from .naming import canonical_language

DEFAULTS: dict[str, Any] = {
    "auto_on_transfer": True,
    "auto_scan_enabled": False,
    "auto_scan_cron": "0 4 * * *",
    "languages": ["zh-Hans", "zh-Hant"],
    "sources": ["assrt"],
    "assrt_token": "",
    "thunder_meta_search": False,
    "min_score": 60,
    "suffix_style": "nexus",
    "overwrite_policy": "skip",
    "sync_policy": "off",
    "sync_max_offset_ms": 60000,
    "sync_min_confidence": 0.75,
    "skip_if_chinese_subtitle": True,
    "hard_sub_detection": "filename",
    "library_roots": "",
    "max_workers": 2,
    "request_timeout": 20,
    "library_delay_seconds": 1.0,
}


def load_config(ctx) -> dict[str, Any]:
    data = dict(DEFAULTS)
    data.update(ctx.get_config() or {})
    data["languages"] = _languages(data.get("languages"))
    data["sources"] = [str(s) for s in (data.get("sources") or ["assrt"])]
    data["max_workers"] = max(1, int(data.get("max_workers") or 2))
    data["request_timeout"] = max(5, int(data.get("request_timeout") or 20))
    data["sync_max_offset_ms"] = max(0, int(data.get("sync_max_offset_ms") or 60000))
    data["sync_min_confidence"] = float(data.get("sync_min_confidence") or 0.75)
    data["min_score"] = max(0, min(100, int(data.get("min_score") or 60)))
    data["library_delay_seconds"] = max(0.0, min(60.0, float(data.get("library_delay_seconds") or 0)))
    return data


def library_roots(config: dict[str, Any]) -> list[str]:
    raw = config.get("library_roots") or ""
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    return [line.strip() for line in str(raw).splitlines() if line.strip()]


def _languages(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    result: list[str] = []
    for item in value or []:
        lang = canonical_language(str(item))
        if lang not in result:
            result.append(lang)
    return result or ["zh-Hans"]
