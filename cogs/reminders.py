import os
import asyncio
from datetime import datetime, timezone
import dateparser

import discord
from discord import app_commands
from discord.ext import commands, tasks
import sentry_sdk

from utils.db import get_pb_client, get_discord_user_id, run_in_executor

class Reminders(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.pb_url = os.getenv("POCKETBASE_URL")
        self.pb_user = os.getenv("POCKETBASE_USER")
        self.pb_password = os.getenv("POCKETBASE_PASSWORD")
        self.owner_id = int(os.getenv("OWNER_ID", "0"))
        
        allowed = os.getenv("ALLOWED_EMAILS", "")
        self.owner_email = allowed.split(",")[0].strip() if allowed else ""
        
        if self.pb_url and self.pb_user and self.pb_password:
            self.check_reminders.start()

    def cog_unload(self):
        self.check_reminders.cancel()

    async def add_reminder(self, user_id: str, when: str, text: str, for_discord: bool = True, user_tz: str = None) -> str:
        tz_map = {
            "jp": "Asia/Tokyo",
            "fr": "Europe/Paris",
            "uk": "Europe/London",
            "de": "Europe/Berlin",
            "us": "US/Eastern",
            "est": "US/Eastern",
            "edt": "US/Eastern",
            "california": "US/Pacific",
            "ca": "US/Pacific",
            "pt": "US/Pacific",
            "pst": "US/Pacific",
            "pdt": "US/Pacific",
            "chicago": "US/Central",
            "il": "US/Central",
            "ct": "US/Central",
            "cst": "US/Central",
            "cdt": "US/Central"
        }
        
        if not user_tz:
            user_tz = "US/Eastern"
        else:
            user_tz = tz_map.get(user_tz.lower(), user_tz)

        settings = {'PREFER_DATES_FROM': 'future', 'TO_TIMEZONE': 'UTC', 'RETURN_AS_TIMEZONE_AWARE': True}
        settings['TIMEZONE'] = user_tz
            
        parsed_time = dateparser.parse(
            when, 
            settings=settings
        )

        if not parsed_time:
            return f"Sorry, I couldn't understand the time '{when}'. Please try another format."

        if parsed_time < datetime.now(timezone.utc):
            return "That time is in the past! Please specify a future time."

        try:
            def add_to_pocketbase():
                pb = get_pb_client()
                pb_user_id = get_discord_user_id(pb, user_id)
                if not pb_user_id:
                    return "Error: You have not linked your Discord account to Shisho. Please link it in the app."

                # We format it to standard ISO string that pocketbase can parse as a Date field, or store it as string
                # We'll use a string formatted as ISO-8601: "2026-05-24 10:00:00.000Z"
                dt_str = parsed_time.strftime("%Y-%m-%d %H:%M:%S.%fZ")
                
                entry = {
                    "owner": str(pb_user_id),
                    "reminder_text": text,
                    "remind_at": dt_str,
                    "is_sent": False
                }
                pb.collection("reminders").create(entry)
                return "success"

            res = await run_in_executor(add_to_pocketbase)
            if res != "success":
                return res
            
            if for_discord:
                unix_timestamp = int(parsed_time.timestamp())
                formatted_time = f"<t:{unix_timestamp}:F> (<t:{unix_timestamp}:R>)"
            else:
                formatted_time = f"`{parsed_time.strftime('%Y-%m-%d %H:%M UTC')}`"
            return f"Got it! I will remind you: **{text}** at {formatted_time}"
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return f"An error occurred while saving the reminder: {e}"

    async def get_reminders_text(self, user_id: str, for_discord: bool = True) -> str:
        try:
            def get_from_pocketbase():
                pb = get_pb_client()
                pb_user_id = get_discord_user_id(pb, user_id)
                if not pb_user_id:
                    return None
                
                # Fetch only for this user, and where is_sent = False
                filter_str = f"owner = '{pb_user_id}' && is_sent = False"
                return pb.collection("reminders").get_full_list(query_params={"filter": filter_str, "sort": "remind_at"})

            records = await run_in_executor(get_from_pocketbase)

            if records is None:
                return "Error: You have not linked your Discord account to Shisho. Please link it in the app."

            if not records:
                return "You have no active reminders."

            response = "**Your Active Reminders:**\n"
            for idx, record in enumerate(records, 1):
                r_text = getattr(record, "reminder_text", "Unknown")
                r_time = getattr(record, "remind_at", "Unknown time")
                
                # Make r_time look nicer
                try:
                    # Pocketbase returns dates like "2022-01-01 00:00:00.000Z"
                    parsed_dt = datetime.strptime(r_time, "%Y-%m-%d %H:%M:%S.%fZ")
                    if for_discord:
                        parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
                        unix_timestamp = int(parsed_dt.timestamp())
                        time_display = f"<t:{unix_timestamp}:F> (<t:{unix_timestamp}:R>)"
                    else:
                        time_display = f"`{parsed_dt.strftime('%Y-%m-%d %H:%M UTC')}`"
                except ValueError:
                    time_display = f"`{r_time}`"

                response += f"{idx}. **{r_text}** (at {time_display})\n"

            return response
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return "An error occurred while fetching your reminders. Ensure the 'reminders' collection exists in PocketBase with 'owner', 'reminder_text', 'remind_at', and 'is_sent' fields."

    @app_commands.command(name="remind", description="Set a reminder.")
    @app_commands.describe(
        when="When to remind you (e.g. 'in 5 minutes', 'tomorrow at 3pm')",
        text="What to remind you about",
        timezone="Optional timezone (defaults to Eastern Time. e.g. 'jp', 'fr', 'Asia/Tokyo')"
    )
    async def set_reminder(self, interaction: discord.Interaction, when: str, text: str, timezone: str = None):
        await interaction.response.defer(ephemeral=True)
        response = await self.add_reminder(str(interaction.user.id), when, text, user_tz=timezone)
        await interaction.followup.send(response)

    @app_commands.command(name="reminders", description="List your active reminders.")
    async def list_reminders(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        response = await self.get_reminders_text(str(interaction.user.id))
        await interaction.followup.send(response)

    async def delete_reminder(self, user_id: str, reminder_id_or_query: str) -> str:
        try:
            def _delete_from_pb():
                pb = get_pb_client()
                pb_user_id = get_discord_user_id(pb, user_id)
                if not pb_user_id:
                    return "Error: You have not linked your Discord account to Shisho. Please link it in the app."

                clean_target = reminder_id_or_query.strip()
                if not clean_target:
                    return "Error: Please specify a reminder ID, text keyword, index number, or 'all'."

                # If user says "all", delete all active reminders
                if clean_target.lower() == "all":
                    filter_str = f"owner = '{pb_user_id}' && is_sent = False"
                    records = pb.collection("reminders").get_full_list(query_params={"filter": filter_str})
                    if not records:
                        return "You have no active reminders to delete."
                    for r in records:
                        pb.collection("reminders").delete(r.id)
                    return f"Successfully deleted all ({len(records)}) active reminder(s)."

                # 1. Try finding by exact ID first
                try:
                    record = pb.collection("reminders").get_one(clean_target)
                    record_owner = getattr(record, "owner", "")
                    if record_owner == pb_user_id:
                        text = getattr(record, "reminder_text", "Reminder")
                        pb.collection("reminders").delete(record.id)
                        return f"Successfully deleted reminder: **{text}**"
                except Exception:
                    pass

                # 2. Try index number (e.g. "1", "2") based on active reminders sorted by remind_at
                filter_active = f"owner = '{pb_user_id}' && is_sent = False"
                active_records = pb.collection("reminders").get_full_list(query_params={"filter": filter_active, "sort": "remind_at"})
                if clean_target.isdigit():
                    idx = int(clean_target)
                    if 1 <= idx <= len(active_records):
                        target = active_records[idx - 1]
                        text = getattr(target, "reminder_text", "Reminder")
                        pb.collection("reminders").delete(target.id)
                        return f"Successfully deleted reminder #{idx}: **{text}**"

                # 3. Search by text keyword among active reminders
                safe_query = clean_target.replace("'", "\\'")
                filter_str = f"owner = '{pb_user_id}' && is_sent = False && reminder_text ~ '{safe_query}'"
                records = pb.collection("reminders").get_full_list(query_params={"filter": filter_str})
                if not records:
                    return f"No active reminder found matching '{clean_target}'."

                # Prefer exact text match if available
                matched = None
                for r in records:
                    r_text = getattr(r, "reminder_text", "")
                    if r_text.lower() == clean_target.lower():
                        matched = r
                        break
                if not matched:
                    matched = records[0]

                text = getattr(matched, "reminder_text", "Reminder")
                pb.collection("reminders").delete(matched.id)
                return f"Successfully deleted reminder: **{text}**"

            return await run_in_executor(_delete_from_pb)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return f"Failed to delete reminder: {e}"

    async def reminder_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        try:
            def _fetch_active():
                pb = get_pb_client()
                pb_user_id = get_discord_user_id(pb, str(interaction.user.id))
                if not pb_user_id:
                    return []
                filter_str = f"owner = '{pb_user_id}' && is_sent = False"
                return pb.collection("reminders").get_full_list(query_params={"filter": filter_str, "sort": "remind_at"})

            records = await run_in_executor(_fetch_active)
            if not records:
                return []

            choices = []
            if not current or "all".startswith(current.lower()):
                choices.append(app_commands.Choice(name="[Delete All Active Reminders]", value="all"))

            clean_cur = current.lower().strip()
            for idx, r in enumerate(records, 1):
                text = getattr(r, "reminder_text", "Reminder")
                if clean_cur and clean_cur != "all" and clean_cur not in text.lower() and str(idx) != clean_cur:
                    continue
                name_preview = f"#{idx}: {text}"[:100]
                choices.append(app_commands.Choice(name=name_preview, value=r.id))
            return choices[:25]
        except Exception:
            return []

    @app_commands.command(name="deletereminder", description="Delete or cancel an active reminder.")
    @app_commands.describe(reminder="The reminder to delete (select from list, type #index, text keyword, or 'all')")
    async def slash_delete_reminder(self, interaction: discord.Interaction, reminder: str):
        await interaction.response.defer(ephemeral=True)
        response = await self.delete_reminder(str(interaction.user.id), reminder)
        await interaction.followup.send(response)

    @slash_delete_reminder.autocomplete("reminder")
    async def slash_delete_reminder_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self.reminder_autocomplete(interaction, current)

    @tasks.loop(seconds=60.0)
    async def check_reminders(self):
        try:
            await asyncio.to_thread(self._check_and_send)
        except Exception as e:
            print(f"Error checking reminders: {e}")
            sentry_sdk.capture_exception(e)

    @check_reminders.before_loop
    async def before_check_reminders(self):
        await self.bot.wait_until_ready()

    def _check_and_send(self):
        pb = get_pb_client()
        
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%fZ")
        filter_str = f"is_sent = False && remind_at <= '{now_str}'"
        
        records = pb.collection("reminders").get_full_list(query_params={"filter": filter_str})
        
        # Cache for pb_user_id to discord_id mapping
        user_cache = {}
        
        for record in records:
            pb_user_id = getattr(record, "owner", "")
            if not pb_user_id:
                continue
                
            discord_id_str = user_cache.get(pb_user_id)
            if not discord_id_str:
                try:
                    user_record = pb.collection("shisho_users").get_one(pb_user_id)
                    discord_id_str = getattr(user_record, "discord_id", None)
                    user_cache[pb_user_id] = discord_id_str
                except Exception:
                    user_cache[pb_user_id] = None
                    discord_id_str = None
                    
            if not discord_id_str:
                # Can't notify if no linked discord ID
                continue
                
            preferences = getattr(user_record, "preferences", {})
            general_prefs = preferences.get("general", {}) if isinstance(preferences, dict) else {}
            send_reminders_to_discord = general_prefs.get("send_reminders_to_discord", False)
            
            if not send_reminders_to_discord:
                # User opted out of Discord reminders
                # Still mark as sent to avoid queue building up? Yes
                pb.collection("reminders").update(record.id, {"is_sent": True})
                continue
                
            user_id = int(discord_id_str)
            text = getattr(record, "reminder_text", "")
            record_id = record.id
            
            # Send DM
            asyncio.run_coroutine_threadsafe(
                self.send_reminder_dm(user_id, text, record_id),
                self.bot.loop
            )
            
            # Mark as sent
            pb.collection("reminders").update(record_id, {"is_sent": True})

    async def send_reminder_dm(self, user_id: int, text: str, record_id: str):
        user = self.bot.get_user(user_id)
        if not user:
            try:
                user = await self.bot.fetch_user(user_id)
            except Exception:
                pass

        if user:
            try:
                await user.send(f"⏰ **Reminder:** {text}")
            except discord.Forbidden:
                print(f"Cannot send DM to user {user_id}")
            except Exception as e:
                sentry_sdk.capture_exception(e)

        # If it's the owner, also send an email
        if user_id == self.owner_id and self.owner_email:
            email_cog = self.bot.get_cog("EmailGateway")
            if email_cog:
                try:
                    await asyncio.to_thread(
                        email_cog._send_email, 
                        self.owner_email, 
                        "Shisho Bot Reminder", 
                        f"Reminder: {text}"
                    )
                except Exception as e:
                    print(f"Failed to send email reminder: {e}")
                    sentry_sdk.capture_exception(e)

async def setup(bot):
    await bot.add_cog(Reminders(bot))
