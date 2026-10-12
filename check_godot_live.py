"""Read-only MCP acceptance. Run with the installed Godot pipx Python."""
import asyncio
import json
from fastmcp import Client

PROJECT = "/home/bridge-user/Transferências/Keyboard Warriors/warriors/"

async def main():
    async with Client("http://127.0.0.1:9090/mcp", timeout=12) as client:
        names = {t.name for t in await client.list_tools()}
        assert "godot_health_check" in names
        health = (await client.call_tool("godot_health_check", {})).structured_content
        assert health["bridge_connected"], health
        project = (await client.call_tool("godot_inspection_get_project_info", {})).structured_content
        assert project["project_path"].rstrip("/") == PROJECT.rstrip("/"), project
        scene = (await client.call_tool("godot_inspection_get_active_scene", {})).structured_content
        tree = (await client.call_tool("godot_inspection_get_scene_tree", {"max_depth": 1, "lightweight": True})).structured_content
        print(json.dumps({"ok": True, "tools": len(names), "project": project["name"],
                          "godot_version": project["godot_version"], "scene": scene,
                          "tree": tree}, ensure_ascii=False))

if __name__ == "__main__":
    asyncio.run(main())
