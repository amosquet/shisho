"""
cogs/calendar.py - Dedicated iCalendar (.ics) generation and export Cog for Shisho.
"""

import io
import re
from typing import Optional
import discord
from discord import app_commands
from discord.ext import commands
import sentry_sdk

from utils.db import get_pb_client, get_discord_user_id, run_in_executor
from utils.discord_helpers import is_user_authorized, UNLINKED_ACCOUNT_MESSAGE
from utils import ical as ical_utils
from utils import reading_pacer as pacer_utils


class Calendar(commands.Cog):
    """iCalendar (.ics) event generator and reminders export."""

    def __init__(self, bot):
        self.bot = bot

    def is_user_authorized(self, user_id: int) -> bool:
        """Check if user has permission to use Calendar commands."""
        return (
            is_user_authorized(user_id, "Calendar")
            or is_user_authorized(user_id, "Reminders")
            or is_user_authorized(user_id, "AIChat")
        )

    calendar_group = app_commands.Group(
        name="calendar",
        description="Generate and export iCalendar (.ics) files for Apple, Google, and Outlook calendars.",
    )

    @calendar_group.command(
        name="create",
        description="Create and download an iCalendar (.ics) event file.",
    )
    @app_commands.describe(
        title="Event title or subject",
        when="When the event occurs (e.g. 'tomorrow at 3pm', 'next Friday at 10am', '2026-09-15 14:00')",
        duration="Duration of the event in minutes (default 60)",
        description="Event description, agenda, or notes",
        location="Physical location or venue name",
        conference_url="Video call link (Zoom, Google Meet, Discord Voice)",
        timezone="Timezone (e.g. 'US/Eastern', 'US/Pacific', 'est', 'pst')",
        all_day="Whether this is an all-day event (default False)",
        alarm_minutes="Reminder notification minutes before start (default 15; 0 for no alarm)",
        repeats="Recurrence frequency if repeating",
    )
    @app_commands.choices(
        repeats=[
            app_commands.Choice(name="Daily", value="DAILY"),
            app_commands.Choice(name="Weekly", value="WEEKLY"),
            app_commands.Choice(name="Monthly", value="MONTHLY"),
            app_commands.Choice(name="Yearly", value="YEARLY"),
        ]
    )
    async def create_event_slash(
        self,
        interaction: discord.Interaction,
        title: str,
        when: str,
        duration: Optional[int] = 60,
        description: Optional[str] = None,
        location: Optional[str] = None,
        conference_url: Optional[str] = None,
        timezone: Optional[str] = None,
        all_day: Optional[bool] = False,
        alarm_minutes: Optional[int] = 15,
        repeats: Optional[app_commands.Choice[str]] = None,
    ):
        if not self.is_user_authorized(interaction.user.id):
            await interaction.response.send_message(
                "You are not authorized to use Calendar commands.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=False)

        recurrence_dict = None
        if repeats:
            recurrence_dict = {"freq": repeats.value, "interval": 1}

        # If all_day is selected and alarm_minutes is untouched (15), default to 0 (no alarm)
        actual_alarm = alarm_minutes
        if all_day and alarm_minutes == 15:
            actual_alarm = 0

        try:
            ics_bytes = await run_in_executor(
                ical_utils.create_single_event_ics,
                summary=title,
                start=when,
                duration_minutes=duration,
                description=description,
                location=location,
                conference_url=conference_url,
                all_day=bool(all_day),
                timezone=timezone,
                alarm_minutes_before=actual_alarm,
                recurrence=recurrence_dict,
            )
        except Exception as e:
            sentry_sdk.capture_exception(e)
            await interaction.followup.send(f"❌ Failed to generate calendar event: {e}")
            return

        safe_filename = re.sub(r'[\\/*?:"<>| ]', "_", title).strip("_") or "event"
        filename = f"{safe_filename}.ics"
        discord_file = discord.File(io.BytesIO(ics_bytes), filename=filename)

        embed = discord.Embed(
            title=f"📅 {title}",
            description=description or "No description provided.",
            color=discord.Color.blue(),
        )
        embed.add_field(name="Start Time", value=f"`{when}`" + (f" ({timezone})" if timezone else ""), inline=True)
        if all_day:
            embed.add_field(name="Type", value="All-Day Event", inline=True)
        elif duration:
            embed.add_field(name="Duration", value=f"{duration} min", inline=True)

        if location:
            embed.add_field(name="Location", value=location, inline=False)
        if conference_url:
            embed.add_field(name="Video Link", value=f"[Join Meeting]({conference_url})", inline=False)
        if actual_alarm and actual_alarm > 0:
            embed.add_field(name="Reminder Alarm", value=f"{actual_alarm} min before", inline=True)
        if repeats:
            embed.add_field(name="Repeats", value=repeats.name, inline=True)

        embed.set_footer(text="Open the attached .ics file to add it to Apple Calendar, Google Calendar, or Outlook.")

        await interaction.followup.send(embed=embed, file=discord_file)

    @calendar_group.command(
        name="create_task",
        description="Create and download an iCalendar To-Do / Task (.ics) file for Apple Reminders or Outlook Tasks.",
    )
    @app_commands.describe(
        title="Task title or action item",
        due="When the task is due (e.g. 'tomorrow at 5pm', 'next Friday at 17:00', '2026-09-30')",
        priority="Priority level (High, Medium, Low)",
        description="Detailed description or checklist notes",
        location="Location for the task (e.g. 'Office', 'Home')",
        alarm_minutes="Reminder notification minutes before due date (default 15; 0 for no alarm)",
        timezone="Timezone (e.g. 'US/Eastern', 'US/Pacific', 'est', 'pst')",
        all_day="Whether the deadline is an all-day date (default False)",
    )
    @app_commands.choices(
        priority=[
            app_commands.Choice(name="High (!!!)", value=1),
            app_commands.Choice(name="Medium (!!)", value=5),
            app_commands.Choice(name="Low (!)", value=9),
        ]
    )
    async def create_task_slash(
        self,
        interaction: discord.Interaction,
        title: str,
        due: Optional[str] = None,
        priority: Optional[app_commands.Choice[int]] = None,
        description: Optional[str] = None,
        location: Optional[str] = None,
        alarm_minutes: Optional[int] = 15,
        timezone: Optional[str] = None,
        all_day: Optional[bool] = False,
    ):
        if not self.is_user_authorized(interaction.user.id):
            await interaction.response.send_message(
                "You are not authorized to use Calendar commands.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=False)

        actual_alarm = alarm_minutes
        if all_day and alarm_minutes == 15:
            actual_alarm = 0

        pri_val = priority.value if priority else None

        try:
            ics_bytes = await run_in_executor(
                ical_utils.create_single_todo_ics,
                summary=title,
                due=due,
                description=description,
                location=location,
                priority=pri_val,
                alarm_minutes_before=actual_alarm,
                timezone=timezone,
                all_day=bool(all_day),
            )
        except Exception as e:
            sentry_sdk.capture_exception(e)
            await interaction.followup.send(f"❌ Failed to generate task file: {e}")
            return

        safe_filename = re.sub(r'[\\/*?:"<>| ]', "_", title).strip("_") or "task"
        filename = f"{safe_filename}.ics"
        discord_file = discord.File(io.BytesIO(ics_bytes), filename=filename)

        embed = discord.Embed(
            title=f"📋 Task: {title}",
            description=description or "No description provided.",
            color=discord.Color.gold(),
        )
        if due:
            embed.add_field(name="Due Date", value=f"`{due}`" + (f" ({timezone})" if timezone else ""), inline=True)
        if priority:
            embed.add_field(name="Priority", value=priority.name, inline=True)
        if location:
            embed.add_field(name="Location", value=location, inline=False)
        if actual_alarm and actual_alarm > 0:
            embed.add_field(name="Reminder Alert", value=f"{actual_alarm} min before", inline=True)

        embed.set_footer(text="Open the attached .ics file to add it directly to Apple Reminders or Outlook Tasks.")
        await interaction.followup.send(embed=embed, file=discord_file)

    @calendar_group.command(
        name="export_reminders",
        description="Export your Shisho reminders to an iCalendar (.ics) file for your calendar or reminders app.",
    )
    @app_commands.describe(
        status="Which reminders to export: 'active' (default), 'sent' (completed), or 'all'",
        as_tasks="Export as checklist tasks for Apple Reminders / Outlook Tasks (default False: calendar events)",
        limit="Maximum number of reminders to export",
        timezone="Timezone for the calendar export (e.g. 'US/Eastern', 'US/Pacific')",
    )
    @app_commands.choices(
        status=[
            app_commands.Choice(name="Active (Upcoming)", value="active"),
            app_commands.Choice(name="Sent (Completed)", value="sent"),
            app_commands.Choice(name="All Reminders", value="all"),
        ]
    )
    async def export_reminders_slash(
        self,
        interaction: discord.Interaction,
        status: Optional[app_commands.Choice[str]] = None,
        as_tasks: Optional[bool] = False,
        limit: Optional[int] = None,
        timezone: Optional[str] = None,
    ):
        if not self.is_user_authorized(interaction.user.id):
            await interaction.response.send_message(
                "You are not authorized to use Calendar commands.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=False)
        status_val = status.value if status else "active"

        def _fetch_from_pb():
            pb = get_pb_client()
            pb_user_id = get_discord_user_id(pb, str(interaction.user.id))
            if not pb_user_id:
                return None

            if status_val == "sent":
                filter_str = f"owner = '{pb_user_id}' && is_sent = true"
                sort_str = "-remind_at"
            elif status_val == "all":
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
            await interaction.followup.send(f"❌ {UNLINKED_ACCOUNT_MESSAGE}")
            return

        if not records:
            await interaction.followup.send(f"You have no reminders matching status `{status_val}` to export.")
            return

        try:
            if as_tasks:
                todo_models = ical_utils.reminders_to_ical_todos(records, user_timezone=timezone)
                ics_bytes = await run_in_executor(
                    ical_utils.build_ical_calendar,
                    todos=todo_models,
                    cal_name=f"Shisho Tasks ({status_val.capitalize()})",
                    cal_timezone=timezone,
                )
                filename = f"reminders_tasks_{status_val}.ics"
                dest_name = "Apple Reminders, Microsoft To-Do, or Outlook Tasks"
                color = discord.Color.gold()
                title_prefix = "📋 Reminders Tasks Export"
                count_str = f"Exported **{len(todo_models)} reminder task(s)** into a To-Do list file."
            else:
                event_models = ical_utils.reminders_to_ical_events(records, user_timezone=timezone)
                ics_bytes = await run_in_executor(
                    ical_utils.build_ical_calendar,
                    events=event_models,
                    cal_name=f"Shisho Reminders ({status_val.capitalize()})",
                    cal_timezone=timezone,
                )
                filename = f"reminders_{status_val}.ics"
                dest_name = "Apple Calendar, Google Calendar, or Outlook"
                color = discord.Color.green()
                title_prefix = "📅 Reminders Calendar Export"
                count_str = f"Exported **{len(event_models)} reminder event(s)** into a calendar feed file."
        except Exception as e:
            sentry_sdk.capture_exception(e)
            await interaction.followup.send(f"❌ Failed to build reminders file: {e}")
            return

        discord_file = discord.File(io.BytesIO(ics_bytes), filename=filename)

        embed = discord.Embed(
            title=f"{title_prefix} ({status_val.capitalize()})",
            description=count_str,
            color=color,
        )
        embed.set_footer(text=f"Download and import into {dest_name}.")

        await interaction.followup.send(embed=embed, file=discord_file)

    @calendar_group.command(
        name="reading_plan",
        description="Generate a daily reading pacing schedule (.ics) for any book.",
    )
    @app_commands.describe(
        book="The title of the book to read",
        total_pages="Total number of pages in the book",
        days="Duration in days to complete the book (default 30)",
        target_date="Specific finish date (e.g. '2026-10-15', overrides days)",
        current_page="Current page offset if already reading (default 0)",
        daily_time="Time of day for reading sessions (default '20:00')",
        schedule_type="Which days to read: Daily, Weekdays Only, or Weekends Only",
        as_tasks="Export as checklist tasks for Apple Reminders instead of calendar events (default False)",
        timezone="Timezone (e.g. 'US/Eastern', 'US/Pacific', 'est', 'pst')",
    )
    @app_commands.choices(
        schedule_type=[
            app_commands.Choice(name="Every Day (Daily)", value="daily"),
            app_commands.Choice(name="Weekdays Only (Mon-Fri)", value="weekdays"),
            app_commands.Choice(name="Weekends Only (Sat-Sun)", value="weekends"),
        ]
    )
    async def reading_plan_slash(
        self,
        interaction: discord.Interaction,
        book: str,
        total_pages: int,
        days: Optional[int] = 30,
        target_date: Optional[str] = None,
        current_page: Optional[int] = 0,
        daily_time: Optional[str] = "20:00",
        schedule_type: Optional[app_commands.Choice[str]] = None,
        as_tasks: Optional[bool] = False,
        timezone: Optional[str] = None,
    ):
        if not self.is_user_authorized(interaction.user.id):
            await interaction.response.send_message(
                "You are not authorized to use Calendar commands.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=False)

        filter_val = schedule_type.value if schedule_type else "daily"
        cur_pg = current_page or 0

        try:
            plan = await run_in_executor(
                pacer_utils.calculate_reading_schedule,
                book_title=book,
                total_pages=total_pages,
                current_page=cur_pg,
                target_date=target_date,
                duration_days=days,
                days_filter=filter_val,
                preferred_time=daily_time,
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to calculate reading schedule: {e}")
            return

        is_todos = bool(as_tasks)
        try:
            ics_bytes = await run_in_executor(
                pacer_utils.generate_reading_plan_ics,
                plan=plan,
                as_tasks=is_todos,
                timezone=timezone,
            )
        except Exception as e:
            sentry_sdk.capture_exception(e)
            await interaction.followup.send(f"❌ Failed to generate reading plan calendar file: {e}")
            return

        safe_title = re.sub(r'[\\/*?:"<>| ]', "_", book).strip("_") or "book"
        filename = f"{safe_title}_Reading_Plan.ics"
        discord_file = discord.File(io.BytesIO(ics_bytes), filename=filename)

        dest_app = "Apple Reminders / Microsoft To-Do" if is_todos else "Apple Calendar / Google Calendar / Outlook"
        pace_str = f"{plan.pace_pages_per_session:.1f}" if plan.pace_pages_per_session % 1 != 0 else f"{int(plan.pace_pages_per_session)}"

        embed = discord.Embed(
            title=f"📖 Reading Plan: {plan.book_title}",
            description=(
                f"**Timeline:** `{plan.start_date}` → `{plan.target_date}`\n"
                f"**Sessions:** {plan.total_sessions} ({plan.days_filter.capitalize()})\n"
                f"**Pace:** ~**{pace_str} pages / session** at `{plan.preferred_time.strftime('%H:%M')}`\n"
                f"**Pages:** {plan.remaining_pages} remaining (starting from page {plan.current_page})"
            ),
            color=discord.Color.teal() if not is_todos else discord.Color.gold(),
        )

        milestone_sessions = [s for s in plan.sessions if s.is_milestone]
        if milestone_sessions:
            m_text = "\n".join(
                f"• `{m.date}`: Page **{m.end_page}** ({m.milestone_label or f'{m.progress_percentage}%'})"
                for m in milestone_sessions
            )
            embed.add_field(name="🎯 Milestones", value=m_text, inline=False)

        embed.set_footer(text=f"Open {filename} to import into {dest_app}.")
        await interaction.followup.send(embed=embed, file=discord_file)

    @commands.command(name="ical", aliases=["calendar"])
    async def ical_prefix_command(self, ctx: commands.Context, *, args: str = ""):
        """
        Prefix command for quick event generation or reminder export.
        Usage:
            !ical export
            !ical "Meeting Title" "tomorrow at 3pm" [optional location]
        """
        if not self.is_user_authorized(ctx.author.id):
            await ctx.send("You are not authorized to use Calendar commands.")
            return

        clean_args = args.strip()
        if not clean_args or clean_args.lower() in ("export", "reminders", "export reminders"):
            # Export reminders
            def _fetch_reminders():
                pb = get_pb_client()
                pb_user_id = get_discord_user_id(pb, str(ctx.author.id))
                if not pb_user_id:
                    return None
                return pb.collection("reminders").get_full_list(
                    query_params={"filter": f"owner = '{pb_user_id}' && (is_sent = false || is_sent = null)", "sort": "remind_at"}
                )

            records = await run_in_executor(_fetch_reminders)
            if records is None:
                await ctx.send(UNLINKED_ACCOUNT_MESSAGE)
                return
            if not records:
                await ctx.send("You have no active reminders to export.")
                return

            event_models = ical_utils.reminders_to_ical_events(records)
            ics_bytes = await run_in_executor(
                ical_utils.build_ical_calendar,
                event_models,
                cal_name="Shisho Active Reminders",
            )
            file = discord.File(io.BytesIO(ics_bytes), filename="reminders.ics")
            await ctx.send(f"📅 Exported **{len(event_models)} active reminder(s)**:", file=file)
            return

        # Try parsing quotes: !ical "Title" "When" "Location"
        matches = re.findall(r'"([^"]+)"|(\S+)', clean_args)
        tokens = [m[0] or m[1] for m in matches]

        if len(tokens) < 2:
            await ctx.send('Usage: `!ical "Event Title" "tomorrow at 3pm" [optional location]` or `!ical export`')
            return

        title = tokens[0]
        when = tokens[1]
        location = tokens[2] if len(tokens) > 2 else None

        try:
            ics_bytes = await run_in_executor(
                ical_utils.create_single_event_ics,
                summary=title,
                start=when,
                location=location,
            )
        except Exception as e:
            await ctx.send(f"❌ Error creating event: {e}")
            return

        safe_filename = re.sub(r'[\\/*?:"<>| ]', "_", title).strip("_") or "event"
        file = discord.File(io.BytesIO(ics_bytes), filename=f"{safe_filename}.ics")
        await ctx.send(f"📅 Created **{title}** for `{when}`:", file=file)


async def setup(bot):
    await bot.add_cog(Calendar(bot))
