"""Tools the assistant can call, plus a small validator for their inputs.

Tool inputs are streamed eagerly, so the API does not validate them for us:
every input is checked against its schema before the handler runs.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from . import db, sysinfo, weather
from .config import settings

Handler = Callable[[dict], Awaitable[Any]]
_HANDLERS: dict[str, Handler] = {}
_SCHEMAS: dict[str, dict] = {}
CLIENT_TOOLS: list[dict] = []


def tool(name: str, description: str, properties: dict, required: list[str] | None = None):
    schema = {"type": "object", "properties": properties, "required": required or []}

    def register(fn: Handler) -> Handler:
        _HANDLERS[name] = fn
        _SCHEMAS[name] = schema
        CLIENT_TOOLS.append(
            {"name": name, "description": description, "input_schema": schema, "eager_input_streaming": True}
        )
        return fn

    return register


_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool}


def validate(name: str, args: Any) -> str | None:
    """Return an error message if args do not match the tool's schema."""
    schema = _SCHEMAS.get(name)
    if schema is None:
        return f"unknown tool {name}"
    if not isinstance(args, dict):
        return "input must be an object"
    for key in schema["required"]:
        if key not in args:
            return f"missing required field {key!r}"
    for key, value in args.items():
        spec = schema["properties"].get(key)
        if spec is None:
            return f"unexpected field {key!r}"
        expected = _TYPES.get(spec.get("type", ""))
        if expected and (not isinstance(value, expected) or (spec["type"] == "integer" and isinstance(value, bool))):
            return f"field {key!r} must be {spec['type']}"
        if "enum" in spec and value not in spec["enum"]:
            return f"field {key!r} must be one of {spec['enum']}"
    return None


async def run(name: str, args: dict) -> str:
    result = await _HANDLERS[name](args)
    return result if isinstance(result, str) else json.dumps(result, default=str)


def _to_utc(when: str) -> str:
    dt = datetime.fromisoformat(when.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(settings.timezone))
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------ memory

@tool(
    "remember",
    "Save a durable fact about the user or their world to long-term memory. Use for preferences, people, "
    "projects, goals, routines, important dates and standing instructions. One self-contained fact per call.",
    {
        "content": {"type": "string", "description": "The fact, written in the third person."},
        "category": {
            "type": "string",
            "enum": ["profile", "preference", "person", "project", "goal", "routine", "date", "instruction", "fact"],
        },
        "importance": {"type": "integer", "description": "1 (trivia) to 5 (always keep in mind). Default 3."},
    },
    ["content"],
)
async def _remember(a: dict) -> dict:
    mid = db.add_memory(a["content"], a.get("category", "fact"), a.get("importance", 3))
    return {"saved": True, "id": mid}


@tool(
    "recall",
    "Search long-term memory for facts matching a query.",
    {"query": {"type": "string"}, "limit": {"type": "integer"}},
    ["query"],
)
async def _recall(a: dict) -> dict:
    hits = db.search_memories(a["query"], min(a.get("limit", 8), 25))
    return {"memories": [{"id": m["id"], "content": m["content"], "category": m["category"]} for m in hits]}


@tool(
    "update_memory",
    "Correct or refresh an existing memory by id.",
    {"id": {"type": "integer"}, "content": {"type": "string"}},
    ["id", "content"],
)
async def _update_memory(a: dict) -> dict:
    return {"updated": db.update_memory(a["id"], a["content"])}


@tool("forget", "Delete a memory by id.", {"id": {"type": "integer"}}, ["id"])
async def _forget(a: dict) -> dict:
    return {"deleted": db.delete_memory(a["id"])}


# ------------------------------------------------------------------- tasks

