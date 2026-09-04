# nexus-media-plugins

Nexus Media 的第三方插件市场（**目录式索引**）。

## 目录与文件放在哪里

```text
nexus-media-plugins/
├─ catalog.json                      # 目录清单（CLI 生成）
├─ plugins/
│  └─ <plugin_id>/
│     ├─ manifest.json               # ★ 唯一人工维护的配置（与包内 manifest 同一份）
│     ├─ backend/ frontend/ …        # 插件代码/资源（会打进 zip）
│     └─ detail.json                 # CLI 生成（manifest+sha/size/download_url），勿手改
├─ dist/
│  └─ <plugin_id>@<version>.zip      # CLI 由 manifest 目录打包生成
├─ tools/build_catalog.py            # 官方构建/校验 CLI
└─ .github/workflows/…               # push 后自动构建
```

| 文件 | 维护方 | 说明 |
|---|---|---|
| `plugins/<id>/manifest.json` | **作者唯一维护** | 与包内 manifest 同一份内容 |
| `plugins/<id>/detail.json` | CLI 自动 | manifest + 包元数据（下载地址/哈希），供客户端浏览/安装 |
| `dist/<id>@<version>.zip` | CLI 自动 | 由 manifest 目录打包 |
| `catalog.json` | CLI 自动 | 轻量目录小表 |

只维护一份配置，不会再出现“详情与包内 manifest 不一致”。

## 新插件发布步骤（作者侧）

1. 新建目录 `plugins/<plugin_id>/`，写 `manifest.json`（id 必须等于目录名；字段规范见后端 `docs/plugin-marketplace.md`），放入插件代码/资源；
2. 本地校验并生成：
   ```bash
   python3 tools/build_catalog.py .
   ```
   （自动打包 zip、写 detail.json、生成 catalog.json；CLI 幂等，内容未变不重写）
3. 提交并 push → GitHub Actions 自动重建并提交 catalog/dist，raw 源即可被客户端发现。

### 放置约束（会被 CLI 拒绝）

- `plugin_id` 全局唯一，不得与 Nexus 内置插件重名；
- `manifest.json` 的 `version` 与 zip 文件名版本一致、`id` 与目录名一致；
- 包内不允许 `../`/符号链接/越界文件，不得包含密钥。

## 在本机/后端如何配置这个市场源（用户侧）

市场源**不写在 config.yaml**，存放在数据库表 `PLUGIN_MARKET_SOURCE`，由“插件 → 管理源”界面或 API 管理：

1. 打开插件市场页 → 管理源 → 添加源；
2. 填名称与 URL：`https://raw.githubusercontent.com/linyuan0213/nexus-media-plugins/master/catalog.json`（GitHub 站点建议用 raw 地址）；
3. 开启自动更新可选；保存后点“同步”即可浏览/安装/更新该源的插件。

安装的插件包落在后端数据目录 `data/plugins/<plugin_id>-<version>`，插件配置存在 `PLUGIN_CONFIG`，运行日志在插件日志页。
