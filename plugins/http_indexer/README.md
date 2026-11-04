# HTTP 索引器

通用 HTTP 索引器插件，可接入任意符合以下契约的索引服务：

```text
GET /api/indexer/info
GET /api/indexer/search
GET /api/indexer/list
```

`search` / `list` 返回：

```json
{
  "total": 100,
  "offset": 0,
  "limit": 100,
  "has_more": false,
  "next_offset": null,
  "items": []
}
```

## 使用

1. 构建插件包：
   ```bash
   python3 tools/build_catalog.py .
   ```
2. 在 Nexus Media 插件中心安装 `dist/http_indexer@1.0.0.zip`
3. 启用后填写索引器 `base_url`、`token`
4. provider 自动注册，搜索页可直接使用

## 响应体规范（api_version=1）

所有接口均为 `application/json`，时间为 ISO-8601，大小为字节。

### `GET /api/indexer/info`

```json
{
  "id": "my-indexer",
  "name": "My Indexer",
  "version": "1.0.0",
  "api_version": "1",
  "capabilities": ["search", "list"],
  "sites": [
    { "id": "site-a", "name": "Site A" }
  ],
  "resource_types": ["magnet", "ed2k"],
  "limits": {
    "max_limit": 500,
    "timeout_seconds": 30
  }
}
```

必填：`id`、`name`、`api_version`、`capabilities`。

### `GET /api/indexer/search`

查询参数：

```text
keyword   string    必填
site      string    可选
offset    int >= 0  默认 0
limit     int       1..500，默认 100
sort      string    可选
category  string    可选
since     string    可选，ISO-8601
dedup     bool      默认 true
```

### `GET /api/indexer/list`

查询参数：

```text
site      string    可选
offset    int >= 0  默认 0
limit     int       1..500，默认 100
sort      string    可选
category  string    可选
since     string    可选，ISO-8601
dedup     bool      默认 true
```

### search/list 响应

```json
{
  "total": 123,
  "offset": 0,
  "limit": 100,
  "has_more": true,
  "next_offset": 100,
  "items": [
    {
      "item_id": "stable-id",
      "site": "site-a",
      "site_name": "Site A",
      "title": "Demo",
      "category": "movie",
      "size_bytes": 1024,
      "published_at": "2026-11-03T04:00:00Z",
      "detail_url": "https://example/detail/1",
      "download_url": "magnet:?xt=urn:btih:...",
      "magnet": "magnet:?xt=urn:btih:...",
      "ed2k": null,
      "info_hash": "0123456789abcdef0123456789abcdef01234567",
      "resource_type": "magnet",
      "description": "Demo",
      "seeders": 10,
      "peers": 2,
      "extra": {}
    }
  ]
}
```

约束：

- `total` 是过滤后的真实总数，不是当前页条数
- `has_more` 为 `true` 时 `next_offset` 必须非空
- `item_id` 在同一 provider/site 内必须稳定
- `resource_type` 只能是 `magnet`、`ed2k`、`torrent`
- `magnet`、`ed2k`、`download_url` 至少一个非空
- 空结果返回 `200` 和 `items: []`
- 旧字段 `size`、`publish_date` 仍可返回，Media 会兼容，但新实现应使用 `size_bytes`、`published_at`

### 错误响应

```json
{
  "error": {
    "code": "invalid_request",
    "message": "limit 不能超过 500"
  }
}
```

HTTP 状态：

- `400` 参数错误
- `401/403` 鉴权失败
- `404` 资源不存在
- `429` 限流
- `5xx` 索引器内部错误
