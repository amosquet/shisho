"""
utils/reading_pacer.py - Reading Pacing Schedule Generator for Shisho.

Calculates realistic day-by-day reading milestones across target dates,
durations, or daily page budgets, and produces iCalendar VEVENT or VTODO files.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
import math
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from utils.ical import (
    ICalAlarm,
    ICalEvent,
    ICalTodo,
    build_ical_calendar,
    parse_datetime_flexible,
    resolve_timezone,
)


DAY_NAME_TO_INT: dict[str, int] = {
    "mo": 0, "mon": 0, "monday": 0,
    "tu": 1, "tue": 1, "tuesday": 1,
    "we": 2, "wed": 2, "wednesday": 2,
    "th": 3, "thu": 3, "thursday": 3,
    "fr": 4, "fri": 4, "friday": 4,
    "sa": 5, "sat": 5, "saturday": 5,
    "su": 6, "sun": 6, "sunday": 6,
}


def render_progress_bar(percentage: int, length: int = 10) -> str:
    """Renders a text progress bar: e.g. [████░░░░░░] 40%"""
    clamped = max(0, min(100, percentage))
    filled_len = int(round((clamped / 100.0) * length))
    empty_len = length - filled_len
    bar = "█" * filled_len + "░" * empty_len
    return f"[{bar}] {clamped}%"


@dataclass
class ReadingSession:
    """Represents a single planned reading session / checkpoint."""
    session_index: int
    date: date
    start_page: int
    end_page: int
    pages_count: int
    progress_percentage: int
    is_milestone: bool = False
    milestone_label: Optional[str] = None


@dataclass
class ReadingPlan:
    """Container for a calculated reading schedule."""
    book_title: str
    author: Optional[str]
    total_pages: int
    current_page: int
    start_date: date
    target_date: date
    sessions: List[ReadingSession]
    preferred_time: time
    minutes_per_session: int
    days_filter: str
    pace_pages_per_session: float

    @property
    def total_sessions(self) -> int:
        return len(self.sessions)

    @property
    def remaining_pages(self) -> int:
        return max(0, self.total_pages - self.current_page)


def _parse_time_of_day(time_str: Optional[str]) -> time:
    """Parses time strings like '20:00', '8:30pm', '19:30' into datetime.time."""
    if not time_str or not time_str.strip():
        return time(20, 0)  # Default 8:00 PM

    cleaned = time_str.strip().lower()
    # Try HH:MM
    match = re.match(r"^(\d{1,2}):(\d{2})$", cleaned)
    if match:
        h, m = int(match.group(1)), int(match.group(2))
        return time(h % 24, m % 60)

    # Try natural formats via dateparser
    parsed = parse_datetime_flexible(f"today {cleaned}", prefer_future=False)
    if isinstance(parsed, datetime):
        return parsed.time()

    return time(20, 0)


def _resolve_active_weekdays(days_filter: Optional[str]) -> Set[int]:
    """Resolves day filter keyword into set of active 0-indexed weekday integers (0=Mon, 6=Sun)."""
    if not days_filter or not days_filter.strip():
        return {0, 1, 2, 3, 4, 5, 6}  # All days

    cleaned = days_filter.strip().lower()
    if cleaned in ("weekdays", "weekday", "workdays"):
        return {0, 1, 2, 3, 4}
    if cleaned in ("weekends", "weekend"):
        return {5, 6}
    if cleaned in ("all", "daily", "every day", "everyday"):
        return {0, 1, 2, 3, 4, 5, 6}

    # Comma or space-separated list of days (e.g. 'mo,we,fr' or 'Mon Wed Fri')
    tokens = re.split(r"[,; ]+", cleaned)
    resolved: Set[int] = set()
    for tok in tokens:
        tok_clean = tok.strip()
        if tok_clean in DAY_NAME_TO_INT:
            resolved.add(DAY_NAME_TO_INT[tok_clean])

    return resolved if resolved else {0, 1, 2, 3, 4, 5, 6}


def calculate_reading_schedule(
    book_title: str,
    total_pages: int,
    current_page: int = 0,
    start_date: Optional[Union[str, date, datetime]] = None,
    target_date: Optional[Union[str, date, datetime]] = None,
    duration_days: Optional[int] = None,
    pages_per_day: Optional[int] = None,
    days_filter: Optional[str] = "daily",
    preferred_time: Optional[str] = "20:00",
    minutes_per_session: int = 30,
    author: Optional[str] = None,
) -> ReadingPlan:
    """
    Computes an optimal reading schedule and distributes pages evenly across
    available reading sessions without gaps or overlaps.
    """
    if total_pages <= 0:
        raise ValueError("Total pages must be greater than 0.")
    if current_page >= total_pages:
        raise ValueError(f"Current page ({current_page}) is already at or past total pages ({total_pages}).")

    remaining_pages = total_pages - current_page

    # 1. Resolve start date
    if start_date is None:
        start_d = date.today()
    elif isinstance(start_date, datetime):
        start_d = start_date.date()
    elif isinstance(start_date, date):
        start_d = start_date
    else:
        parsed = parse_datetime_flexible(start_date, prefer_future=True)
        start_d = parsed.date() if isinstance(parsed, datetime) else parsed

    active_weekdays = _resolve_active_weekdays(days_filter)
    pref_time = _parse_time_of_day(preferred_time)

    # 2. Determine reading session calendar dates
    session_dates: List[date] = []

    if target_date is not None:
        if isinstance(target_date, datetime):
            target_d = target_date.date()
        elif isinstance(target_date, date):
            target_d = target_date
        else:
            parsed = parse_datetime_flexible(target_date, prefer_future=True)
            target_d = parsed.date() if isinstance(parsed, datetime) else parsed

        if target_d < start_d:
            raise ValueError(f"Target completion date ({target_d}) cannot be earlier than start date ({start_d}).")

        curr = start_d
        while curr <= target_d:
            if curr.weekday() in active_weekdays:
                session_dates.append(curr)
            curr += timedelta(days=1)

    elif pages_per_day is not None and pages_per_day > 0:
        needed_sessions = max(1, math.ceil(remaining_pages / pages_per_day))
        curr = start_d
        while len(session_dates) < needed_sessions:
            if curr.weekday() in active_weekdays:
                session_dates.append(curr)
            curr += timedelta(days=1)

    else:
        # Duration based (default 30 days if duration_days not provided)
        span_days = duration_days if (duration_days and duration_days > 0) else 30
        curr = start_d
        end_boundary = start_d + timedelta(days=span_days - 1)
        while curr <= end_boundary:
            if curr.weekday() in active_weekdays:
                session_dates.append(curr)
            curr += timedelta(days=1)

    if not session_dates:
        raise ValueError("No eligible reading session dates found matching the specified calendar constraints.")

    num_sessions = len(session_dates)

    # 3. Distribute pages evenly across sessions (integer math)
    base_pages = remaining_pages // num_sessions
    remainder = remaining_pages % num_sessions

    sessions: List[ReadingSession] = []
    curr_start = current_page + 1

    # Milestones tracking
    milestone_targets = [25, 50, 75, 100]
    next_milestone_idx = 0

    for idx, s_date in enumerate(session_dates, start=1):
        # Sessions get base_pages + 1 until remainder is distributed
        alloc = base_pages + (1 if idx <= remainder else 0)
        curr_end = min(total_pages, curr_start + alloc - 1)
        if idx == num_sessions:
            curr_end = total_pages  # Ensure last session strictly reaches the final page

        pct = int(round((curr_end / total_pages) * 100))

        # Check for milestone trigger
        is_milestone = False
        m_label = None
        if next_milestone_idx < len(milestone_targets):
            target_pct = milestone_targets[next_milestone_idx]
            if pct >= target_pct or idx == num_sessions:
                is_milestone = True
                if target_pct == 25:
                    m_label = "🌱 Great Start! (25% checkpoint)"
                elif target_pct == 50:
                    m_label = "🎉 Halfway Point! (50% checkpoint)"
                elif target_pct == 75:
                    m_label = "🔥 The Home Stretch! (75% checkpoint)"
                elif target_pct == 100 or idx == num_sessions:
                    m_label = "🏆 Book Completed! (100% finished)"
                next_milestone_idx += 1

        session = ReadingSession(
            session_index=idx,
            date=s_date,
            start_page=curr_start,
            end_page=curr_end,
            pages_count=max(1, curr_end - curr_start + 1),
            progress_percentage=pct,
            is_milestone=is_milestone,
            milestone_label=m_label,
        )
        sessions.append(session)
        curr_start = curr_end + 1

    pace = remaining_pages / num_sessions

    return ReadingPlan(
        book_title=book_title.strip(),
        author=author.strip() if author else None,
        total_pages=total_pages,
        current_page=current_page,
        start_date=session_dates[0],
        target_date=session_dates[-1],
        sessions=sessions,
        preferred_time=pref_time,
        minutes_per_session=minutes_per_session,
        days_filter=days_filter or "daily",
        pace_pages_per_session=pace,
    )


def plan_to_ical_events(
    plan: ReadingPlan,
    alarm_minutes: int = 15,
    timezone: Optional[str] = None,
) -> List[ICalEvent]:
    """Converts a ReadingPlan into a list of ICalEvent objects (VEVENT)."""
    events: List[ICalEvent] = []

    for s in plan.sessions:
        star_badge = " ⭐" if s.is_milestone else ""
        summary = f"📖 {plan.book_title}: pp. {s.start_page}–{s.end_page}{star_badge}"

        dt_start = datetime.combine(s.date, plan.preferred_time)

        # Build informative session notes
        bar = render_progress_bar(s.progress_percentage)
        desc_lines = [
            f"Book: {plan.book_title}" + (f" by {plan.author}" if plan.author else ""),
            f"Target: Pages {s.start_page} to {s.end_page} ({s.pages_count} pages)",
            f"Session: {s.session_index} of {plan.total_sessions}",
            f"Book Progress: {bar}",
        ]
        if s.is_milestone and s.milestone_label:
            desc_lines.append(f"\n{s.milestone_label}")

        alarms = (
            [ICalAlarm(trigger_minutes_before=alarm_minutes, description=f"Time to read: {plan.book_title}")]
            if alarm_minutes > 0
            else []
        )

        ev = ICalEvent(
            summary=summary,
            start=dt_start,
            duration_minutes=plan.minutes_per_session,
            description="\n".join(desc_lines),
            location="Reading Nook / Cozy Spot",
            categories=["Reading", "Books", plan.book_title],
            timezone=timezone,
            alarms=alarms,
        )
        events.append(ev)

    return events


def plan_to_ical_todos(
    plan: ReadingPlan,
    timezone: Optional[str] = None,
) -> List[ICalTodo]:
    """Converts a ReadingPlan into a list of ICalTodo task items (VTODO)."""
    todos: List[ICalTodo] = []

    for s in plan.sessions:
        summary = f"Read {plan.book_title}: pp. {s.start_page}–{s.end_page}"
        dt_due = datetime.combine(s.date, plan.preferred_time)

        bar = render_progress_bar(s.progress_percentage)
        desc_lines = [
            f"Pages: {s.start_page}–{s.end_page} ({s.pages_count} pages)",
            f"Progress: {bar}",
            f"Session: {s.session_index}/{plan.total_sessions}",
        ]
        if s.is_milestone and s.milestone_label:
            desc_lines.append(f"\n{s.milestone_label}")

        priority = 1 if s.session_index == plan.total_sessions else (3 if s.is_milestone else 5)

        td = ICalTodo(
            summary=summary,
            due=dt_due,
            description="\n".join(desc_lines),
            priority=priority,
            categories=["Reading", "Tasks", plan.book_title],
            timezone=timezone,
            alarms=[ICalAlarm(trigger_minutes_before=15, description=f"Task Due: {summary}")],
        )
        todos.append(td)

    return todos


def generate_reading_plan_ics(
    plan: ReadingPlan,
    as_tasks: bool = False,
    alarm_minutes: int = 15,
    timezone: Optional[str] = None,
) -> bytes:
    """Generates an RFC 5545 .ics byte payload for a reading pacing plan."""
    cal_name = f"{plan.book_title} Reading Schedule"

    if as_tasks:
        todos = plan_to_ical_todos(plan, timezone=timezone)
        return build_ical_calendar(
            todos=todos,
            cal_name=cal_name,
            cal_timezone=timezone,
        )
    else:
        events = plan_to_ical_events(plan, alarm_minutes=alarm_minutes, timezone=timezone)
        return build_ical_calendar(
            events=events,
            cal_name=cal_name,
            cal_timezone=timezone,
        )
