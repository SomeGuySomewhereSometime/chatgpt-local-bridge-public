"""Pinned upstream server, private telemetry off; small read-only context helpers.

The addon remains upstream and unchanged. Custom authoring tools stay available.
"""
import json
import logging
import os
from pathlib import Path

os.environ["BLENDER_MCP_DISABLE_TELEMETRY"] = "1"
from blender_mcp import server
from mcp.types import ToolAnnotations

rules = Path("/home/bridge-user/AGENTS.md")
if rules.is_file():
    # FastMCP 1.30 exposes a read-only public property; the pinned underlying
    # MCP server owns the initialize instructions field.
    server.mcp._mcp_server.instructions = rules.read_text(encoding="utf-8")

# Upstream INFO logs include complete Python arguments. Keep diagnostics without
# routinely copying authoring code into the service journal.
logging.getLogger().setLevel(logging.WARNING)
logging.getLogger("BlenderMCPServer").setLevel(logging.WARNING)


@server.mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
def get_context() -> str:
    """Read Blender scene, active object, selection, mode and unsaved state. No mutation."""
    code = """import bpy, json
active = bpy.context.view_layer.objects.active
print(json.dumps({"scene": bpy.context.scene.name, "file": bpy.data.filepath,
 "is_dirty": bpy.data.is_dirty, "is_saved": bpy.data.is_saved,
 "mode": bpy.context.mode, "view_layer": bpy.context.view_layer.name,
 "active_object": active.name if active else None,
 "selected_objects": [o.name for o in bpy.context.selected_objects],
 "auto_start_server": bpy.context.scene.blendermcp_auto_start_server,
 "object_count": len(bpy.context.scene.objects)}))"""
    result = server.get_blender_connection().send_command("execute_code", {"code": code})
    return result.get("result", "{}")


@server.mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
def get_scene_objects(offset: int = 0, limit: int = 100) -> str:
    """Read a page of scene objects. Includes total and truncation; does not alter selection."""
    offset, limit = max(0, int(offset)), max(1, min(int(limit), 500))
    code = f"""import bpy, json
objects = list(bpy.context.scene.objects)
page = objects[{offset}:{offset + limit}]
print(json.dumps({{"total": len(objects), "offset": {offset}, "limit": {limit},
 "has_more": {offset + limit} < len(objects),
 "objects": [{{"name": o.name, "type": o.type, "location": list(o.location)}} for o in page]}}))"""
    result = server.get_blender_connection().send_command("execute_code", {"code": code})
    return result.get("result", "{}")


if __name__ == "__main__":
    server.main()
