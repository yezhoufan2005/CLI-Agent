"""Tiny MCP stdio server used by tests: tools 'echo' and 'fail'."""

import json
import sys

print("server log line that is not JSON", flush=True)
for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue
    method, rid = msg["method"], msg["id"]
    if method == "initialize":
        result = {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "echo", "version": "1"},
        }
    elif method == "tools/list":
        if not msg.get("params", {}).get("cursor"):
            result = {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Echo text back",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                        },
                    }
                ],
                "nextCursor": "page2",
            }
        else:
            result = {"tools": [{"name": "fail", "description": "Always fails"}]}
    elif method == "tools/call":
        name, args = msg["params"]["name"], msg["params"]["arguments"]
        if name == "echo":
            result = {"content": [{"type": "text", "text": "echo: " + args.get("text", "")}]}
        else:
            result = {"content": [{"type": "text", "text": "it broke"}], "isError": True}
    else:
        print(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "error": {"code": -32601, "message": "no such method"},
                }
            ),
            flush=True,
        )
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}), flush=True)
