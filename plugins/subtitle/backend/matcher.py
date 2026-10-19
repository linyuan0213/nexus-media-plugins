"""字幕候选匹配打分."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from .models import Candidate, SearchQuery
from .naming import canonical_language

_TAG_RE = re.compile(r"\[[^\]]*]|\([^)]*\)")
_SPLIT_RE = re.compile(r"[.\s_\-—–:：/\\]+")
_RELEASE_RE = re.compile(
    r"(?i)\b(480p?|576p?|720p?|1080p?|2160p?|4k|bluray|blu-ray|web-dl|webrip|"
    r"hdrip|bdrip|hdtv|x264|x265|h264|h265|hevc|avc|aac|ac3|dts|ddp?|hdr|10bit)\b"
)


def normalize_title(title: str) -> str:
    text = _TAG_RE.sub(" ", str(title or ""))
    text = re.sub(r"-[A-Za-z0-9]{2,}$", " ", text)
    text = _RELEASE_RE.sub(" ", text)
    text = _SPLIT_RE.sub(" ", text)
    text = re.sub(r"(?i)\b(19|20)\d{2}\b", " ", text)
    return " ".join(text.lower().split())


def title_similarity(left: str, right: str) -> float:
    a, b = normalize_title(left), normalize_title(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.9
    return SequenceMatcher(None, a, b).ratio()


def score_candidate(query: SearchQuery, candidate: Candidate) -> int:
    score = int(title_similarity(query.title, candidate.title) * 55)
    score += _year_score(query.year, candidate.title)
    score += _episode_score(query, candidate)
    if canonical_language(candidate.language) in {canonical_language(lang) for lang in query.languages}:
        score += 8
    score += {"ass": 6, "srt": 5, "ssa": 3, "vtt": 2}.get(candidate.format.lower(), 0)
    score += min(6, max(0, candidate.downloads // 1000))
    return max(0, min(100, score))


def rank_candidates(query: SearchQuery, candidates: list[Candidate], min_score: int = 60) -> list[Candidate]:
    scored = []
    for candidate in candidates:
        candidate.score = score_candidate(query, candidate)
        if candidate.score >= min_score:
            scored.append(candidate)
    scored.sort(key=lambda c: (c.score, c.downloads), reverse=True)
    return scored


def best_per_language(query: SearchQuery, candidates: list[Candidate], min_score: int = 60) -> list[Candidate]:
    result: dict[str, Candidate] = {}
    for candidate in rank_candidates(query, candidates, min_score):
        lang = canonical_language(candidate.language)
        if lang not in result:
            result[lang] = candidate
    return [result[lang] for lang in query.languages if lang in result]


def _year_score(query_year: str | None, title: str) -> int:
    if not query_year:
        return 5
    match = re.search(r"(19|20)\d{2}", str(title))
    if not match:
        return 5
    try:
        diff = abs(int(query_year) - int(match.group(0)))
    except ValueError:
        return 5
    if diff == 0:
        return 15
    if diff == 1:
        return 10
    return 0


def _episode_score(query: SearchQuery, candidate: Candidate) -> int:
    if query.media_type == "movie":
        return 10
    score = 0
    if query.season is not None and _contains_number(candidate.title, query.season):
        score += 6
    if query.episode is not None and _contains_number(candidate.title, query.episode):
        score += 6
    return score


def _contains_number(text: str, number: int) -> bool:
    return bool(re.search(rf"(?<!\d)0*{int(number)}(?!\d)", str(text)))
