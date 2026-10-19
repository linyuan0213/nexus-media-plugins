"""字幕时间轴偏移/参考字幕对齐（文本字幕）."""

from __future__ import annotations

import bisect
import math
import os
import shutil
import statistics
import sys
import tempfile
import wave
from array import array
from datetime import timedelta
from pathlib import Path

import srt

from app.infrastructure.ffmpeg import FfmpegProcessor

from .models import SyncAdjustment


class SyncUnavailable(RuntimeError):
    pass


def apply_sync(content: bytes, ext: str, adjustment: SyncAdjustment) -> bytes:
    if adjustment.is_noop():
        return content
    if ext.lower() != ".srt":
        raise SyncUnavailable(f"{ext} 暂不支持自动时间轴修改")
    text = content.decode("utf-8-sig", errors="replace")
    cues = list(srt.parse(text))
    for cue in cues:
        cue.start = _transform(cue.start, adjustment)
        cue.end = _transform(cue.end, adjustment)
        if cue.end <= cue.start:
            cue.end = cue.start + timedelta(milliseconds=200)
    return srt.compose(cues).encode("utf-8")


def detect_reference_offset(content: bytes, reference: bytes) -> SyncAdjustment:
    try:
        cues = list(srt.parse(content.decode("utf-8-sig", errors="replace")))
        refs = list(srt.parse(reference.decode("utf-8-sig", errors="replace")))
    except (ValueError, TypeError) as e:
        raise SyncUnavailable(f"SRT 解析失败: {e}") from e
    if len(cues) < 5 or len(refs) < 5:
        raise SyncUnavailable("字幕或参考字幕 cue 太少")

    ref_starts = sorted(cue.start.total_seconds() for cue in refs)
    diffs = []
    for cue in cues:
        start = cue.start.total_seconds()
        nearest = min(ref_starts, key=lambda value: abs(value - start))
        diffs.append(start - nearest)
    median = statistics.median(diffs)
    mad = statistics.median(abs(value - median) for value in diffs)
    confidence = max(0.0, min(1.0, 1.0 - mad / max(1.0, abs(median) + 1.0)))
    return SyncAdjustment(
        offset_ms=int(round(-median * 1000)),
        rate=1.0,
        method="reference",
        confidence=round(confidence, 3),
    )


def detect_audio_offset(
    video_path: str,
    content: bytes,
    *,
    max_seconds: int = 1800,
) -> SyncAdjustment:
    """通过音频语音起始点估计恒定偏移；仅支持本地视频和 SRT."""
    if not os.path.isfile(video_path):
        raise SyncUnavailable("音频对齐仅支持本地视频")
    if not shutil.which("ffmpeg"):
        raise SyncUnavailable("缺少 ffmpeg")
    try:
        cues = list(srt.parse(content.decode("utf-8-sig", errors="replace")))
    except (ValueError, TypeError) as e:
        raise SyncUnavailable(f"SRT 解析失败: {e}") from e
    if len(cues) < 5:
        raise SyncUnavailable("字幕 cue 太少")
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        audio_path = tmp.name
    try:
        if not FfmpegProcessor.extract_wav_from_video(
            video_path,
            audio_path,
            duration=max(60, int(max_seconds)),
        ):
            raise SyncUnavailable("音频提取失败")
        try:
            with wave.open(audio_path, "rb") as wf:
                sample_rate = wf.getframerate()
                pcm = wf.readframes(wf.getnframes())
        except (OSError, wave.Error) as e:
            raise SyncUnavailable(f"音频读取失败: {e}") from e
    finally:
        Path(audio_path).unlink(missing_ok=True)
    if not pcm:
        raise SyncUnavailable("音频提取失败")
    onsets = _speech_onsets(pcm, sample_rate)
    if len(onsets) < 5:
        raise SyncUnavailable("未检测到足够的语音起始点")
    cue_starts = sorted(
        cue.start.total_seconds() for cue in cues if cue.start.total_seconds() <= max_seconds
    )
    diffs = []
    for start in cue_starts:
        index = bisect.bisect_left(onsets, start)
        nearby = [onsets[i] for i in (index - 1, index) if 0 <= i < len(onsets)]
        if not nearby:
            continue
        diff = start - min(nearby, key=lambda value: abs(value - start))
        if abs(diff) <= 10:
            diffs.append(diff)
    if len(diffs) < 5:
        raise SyncUnavailable("字幕与语音起始点匹配不足")
    median = statistics.median(diffs)
    mad = statistics.median(abs(value - median) for value in diffs)
    confidence = max(0.0, min(1.0, 1.0 - mad / max(1.0, abs(median) + 1.0)))
    return SyncAdjustment(
        offset_ms=int(round(-median * 1000)),
        rate=1.0,
        method="audio",
        confidence=round(confidence, 3),
    )


def _speech_onsets(pcm: bytes, sample_rate: int) -> list[float]:
    samples = array("h")
    usable = len(pcm) - len(pcm) % 2
    samples.frombytes(pcm[:usable])
    if sys.byteorder == "big":
        samples.byteswap()
    frame_size = max(1, sample_rate // 20)
    energies = []
    for start in range(0, len(samples), frame_size):
        frame = samples[start : start + frame_size]
        if not frame:
            continue
        energies.append(math.sqrt(sum(value * value for value in frame) / len(frame)))
    if not energies:
        return []
    threshold = max(200.0, statistics.median(energies) * 1.6)
    onsets: list[float] = []
    speaking = False
    last = -1.0
    for index, energy in enumerate(energies):
        active = energy > threshold
        if active and not speaking:
            moment = index * frame_size / sample_rate
            if moment - last >= 0.3:
                onsets.append(moment)
                last = moment
        speaking = active
    return onsets


def sample_times(content: bytes, limit: int = 3) -> list[dict[str, str]]:
    try:
        cues = list(srt.parse(content.decode("utf-8-sig", errors="replace")))
    except (ValueError, TypeError):
        return []
    return [
        {"start": str(cue.start), "end": str(cue.end), "text": cue.content[:80]}
        for cue in cues[:limit]
    ]


def _transform(value: timedelta, adjustment: SyncAdjustment) -> timedelta:
    seconds = value.total_seconds() * float(adjustment.rate) + adjustment.offset_ms / 1000
    return timedelta(seconds=max(0.0, seconds))
