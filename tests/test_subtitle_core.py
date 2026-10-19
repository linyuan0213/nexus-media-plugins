"""插件核心逻辑测试（不依赖后端运行时）."""

from __future__ import annotations

import base64
import hashlib
import io
import sys
from array import array
from pathlib import Path
from types import SimpleNamespace

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "subtitle"))

from backend.bluray import find_bluray_main, is_bluray_directory  # noqa: E402
from backend.matcher import best_per_language, rank_candidates  # noqa: E402
from backend.models import Candidate, SearchQuery, SyncAdjustment  # noqa: E402
from backend.naming import (  # noqa: E402
    build_subtitle_path,
    canonical_language,
    normalize_flags,
    parse_language_and_flags,
)
from backend.placer import Placer  # noqa: E402
from backend.sources.thunder import ThunderSource, _cid_from_stream  # noqa: E402
from backend.sources.zimuku import ZimukuSource, _captcha_image, _js_hex  # noqa: E402
from backend.store import PluginStore  # noqa: E402
from backend.subtitle_presence import (  # noqa: E402
    check_external_subtitles,
    check_hard_subtitle_filename,
    check_soft_subtitles,
    detect_chinese_subtitles,
)
from backend.sync import _speech_onsets, apply_sync, detect_reference_offset  # noqa: E402


class FakeBackend:
    def __init__(self):
        self.files: dict[str, bytes] = {}

    def exists(self, path: str) -> bool:
        return path in self.files

    def mkdir(self, *_args, **_kwargs):
        return None

    def write_stream(self, path: str, stream, size: int = 0, chunk_size: int = 0):
        self.files[path] = stream.read()

    def move(self, src: str, dst: str):
        self.files[dst] = self.files.pop(src)

    def remove(self, path: str, *_args, **_kwargs):
        self.files.pop(path, None)

    def list_dir(self, directory: str):
        for path in sorted(self.files):
            if path.startswith(directory.rstrip("/") + "/"):
                yield SimpleNamespace(path=path, is_dir=False)


def srt(*times: tuple[int, int]) -> bytes:
    blocks = []
    for index, (start, end) in enumerate(times, 1):
        blocks.append(f"{index}\n00:00:{start:02d},000 --> 00:00:{end:02d},000\nLine {index}\n")
    return "\n".join(blocks).encode()


def test_language_aliases_and_flags():
    assert canonical_language("zh-cn") == "zh-Hans"
    assert canonical_language("cht") == "zh-Hant"
    assert canonical_language("chs&eng") == "mul"
    assert normalize_flags(["sdh", "forced", "cc"]) == ["forced", "sdh", "cc"]
    language, flags, stripped = parse_language_and_flags("Movie.2020.chs&eng.forced.srt")
    assert language == "mul"
    assert flags == ["forced", "bilingual"]
    assert stripped == "Movie.2020"


def test_suffix_styles():
    assert build_subtitle_path("/m/Movie.mkv", "zh-Hans", ["forced"], ".srt", "bcp47") == "/m/Movie.zh-Hans.forced.srt"
    assert build_subtitle_path("/m/Movie.mkv", "zh-Hant", [], ".ass", "nexus") == "/m/Movie.zh-tw.ass"
    assert build_subtitle_path("/m/Movie.mkv", "en", ["sdh"], ".srt", "iso639") == "/m/Movie.en.sdh.srt"


def test_matcher_prefers_language_and_episode():
    query = SearchQuery(title="The Matrix", year="1999", languages=["zh-Hans", "en"])
    candidates = [
        Candidate("assrt", "The Matrix 1999", "en", "srt", ""),
        Candidate("assrt", "The Matrix 1999", "zh-Hans", "ass", ""),
        Candidate("assrt", "Other Movie 2020", "zh-Hans", "srt", ""),
    ]
    ranked = rank_candidates(query, candidates, 60)
    assert ranked[0].language == "zh-Hans"
    assert best_per_language(query, candidates, 60)[0].language == "zh-Hans"


