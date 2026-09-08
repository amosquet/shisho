"""
utils/ical.py - Comprehensive iCalendar (RFC 5545 / RFC 7986) Generation Engine.

Supports single and multi-event calendars, rich metadata, flexible timezone handling,
all-day events, recurrence rules (RRULE), alarms (VALARM), attendees/organizers,
video conference links, and export of PocketBase reminders.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
import re
from typing import Any, Dict, List, Optional, Tuple, Union
import uuid
from zoneinfo import ZoneInfo

import dateparser
from icalendar import (
    Alarm,
    Calendar,
    Event,
    Timezone,
    Todo,
    vCalAddress,
    vDate,
    vDatetime,
    vGeo,
    vRecur,
    vText,
)


# Standard shorthand timezone mapping for convenient user inputs
TIMEZONE_ALIASES: dict[str, str] = {
    "est": "America/New_York",
    "edt": "America/New_York",
    "et": "America/New_York",
    "eastern": "America/New_York",
    "ny": "America/New_York",
    "cst": "America/Chicago",
    "cdt": "America/Chicago",
    "ct": "America/Chicago",
    "central": "America/Chicago",
    "chicago": "America/Chicago",
    "mst": "America/Denver",
    "mdt": "America/Denver",
    "mt": "America/Denver",
    "mountain": "America/Denver",
    "denver": "America/Denver",
    "pst": "America/Los_Angeles",
    "pdt": "America/Los_Angeles",
    "pt": "America/Los_Angeles",
    "pacific": "America/Los_Angeles",
    "la": "America/Los_Angeles",
    "sf": "America/Los_Angeles",
    "utc": "UTC",
    "gmt": "UTC",
    "uk": "Europe/London",
    "london": "Europe/London",
    "bst": "Europe/London",
    "cet": "Europe/Paris",
    "cest": "Europe/Paris",
    "paris": "Europe/Paris",
    "berlin": "Europe/Berlin",
    "tokyo": "Asia/Tokyo",
    "jst": "Asia/Tokyo",
    "jp": "Asia/Tokyo",
    "sydney": "Australia/Sydney",
    "aest": "Australia/Sydney",
}


def resolve_timezone(tz_input: Optional[str]) -> Optional[ZoneInfo]:
    """Resolves a timezone string or alias to a ZoneInfo object, or None if invalid/empty."""
    if not tz_input or not str(tz_input).strip():
        return None

    cleaned = str(tz_input).strip()
    norm = cleaned.lower().replace(" ", "_")
    target = TIMEZONE_ALIASES.get(norm, cleaned)

    try:
        return ZoneInfo(target)
    except Exception:
        # Fallback case-insensitive search or default to None
        for alias, iana in TIMEZONE_ALIASES.items():
            if alias == norm:
                try:
                    return ZoneInfo(iana)
                except Exception:
                    pass
        return None


def parse_datetime_flexible(
    val: Union[str, datetime, date],
    default_tz: Optional[str] = None,
    prefer_future: bool = True,
) -> Union[datetime, date]:
    """
    Parses a string, date, or datetime into a datetime or date object.
    Supports ISO formats, natural language strings (e.g. 'tomorrow at 3pm'),
    and resolves timezone information.
    """
    if isinstance(val, datetime):
        if val.tzinfo is None and default_tz:
            tz = resolve_timezone(default_tz)
            if tz:
                return val.replace(tzinfo=tz)
        return val

    if isinstance(val, date):
        return val

    val_str = str(val).strip()
    if not val_str:
        raise ValueError("Cannot parse empty datetime string.")

    # Check for strictly YYYY-MM-DD all-day date format
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", val_str):
        try:
            return date.fromisoformat(val_str)
        except Exception:
            pass

    # Try standard ISO format
    try:
        clean_iso = val_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_iso)
        if dt.tzinfo is None and default_tz:
            tz = resolve_timezone(default_tz)
            if tz:
                dt = dt.replace(tzinfo=tz)
        return dt
    except Exception:
        pass

    # Natural language parsing with dateparser
    tz_obj = resolve_timezone(default_tz)
    settings: dict[str, Any] = {
        "RETURN_AS_TIMEZONE_AWARE": True if tz_obj else False,
    }
    if prefer_future:
        settings["PREFER_DATES_FROM"] = "future"
    if tz_obj:
        settings["TIMEZONE"] = str(tz_obj)
        settings["TO_TIMEZONE"] = str(tz_obj)

    parsed = dateparser.parse(val_str, settings=settings)
    if parsed is not None:
        return parsed

    raise ValueError(f"Could not parse datetime string: '{val_str}'")


# =========================================================================
# Data Models
# =========================================================================

@dataclass
class ICalAlarm:
    """Represents a VALARM notification component."""
    trigger_minutes_before: Optional[int] = None
    trigger_duration: Optional[str] = None  # e.g. "-PT15M", "-P1D"
    trigger_datetime: Optional[datetime] = None
    action: str = "DISPLAY"  # "DISPLAY", "AUDIO", "EMAIL"
    description: Optional[str] = None
    summary: Optional[str] = None
    repeat: Optional[int] = None
    duration: Optional[str] = None  # e.g. "PT5M"


@dataclass
class ICalAttendee:
    """Represents an ATTENDEE in an event."""
    email: str
    name: Optional[str] = None
    role: str = "REQ-PARTICIPANT"  # "REQ-PARTICIPANT", "OPT-PARTICIPANT", "CHAIR", "NON-PARTICIPANT"
    partstat: str = "NEEDS-ACTION"  # "NEEDS-ACTION", "ACCEPTED", "DECLINED", "TENTATIVE"
    rsvp: bool = False
    cutype: str = "INDIVIDUAL"  # "INDIVIDUAL", "GROUP", "RESOURCE", "ROOM"


@dataclass
class ICalOrganizer:
    """Represents an ORGANIZER in an event."""
    email: str
    name: Optional[str] = None


@dataclass
class RecurrenceRule:
    """Represents an RRULE recurrence pattern."""
    freq: str  # "DAILY", "WEEKLY", "MONTHLY", "YEARLY"
    interval: int = 1
    count: Optional[int] = None
    until: Optional[Union[datetime, date]] = None
    byday: Optional[List[str]] = None  # e.g. ["MO", "WE", "FR"] or ["2MO", "-1FR"]
    bymonthday: Optional[List[int]] = None
    bymonth: Optional[List[int]] = None
    byyearday: Optional[List[int]] = None
    byweekno: Optional[List[int]] = None
    bysetpos: Optional[List[int]] = None
    wkst: Optional[str] = None  # "MO", "SU"


@dataclass
class ICalEvent:
    """Full representation of an iCalendar VEVENT."""
    summary: str
    start: Union[datetime, date]
    end: Optional[Union[datetime, date]] = None
    duration_minutes: Optional[int] = None
    description: Optional[str] = None
    location: Optional[str] = None
    geo: Optional[Tuple[float, float]] = None  # (latitude, longitude)
    url: Optional[str] = None
    conference_url: Optional[str] = None  # RFC 7986 CONFERENCE property (e.g. Zoom, Meet)
    categories: Optional[List[str]] = None
    status: str = "CONFIRMED"  # "CONFIRMED", "TENTATIVE", "CANCELLED"
    priority: Optional[int] = None  # 0 to 9
    classification: str = "PUBLIC"  # "PUBLIC", "PRIVATE", "CONFIDENTIAL"
    transparency: str = "OPAQUE"  # "OPAQUE" (busy), "TRANSPARENT" (free)
    all_day: bool = False
    timezone: Optional[str] = None
    uid: Optional[str] = None
    sequence: int = 0
    created: Optional[datetime] = None
    last_modified: Optional[datetime] = None
    organizer: Optional[ICalOrganizer] = None
    attendees: Optional[List[ICalAttendee]] = None
    recurrence: Optional[RecurrenceRule] = None
    exdates: Optional[List[Union[datetime, date]]] = None
    # If alarms is None, a default 15-minute alarm is attached to timed events (none for all-day).
    # If alarms is an explicit list (even empty []), that exact configuration is used.
    alarms: Optional[List[ICalAlarm]] = None
    color: Optional[str] = None  # RFC 7986 COLOR (e.g. "#3498db" or "blue")
    apple_structured_location: Optional[dict] = None


@dataclass
class ICalTodo:
    """Full representation of an iCalendar VTODO (To-Do / Task / Action Item)."""
    summary: str
    due: Optional[Union[datetime, date]] = None
    start: Optional[Union[datetime, date]] = None
    duration_minutes: Optional[int] = None
    description: Optional[str] = None
    location: Optional[str] = None
    status: str = "NEEDS-ACTION"  # "NEEDS-ACTION", "IN-PROCESS", "COMPLETED", "CANCELLED"
    completed: Optional[datetime] = None
    percent_complete: Optional[int] = None  # 0 to 100
    priority: Optional[int] = None  # 1 (high) to 9 (low)
    classification: str = "PUBLIC"  # "PUBLIC", "PRIVATE", "CONFIDENTIAL"
    timezone: Optional[str] = None
    uid: Optional[str] = None
    sequence: int = 0
    created: Optional[datetime] = None
    last_modified: Optional[datetime] = None
    organizer: Optional[ICalOrganizer] = None
    attendees: Optional[List[ICalAttendee]] = None
    recurrence: Optional[RecurrenceRule] = None
    alarms: Optional[List[ICalAlarm]] = None
    categories: Optional[List[str]] = None
    url: Optional[str] = None
    all_day: bool = False


# =========================================================================
# Calendar Builder
# =========================================================================

def build_ical_calendar(
    events: Optional[List[ICalEvent]] = None,
    todos: Optional[List[ICalTodo]] = None,
    cal_name: Optional[str] = None,
    cal_timezone: Optional[str] = None,
    method: str = "PUBLISH",
    prodid: str = "-//Shisho//Calendar Generator//EN",
) -> bytes:
    """
    Builds a fully compliant RFC 5545 / RFC 7986 iCalendar byte stream.
    Strictly wraps CRLF lines at 75 octets and sets up VTIMEZONE definitions.
    Supports both VEVENT (calendar events) and VTODO (action items/tasks).
    """
    cal = Calendar()
    cal.add("prodid", prodid)
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", method.upper())

    if cal_name:
        cal.add("x-wr-calname", cal_name)
        cal.add("name", cal_name)

    if cal_timezone:
        cal.add("x-wr-timezone", cal_timezone)

    events_list = events or []
    todos_list = todos or []

    # Collect timezones to embed VTIMEZONE components
    embedded_tz_names: set[str] = set()
    for item in (*events_list, *todos_list):
        tz_candidate = item.timezone
        item_time = getattr(item, "start", None) or getattr(item, "due", None)
        if not tz_candidate and isinstance(item_time, datetime) and item_time.tzinfo:
            tz_candidate = str(item_time.tzinfo)

        if tz_candidate:
            tz_obj = resolve_timezone(tz_candidate)
            if tz_obj and str(tz_obj) not in ("UTC", "GMT"):
                tz_key = str(tz_obj)
                if tz_key not in embedded_tz_names:
                    embedded_tz_names.add(tz_key)
                    try:
                        cal.add_component(Timezone.from_tzinfo(tz_obj))
                    except Exception:
                        pass

    now_utc = datetime.now(timezone.utc)

    for ev in events_list:
        vevent = Event()

        # Identification & audit
        event_uid = ev.uid or f"{uuid.uuid4()}@shisho.local"
        vevent.add("uid", event_uid)
        vevent.add("dtstamp", now_utc)
        vevent.add("sequence", ev.sequence)
        vevent.add("created", ev.created or now_utc)
        vevent.add("last-modified", ev.last_modified or now_utc)

        # Summary / Title
        vevent.add("summary", ev.summary)

        # Start & End / Duration
        if ev.all_day:
            start_date = ev.start.date() if isinstance(ev.start, datetime) else ev.start
            vevent.add("dtstart", start_date)

            if ev.end:
                end_date = ev.end.date() if isinstance(ev.end, datetime) else ev.end
                # RFC 5545 specifies DTEND for all-day events is exclusive
                if end_date <= start_date:
                    end_date = start_date + timedelta(days=1)
                vevent.add("dtend", end_date)
            elif ev.duration_minutes:
                days = max(1, ev.duration_minutes // 1440)
                vevent.add("dtend", start_date + timedelta(days=days))
            else:
                # Default single all-day event ends next day
                vevent.add("dtend", start_date + timedelta(days=1))
        else:
            # Timed event
            start_dt = ev.start
            if isinstance(start_dt, date) and not isinstance(start_dt, datetime):
                start_dt = datetime.combine(start_dt, time(9, 0))

            # Apply timezone if start is naive and timezone specified
            if isinstance(start_dt, datetime) and start_dt.tzinfo is None and ev.timezone:
                tz_obj = resolve_timezone(ev.timezone)
                if tz_obj:
                    start_dt = start_dt.replace(tzinfo=tz_obj)

            vevent.add("dtstart", start_dt)

            if ev.end:
                end_dt = ev.end
                if isinstance(end_dt, date) and not isinstance(end_dt, datetime):
                    end_dt = datetime.combine(end_dt, time(10, 0))
                if isinstance(end_dt, datetime) and end_dt.tzinfo is None and ev.timezone:
                    tz_obj = resolve_timezone(ev.timezone)
                    if tz_obj:
                        end_dt = end_dt.replace(tzinfo=tz_obj)
                vevent.add("dtend", end_dt)
            elif ev.duration_minutes is not None:
                vevent.add("duration", timedelta(minutes=ev.duration_minutes))
            else:
                # Default 1 hour duration
                if isinstance(start_dt, datetime):
                    vevent.add("dtend", start_dt + timedelta(hours=1))

        # Text and metadata
        if ev.description:
            vevent.add("description", ev.description)
        if ev.location:
            vevent.add("location", ev.location)
        if ev.url:
            vevent.add("url", ev.url)
        if ev.categories:
            vevent.add("categories", ev.categories)

        if ev.geo:
            vevent.add("geo", ev.geo)

        if ev.status:
            vevent.add("status", ev.status.upper())
        if ev.priority is not None:
            vevent.add("priority", int(ev.priority))
        if ev.classification:
            vevent.add("class", ev.classification.upper())
        if ev.transparency:
            vevent.add("transp", ev.transparency.upper())

        # RFC 7986 modern properties
        if ev.conference_url:
            vevent.add(
                "conference",
                ev.conference_url,
                parameters={"VALUE": "URI", "FEATURE": "VIDEO"},
            )
        if ev.color:
            vevent.add("color", ev.color)

        # Apple structured location extension
        if ev.apple_structured_location:
            loc_data = ev.apple_structured_location
            title = loc_data.get("title", ev.location or "Location")
            lat = loc_data.get("latitude")
            lon = loc_data.get("longitude")
            radius = loc_data.get("radius", 100)
            if lat is not None and lon is not None:
                uri = f"geo:{lat},{lon}"
                vevent.add(
                    "x-apple-structured-location",
                    uri,
                    parameters={
                        "VALUE": "URI",
                        "X-ADDRESS": title,
                        "X-APPLE-RADIUS": str(radius),
                        "X-TITLE": title,
                    },
                )

        # Organizer
        if ev.organizer:
            org_addr = vCalAddress(f"MAILTO:{ev.organizer.email.strip()}")
            if ev.organizer.name:
                org_addr.params["CN"] = vText(ev.organizer.name)
            vevent["organizer"] = org_addr

        # Attendees
        if ev.attendees:
            for att in ev.attendees:
                att_addr = vCalAddress(f"MAILTO:{att.email.strip()}")
                if att.name:
                    att_addr.params["CN"] = vText(att.name)
                if att.role:
                    att_addr.params["ROLE"] = vText(att.role.upper())
                if att.partstat:
                    att_addr.params["PARTSTAT"] = vText(att.partstat.upper())
                att_addr.params["RSVP"] = vText("TRUE" if att.rsvp else "FALSE")
                if att.cutype:
                    att_addr.params["CUTYPE"] = vText(att.cutype.upper())
                vevent.add("attendee", att_addr, encode=0)

        # Recurrence (RRULE)
        if ev.recurrence:
            r = ev.recurrence
            r_dict: dict[str, Any] = {"freq": r.freq.upper()}
            if r.interval and r.interval > 1:
                r_dict["interval"] = r.interval
            if r.count:
                r_dict["count"] = r.count
            if r.until:
                r_dict["until"] = r.until
            if r.byday:
                r_dict["byday"] = [d.upper() for d in r.byday]
            if r.bymonthday:
                r_dict["bymonthday"] = r.bymonthday
            if r.bymonth:
                r_dict["bymonth"] = r.bymonth
            if r.byyearday:
                r_dict["byyearday"] = r.byyearday
            if r.byweekno:
                r_dict["byweekno"] = r.byweekno
            if r.bysetpos:
                r_dict["bysetpos"] = r.bysetpos
            if r.wkst:
                r_dict["wkst"] = r.wkst.upper()
            vevent.add("rrule", r_dict)

        # Exception Dates (EXDATE)
        if ev.exdates:
            for ex in ev.exdates:
                vevent.add("exdate", ex)

        # Alarms (VALARM)
        # Default policy:
        # - If alarms is None and not all_day -> default 15-minute display popup
        # - If alarms is None and all_day -> no alarm (as per user configuration)
        # - If alarms is provided (list) -> use specified alarms (empty list [] means none)
        alarms_to_process: List[ICalAlarm] = []
        if ev.alarms is None:
            if not ev.all_day:
                alarms_to_process = [
                    ICalAlarm(
                        trigger_minutes_before=15,
                        description=f"Reminder: {ev.summary}",
                    )
                ]
        else:
            alarms_to_process = ev.alarms

        for al in alarms_to_process:
            alarm_comp = Alarm()
            alarm_comp.add("action", al.action.upper())
            alarm_comp.add("description", al.description or f"Reminder: {ev.summary}")

            if al.trigger_minutes_before is not None:
                alarm_comp.add("trigger", timedelta(minutes=-abs(al.trigger_minutes_before)))
            elif al.trigger_duration:
                # Custom duration string e.g. "-PT30M"
                dur_str = al.trigger_duration.strip()
                if not dur_str.startswith("-") and not dur_str.startswith("+"):
                    dur_str = f"-{dur_str}"
                alarm_comp.add("trigger", vText(dur_str))
            elif al.trigger_datetime:
                alarm_comp.add("trigger", al.trigger_datetime)
            else:
                alarm_comp.add("trigger", timedelta(minutes=-15))

            if al.repeat and al.duration:
                alarm_comp.add("repeat", al.repeat)
                alarm_comp.add("duration", vText(al.duration))

            if al.summary and al.action.upper() == "EMAIL":
                alarm_comp.add("summary", al.summary)

            vevent.add_component(alarm_comp)

        cal.add_component(vevent)

    # Process VTODO components
    for td in todos_list:
        vtodo = Todo()

        # Identification & audit
        todo_uid = td.uid or f"{uuid.uuid4()}@shisho.local"
        vtodo.add("uid", todo_uid)
        vtodo.add("dtstamp", now_utc)
        vtodo.add("sequence", td.sequence)
        vtodo.add("created", td.created or now_utc)
        vtodo.add("last-modified", td.last_modified or now_utc)

        # Summary / Title
        vtodo.add("summary", td.summary)

        # Due date / deadline
        if td.due:
            if td.all_day:
                due_date = td.due.date() if isinstance(td.due, datetime) else td.due
                vtodo.add("due", due_date)
            else:
                due_dt = td.due
                if isinstance(due_dt, date) and not isinstance(due_dt, datetime):
                    due_dt = datetime.combine(due_dt, time(17, 0))
                if isinstance(due_dt, datetime) and due_dt.tzinfo is None and td.timezone:
                    tz_obj = resolve_timezone(td.timezone)
                    if tz_obj:
                        due_dt = due_dt.replace(tzinfo=tz_obj)
                vtodo.add("due", due_dt)

        # Start date
        if td.start:
            if td.all_day:
                start_date = td.start.date() if isinstance(td.start, datetime) else td.start
                vtodo.add("dtstart", start_date)
            else:
                start_dt = td.start
                if isinstance(start_dt, date) and not isinstance(start_dt, datetime):
                    start_dt = datetime.combine(start_dt, time(9, 0))
                if isinstance(start_dt, datetime) and start_dt.tzinfo is None and td.timezone:
                    tz_obj = resolve_timezone(td.timezone)
                    if tz_obj:
                        start_dt = start_dt.replace(tzinfo=tz_obj)
                vtodo.add("dtstart", start_dt)

        if td.duration_minutes is not None:
            vtodo.add("duration", timedelta(minutes=td.duration_minutes))

        # Text and metadata
        if td.description:
            vtodo.add("description", td.description)
        if td.location:
            vtodo.add("location", td.location)
        if td.url:
            vtodo.add("url", td.url)
        if td.categories:
            vtodo.add("categories", td.categories)

        if td.status:
            vtodo.add("status", td.status.upper())
        if td.completed:
            vtodo.add("completed", td.completed)
        if td.percent_complete is not None:
            vtodo.add("percent-complete", int(td.percent_complete))
        if td.priority is not None:
            vtodo.add("priority", int(td.priority))
        if td.classification:
            vtodo.add("class", td.classification.upper())

        # Organizer
        if td.organizer:
            org_addr = vCalAddress(f"MAILTO:{td.organizer.email.strip()}")
            if td.organizer.name:
                org_addr.params["CN"] = vText(td.organizer.name)
            vtodo["organizer"] = org_addr

        # Attendees
        if td.attendees:
            for att in td.attendees:
                att_addr = vCalAddress(f"MAILTO:{att.email.strip()}")
                if att.name:
                    att_addr.params["CN"] = vText(att.name)
                if att.role:
                    att_addr.params["ROLE"] = vText(att.role.upper())
                if att.partstat:
                    att_addr.params["PARTSTAT"] = vText(att.partstat.upper())
                att_addr.params["RSVP"] = vText("TRUE" if att.rsvp else "FALSE")
                if att.cutype:
                    att_addr.params["CUTYPE"] = vText(att.cutype.upper())
                vtodo.add("attendee", att_addr, encode=0)

        # Recurrence (RRULE)
        if td.recurrence:
            r = td.recurrence
            r_dict: dict[str, Any] = {"freq": r.freq.upper()}
            if r.interval and r.interval > 1:
                r_dict["interval"] = r.interval
            if r.count:
                r_dict["count"] = r.count
            if r.until:
                r_dict["until"] = r.until
            if r.byday:
                r_dict["byday"] = [d.upper() for d in r.byday]
            if r.bymonthday:
                r_dict["bymonthday"] = r.bymonthday
            if r.bymonth:
                r_dict["bymonth"] = r.bymonth
            if r.byyearday:
                r_dict["byyearday"] = r.byyearday
            if r.byweekno:
                r_dict["byweekno"] = r.byweekno
            if r.bysetpos:
                r_dict["bysetpos"] = r.bysetpos
            if r.wkst:
                r_dict["wkst"] = r.wkst.upper()
            vtodo.add("rrule", r_dict)

        # Alarms (VALARM)
        alarms_to_process: List[ICalAlarm] = []
        if td.alarms is None:
            if td.due and not td.all_day:
                alarms_to_process = [
                    ICalAlarm(
                        trigger_minutes_before=15,
                        description=f"Task Due: {td.summary}",
                    )
                ]
        else:
            alarms_to_process = td.alarms

        for al in alarms_to_process:
            alarm_comp = Alarm()
            alarm_comp.add("action", al.action.upper())
            alarm_comp.add("description", al.description or f"Task Reminder: {td.summary}")

            if al.trigger_minutes_before is not None:
                alarm_comp.add("trigger", timedelta(minutes=-abs(al.trigger_minutes_before)))
            elif al.trigger_duration:
                dur_str = al.trigger_duration.strip()
                if not dur_str.startswith("-") and not dur_str.startswith("+"):
                    dur_str = f"-{dur_str}"
                alarm_comp.add("trigger", vText(dur_str))
            elif al.trigger_datetime:
                alarm_comp.add("trigger", al.trigger_datetime)
            else:
                alarm_comp.add("trigger", timedelta(minutes=-15))

            vtodo.add_component(alarm_comp)

        cal.add_component(vtodo)

    return cal.to_ical()


def create_single_event_ics(
    summary: str,
    start: Union[str, datetime, date],
    end: Optional[Union[str, datetime, date]] = None,
    duration_minutes: Optional[int] = None,
    description: Optional[str] = None,
    location: Optional[str] = None,
    conference_url: Optional[str] = None,
    url: Optional[str] = None,
    all_day: bool = False,
    timezone: Optional[str] = None,
    alarm_minutes_before: Optional[int] = None,
    categories: Optional[List[str]] = None,
    attendees: Optional[List[Dict[str, Any]]] = None,
    organizer: Optional[Dict[str, str]] = None,
    recurrence: Optional[Dict[str, Any]] = None,
) -> bytes:
    """
    Convenience helper to create an RFC 5545 .ics payload for a single event.
    """
    parsed_start = parse_datetime_flexible(start, default_tz=timezone)
    parsed_end = parse_datetime_flexible(end, default_tz=timezone) if end else None

    # Attendees parsing
    attendee_objs: Optional[List[ICalAttendee]] = None
    if attendees:
        attendee_objs = []
        for att in attendees:
            if isinstance(att, dict) and att.get("email"):
                attendee_objs.append(
                    ICalAttendee(
                        email=att["email"],
                        name=att.get("name"),
                        role=att.get("role", "REQ-PARTICIPANT"),
                        partstat=att.get("partstat", "NEEDS-ACTION"),
                        rsvp=bool(att.get("rsvp", False)),
                        cutype=att.get("cutype", "INDIVIDUAL"),
                    )
                )

    # Organizer parsing
    organizer_obj: Optional[ICalOrganizer] = None
    if organizer and organizer.get("email"):
        organizer_obj = ICalOrganizer(
            email=organizer["email"],
            name=organizer.get("name"),
        )

    # Recurrence parsing
    recurrence_obj: Optional[RecurrenceRule] = None
    if recurrence and recurrence.get("freq"):
        recurrence_obj = RecurrenceRule(
            freq=recurrence["freq"],
            interval=int(recurrence.get("interval", 1)),
            count=int(recurrence["count"]) if recurrence.get("count") else None,
            until=parse_datetime_flexible(recurrence["until"], default_tz=timezone) if recurrence.get("until") else None,
            byday=recurrence.get("byday") if isinstance(recurrence.get("byday"), list) else ([recurrence["byday"]] if recurrence.get("byday") else None),
            bymonthday=recurrence.get("bymonthday"),
            bymonth=recurrence.get("bymonth"),
            bysetpos=recurrence.get("bysetpos"),
            wkst=recurrence.get("wkst"),
        )

    # Alarm handling
    alarms: Optional[List[ICalAlarm]] = None
    if alarm_minutes_before is not None:
        if alarm_minutes_before > 0:
            alarms = [
                ICalAlarm(
                    trigger_minutes_before=alarm_minutes_before,
                    description=f"Reminder: {summary}",
                )
            ]
        elif alarm_minutes_before == 0:
            alarms = []  # No alarm requested

    event = ICalEvent(
        summary=summary,
        start=parsed_start,
        end=parsed_end,
        duration_minutes=duration_minutes,
        description=description,
        location=location,
        conference_url=conference_url,
        url=url,
        all_day=all_day,
        timezone=timezone,
        categories=categories,
        attendees=attendee_objs,
        organizer=organizer_obj,
        recurrence=recurrence_obj,
        alarms=alarms,
    )

    return build_ical_calendar([event], cal_name=summary, cal_timezone=timezone)


def reminders_to_ical_events(
    records: List[Any],
    user_timezone: Optional[str] = None,
) -> List[ICalEvent]:
    """
    Converts a list of PocketBase reminder records into ICalEvent objects.
    """
    events: List[ICalEvent] = []

    for record in records:
        r_id = getattr(record, "id", "") or (record.get("id", "") if isinstance(record, dict) else "")
        r_text = getattr(record, "reminder_text", "") or (record.get("reminder_text", "") if isinstance(record, dict) else "Reminder")
        r_time = getattr(record, "remind_at", None) or (record.get("remind_at", None) if isinstance(record, dict) else None)
        r_sent = getattr(record, "is_sent", False) or (record.get("is_sent", False) if isinstance(record, dict) else False)

        if not r_time:
            continue

        try:
            parsed_dt = parse_datetime_flexible(r_time, default_tz=user_timezone)
        except Exception:
            continue

        status = "CANCELLED" if r_sent else "CONFIRMED"
        desc = f"Shisho Reminder (ID: {r_id})"
        if r_sent:
            desc += " - Status: Completed/Sent"

        ev = ICalEvent(
            summary=r_text,
            start=parsed_dt,
            duration_minutes=30,
            description=desc,
            status=status,
            timezone=user_timezone,
            uid=f"reminder-{r_id}@shisho.local" if r_id else None,
            categories=["Reminders", "Shisho"],
            alarms=[
                ICalAlarm(
                    trigger_minutes_before=15,
                    description=f"Reminder: {r_text}",
                )
            ] if not r_sent else [],
        )
        events.append(ev)

    return events


def create_single_todo_ics(
    summary: str,
    due: Optional[Union[str, datetime, date]] = None,
    start: Optional[Union[str, datetime, date]] = None,
    description: Optional[str] = None,
    location: Optional[str] = None,
    priority: Optional[int] = None,
    status: str = "NEEDS-ACTION",
    percent_complete: Optional[int] = None,
    alarm_minutes_before: Optional[int] = None,
    timezone: Optional[str] = None,
    all_day: bool = False,
    categories: Optional[List[str]] = None,
    recurrence: Optional[Dict[str, Any]] = None,
) -> bytes:
    """
    Convenience helper to create an RFC 5545 .ics payload for a single VTODO task.
    """
    parsed_due = parse_datetime_flexible(due, default_tz=timezone) if due else None
    parsed_start = parse_datetime_flexible(start, default_tz=timezone) if start else None

    recurrence_obj: Optional[RecurrenceRule] = None
    if recurrence and recurrence.get("freq"):
        recurrence_obj = RecurrenceRule(
            freq=recurrence["freq"],
            interval=int(recurrence.get("interval", 1)),
            count=int(recurrence["count"]) if recurrence.get("count") else None,
            until=parse_datetime_flexible(recurrence["until"], default_tz=timezone) if recurrence.get("until") else None,
            byday=recurrence.get("byday") if isinstance(recurrence.get("byday"), list) else ([recurrence["byday"]] if recurrence.get("byday") else None),
            bymonthday=recurrence.get("bymonthday"),
            bymonth=recurrence.get("bymonth"),
            bysetpos=recurrence.get("bysetpos"),
            wkst=recurrence.get("wkst"),
        )

    alarms: Optional[List[ICalAlarm]] = None
    if alarm_minutes_before is not None:
        if alarm_minutes_before > 0:
            alarms = [
                ICalAlarm(
                    trigger_minutes_before=alarm_minutes_before,
                    description=f"Task Due: {summary}",
                )
            ]
        elif alarm_minutes_before == 0:
            alarms = []

    todo = ICalTodo(
        summary=summary,
        due=parsed_due,
        start=parsed_start,
        description=description,
        location=location,
        priority=priority,
        status=status,
        percent_complete=percent_complete,
        alarms=alarms,
        timezone=timezone,
        all_day=all_day,
        categories=categories,
        recurrence=recurrence_obj,
    )

    return build_ical_calendar(todos=[todo], cal_name=summary, cal_timezone=timezone)


def reminders_to_ical_todos(
    records: List[Any],
    user_timezone: Optional[str] = None,
) -> List[ICalTodo]:
    """
    Converts a list of PocketBase reminder records into ICalTodo task objects.
    Sent/completed reminders are marked as COMPLETED with 100% completion.
    """
    todos: List[ICalTodo] = []

    for record in records:
        r_id = getattr(record, "id", "") or (record.get("id", "") if isinstance(record, dict) else "")
        r_text = getattr(record, "reminder_text", "") or (record.get("reminder_text", "") if isinstance(record, dict) else "Task")
        r_time = getattr(record, "remind_at", None) or (record.get("remind_at", None) if isinstance(record, dict) else None)
        r_sent = getattr(record, "is_sent", False) or (record.get("is_sent", False) if isinstance(record, dict) else False)

        parsed_dt = None
        if r_time:
            try:
                parsed_dt = parse_datetime_flexible(r_time, default_tz=user_timezone)
            except Exception:
                pass

        status = "COMPLETED" if r_sent else "NEEDS-ACTION"
        pct = 100 if r_sent else 0
        desc = f"Shisho Reminder (ID: {r_id})"

        td = ICalTodo(
            summary=r_text,
            due=parsed_dt,
            description=desc,
            status=status,
            percent_complete=pct,
            completed=datetime.now(timezone.utc) if r_sent else None,
            timezone=user_timezone,
            uid=f"todo-reminder-{r_id}@shisho.local" if r_id else None,
            categories=["Reminders", "Tasks"],
            alarms=[
                ICalAlarm(
                    trigger_minutes_before=15,
                    description=f"Task Due: {r_text}",
                )
            ] if not r_sent and parsed_dt else [],
        )
        todos.append(td)

    return todos
