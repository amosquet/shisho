import os
import asyncio
import json
from datetime import datetime, timezone
from typing import Any
import aiohttp
import dateparser

import discord
from discord import app_commands
from discord.ext import commands, tasks
import sentry_sdk

from utils.db import get_pb_client, get_pb_url, get_discord_user_id, run_in_executor


def _get_field(record: Any, field_name: str, default: Any = None) -> Any:
    """Helper to get a field value from either a dict or a PocketBase Record object."""
    if isinstance(record, dict):
        return record.get(field_name, default)
    return getattr(record, field_name, default)


def _parse_remind_at(remind_at_val: Any) -> datetime | None:
    """Parse various datetime representations into a UTC-aware datetime."""
    if not remind_at_val:
        return None
    if isinstance(remind_at_val, datetime):
        if remind_at_val.tzinfo is None:
            return remind_at_val.replace(tzinfo=timezone.utc)
        return remind_at_val.astimezone(timezone.utc)
    if isinstance(remind_at_val, str):
        cleaned = remind_at_val.replace("Z", "+00:00").replace(" ", "T")
        try:
            dt = datetime.fromisoformat(cleaned)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            try:
                return dateparser.parse(
                    remind_at_val,
                    settings={"TO_TIMEZONE": "UTC", "RETURN_AS_TIMEZONE_AWARE": True},
                )
            except Exception:
                return None
    return None


