"""
tools/ical.py - AI Tool definitions and handlers for generating iCalendar (.ics) event files.
"""

from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Optional
from google.genai import types

from utils.db import get_pb_client, get_discord_user_id, run_in_executor
from utils.discord_helpers import UNLINKED_ACCOUNT_MESSAGE
from utils import ical as ical_utils


# =========================================================================
# Tool Function Declarations
# =========================================================================

CREATE_ICAL_EVENT_TOOL = types.FunctionDeclaration(
    name="create_ical_event",
    description="Creates a downloadable iCalendar (.ics) event invite file supporting all-day or timed events, timezones, recurrence rules (RRULE), alarms/reminders (VALARM), attendees/organizers, locations, and conference links. The resulting .ics file is attached directly to the message for 1-click import into Apple Calendar, Google Calendar, or Microsoft Outlook.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "title": types.Schema(
                type=types.Type.STRING,
                description="The title or summary of the event (e.g. 'Sprint Planning', 'Dentist Appointment', 'Book Club Meeting').",
            ),
            "start_time": types.Schema(
                type=types.Type.STRING,
                description="When the event starts. Accepts natural language (e.g. 'tomorrow at 3pm', 'next Tuesday at 10am', 'September 15 at 19:00') or ISO format.",
            ),
            "end_time": types.Schema(
                type=types.Type.STRING,
                description="Optional end date/time. If omitted, duration_minutes is used.",
            ),
            "duration_minutes": types.Schema(
                type=types.Type.INTEGER,
                description="Duration of the event in minutes (e.g. 30, 60, 90). Defaults to 60 if end_time is not provided.",
            ),
            "description": types.Schema(
                type=types.Type.STRING,
                description="Detailed description, notes, or agenda for the event.",
            ),
            "location": types.Schema(
                type=types.Type.STRING,
                description="Physical address, room name, or virtual venue (e.g. 'Room 402', '123 Main St', 'Discord Voice').",
            ),
            "conference_url": types.Schema(
                type=types.Type.STRING,
                description="Video call or meeting link (e.g. Google Meet, Zoom, Teams, Discord channel URL).",
            ),
            "url": types.Schema(
                type=types.Type.STRING,
                description="Associated web link or resource URL.",
            ),
            "timezone": types.Schema(
                type=types.Type.STRING,
                description="Timezone for the event (e.g. 'US/Eastern', 'US/Pacific', 'US/Central', 'Asia/Tokyo', 'Europe/Paris', 'UTC', or abbreviations like 'est', 'pst').",
            ),
            "all_day": types.Schema(
                type=types.Type.BOOLEAN,
                description="True if this is an all-day event (e.g. birthdays, holidays, deadlines).",
            ),
            "alarm_minutes_before": types.Schema(
                type=types.Type.INTEGER,
                description="Minutes before the event to trigger a reminder popup notification (default is 15 minutes for timed events; set to 0 to disable alarm).",
            ),
            "recurrence_freq": types.Schema(
                type=types.Type.STRING,
                description="Recurrence frequency if recurring: 'DAILY', 'WEEKLY', 'MONTHLY', or 'YEARLY'.",
            ),
            "recurrence_interval": types.Schema(
                type=types.Type.INTEGER,
                description="Recurrence interval (e.g. 2 for every 2 weeks/months). Defaults to 1.",
            ),
            "recurrence_count": types.Schema(
                type=types.Type.INTEGER,
                description="Stop repeating after this many occurrences.",
            ),
            "recurrence_until": types.Schema(
                type=types.Type.STRING,
                description="Stop repeating on or before this date/time.",
            ),
            "recurrence_byday": types.Schema(
                type=types.Type.ARRAY,
                description="Days of the week for recurrence: e.g. ['MO', 'WE', 'FR'] or ['2MO'].",
                items=types.Schema(type=types.Type.STRING),
            ),
            "categories": types.Schema(
                type=types.Type.ARRAY,
                description="Categories or tags for the event (e.g. ['Work', 'Meeting', 'Reading']).",
                items=types.Schema(type=types.Type.STRING),
            ),
            "organizer_email": types.Schema(
                type=types.Type.STRING,
                description="Email of the organizer/host.",
            ),
            "organizer_name": types.Schema(
                type=types.Type.STRING,
                description="Display name of the organizer.",
            ),
            "attendees": types.Schema(
                type=types.Type.ARRAY,
                description="List of attendee participants to invite.",
                items=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "email": types.Schema(type=types.Type.STRING, description="Attendee email address."),
                        "name": types.Schema(type=types.Type.STRING, description="Attendee display name."),
                        "rsvp": types.Schema(type=types.Type.BOOLEAN, description="Whether RSVP is requested (true/false)."),
                        "role": types.Schema(type=types.Type.STRING, description="'REQ-PARTICIPANT', 'OPT-PARTICIPANT', or 'CHAIR'."),
                    },
                    required=["email"],
                ),
            ),
            "color": types.Schema(
                type=types.Type.STRING,
                description="Optional event color hex code or CSS name (e.g. '#3498db', 'royalblue').",
            ),
        },
        required=["title", "start_time"],
    ),
)


