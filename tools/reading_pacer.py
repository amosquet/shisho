"""
tools/reading_pacer.py - AI Tool definitions and handlers for Reading Pacing Schedule Generation.
"""

from datetime import datetime
import re
from typing import Any, Dict, List, Optional
from google.genai import types

from utils.db import run_in_executor
from utils import reading_pacer as pacer_utils


GENERATE_READING_PLAN_TOOL = types.FunctionDeclaration(
    name="generate_reading_plan",
    description="Calculates a customized, day-by-day reading schedule for any book and generates a downloadable iCalendar (.ics) file (calendar events or task checklist). Distributes pages evenly across deadlines, target dates, or daily budgets with progress milestones.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "book_title": types.Schema(
                type=types.Type.STRING,
                description="The title of the book (e.g. 'Dune', 'Project Hail Mary', 'Crime and Punishment').",
            ),
            "total_pages": types.Schema(
                type=types.Type.INTEGER,
                description="Total number of pages in the book.",
            ),
            "current_page": types.Schema(
                type=types.Type.INTEGER,
                description="Current page offset if already reading (default 0).",
            ),
            "target_date": types.Schema(
                type=types.Type.STRING,
                description="Target completion date (e.g. '2026-10-15', 'October 31', 'next month').",
            ),
            "duration_days": types.Schema(
                type=types.Type.INTEGER,
                description="Reading duration in days (e.g. 14 for 2 weeks, 30 for a month).",
            ),
            "pages_per_day": types.Schema(
                type=types.Type.INTEGER,
                description="Target daily page count budget (e.g. 20 pages/day).",
            ),
            "days_filter": types.Schema(
                type=types.Type.STRING,
                description="Which days to read: 'daily' (every day), 'weekdays' (Mon-Fri), 'weekends' (Sat-Sun), or specific days like 'Mon,Wed,Fri'.",
            ),
            "preferred_time": types.Schema(
                type=types.Type.STRING,
                description="Time of day for reading sessions (e.g. '20:00', '8:30pm'). Defaults to '20:00'.",
            ),
            "minutes_per_session": types.Schema(
                type=types.Type.INTEGER,
                description="Minutes budgeted per reading session (default 30).",
            ),
            "export_as": types.Schema(
                type=types.Type.STRING,
                description="Export format: 'events' (default, calendar events for Apple/Google Calendar) or 'todos' (checklist tasks for Apple Reminders / Microsoft To-Do).",
            ),
            "timezone": types.Schema(
                type=types.Type.STRING,
                description="User timezone (e.g. 'US/Eastern', 'US/Pacific', 'est', 'pst').",
            ),
            "author": types.Schema(
                type=types.Type.STRING,
                description="Author of the book (optional).",
            ),
        },
        required=["book_title", "total_pages"],
    ),
)


async def handle_generate_reading_plan(
    bot: Any, args: dict, user_id: str, context: Optional[dict] = None
) -> str:
    """Handles calculating a reading schedule and attaching the resulting .ics file."""
    book_title = (args.get("book_title") or "Book").strip()
    total_pages = int(args.get("total_pages", 0))
    if total_pages <= 0:
        return "Error: 'total_pages' must be greater than 0."

    current_page = int(args.get("current_page", 0))
    target_date = args.get("target_date")
    duration_days = args.get("duration_days")
    pages_per_day = args.get("pages_per_day")
    days_filter = args.get("days_filter", "daily")
    preferred_time = args.get("preferred_time", "20:00")
    minutes_per_session = int(args.get("minutes_per_session", 30))
    export_as = (args.get("export_as") or "events").strip().lower()
    user_tz = args.get("timezone")
    author = args.get("author")

    try:
        plan = await run_in_executor(
            pacer_utils.calculate_reading_schedule,
            book_title=book_title,
            total_pages=total_pages,
            current_page=current_page,
            target_date=target_date,
            duration_days=duration_days,
            pages_per_day=pages_per_day,
            days_filter=days_filter,
            preferred_time=preferred_time,
            minutes_per_session=minutes_per_session,
            author=author,
        )
    except Exception as e:
        return f"Error calculating reading schedule: {str(e)}"

    is_todos = export_as in ("todos", "todo", "tasks", "task")

    try:
        ics_bytes = await run_in_executor(
            pacer_utils.generate_reading_plan_ics,
            plan=plan,
            as_tasks=is_todos,
            timezone=user_tz,
        )
    except Exception as e:
        return f"Error generating reading plan calendar file: {str(e)}"

    safe_title = re.sub(r'[\\/*?:"<>| ]', "_", book_title).strip("_") or "book"
    filename = f"{safe_title}_Reading_Plan.ics"

    if context is not None and isinstance(context, dict):
        if "out_files" not in context:
            context["out_files"] = []
        context["out_files"].append({
            "filename": filename,
            "bytes": ics_bytes,
            "content_type": "text/calendar; charset=utf-8",
        })

    # Format summary response
    dest_app = "Apple Reminders / Microsoft To-Do" if is_todos else "Apple Calendar / Google Calendar / Outlook"
    pace_display = f"{plan.pace_pages_per_session:.1f}" if plan.pace_pages_per_session % 1 != 0 else f"{int(plan.pace_pages_per_session)}"

    milestone_sessions = [s for s in plan.sessions if s.is_milestone]

    lines = [
        f"📖 **Reading Schedule Generated: {plan.book_title}**" + (f" by *{plan.author}*" if plan.author else ""),
        f"- Attached file: `{filename}` ({len(ics_bytes):,} bytes)",
        f"- **Timeline:** `{plan.start_date}` → `{plan.target_date}` ({plan.total_sessions} reading sessions)",
        f"- **Reading Pace:** ~**{pace_display} pages / session** ({plan.minutes_per_session} mins at `{plan.preferred_time.strftime('%H:%M')}`)",
        f"- **Schedule Filter:** `{plan.days_filter.capitalize()}`",
        f"- **Remaining:** {plan.remaining_pages} of {plan.total_pages} pages (starting from page {plan.current_page})",
        "",
        "**Key Milestones:**",
    ]

    for m in milestone_sessions:
        lines.append(f"- `{m.date}`: Page **{m.end_page}** ({m.milestone_label or f'{m.progress_percentage}%'})")

    lines.append("")
    lines.append("**Session Preview:**")
    for s in plan.sessions[:3]:
        lines.append(f"- **Day {s.session_index}** (`{s.date}`): pp. {s.start_page}–{s.end_page} ({s.pages_count} pp.)")
    if len(plan.sessions) > 4:
        lines.append(f"- *... {len(plan.sessions) - 4} intermediate sessions ...*")
    if len(plan.sessions) >= 4:
        last = plan.sessions[-1]
        lines.append(f"- **Day {last.session_index}** (`{last.date}`): pp. {last.start_page}–{last.end_page} (Final session! 🏁)")

    lines.append(f"\n*Import `{filename}` into {dest_app} to sync your reading goals, or ask me to schedule these reminders directly into your Shisho database!*")
    return "\n".join(lines)
