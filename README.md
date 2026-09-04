# nexus-media-plugins

第三方插件市场（目录式索引）：`catalog.json` + `plugins/<id>.json` + `dist/*.zip`。

## 构建 / 校验（官方 CLI）

```bash
python3 tools/build_catalog.py .        # 校验 + 重算 sha256/size + 生成 catalog.json
```

- 每个插件只需维护 `plugins/<id>.json` 与 `dist/<pkg>.zip`；
- 本地包自动重算哈希，非本地包必须声明 `sha256`；
- 建议接入 GitHub Actions：push 后执行 build 并把 `catalog.json` 变更一并提交发布。
