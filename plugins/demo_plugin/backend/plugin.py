"""示例插件后端"""
class DemoPlugin:
    def __init__(self, context=None, **kwargs):
        self.context = context

    def run(self, *args, **kwargs):
        return {"message": "hello from demo_plugin"}

    def agent_tool(self, name, arguments):
        return {"success": True, "data": {"tool": name, "echo": arguments.get("text", "")}}