def test_placer_dry_run_conflict_and_suffix():
    backend = FakeBackend()
    placer = Placer()
    result = placer.place(
        backend,
        "/m/Movie.mkv",
        srt((1, 2)),
        language="zh-Hans",
        flags=["forced"],
        ext=".srt",
        suffix_style="bcp47",
    )
    assert result.action == "placed"
    assert "/m/Movie.zh-Hans.forced.srt" in backend.files

    skipped = placer.place(
        backend,
        "/m/Movie.mkv",
        srt((1, 2)),
        language="zh-Hans",
        flags=["forced"],
        ext=".srt",
        suffix_style="bcp47",
    )
    assert skipped.action == "skipped"

    suffixed = placer.place(
        backend,
        "/m/Movie.mkv",
        srt((1, 2)),
        language="zh-Hans",
        flags=["forced"],
        ext=".srt",
        suffix_style="bcp47",
        overwrite_policy="suffix",
    )
    assert suffixed.target_path.endswith(".1.srt")

    dry = placer.place(
        backend,
        "/m/Movie.mkv",
        srt((1, 2)),
        language="en",
        flags=[],
        ext=".srt",
        suffix_style="bcp47",
        dry_run=True,
    )
    assert dry.action == "dry_run"
    assert dry.target_path not in backend.files


def test_srt_offset_and_reference_detection():
    content = srt((10, 11), (20, 21), (30, 31), (40, 41), (50, 51))
    shifted = apply_sync(content, ".srt", SyncAdjustment(offset_ms=2000))
    assert b"00:00:12,000 --> 00:00:13,000" in shifted

    reference = srt((12, 13), (22, 23), (32, 33), (42, 43), (52, 53))
    adjustment = detect_reference_offset(content, reference)
    assert adjustment.offset_ms == 2000
    assert adjustment.confidence >= 0.5


def test_store_history_and_fingerprint(tmp_path):
    store = PluginStore(str(tmp_path))
    assert not store.is_duplicate("x")
    store.add_fingerprint("x")
    assert store.is_duplicate("x")
    store.add_history({"action": "placed", "target": "/m/a.srt"})
    assert store.history()["total"] == 1
    store.cache_set("key", "value", ttl=60)
    assert store.cache_get("key") == "value"