class Reminders(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.pb_url = os.getenv("POCKETBASE_URL")
        self.pb_user = os.getenv("POCKETBASE_USER")
        self.pb_password = os.getenv("POCKETBASE_PASSWORD")
        self.owner_id = int(os.getenv("OWNER_ID", "0"))

        allowed = os.getenv("ALLOWED_EMAILS", "")
        self.owner_email = allowed.split(",")[0].strip() if allowed else ""

        self._scheduled_tasks: dict[str, asyncio.Task] = {}
        self._dispatched_ids: set[str] = set()
        self._in_flight_ids: set[str] = set()
        self._dispatch_lock = asyncio.Lock()
        self._sse_task: asyncio.Task | None = None
        self._user_cache: dict[str, tuple[str | None, bool]] = {}

    async def cog_load(self):
        if self.pb_url and self.pb_user and self.pb_password:
            self._sse_task = asyncio.create_task(self._run_sse_listener())
            self.fallback_sync.start()

    def cog_unload(self):
        self.fallback_sync.cancel()
        if self._sse_task and not self._sse_task.done():
            self._sse_task.cancel()
        for task in list(self._scheduled_tasks.values()):
            if not task.done():
                task.cancel()
        self._scheduled_tasks.clear()
        self._in_flight_ids.clear()

    # -------------------------------------------------------------
    # In-Memory Scheduler & Event Handling
    # -------------------------------------------------------------
    def _cancel_scheduled(self, record_id: str) -> None:
        """Cancel an in-memory scheduled reminder task if present."""
        task = self._scheduled_tasks.pop(record_id, None)
        if task and not task.done():
            task.cancel()

    def _schedule_from_record(self, record_data: dict | Any) -> None:
        """Schedule an in-memory timer for a reminder record."""
        record_id = _get_field(record_data, "id")
        if not record_id:
            return

        # If already dispatched or currently in-flight, do not reschedule
        if record_id in self._dispatched_ids or record_id in self._in_flight_ids:
            self._cancel_scheduled(record_id)
            return

        is_sent = _get_field(record_data, "is_sent", False)
        if is_sent:
            self._dispatched_ids.add(record_id)
            self._cancel_scheduled(record_id)
            return

        remind_at_raw = _get_field(record_data, "remind_at")
        remind_at_dt = _parse_remind_at(remind_at_raw)
        if not remind_at_dt:
            return

        now = datetime.now(timezone.utc)
        delay = (remind_at_dt - now).total_seconds()

        # Cancel any existing task for this record before rescheduling
        self._cancel_scheduled(record_id)

        task = asyncio.create_task(self._reminder_timer(record_data, delay))
        self._scheduled_tasks[record_id] = task

    async def _reminder_timer(self, record_data: dict | Any, delay: float) -> None:
        """Sleeps until remind_at timestamp and dispatches the reminder."""
        record_id = _get_field(record_data, "id")
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            await self._dispatch_reminder(record_data)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"Error in reminder timer task for {record_id}: {e}")
            sentry_sdk.capture_exception(e)
        finally:
            self._scheduled_tasks.pop(record_id, None)

    async def _dispatch_reminder(self, record_data: dict | Any) -> None:
        """Verify reminder status, mark is_sent = True, and deliver DM / email."""
        record_id = _get_field(record_data, "id")
        if not record_id:
            return

        # Atomically check and claim dispatching
        async with self._dispatch_lock:
            if record_id in self._dispatched_ids or record_id in self._in_flight_ids:
                return
            self._in_flight_ids.add(record_id)

        # Cancel any scheduled timer task for this record
        self._cancel_scheduled(record_id)

        def _fetch_user_and_claim():
            pb = get_pb_client()
            try:
                current_rec = pb.collection("reminders").get_one(record_id)
                if getattr(current_rec, "is_sent", False):
                    return None, None, False, False
            except Exception as e:
                try:
                    pb = get_pb_client(refresh=True)
                    current_rec = pb.collection("reminders").get_one(record_id)
                    if getattr(current_rec, "is_sent", False):
                        return None, None, False, False
                except Exception:
                    # If record check completely fails and is_sent is unknown, do not duplicate
                    return None, None, False, False

            pb_user_id = getattr(current_rec, "owner", "")
            text = getattr(current_rec, "reminder_text", "")
            if not text:
                text = _get_field(record_data, "reminder_text", "")

            # Attempt to mark is_sent = True in PocketBase immediately to claim it
            try:
                pb.collection("reminders").update(record_id, {"is_sent": True})
            except Exception as e:
                print(f"Failed to pre-mark reminder {record_id} as sent in PB: {e}")
                try:
                    pb = get_pb_client(refresh=True)
                    pb.collection("reminders").update(record_id, {"is_sent": True})
                except Exception as e2:
                    print(f"Retry marking reminder {record_id} as sent also failed: {e2}")
                    sentry_sdk.capture_exception(e2)

            if not pb_user_id:
                pb_user_id = _get_field(record_data, "owner", "")

            if not pb_user_id:
                return None, text, False, True

            cached = self._user_cache.get(pb_user_id)
            if cached is not None:
                discord_id_str, send_reminders_to_discord = cached
            else:
                discord_id_str = None
                send_reminders_to_discord = False
                try:
                    user_record = pb.collection("shisho_users").get_one(pb_user_id)
                    discord_id_str = getattr(user_record, "discord_id", None)
                    preferences = getattr(user_record, "preferences", {})
                    general_prefs = (
                        preferences.get("general", {})
                        if isinstance(preferences, dict)
                        else {}
                    )
                    send_reminders_to_discord = general_prefs.get(
                        "send_reminders_to_discord", False
                    )
                    self._user_cache[pb_user_id] = (
                        discord_id_str,
                        send_reminders_to_discord,
                    )
                except Exception:
                    self._user_cache[pb_user_id] = (None, False)

            return discord_id_str, text, send_reminders_to_discord, True

        try:
            (
                discord_id_str,
                text,
                send_reminders_to_discord,
                should_deliver,
            ) = await run_in_executor(_fetch_user_and_claim)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            should_deliver = False
            discord_id_str = None
            text = None
            send_reminders_to_discord = False

        try:
            if should_deliver and text:
                if send_reminders_to_discord and discord_id_str:
                    try:
                        user_id = int(discord_id_str)
                        await self.send_reminder_dm(user_id, text, record_id)
                    except Exception as e:
                        sentry_sdk.capture_exception(e)
        finally:
            self._dispatched_ids.add(record_id)
            self._in_flight_ids.discard(record_id)

    async def _handle_realtime_event(self, action: str, record: dict):
        """Process incoming PocketBase realtime CRUD events for reminders."""
        record_id = record.get("id")
        if not record_id:
            return

        if action == "delete":
            self._dispatched_ids.discard(record_id)
            self._in_flight_ids.discard(record_id)
            self._cancel_scheduled(record_id)
        elif action == "create":
            self._dispatched_ids.discard(record_id)
            self._in_flight_ids.discard(record_id)
            is_sent = record.get("is_sent", False)
            if is_sent:
                self._dispatched_ids.add(record_id)
                self._cancel_scheduled(record_id)
            else:
                self._schedule_from_record(record)
        elif action == "update":
            is_sent = record.get("is_sent", False)
            if is_sent:
                self._dispatched_ids.add(record_id)
                self._cancel_scheduled(record_id)
            else:
                self._schedule_from_record(record)

    # -------------------------------------------------------------
    # PocketBase Realtime SSE Stream & Reconnection
    # -------------------------------------------------------------
    async def _run_sse_listener(self):
        """Persistent background task consuming the PocketBase realtime SSE stream."""
        await self.bot.wait_until_ready()
        base_url = get_pb_url(self.pb_url)
        if not base_url:
            return

        backoff = 1
        while True:
            try:
                sse_url = f"{base_url}/api/realtime"
                timeout = aiohttp.ClientTimeout(
                    total=None, sock_connect=10, sock_read=None
                )
                headers = {"Accept": "text/event-stream"}

                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(sse_url, headers=headers) as response:
                        if response.status != 200:
                            raise aiohttp.ClientError(
                                f"Unexpected status from SSE endpoint: {response.status}"
                            )

                        backoff = 1  # Reset backoff on connection success
                        current_event = ""
                        current_data = []
                        current_id = ""

                        async for raw_line in response.content:
                            line = raw_line.decode("utf-8", errors="replace").rstrip(
                                "\r\n"
                            )
                            if not line:
                                if current_event or current_data:
                                    data_str = "\n".join(current_data)
                                    await self._handle_raw_sse_message(
                                        current_event,
                                        data_str,
                                        current_id,
                                        session,
                                        base_url,
                                    )
                                    current_event = ""
                                    current_data = []
                                    current_id = ""
                                continue

                            if line.startswith(":"):
                                continue

                            if ":" in line:
                                field, value = line.split(":", 1)
                                if value.startswith(" "):
                                    value = value[1:]
                            else:
                                field = line
                                value = ""

                            if field == "event":
                                current_event = value
                            elif field == "data":
                                current_data.append(value)
                            elif field == "id":
                                current_id = value

                # If the stream exited cleanly (e.g. server closed connection / EOF),
                # sleep before reconnecting so we never tight-loop reconnects
                await asyncio.sleep(2)

            except asyncio.CancelledError:
                break
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                print(
                    f"PocketBase reminders SSE stream disconnected ({type(e).__name__}: {e}). Reconnecting in {backoff}s..."
                )
                try:
                    await asyncio.sleep(backoff)
                except asyncio.CancelledError:
                    break
                backoff = min(backoff * 2, 60)
            except Exception as e:
                print(
                    f"PocketBase reminders SSE unexpected error: {e}. Reconnecting in {backoff}s..."
                )
                sentry_sdk.capture_exception(e)
                try:
                    await asyncio.sleep(backoff)
                except asyncio.CancelledError:
                    break
                backoff = min(backoff * 2, 60)

    async def _handle_raw_sse_message(
        self,
        event_name: str,
        data_str: str,
        event_id: str,
        session: aiohttp.ClientSession,
        base_url: str,
    ):
        try:
            if event_name == "PB_CONNECT":
                payload = json.loads(data_str) if data_str else {}
                client_id = payload.get("clientId")
                if client_id:
                    def _get_token():
                        pb = get_pb_client()
                        return getattr(getattr(pb, "auth_store", None), "token", "")

                    token = await run_in_executor(_get_token)
                    sub_headers = {"Content-Type": "application/json"}
                    if token:
                        sub_headers["Authorization"] = token

                    sub_body = {
                        "clientId": client_id,
                        "subscriptions": ["reminders"],
                    }
                    async with session.post(
                        f"{base_url}/api/realtime",
                        json=sub_body,
                        headers=sub_headers,
                    ) as sub_res:
                        if sub_res.status >= 300:
                            print(
                                f"Failed to subscribe to reminders SSE: HTTP {sub_res.status}"
                            )

                    # Synchronize active records on connection
                    await self._sync_pending_reminders()

            elif event_name == "reminders" or event_name.startswith("reminders/"):
                payload = json.loads(data_str) if data_str else {}
                action = payload.get("action", "")
                record = payload.get("record", {})
                if record:
                    await self._handle_realtime_event(action, record)
        except Exception as e:
            print(f"Error handling SSE message ({event_name}): {e}")
            sentry_sdk.capture_exception(e)

    # -------------------------------------------------------------
    # Fallback Sync Task
    # -------------------------------------------------------------
    async def _sync_pending_reminders(self):
        """Fetch all unsent reminders from PocketBase and synchronize in-memory timers."""
        def _fetch_unsent():
            pb = get_pb_client()
            try:
                return pb.collection("reminders").get_full_list(
                    query_params={"filter": "is_sent = False"}
                )
            except Exception:
                try:
                    pb = get_pb_client(refresh=True)
                    return pb.collection("reminders").get_full_list(
                        query_params={"filter": "is_sent = False"}
                    )
                except Exception as e:
                    sentry_sdk.capture_exception(e)
                    return []

        try:
            records = await run_in_executor(_fetch_unsent)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return

        active_ids = set()
        for rec in records:
            rec_id = getattr(rec, "id", None)
            if not rec_id:
                continue

            # If already dispatched in memory, do not schedule or fire again!
            if rec_id in self._dispatched_ids or rec_id in self._in_flight_ids:
                def _bg_mark(rid=rec_id):
                    try:
                        pb = get_pb_client()
                        pb.collection("reminders").update(rid, {"is_sent": True})
                    except Exception:
                        pass
                asyncio.create_task(run_in_executor(_bg_mark))
                continue

            active_ids.add(rec_id)
            self._schedule_from_record(rec)

        # Cancel any scheduled task that is no longer in unsent records
        for task_id in list(self._scheduled_tasks.keys()):
            if task_id not in active_ids:
                self._cancel_scheduled(task_id)

    @tasks.loop(minutes=5.0)
    async def fallback_sync(self):
        try:
            await self._sync_pending_reminders()
        except Exception as e:
            print(f"Error in fallback reminder sync: {e}")
            sentry_sdk.capture_exception(e)

    @fallback_sync.before_loop
    async def before_fallback_sync(self):
        await self.bot.wait_until_ready()

    # -------------------------------------------------------------
    # Reminder Management Commands & Helpers
    # -------------------------------------------------------------
    async def add_reminder(
        self,
        user_id: str,
        when: str,
        text: str,
        for_discord: bool = True,
        user_tz: str = None,
    ) -> str:
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
            "cdt": "US/Central",
        }

        if not user_tz:
            user_tz = "US/Eastern"
        else:
            user_tz = tz_map.get(user_tz.lower(), user_tz)

        settings = {
            "PREFER_DATES_FROM": "future",
            "TO_TIMEZONE": "UTC",
            "RETURN_AS_TIMEZONE_AWARE": True,
        }
        settings["TIMEZONE"] = user_tz

        parsed_time = dateparser.parse(when, settings=settings)

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

                dt_str = parsed_time.strftime("%Y-%m-%d %H:%M:%S.%fZ")

                entry = {
                    "owner": str(pb_user_id),
                    "reminder_text": text,
                    "remind_at": dt_str,
                    "is_sent": False,
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

    async def get_reminders_text(
        self, user_id: str, for_discord: bool = True
    ) -> str:
        try:
            def get_from_pocketbase():
                pb = get_pb_client()
                pb_user_id = get_discord_user_id(pb, user_id)
                if not pb_user_id:
                    return None

                filter_str = f"owner = '{pb_user_id}' && is_sent = False"
                return pb.collection("reminders").get_full_list(
                    query_params={"filter": filter_str, "sort": "remind_at"}
                )

            records = await run_in_executor(get_from_pocketbase)

            if records is None:
                return "Error: You have not linked your Discord account to Shisho. Please link it in the app."

            if not records:
                return "You have no active reminders."

            response = "**Your Active Reminders:**\n"
            for idx, record in enumerate(records, 1):
                r_text = getattr(record, "reminder_text", "Unknown")
                r_time = getattr(record, "remind_at", "Unknown time")

                try:
                    parsed_dt = datetime.strptime(
                        r_time, "%Y-%m-%d %H:%M:%S.%fZ"
                    )
                    if for_discord:
                        parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
                        unix_timestamp = int(parsed_dt.timestamp())
                        time_display = (
                            f"<t:{unix_timestamp}:F> (<t:{unix_timestamp}:R>)"
                        )
                    else:
                        time_display = (
                            f"`{parsed_dt.strftime('%Y-%m-%d %H:%M UTC')}`"
                        )
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
        timezone="Optional timezone (defaults to Eastern Time. e.g. 'jp', 'fr', 'Asia/Tokyo')",
    )
    async def set_reminder(
        self,
        interaction: discord.Interaction,
        when: str,
        text: str,
        timezone: str = None,
    ):
        await interaction.response.defer(ephemeral=True)
        response = await self.add_reminder(
            str(interaction.user.id), when, text, user_tz=timezone
        )
        await interaction.followup.send(response)

    @app_commands.command(
        name="reminders", description="List your active reminders."
    )
    async def list_reminders(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        response = await self.get_reminders_text(str(interaction.user.id))
        await interaction.followup.send(response)

    async def delete_reminder(
        self, user_id: str, reminder_id_or_query: str
    ) -> str:
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
                    records = pb.collection("reminders").get_full_list(
                        query_params={"filter": filter_str}
                    )
                    if not records:
                        return "You have no active reminders to delete."
                    for r in records:
                        pb.collection("reminders").delete(r.id)
                        self._cancel_scheduled(r.id)
                    return f"Successfully deleted all ({len(records)}) active reminder(s)."

                # 1. Try finding by exact ID first
                try:
                    record = pb.collection("reminders").get_one(clean_target)
                    record_owner = getattr(record, "owner", "")
                    if record_owner == pb_user_id:
                        text = getattr(record, "reminder_text", "Reminder")
                        pb.collection("reminders").delete(record.id)
                        self._cancel_scheduled(record.id)
                        return f"Successfully deleted reminder: **{text}**"
                except Exception:
                    pass

                # 2. Try index number (e.g. "1", "2") based on active reminders sorted by remind_at
                filter_active = f"owner = '{pb_user_id}' && is_sent = False"
                active_records = pb.collection("reminders").get_full_list(
                    query_params={
                        "filter": filter_active,
                        "sort": "remind_at",
                    }
                )
                if clean_target.isdigit():
                    idx = int(clean_target)
                    if 1 <= idx <= len(active_records):
                        target = active_records[idx - 1]
                        text = getattr(target, "reminder_text", "Reminder")
                        pb.collection("reminders").delete(target.id)
                        self._cancel_scheduled(target.id)
                        return f"Successfully deleted reminder #{idx}: **{text}**"

                # 3. Search by text keyword among active reminders
                safe_query = clean_target.replace("'", "\\'")
                filter_str = f"owner = '{pb_user_id}' && is_sent = False && reminder_text ~ '{safe_query}'"
                records = pb.collection("reminders").get_full_list(
                    query_params={"filter": filter_str}
                )
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
                self._cancel_scheduled(matched.id)
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
                return pb.collection("reminders").get_full_list(
                    query_params={"filter": filter_str, "sort": "remind_at"}
                )

            records = await run_in_executor(_fetch_active)
            if not records:
                return []

            choices = []
            if not current or "all".startswith(current.lower()):
                choices.append(
                    app_commands.Choice(
                        name="[Delete All Active Reminders]", value="all"
                    )
                )

            clean_cur = current.lower().strip()
            for idx, r in enumerate(records, 1):
                text = getattr(r, "reminder_text", "Reminder")
                if (
                    clean_cur
                    and clean_cur != "all"
                    and clean_cur not in text.lower()
                    and str(idx) != clean_cur
                ):
                    continue
                name_preview = f"#{idx}: {text}"[:100]
                choices.append(
                    app_commands.Choice(name=name_preview, value=r.id)
                )
            return choices[:25]
        except Exception:
            return []

    @app_commands.command(
        name="deletereminder",
        description="Delete or cancel an active reminder.",
    )
    @app_commands.describe(
        reminder="The reminder to delete (select from list, type #index, text keyword, or 'all')"
    )
    async def slash_delete_reminder(
        self, interaction: discord.Interaction, reminder: str
    ):
        await interaction.response.defer(ephemeral=True)
        response = await self.delete_reminder(str(interaction.user.id), reminder)
        await interaction.followup.send(response)

    @slash_delete_reminder.autocomplete("reminder")
    async def slash_delete_reminder_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self.reminder_autocomplete(interaction, current)

    async def send_reminder_dm(
        self, user_id: int, text: str, record_id: str
    ):
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
                        f"Reminder: {text}",
                    )
                except Exception as e:
                    print(f"Failed to send email reminder: {e}")
                    sentry_sdk.capture_exception(e)


async def setup(bot):
    await bot.add_cog(Reminders(bot))

