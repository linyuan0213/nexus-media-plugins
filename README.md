# nexus-media-plugins

Nexus Media 的第三方插件市场（**目录式索引**）。

## 目录与文件放在哪里

```text
nexus-media-plugins/
├─ catalog.json                 # 目录清单（构建生成，不要手改）
├─ plugins/
│  └─ <plugin_id>.json          # ★ 每个插件唯一的元数据文件（作者维护）
├─ dist/
│  └─ <plugin_id>@<version>.zip # ★ 插件安装包（zip）
├─ tools/build_catalog.py       # 官方构建/校验 CLI
└─ .github/workflows/…          # push 后自动重建 catalog
```

| 文件 | 说明 |
|---|---|
| `plugins/<plugin_id>.json` | 作者维护；文件名的 id 必须与内容 `id` 一致，含 summary/描述/版本/`download_url`/sha256 等 |
| `dist/<plugin_id>@<version>.zip` | 插件包；内容为插件工程根（含 `manifest.json`），文件名版本号须与 manifest 一致 |
| `catalog.json` | 由 `tools/build_catalog.py` 生成；本地包自动重算 `sha256/size` |

## 新插件发布步骤（作者侧）

1. 本地插件工程根目录写好 `manifest.json`（id/name/version/category/tags/backend/frontend，字段规范见后端 `docs/plugin-marketplace.md`）；
2. 打包：`zip -r dist/<id>@<version>.zip .`（zip 根须直接含 manifest.json）；
3. 新增 `plugins/<id>.json` 元数据（可参考 `plugins/demo_plugin.json`），`download_url` 填 `dist/<id>@<version>.zip`；
4. 本地校验：`python3 tools/build_catalog.py .`；
5. 提交并 push → GitHub Actions 自动重建 catalog，raw 源即可被客户端发现。

### 放置约束（会被校验拒绝）

- 不允许改动其他插件目录；`plugin_id` 全局唯一且不得与 Nexus 内置插件重名（内置以 `nexus_*`/官方插件占用）；
- zip 内不允许 `../`/符号链接/超范围文件，不得包含密钥；
- `manifest.json` 的 `version` 与 zip 文件名版本一致。

## 在本机/后端如何配置这个市场源（用户侧）

市场源**不写在 config.yaml**，存放在数据库表 `PLUGIN_MARKET_SOURCE`，由“插件 → 管理源”界面或 API 管理：

1. 打开插件市场页 → 管理源 → 添加源；
2. 填名称与 URL：`https://raw.githubusercontent.com/linyuan0213/nexus-media-plugins/master/catalog.json`（GitHub 站点建议用 raw 地址）；
3. 开启自动更新可选；保存后点“同步”即可浏览/安装/更新该源的插件。

安装的插件包落在后端数据目录 `data/plugins/<plugin_id>-<version>`，插件配置存在 `PLUGIN_CONFIG`，运行日志在插件日志页。