def test_thunder_cid_algorithm():
    data = bytes((index * 31) % 256 for index in range(200_000))
    size = len(data)
    chunk = 0x5000
    expected = hashlib.sha1(
        data[:chunk] + data[size // 3 : size // 3 + chunk] + data[-chunk:]
    ).hexdigest().upper()
    assert _cid_from_stream(io.BytesIO(data), size) == expected


def test_thunder_payload_parsing():
    payload = {
        "code": 0,
        "data": [
            {
                "name": "Movie.zh.srt",
                "url": "https://example.test/a.srt",
                "ext": "srt",
                "cid": "ABC",
                "languages": ["简体"],
                "score": 10,
            },
            {
                "name": "Movie.en.srt",
                "url": "https://example.test/b.srt",
                "ext": "srt",
                "cid": "XYZ",
                "languages": ["英"],
                "score": 99,
            },
        ],
    }
    items = ThunderSource()._parse_payload(payload, "ABC")
    assert len(items) == 2
    assert items[0].payload["match"] is True
    assert items[0].language == "zh-Hans"
    assert items[1].language == "en"


def test_zimuku_captcha_helpers():
    raw = b"fake-bmp"
    data = base64.b64encode(raw).decode()
    assert _captcha_image(f'<img src="data:image/bmp;base64,{data}">') is not None
    assert _js_hex("09754") == "3039373534"


def test_zimuku_search_parser():
    html = """
    <div class="item">
      <p class="tt clearfix"><a href="/detail/123.html" title="千与千寻">千与千寻</a></p>
      <div class="sublist"><table><tbody>
        <tr>
          <td class="first"><a href="/detail/123.html" title="千与千寻.zh.srt">千与千寻.zh.srt</a></td>
          <td><img alt="简体"></td>
          <td>chinese.srt</td>
        </tr>
      </tbody></table></div>
    </div>
    """
    source = ZimukuSource()
    try:
        items = source._parse_search(html)
    finally:
        source.close()
    assert len(items) == 1
    assert items[0].language == "zh-Hans"
    assert items[0].format == "srt"
    assert items[0].url.endswith("/detail/123.html")


def test_chinese_subtitle_presence(tmp_path):
    backend = FakeBackend()
    video = "/m/Movie.mkv"
    backend.files[video] = b"video"
    backend.files["/m/Movie.zh-Hans.srt"] = "1\n00:00:01,000 --> 00:00:02,000\n你好\n".encode()
    assert check_external_subtitles(backend, video)
    assert detect_chinese_subtitles(
        backend, video, {"hard_sub_detection": "off"}
    ).kind == "external"
    assert check_hard_subtitle_filename("/m/Movie.中字.mkv")


def test_chinese_subtitle_presence_soft(monkeypatch, tmp_path):
    video = tmp_path / "Movie.mkv"
    video.write_bytes(b"x")
    monkeypatch.setattr("backend.subtitle_presence.shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr(
        "backend.subtitle_presence.FfmpegProcessor.get_video_metadata",
        lambda _path: {"streams": [{"tags": {"language": "chi", "title": "简体"}}]},
    )
    assert check_soft_subtitles(str(video))


def test_zimuku_captcha_flow_with_fake_client():
    class Response:
        def __init__(self, url, status, text):
            self.url = url
            self.status_code = status
            self.text = text
            self.headers = {"Content-Type": "text/html"}
            self.content = text.encode()

        def raise_for_status(self):
            return None

    class Client:
        def __init__(self):
            self.cookies = httpx.Cookies()
            self.calls = []

        def close(self):
            return None

        def get(self, url, **kwargs):
            self.calls.append((str(url), kwargs))
            if "security_verify_img=" in str(url):
                return Response(url, 200, "verified")
            if len([c for c in self.calls if "security_verify_img=" not in c[0]]) == 1:
                html = (
                    '<img src="data:image/bmp;base64,ZmFrZQ==">'
                    '<script>self.location = "/search?q=x&security_verify_img=" + stringToHex(x)</script>'
                )
                return Response(url, 404, html)
            return Response(url, 200, "ok")

    source = ZimukuSource()
    fake = Client()
    source._client = fake
    source._ocr.get_captcha_text = lambda image_b64: "1234"
    try:
        response = source._request("https://zimuku.org/search?q=x")
    finally:
        source.close()
    assert response.text == "ok"
    assert any("security_verify_img=31323334" in call[0] for call in fake.calls)
    assert fake.cookies.get("srcurl")


def test_find_bluray_main():
    class B:
        def list_dir(self, path):
            if path.endswith("BDMV/STREAM"):
                yield SimpleNamespace(path=f"{path}/00001.m2ts", is_dir=False, size=10)
                yield SimpleNamespace(path=f"{path}/00002.m2ts", is_dir=False, size=100)
            else:
                yield SimpleNamespace(path=f"{path}/README.txt", is_dir=False, size=1)

    assert find_bluray_main(B(), "/disc").endswith("00002.m2ts")


def test_speech_onsets_detects_segments():
    silence = array("h", [0] * 8000)
    speech = array("h", [2000] * 1200)
    pcm = (silence + speech + silence + speech).tobytes()
    onsets = _speech_onsets(pcm, 8000)
    assert len(onsets) == 2
    assert abs(onsets[0] - 1.0) < 0.1
    assert abs(onsets[1] - 2.15) < 0.1


def test_is_bluray_directory():
    class B:
        def list_dir(self, path):
            yield SimpleNamespace(path=f"{path}/BDMV", is_dir=True)

    assert is_bluray_directory(B(), "/disc")