@tool(
    "add_task",
    "Add an item to the user's task list.",
    {
        "title": {"type": "string"},
        "notes": {"type": "string"},
        "priority": {"type": "string", "enum": ["low", "normal", "high"]},
        "due": {"type": "string", "description": "Optional due date or datetime, ISO-8601."},
    },
    ["title"],
)
async def _add_task(a: dict) -> dict:
    tid = db.add_task(a["title"], a.get("notes", ""), a.get("priority", "normal"), a.get("due"))
    return {"added": True, "id": tid}


@tool(
    "list_tasks",
    "List tasks.",
    {"status": {"type": "string", "enum": ["open", "done", "all"]}},
)
async def _list_tasks(a: dict) -> dict:
    return {"tasks": db.list_tasks(a.get("status", "open"))}


@tool("complete_task", "Mark a task as done.", {"id": {"type": "integer"}}, ["id"])
async def _complete_task(a: dict) -> dict:
    return {"completed": db.complete_task(a["id"])}


@tool("delete_task", "Delete a task permanently.", {"id": {"type": "integer"}}, ["id"])
async def _delete_task(a: dict) -> dict:
    return {"deleted": db.delete_task(a["id"])}


# --------------------------------------------------------------- reminders

@tool(
    "set_reminder",
    "Schedule a reminder. It is spoken on the dashboard and pushed to the phone (if configured) when due.",
    {
        "text": {"type": "string", "description": "What to remind the user about."},
        "when": {
            "type": "string",
            "description": "Absolute ISO-8601 datetime. Include the UTC offset; if omitted the user's timezone is assumed.",
        },
    },
    ["text", "when"],
)
async def _set_reminder(a: dict) -> dict:
    try:
        due = _to_utc(a["when"])
    except ValueError:
        return {"error": "when must be an ISO-8601 datetime, e.g. 2026-05-01T09:00:00+01:00"}
    rid = db.add_reminder(a["text"], due)
    return {"scheduled": True, "id": rid, "due_utc": due}


@tool("list_reminders", "List pending reminders.", {})
async def _list_reminders(a: dict) -> dict:
    return {"reminders": db.list_reminders("pending")}


@tool("cancel_reminder", "Cancel a pending reminder by id.", {"id": {"type": "integer"}}, ["id"])
async def _cancel_reminder(a: dict) -> dict:
    return {"cancelled": db.set_reminder_status(a["id"], "cancelled")}


# ------------------------------------------------------------------- notes

@tool(
    "save_note",
    "Save a longer piece of text (meeting notes, ideas, drafts, instructions) for later.",
    {"title": {"type": "string"}, "body": {"type": "string"}},
    ["title", "body"],
)
async def _save_note(a: dict) -> dict:
    return {"saved": True, "id": db.add_note(a["title"], a["body"])}


@tool(
    "search_notes",
    "Search saved notes. An empty query returns the most recent notes.",
    {"query": {"type": "string"}},
    ["query"],
)
async def _search_notes(a: dict) -> dict:
    return {"notes": db.search_notes(a["query"])}


# ------------------------------------------------------------------- world

@tool(
    "get_weather",
    "Current conditions and a three-day forecast for a place.",
    {"location": {"type": "string", "description": "City name. Defaults to the user's home location."}},
)
async def _get_weather(a: dict) -> dict:
    location = a.get("location") or settings.default_location
    if not location:
        return {"error": "No location given and DEFAULT_LOCATION is not configured."}
    return await weather.get_weather(location)


@tool("system_status", "Health of the server this assistant runs on: CPU, memory, disk, network, uptime.", {})
async def _system_status(a: dict) -> dict:
    return sysinfo.snapshot()


# Tools that change dashboard panels; the UI refreshes after these run.
MUTATING = {
    "remember", "update_memory", "forget", "add_task", "complete_task", "delete_task",
    "set_reminder", "cancel_reminder", "save_note",
}


def all_tools() -> list[dict]:
    tools = sorted(CLIENT_TOOLS, key=lambda t: t["name"])
    if settings.web_search:
        tools += [
            {"type": "web_search_20260209", "name": "web_search", "max_uses": 5},
            {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 3},
        ]
    return tools