CREATE_ICAL_TODO_TOOL = types.FunctionDeclaration(
    name="create_ical_todo",
    description="Creates a downloadable iCalendar (.ics) To-Do / Task file (VTODO component) ready for 1-click import into Apple Reminders, Microsoft To-Do, or Outlook Tasks. Supports deadlines (due date), priorities (1-9), completion status, percent complete, categories, and alarm reminders.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "title": types.Schema(
                type=types.Type.STRING,
                description="The task name, title, or action item (e.g. 'File tax return', 'Buy groceries', 'Submit project report').",
            ),
            "due_time": types.Schema(
                type=types.Type.STRING,
                description="When the task is due / deadline (e.g. 'tomorrow at 5pm', 'next Friday at 17:00', '2026-09-30').",
            ),
            "start_time": types.Schema(
                type=types.Type.STRING,
                description="Optional start date/time for when to begin the task.",
            ),
            "description": types.Schema(
                type=types.Type.STRING,
                description="Detailed description, checklist items, or sub-notes for the task.",
            ),
            "priority": types.Schema(
                type=types.Type.INTEGER,
                description="Priority level: 1 (high / !!!), 5 (medium / !!), 9 (low / !), or 0 (none).",
            ),
            "status": types.Schema(
                type=types.Type.STRING,
                description="Task status: 'NEEDS-ACTION' (default), 'IN-PROCESS', 'COMPLETED', or 'CANCELLED'.",
            ),
            "percent_complete": types.Schema(
                type=types.Type.INTEGER,
                description="Percentage completed (0 to 100).",
            ),
            "location": types.Schema(
                type=types.Type.STRING,
                description="Location associated with the task (e.g. 'Home', 'Supermarket', 'Office').",
            ),
            "timezone": types.Schema(
                type=types.Type.STRING,
                description="Timezone for the task deadline (e.g. 'US/Eastern', 'US/Pacific', 'est', 'pst').",
            ),
            "all_day": types.Schema(
                type=types.Type.BOOLEAN,
                description="True if the deadline is an all-day date rather than a specific hour.",
            ),
            "alarm_minutes_before": types.Schema(
                type=types.Type.INTEGER,
                description="Minutes before deadline to trigger a reminder popup notification (default is 15 minutes if due date given; set to 0 to disable alarm).",
            ),
            "categories": types.Schema(
                type=types.Type.ARRAY,
                description="Tags or categories for the task (e.g. ['Work', 'Errands', 'Personal']).",
                items=types.Schema(type=types.Type.STRING),
            ),
            "recurrence_freq": types.Schema(
                type=types.Type.STRING,
                description="Recurrence frequency if repeating: 'DAILY', 'WEEKLY', 'MONTHLY', or 'YEARLY'.",
            ),
        },
        required=["title"],
    ),
)


EXPORT_REMINDERS_ICAL_TOOL = types.FunctionDeclaration(
    name="export_reminders_ical",
    description="Exports the user's active, sent, or all reminders from PocketBase as a downloadable iCalendar (.ics) calendar or to-do file ready for importing into Apple Calendar, Apple Reminders, Google Calendar, or Outlook.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "status": types.Schema(
                type=types.Type.STRING,
                description="Filter reminders by status: 'active' (default for upcoming reminders), 'sent' (past reminders), or 'all'.",
            ),
            "export_as": types.Schema(
                type=types.Type.STRING,
                description="Export format: 'events' (default, exports as calendar events for Google/Apple Calendar) or 'todos' (exports as tasks for Apple Reminders / Microsoft To-Do).",
            ),
            "limit": types.Schema(
                type=types.Type.INTEGER,
                description="Maximum number of reminders to export (optional).",
            ),
            "timezone": types.Schema(
                type=types.Type.STRING,
                description="Optional timezone for the calendar export (e.g. 'US/Eastern', 'US/Pacific').",
            ),
        },
    ),
)


# =========================================================================
# Tool Handlers
# =========================================================================

