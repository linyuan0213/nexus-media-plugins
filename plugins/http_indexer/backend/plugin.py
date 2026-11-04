"""索引器插件入口；provider 由 manifest 的 indexer_providers 自动注册。"""


class HttpIndexerPlugin:
    def __init__(self, ctx):
        self.ctx = ctx

    def on_enable(self) -> None:
        self.ctx.info("HTTP 索引器已启用")
