"""Cartograph MCP Server — exposes agent tools over HTTP.

Phase 0: action_items, chat, broadcast tools.
More tools added in subsequent phases.
"""

import json
import logging
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler

from shared.db import init_pool, close_pool, execute_one
from shared.migrations import run_migrations
from cartograph_mcp.tools import action_items, chat, broadcast

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


def _get_agent_type(agent_id: str) -> str | None:
    row = execute_one(
        "SELECT agent_type FROM agent_runs WHERE agent_id = %s",
        (agent_id,),
    )
    return row["agent_type"] if row else None


TOOL_HANDLERS = {}


def register_tool(name: str):
    def decorator(fn):
        TOOL_HANDLERS[name] = fn
        return fn
    return decorator


# --- Phase 0 Tools ---

@register_tool("get_action_items_summary")
def handle_get_action_items_summary(params: dict) -> dict:
    agent_id = params["agent_id"]
    agent_type = _get_agent_type(agent_id)
    if agent_type is None:
        raise ValueError(f"Agent {agent_id} not found")
    return action_items.get_action_items_summary(agent_id, agent_type)


@register_tool("get_action_items_detail")
def handle_get_action_items_detail(params: dict) -> dict:
    agent_id = params["agent_id"]
    agent_type = _get_agent_type(agent_id)
    if agent_type is None:
        raise ValueError(f"Agent {agent_id} not found")
    return action_items.get_action_items_detail(agent_id, agent_type)


@register_tool("send_chat")
def handle_send_chat(params: dict) -> dict:
    return chat.send_chat(params["from_agent_id"], params["to_agent_id"], params["message"])


@register_tool("ack_chats")
def handle_ack_chats(params: dict) -> dict:
    count = chat.ack_chats(params["agent_id"], params["communication_ids"])
    return {"acked": count}


@register_tool("get_unacked_chats")
def handle_get_unacked_chats(params: dict) -> dict:
    return {"messages": chat.get_unacked_chats(params["agent_id"])}


@register_tool("get_chat_history")
def handle_get_chat_history(params: dict) -> dict:
    return {
        "messages": chat.get_chat_history(
            params["agent_id"],
            params.get("page", 1),
            params.get("limit", 20),
        )
    }


@register_tool("send_broadcast")
def handle_send_broadcast(params: dict) -> dict:
    return broadcast.send_broadcast(
        params["from_agent_id"], params["to_agent_type"], params["message"]
    )


@register_tool("ack_broadcast")
def handle_ack_broadcast(params: dict) -> dict:
    result = broadcast.ack_broadcast(params["agent_id"], params["communication_id"])
    return result or {"status": "already_acked"}


@register_tool("get_unacked_broadcasts")
def handle_get_unacked_broadcasts(params: dict) -> dict:
    agent_type = _get_agent_type(params["agent_id"])
    if agent_type is None:
        raise ValueError(f"Agent {params['agent_id']} not found")
    return {
        "messages": broadcast.get_unacked_broadcasts(params["agent_id"], agent_type)
    }


# --- HTTP Handler ---

class MCPHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        try:
            request = json.loads(body)
            tool_name = request.get("tool")
            params = request.get("params", {})

            if tool_name not in TOOL_HANDLERS:
                self._respond(404, {"error": f"Unknown tool: {tool_name}"})
                return

            result = TOOL_HANDLERS[tool_name](params)
            self._respond(200, {"result": result})

        except ValueError as e:
            self._respond(400, {"error": str(e)})
        except Exception as e:
            logger.exception("Tool call failed")
            self._respond(500, {"error": str(e)})

    def _respond(self, status: int, body: dict):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body, default=str).encode())

    def log_message(self, format, *args):
        logger.debug(format, *args)


def main(port: int = 8100) -> None:
    init_pool()
    run_migrations()
    server = HTTPServer(("0.0.0.0", port), MCPHandler)
    logger.info("Cartograph MCP server listening on port %d", port)
    logger.info("Registered tools: %s", list(TOOL_HANDLERS.keys()))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        server.server_close()
        close_pool()


if __name__ == "__main__":
    main()
