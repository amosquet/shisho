import os
import asyncio
from datetime import datetime, timezone
import dateparser

import discord
from discord import app_commands
from discord.ext import commands, tasks
import sentry_sdk
from pocketbase import PocketBase

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

    @app_commands.command(name="remind", description="Set a reminder.")
    @app_commands.describe(
        when="When to remind you (e.g. 'in 5 minutes', 'tomorrow at 3pm')",
        text="What to remind you about"
    )
    async def set_reminder(self, interaction: discord.Interaction, when: str, text: str):
        await interaction.response.defer(ephemeral=True)

        # Parse the time
        parsed_time = dateparser.parse(
            when, 
            settings={'PREFER_DATES_FROM': 'future', 'TIMEZONE': 'UTC', 'TO_TIMEZONE': 'UTC', 'RETURN_AS_TIMEZONE_AWARE': True}
        )

        if not parsed_time:
            await interaction.followup.send(f"Sorry, I couldn't understand the time '{when}'. Please try another format.")
            return

        if parsed_time < datetime.now(timezone.utc):
            await interaction.followup.send("That time is in the past! Please specify a future time.")
            return

        try:
            def add_to_pocketbase():
                pb = PocketBase(self.pb_url or "")
                pb.collection("users").auth_with_password(self.pb_user or "", self.pb_password or "")
                
                # We format it to standard ISO string that pocketbase can parse as a Date field, or store it as string
                # We'll use a string formatted as ISO-8601: "2026-05-24 10:00:00.000Z"
                dt_str = parsed_time.strftime("%Y-%m-%d %H:%M:%S.%fZ")
                
                entry = {
                    "user_id": str(interaction.user.id),
                    "reminder_text": text,
                    "remind_at": dt_str,
                    "is_sent": False
                }
                pb.collection("reminders").create(entry)

            await self.bot.loop.run_in_executor(None, add_to_pocketbase)
            
            formatted_time = parsed_time.strftime("%Y-%m-%d %H:%M UTC")
            await interaction.followup.send(f"Got it! I will remind you: **{text}** at `{formatted_time}`")
        except Exception as e:
            sentry_sdk.capture_exception(e)
            await interaction.followup.send(f"An error occurred while saving the reminder: {e}")

    @app_commands.command(name="reminders", description="List your active reminders.")
    async def list_reminders(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        try:
            def get_from_pocketbase():
                pb = PocketBase(self.pb_url or "")
                pb.collection("users").auth_with_password(self.pb_user or "", self.pb_password or "")
                # Fetch only for this user, and where is_sent = False
                filter_str = f"user_id = '{interaction.user.id}' && is_sent = False"
                return pb.collection("reminders").get_full_list(query_params={"filter": filter_str, "sort": "remind_at"})

            records = await self.bot.loop.run_in_executor(None, get_from_pocketbase)

            if not records:
                await interaction.followup.send("You have no active reminders.")
                return

            response = "**Your Active Reminders:**\n"
            for idx, record in enumerate(records, 1):
                r_text = getattr(record, "reminder_text", "Unknown")
                r_time = getattr(record, "remind_at", "Unknown time")
                
                # Make r_time look nicer
                try:
                    # Pocketbase returns dates like "2022-01-01 00:00:00.000Z"
                    parsed_dt = datetime.strptime(r_time, "%Y-%m-%d %H:%M:%S.%fZ")
                    r_time = parsed_dt.strftime("%Y-%m-%d %H:%M UTC")
                except ValueError:
                    pass

                response += f"{idx}. **{r_text}** (at `{r_time}`)\n"

            await interaction.followup.send(response)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            await interaction.followup.send("An error occurred while fetching your reminders. Ensure the 'reminders' collection exists in PocketBase with 'user_id', 'reminder_text', 'remind_at', and 'is_sent' fields.")

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
        pb = PocketBase(self.pb_url or "")
        pb.collection("users").auth_with_password(self.pb_user or "", self.pb_password or "")
        
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%fZ")
        filter_str = f"is_sent = False && remind_at <= '{now_str}'"
        
        records = pb.collection("reminders").get_full_list(query_params={"filter": filter_str})
        
        for record in records:
            user_id = int(getattr(record, "user_id", "0"))
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