async def handle_create_ical_event(
    bot: Any, args: dict, user_id: str, context: Optional[dict] = None
) -> str:
    """Handles creating an .ics calendar event file and attaching it to the message context."""
    title = (args.get("title") or "Event").strip()
    start_time = args.get("start_time")
    if not start_time:
        return "Error: 'start_time' is required to create a calendar event."

    end_time = args.get("end_time")
    duration_minutes = args.get("duration_minutes")
    description = args.get("description")
    location = args.get("location")
    conference_url = args.get("conference_url")
    url = args.get("url")
    user_tz = args.get("timezone")
    all_day = bool(args.get("all_day", False))
    alarm_minutes = args.get("alarm_minutes_before")
    # Default alarm is 15 minutes unless specified
    if alarm_minutes is None:
        alarm_minutes = 15 if not all_day else 0

    categories = args.get("categories")
    color = args.get("color")
    organizer_email = args.get("organizer_email")
    organizer_name = args.get("organizer_name")

    organizer_dict = None
    if organizer_email:
        organizer_dict = {"email": organizer_email, "name": organizer_name}

    attendees_list = args.get("attendees")

    # Recurrence
    recurrence_dict = None
    rfreq = args.get("recurrence_freq")
    if rfreq:
        recurrence_dict = {
            "freq": rfreq,
            "interval": args.get("recurrence_interval", 1),
            "count": args.get("recurrence_count"),
            "until": args.get("recurrence_until"),
            "byday": args.get("recurrence_byday"),
        }

    # Generate .ics bytes
    try:
        ics_bytes = await run_in_executor(
            ical_utils.create_single_event_ics,
            summary=title,
            start=start_time,
            end=end_time,
            duration_minutes=duration_minutes,
            description=description,
            location=location,
            conference_url=conference_url,
            url=url,
            all_day=all_day,
            timezone=user_tz,
            alarm_minutes_before=alarm_minutes,
            categories=categories,
            attendees=attendees_list,
            organizer=organizer_dict,
            recurrence=recurrence_dict,
        )
    except Exception as e:
        return f"Error generating iCalendar file: {str(e)}"

    # Register output file for Discord / Email attachment
    safe_filename = re.sub(r'[\\/*?:"<>| ]', "_", title).strip("_") or "event"
    ics_filename = f"{safe_filename}.ics"

    if context is not None and isinstance(context, dict):
        if "out_files" not in context:
            context["out_files"] = []
        context["out_files"].append({
            "filename": ics_filename,
            "bytes": ics_bytes,
            "content_type": "text/calendar; charset=utf-8",
        })

    # Build human-readable response preview
    details = [
        f"Successfully created calendar invite: **{title}**",
        f"- Attached file: `{ics_filename}` ({len(ics_bytes):,} bytes)",
        f"- **Start:** `{start_time}`" + (f" ({user_tz})" if user_tz else ""),
    ]

    if end_time:
        details.append(f"- **End:** `{end_time}`")
    elif duration_minutes:
        details.append(f"- **Duration:** {duration_minutes} minutes")

    if all_day:
        details.append("- **All-Day Event:** Yes")
    if location:
        details.append(f"- **Location:** {location}")
    if conference_url:
        details.append(f"- **Video Call:** {conference_url}")
    if alarm_minutes and alarm_minutes > 0:
        details.append(f"- **Reminder Alert:** {alarm_minutes} minute(s) before")
    if recurrence_dict:
        r_str = recurrence_dict['freq'].capitalize()
        if recurrence_dict.get('interval', 1) > 1:
            r_str += f" (every {recurrence_dict['interval']} intervals)"
        details.append(f"- **Repeats:** {r_str}")
    if attendees_list:
        details.append(f"- **Attendees:** {len(attendees_list)} invited")

    details.append("\n*Open the attached `.ics` file to add it directly to Apple Calendar, Google Calendar, or Outlook.*")
    return "\n".join(details)


