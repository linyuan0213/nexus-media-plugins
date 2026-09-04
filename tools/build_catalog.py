#!/usr/bin/env python3
"""nexus-market build — 插件市场目录构建/校验

用法:
    python3 tools/build_catalog.py [market_dir]

行为:
  1. 校验 plugins/<id>.json（必需字段 id/version/category/tags/download_url）
  2. 若 download_url 指向本地 zip：重算并回写 sha256/size
  3. 生成 catalog.json（插件小表 + updated_at）

任何插件不合法即以非零码退出（适合 CI）。
"""

import hashlib
import json
import os
import pathlib
import sys
from datetime import datetime, timezone

REQUIRED = ("id", "version", "category", "tags", "download_url")
MARKET_VERSION = "1.0"


def fail(msg: str) -> None:
    print(f"[market-build] 错误: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> int:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    plugins_dir = root / "plugins"
    dist_dir = root / "dist"
    if not plugins_dir.is_dir():
        fail(f"缺少 plugins/ 目录: {root}")
    entries = []
    for detail_path in sorted(plugins_dir.glob("*.json")):
        detail = json.loads(detail_path.read_text(encoding="utf-8"))
        missing = [k for k in REQUIRED if k not in detail]
        if missing:
            fail(f"{detail_path.name} 缺少字段: {missing}")
        if detail["id"] != detail_path.stem:
            fail(f"{detail_path.name} 的 id 与文件名不一致: {detail['id']}")
        download = detail.get("download_url", "")
        if download.startswith("dist/"):
            pkg = dist_dir / os.path.basename(download)
            if not pkg.is_file():
                fail(f"{detail_path.name} 引用的包不存在: {pkg}")
            data = pkg.read_bytes()
            detail["sha256"] = hashlib.sha256(data).hexdigest()
            detail["size"] = len(data)
            detail_path.write_text(json.dumps(detail, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        elif not detail.get("sha256"):
            fail(f"{detail_path.name} 非本地包且未声明 sha256")
        entries.append({"id": detail["id"], "path": f"plugins/{detail_path.name}", "updated_at": detail.get("updated_at", "")})

    catalog = {
        "market_version": MARKET_VERSION,
        "id": root.name,
        "name": root.name,
        "plugins": entries,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    catalog_path = root / "catalog.json"
    # 幂等：内容（不含 updated_at）未变化则不重写，避免 CI 反复提交
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
