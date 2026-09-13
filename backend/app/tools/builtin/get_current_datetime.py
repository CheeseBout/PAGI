from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ...config import get_settings
from ..base import ToolContext, ToolSpec, register


async def _handler(ctx: ToolContext, args: dict) -> dict:
    tz_name = args.get("timezone") or get_settings().timezone
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        return {"error": f"unknown timezone: {tz_name}"}
    now = datetime.now(tz)
    return {"iso8601": now.isoformat(), "timezone": tz_name}


SPEC = register(
    ToolSpec(
        name="get_current_datetime",
        description="Return the current date and time in the given IANA timezone "
        "(default: the server's configured timezone).",
        parameters={
            "type": "object",
            "properties": {
                "timezone": {"type": "string", "description": "IANA tz, e.g. 'Asia/Ho_Chi_Minh'"}
            },
            "required": [],
        },
        handler=_handler,
        requires_approval=False,
    )
)
