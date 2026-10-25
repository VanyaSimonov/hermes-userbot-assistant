"""CalDAV calendar + tasks tools for the Hermes agent.

Each tool reads CalDAV credentials from the ACTIVE PROFILE's environment
(``get_scoped_secret``), so routed profiles (ivan, elena, …) each act on their
own CalDAV account, and a shared collection (``CALDAV_SHARED_USER``, default
"shared") is the common family space.

Collections follow the Radicale layout ``<base>/<user>/calendar`` and
``<base>/<user>/tasks``; point ``CALDAV_URL`` elsewhere for another server.
Handlers return short human-readable strings the agent relays to the user.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, date, timezone
from typing import Any, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]

try:
    import caldav
    from icalendar import Calendar as _ICal, Event as _IEvent, Todo as _ITodo
    CALDAV_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised where deps are absent
    CALDAV_AVAILABLE = False
    caldav = None  # type: ignore[assignment]

try:
    from gateway.platforms._shared import get_scoped_secret as _secret
except Exception:  # pragma: no cover - fallback to plain env outside the gateway
    import os

    def _secret(name: str, default: Any = None, **_kw: Any) -> Any:
        return os.environ.get(name, default)

logger = logging.getLogger(__name__)

DEFAULT_URL = "http://127.0.0.1:5232"
DEFAULT_SHARED = "shared"
DEFAULT_TZ = "Europe/Moscow"


# --------------------------------------------------------------------------- #
# Config / client
# --------------------------------------------------------------------------- #

def _cfg() -> dict[str, str]:
    return {
        "url": (_secret("CALDAV_URL", "") or DEFAULT_URL).rstrip("/"),
        "user": (_secret("CALDAV_USERNAME", "") or "").strip(),
        "password": _secret("CALDAV_PASSWORD", "") or "",
        "shared": (_secret("CALDAV_SHARED_USER", "") or DEFAULT_SHARED).strip(),
        "tz": (_secret("CALDAV_TZ", "") or DEFAULT_TZ).strip(),
    }


def _tz(cfg: dict[str, str]):
    if ZoneInfo is not None:
        try:
            return ZoneInfo(cfg["tz"])
        except Exception:
            pass
    return timezone.utc


def check_requirements() -> bool:
    """Passive probe: deps importable AND minimally configured."""
    cfg = _cfg()
    return CALDAV_AVAILABLE and bool(cfg["user"] and cfg["password"])


def _client():
    cfg = _cfg()
    return caldav.DAVClient(url=cfg["url"], username=cfg["user"], password=cfg["password"]), cfg


def _owner(cfg: dict[str, str], scope: str) -> str:
    return cfg["shared"] if scope == "shared" else cfg["user"]


def _collection(client, cfg: dict[str, str], kind: str, scope: str):
    """Return a caldav Calendar for ``kind`` ('calendar'|'tasks') and scope."""
    url = f"{cfg['url']}/{_owner(cfg, scope)}/{kind}/"
    return client.calendar(url=url)


def _scopes(scope: str) -> list[str]:
    if scope == "both":
        return ["personal", "shared"]
    return [scope if scope in ("personal", "shared") else "personal"]


# --------------------------------------------------------------------------- #
# Datetime parsing / formatting
# --------------------------------------------------------------------------- #

def _parse_dt(value: str, tzinfo) -> tuple[Any, bool]:
    """Parse a datetime. Returns (value, all_day). Accepts 'YYYY-MM-DD',
    'YYYY-MM-DD HH:MM', ISO 8601 (with or without offset)."""
    s = (value or "").strip().replace("/", "-")
    if not s:
        raise ValueError("empty date/time")
    if len(s) == 10 and s.count("-") == 2:  # date only -> all-day
        return date.fromisoformat(s), True
    s = s.replace(" ", "T", 1)
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tzinfo)
    return dt, False


def _fmt(dt) -> str:
    if isinstance(dt, datetime):
        return dt.strftime("%Y-%m-%d %H:%M")
    return str(dt)  # date (all-day)


def _comp(obj):
    """icalendar VEVENT/VTODO component from a caldav resource, best-effort."""
    try:
        return obj.icalendar_component
    except Exception:
        try:
            cal = _ICal.from_ical(obj.data)
            for c in cal.walk():
                if c.name in ("VEVENT", "VTODO"):
                    return c
        except Exception:
            return None
    return None


# --------------------------------------------------------------------------- #
# Tool schemas
# --------------------------------------------------------------------------- #

def _schema(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required},
    }


_SCOPE_PROP = {
    "type": "string", "enum": ["personal", "shared"],
    "description": "Which calendar: 'personal' (this user's own) or 'shared' (the common family/team space). Default personal.",
}
_SCOPE_PROP_BOTH = {
    "type": "string", "enum": ["personal", "shared", "both"],
    "description": "Which calendar(s) to read. Default 'both'.",
}

CAL_ADD = _schema(
    "calendar_add_event",
    "Create a calendar event. Times are 'YYYY-MM-DD HH:MM' (local timezone) or 'YYYY-MM-DD' for an all-day event.",
    {
        "title": {"type": "string", "description": "Event title/summary"},
        "start": {"type": "string", "description": "Start, e.g. '2026-10-28 09:00' or '2026-10-28' (all-day)"},
        "end": {"type": "string", "description": "End time (optional; same format as start)"},
        "duration_minutes": {"type": "integer", "description": "Duration if no end given (default 60)"},
        "scope": _SCOPE_PROP,
        "location": {"type": "string", "description": "Optional location"},
        "description": {"type": "string", "description": "Optional notes"},
    },
    ["title", "start"],
)

CAL_LIST = _schema(
    "calendar_list",
    "List upcoming calendar events within the next N days.",
    {"scope": _SCOPE_PROP_BOTH, "days": {"type": "integer", "description": "Look-ahead window in days (default 7)"}},
    [],
)

CAL_DEL = _schema(
    "calendar_delete_event",
    "Delete a calendar event matching a title (optionally on a given date).",
    {
        "query": {"type": "string", "description": "Text to match in the event title"},
        "scope": _SCOPE_PROP,
        "date": {"type": "string", "description": "Optional 'YYYY-MM-DD' to disambiguate"},
    },
    ["query"],
)

TASK_ADD = _schema(
    "task_add",
    "Add a to-do / task.",
    {
        "title": {"type": "string", "description": "Task title"},
        "due": {"type": "string", "description": "Optional due date/time ('YYYY-MM-DD' or 'YYYY-MM-DD HH:MM')"},
        "scope": _SCOPE_PROP,
        "priority": {"type": "integer", "description": "Optional 1 (high) .. 9 (low)"},
    },
    ["title"],
)

TASK_LIST = _schema(
    "task_list", "List open (incomplete) tasks.",
    {"scope": _SCOPE_PROP_BOTH}, [],
)

TASK_DONE = _schema(
    "task_complete", "Mark a task complete by matching its title.",
    {"query": {"type": "string", "description": "Text to match in the task title"}, "scope": _SCOPE_PROP},
    ["query"],
)


# --------------------------------------------------------------------------- #
# Handlers
# --------------------------------------------------------------------------- #

def _label(scope: str) -> str:
    return "общий" if scope == "shared" else "личный"


def handle_calendar_add_event(args: dict[str, Any], **_kw: Any) -> str:
    if not CALDAV_AVAILABLE:
        return "CalDAV недоступен: не установлен пакет caldav."
    try:
        client, cfg = _client()
        tzinfo = _tz(cfg)
        scope = args.get("scope") or "personal"
        start, all_day = _parse_dt(args["start"], tzinfo)
        ev = _IEvent()
        ev.add("uid", f"{uuid.uuid4()}@hermes")
        ev.add("dtstamp", datetime.now(tz=timezone.utc))
        ev.add("summary", args["title"])
        ev.add("dtstart", start)
        if args.get("end"):
            end, _ = _parse_dt(args["end"], tzinfo)
        elif all_day:
            end = start + timedelta(days=1)
        else:
            end = start + timedelta(minutes=int(args.get("duration_minutes") or 60))
        ev.add("dtend", end)
        if args.get("location"):
            ev.add("location", args["location"])
        if args.get("description"):
            ev.add("description", args["description"])
        cal = _ICal()
        cal.add("prodid", "-//hermes//caldav-organizer//EN")
        cal.add("version", "2.0")
        cal.add_component(ev)
        _collection(client, cfg, "calendar", scope).save_event(cal.to_ical().decode())
        return f"✅ Событие «{args['title']}» добавлено в {_label(scope)} календарь на {_fmt(start)}."
    except Exception as e:
        logger.warning("calendar_add_event failed: %s", e)
        return f"Не удалось добавить событие: {e}"


def handle_calendar_list(args: dict[str, Any], **_kw: Any) -> str:
    if not CALDAV_AVAILABLE:
        return "CalDAV недоступен: не установлен пакет caldav."
    try:
        client, cfg = _client()
        tzinfo = _tz(cfg)
        days = int(args.get("days") or 7)
        now = datetime.now(tz=tzinfo)
        end = now + timedelta(days=days)
        rows: list[tuple[Any, str]] = []
        for scope in _scopes(args.get("scope") or "both"):
            try:
                found = _collection(client, cfg, "calendar", scope).search(
                    start=now, end=end, event=True, expand=True)
            except Exception as e:
                logger.debug("search %s failed: %s", scope, e)
                continue
            for obj in found:
                c = _comp(obj)
                if c is None:
                    continue
                dt = c.get("dtstart").dt if c.get("dtstart") else None
                rows.append((dt, f"{_fmt(dt)} — {c.get('summary')} [{_label(scope)}]"))
        if not rows:
            return f"На ближайшие {days} дн. событий нет."
        rows.sort(key=lambda r: str(r[0]))
        return f"События на {days} дн.:\n" + "\n".join(r[1] for r in rows)
    except Exception as e:
        logger.warning("calendar_list failed: %s", e)
        return f"Не удалось получить события: {e}"


def handle_calendar_delete_event(args: dict[str, Any], **_kw: Any) -> str:
    if not CALDAV_AVAILABLE:
        return "CalDAV недоступен: не установлен пакет caldav."
    try:
        client, cfg = _client()
        scope = args.get("scope") or "personal"
        q = (args.get("query") or "").lower()
        want_date = (args.get("date") or "").strip()
        cal = _collection(client, cfg, "calendar", scope)
        deleted = 0
        for obj in cal.events():
            c = _comp(obj)
            if c is None or q not in str(c.get("summary", "")).lower():
                continue
            if want_date:
                dt = c.get("dtstart").dt if c.get("dtstart") else None
                if not dt or not str(dt).startswith(want_date):
                    continue
            obj.delete()
            deleted += 1
        return (f"🗑 Удалено событий: {deleted}." if deleted
                else f"Событие по запросу «{args.get('query')}» не найдено.")
    except Exception as e:
        logger.warning("calendar_delete_event failed: %s", e)
        return f"Не удалось удалить событие: {e}"


def handle_task_add(args: dict[str, Any], **_kw: Any) -> str:
    if not CALDAV_AVAILABLE:
        return "CalDAV недоступен: не установлен пакет caldav."
    try:
        client, cfg = _client()
        tzinfo = _tz(cfg)
        scope = args.get("scope") or "personal"
        todo = _ITodo()
        todo.add("uid", f"{uuid.uuid4()}@hermes")
        todo.add("dtstamp", datetime.now(tz=timezone.utc))
        todo.add("summary", args["title"])
        todo.add("status", "NEEDS-ACTION")
        if args.get("due"):
            due, _ = _parse_dt(args["due"], tzinfo)
            todo.add("due", due)
        if args.get("priority"):
            todo.add("priority", int(args["priority"]))
        cal = _ICal()
        cal.add("prodid", "-//hermes//caldav-organizer//EN")
        cal.add("version", "2.0")
        cal.add_component(todo)
        _collection(client, cfg, "tasks", scope).save_todo(cal.to_ical().decode())
        due_txt = f" (к {args['due']})" if args.get("due") else ""
        return f"✅ Задача «{args['title']}»{due_txt} добавлена в {_label(scope)} список."
    except Exception as e:
        logger.warning("task_add failed: %s", e)
        return f"Не удалось добавить задачу: {e}"


def handle_task_list(args: dict[str, Any], **_kw: Any) -> str:
    if not CALDAV_AVAILABLE:
        return "CalDAV недоступен: не установлен пакет caldav."
    try:
        client, cfg = _client()
        lines: list[str] = []
        for scope in _scopes(args.get("scope") or "both"):
            try:
                todos = _collection(client, cfg, "tasks", scope).todos(include_completed=False)
            except Exception as e:
                logger.debug("todos %s failed: %s", scope, e)
                continue
            for obj in todos:
                c = _comp(obj)
                if c is None:
                    continue
                due = c.get("due").dt if c.get("due") else None
                due_txt = f" (к {_fmt(due)})" if due else ""
                lines.append(f"• {c.get('summary')}{due_txt} [{_label(scope)}]")
        if not lines:
            return "Открытых задач нет."
        return "Открытые задачи:\n" + "\n".join(lines)
    except Exception as e:
        logger.warning("task_list failed: %s", e)
        return f"Не удалось получить задачи: {e}"


def handle_task_complete(args: dict[str, Any], **_kw: Any) -> str:
    if not CALDAV_AVAILABLE:
        return "CalDAV недоступен: не установлен пакет caldav."
    try:
        client, cfg = _client()
        scope = args.get("scope") or "personal"
        q = (args.get("query") or "").lower()
        done = 0
        for obj in _collection(client, cfg, "tasks", scope).todos(include_completed=False):
            c = _comp(obj)
            if c is None or q not in str(c.get("summary", "")).lower():
                continue
            try:
                obj.complete()
            except Exception:
                c = _comp(obj)
                c["status"] = "COMPLETED"
                c.add("completed", datetime.now(tz=timezone.utc))
                obj.save()
            done += 1
        return (f"✅ Отмечено выполненными: {done}." if done
                else f"Задача по запросу «{args.get('query')}» не найдена.")
    except Exception as e:
        logger.warning("task_complete failed: %s", e)
        return f"Не удалось завершить задачу: {e}"


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #

_TOOLS = (
    ("calendar_add_event",    CAL_ADD,   handle_calendar_add_event,    "📅"),
    ("calendar_list",         CAL_LIST,  handle_calendar_list,         "🗓️"),
    ("calendar_delete_event", CAL_DEL,   handle_calendar_delete_event, "🗑️"),
    ("task_add",              TASK_ADD,  handle_task_add,              "➕"),
    ("task_list",             TASK_LIST, handle_task_list,             "📋"),
    ("task_complete",         TASK_DONE, handle_task_complete,         "✔️"),
)


def register(ctx) -> None:
    """Plugin entry point — register the ``calendar`` toolset."""
    for name, schema, handler, emoji in _TOOLS:
        ctx.register_tool(
            name=name, toolset="calendar", schema=schema, handler=handler,
            check_fn=check_requirements, emoji=emoji, is_async=False,
            description=schema["description"],
            requires_env=["CALDAV_USERNAME", "CALDAV_PASSWORD"],
        )