async def handle_create_ical_todo(
    bot: Any, args: dict, user_id: str, context: Optional[dict] = None
) -> str:
    """Handles creating an .ics VTODO task file and attaching it to the message context."""
    title = (args.get("title") or "Task").strip()
    due_time = args.get("due_time")
    start_time = args.get("start_time")
    description = args.get("description")
    location = args.get("location")
    priority = args.get("priority")
    status = (args.get("status") or "NEEDS-ACTION").strip().upper()
    percent_complete = args.get("percent_complete")
    user_tz = args.get("timezone")
    all_day = bool(args.get("all_day", False))
    alarm_minutes = args.get("alarm_minutes_before")
    categories = args.get("categories")

    recurrence_dict = None
    rfreq = args.get("recurrence_freq")
    if rfreq:
        recurrence_dict = {"freq": rfreq, "interval": 1}

    try:
        ics_bytes = await run_in_executor(
            ical_utils.create_single_todo_ics,
            summary=title,
            due=due_time,
            start=start_time,
            description=description,
            location=location,
            priority=priority,
            status=status,
            percent_complete=percent_complete,
            alarm_minutes_before=alarm_minutes,
            timezone=user_tz,
            all_day=all_day,
            categories=categories,
            recurrence=recurrence_dict,
        )
    except Exception as e:
        return f"Error generating iCalendar task file: {str(e)}"

    safe_filename = re.sub(r'[\\/*?:"<>| ]', "_", title).strip("_") or "task"
    ics_filename = f"{safe_filename}.ics"

    if context is not None and isinstance(context, dict):
        if "out_files" not in context:
            context["out_files"] = []
        context["out_files"].append({
            "filename": ics_filename,
            "bytes": ics_bytes,
            "content_type": "text/calendar; charset=utf-8",
        })

    details = [
        f"Successfully created To-Do task: **{title}**",
        f"- Attached file: `{ics_filename}` ({len(ics_bytes):,} bytes)",
    ]
    if due_time:
        details.append(f"- **Due Date:** `{due_time}`" + (f" ({user_tz})" if user_tz else ""))
    if priority:
        pri_label = {1: "High (!!!)", 5: "Medium (!!)", 9: "Low (!)"}.get(priority, str(priority))
        details.append(f"- **Priority:** {pri_label}")
    if status != "NEEDS-ACTION":
        details.append(f"- **Status:** {status}")
    if percent_complete is not None:
        details.append(f"- **Completed:** {percent_complete}%")
    if location:
        details.append(f"- **Location:** {location}")
    if categories:
        details.append(f"- **Categories:** {', '.join(categories)}")

    details.append("\n*Open the attached `.ics` file to add it directly to Apple Reminders, Microsoft To-Do, or Outlook Tasks.*")
    return "\n".join(details)


async def handle_export_reminders_ical(
    bot: Any, args: dict, user_id: str, context: Optional[dict] = None
) -> str:
    """Handles querying reminders from PocketBase and exporting them as an .ics file."""
    status_filter = (args.get("status") or "active").strip().lower()
    export_as = (args.get("export_as") or "events").strip().lower()
    limit = args.get("limit")
    user_tz = args.get("timezone")

    def _fetch_from_pb():
        pb = get_pb_client()
        pb_user_id = get_discord_user_id(pb, user_id)
        if not pb_user_id:
            return None

        if status_filter == "sent":
            filter_str = f"owner = '{pb_user_id}' && is_sent = true"
            sort_str = "-remind_at"
        elif status_filter in ("all", "both", "any"):
            filter_str = f"owner = '{pb_user_id}'"
            sort_str = "-remind_at"
        else:  # active
            filter_str = f"owner = '{pb_user_id}' && (is_sent = false || is_sent = null)"
            sort_str = "remind_at"

        records = pb.collection("reminders").get_full_list(
            query_params={"filter": filter_str, "sort": sort_str}
        )
        if limit and limit > 0:
            records = records[:limit]
        return records

    records = await run_in_executor(_fetch_from_pb)
    if records is None:
        return f"Error: {UNLINKED_ACCOUNT_MESSAGE}"

    if not records:
        return f"No reminders found to export matching status '{status_filter}'."

    is_todos = export_as in ("todos", "todo", "tasks", "task")
    try:
        if is_todos:
            models = ical_utils.reminders_to_ical_todos(records, user_timezone=user_tz)
            ics_bytes = await run_in_executor(
                ical_utils.build_ical_calendar,
                todos=models,
                cal_name=f"Shisho Tasks ({status_filter.capitalize()})",
                cal_timezone=user_tz,
            )
            filename = f"reminders_tasks_{status_filter}.ics"
            dest_app = "Apple Reminders, Microsoft To-Do, or Outlook Tasks"
        else:
            models = ical_utils.reminders_to_ical_events(records, user_timezone=user_tz)
            ics_bytes = await run_in_executor(
                ical_utils.build_ical_calendar,
                events=models,
                cal_name=f"Shisho Reminders ({status_filter.capitalize()})",
                cal_timezone=user_tz,
            )
            filename = f"reminders_{status_filter}.ics"
            dest_app = "Apple Calendar, Google Calendar, or Outlook"
    except Exception as e:
        return f"Error building reminders calendar file: {str(e)}"

    if context is not None and isinstance(context, dict):
        if "out_files" not in context:
            context["out_files"] = []
        context["out_files"].append({
            "filename": filename,
            "bytes": ics_bytes,
            "content_type": "text/calendar; charset=utf-8",
        })

    item_type = "task(s)" if is_todos else "event(s)"
    return (
        f"Successfully exported **{len(models)} reminder {item_type}** to `{filename}` ({len(ics_bytes):,} bytes)!\n"
        f"Attached file is ready to import into {dest_app}."
    )
