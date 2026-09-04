#!/usr/bin/env python3
"""nexus-market build — 插件市场目录构建/校验

用法:
    python3 tools/build_catalog.py [market_dir]

单一人为配置源：plugins/<plugin_id>/manifest.json（与包内 manifest 同一份）。
本工具自动完成：
  1. 校验 manifest（必需字段）
  2. 由插件目录打包 dist/<id>@<version>.zip
  3. 生成 plugins/<id>/detail.json（manifest + 包元数据，勿手改）
  4. 生成 catalog.json（保持幂等：内容未变不重写）
"""

import hashlib
import io
import json
import os
import pathlib
import sys
import zipfile
from datetime import datetime, timezone

REQUIRED = ("id", "name", "version", "category", "tags")
MARKET_VERSION = "1.0"


def fail(msg: str) -> None:
    print(f"[market-build] 错误: {msg}", file=sys.stderr)
    sys.exit(1)


def build_zip(folder: pathlib.Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(folder.rglob("*")):
            if not path.is_file():
                continue
            if path.name == "detail.json":
                continue  # 生成物不能打进包（否则哈希依赖自身导致循环漂移）
            zf.writestr(path.relative_to(folder).as_posix(), path.read_bytes())
    return buf.getvalue()


def stable_write(path: pathlib.Path, text: str, tag: str) -> bool:
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        print(f"[market-build] {tag}: 未变化")
        return False
    path.write_text(text, encoding="utf-8")
    print(f"[market-build] {tag}: 已更新")
    return True


def main() -> int:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    plugins_dir = root / "plugins"
    dist_dir = root / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)
    if not plugins_dir.is_dir():
        fail(f"缺少 plugins/ 目录: {root}")

    entries = []
    for folder in sorted(plugins_dir.iterdir()):
        manifest_path = folder / "manifest.json"
        if not manifest_path.is_file():
            fail(f"{folder.name}/manifest.json 不存在")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        missing = [k for k in REQUIRED if k not in manifest]
        if missing:
            fail(f"{manifest_path} 缺少字段: {missing}")
        if manifest["id"] != folder.name:
            fail(f"{manifest_path} 的 id 与目录名不一致: {manifest['id']}")
        # 由 manifest 打包（单一配置源 → 包），并生成 detail/meta
        data = build_zip(folder)
        sha = hashlib.sha256(data).hexdigest()
        pkg_name = f"{manifest['id']}@{manifest['version']}.zip"
        (dist_dir / pkg_name).write_bytes(data)
        detail = dict(manifest)
        detail.update(
            {
                "download_url": f"dist/{pkg_name}",
                "sha256": sha,
                "size": len(data),
                "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )
        detail_path = folder / "detail.json"
        if detail_path.is_file():
            try:
                previous = json.loads(detail_path.read_text(encoding="utf-8"))
                prev_sig = {k: v for k, v in previous.items() if k != "updated_at"}
                new_sig = {k: v for k, v in detail.items() if k != "updated_at"}
                if prev_sig == new_sig:
                    continue  # 内容未变，保留原 updated_at，避免 CI 反复提交
            except Exception:  # noqa: BLE001
                pass
        detail_path.write_text(json.dumps(detail, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        entries.append({"id": manifest["id"], "path": f"plugins/{folder.name}/detail.json", "updated_at": detail["updated_at"]})

    catalog = {
        "market_version": MARKET_VERSION,
        "id": root.name,
        "name": root.name,
        "plugins": entries,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    # 幂等比较（忽略 updated_at）
    catalog_path = root / "catalog.json"
    if catalog_path.is_file():
        try:
            previous = json.loads(catalog_path.read_text(encoding="utf-8"))
            prev_sig = {k: v for k, v in previous.items() if k != "updated_at"}
            new_sig = {k: v for k, v in catalog.items() if k != "updated_at"}
            if prev_sig == new_sig:
                print(f"[market-build] OK: {len(entries)} 个插件 → catalog.json 已是最新")
                return 0
        except Exception:  # noqa: BLE001
            pass
    catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[market-build] OK: {len(entries)} 个插件 → catalog.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
