import aiohttp.web
import asyncio
import os
import random
import time
import secrets
import json
import discord
from discord.ext import commands
import sentry_sdk
from pocketbase import PocketBase

class API(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.app = aiohttp.web.Application()
        
        self.app.add_routes([
            aiohttp.web.get('/api/health', self.handle_health),
            aiohttp.web.post('/api/auth/request-pin', self.handle_request_pin),
            aiohttp.web.post('/api/auth/link-discord', self.handle_link_discord),
            aiohttp.web.post('/api/auth/unlink-discord', self.handle_unlink_discord),
            aiohttp.web.get('/api/announcements', self.handle_get_announcements)
        ])
        
        self.runner = None
        self.site = None
        self.pending_pins = {}
        self.rate_limits = {}
        
        self.pb_url = os.getenv("POCKETBASE_URL")
        self.pb_user = os.getenv("POCKETBASE_USER")
        self.pb_password = os.getenv("POCKETBASE_PASSWORD")

    def _get_pb(self):
        pb = PocketBase(self.pb_url or "")
        pb.collection("users").auth_with_password(self.pb_user or "", self.pb_password or "")
        return pb

    def _validate_pb_token(self, token: str):
        """Validate a PocketBase user token via authRefresh.
        Returns the user record dict on success, or None on failure."""
        try:
            pb = PocketBase(self.pb_url or "")
            pb.auth_store.save(token, None)
            result = pb.collection("shisho_users").auth_refresh()
            return result.record
        except Exception:
            return None

    def _extract_bearer_token(self, request: aiohttp.web.Request) -> str | None:
        """Extract the Bearer token from the Authorization header."""
        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            return auth_header[7:].strip()
        return None

    async def cog_load(self):
        self.runner = aiohttp.web.AppRunner(self.app)
        await self.runner.setup()
        self.site = aiohttp.web.TCPSite(self.runner, '0.0.0.0', 8080)
        await self.site.start()
        print("Shisho API server started on port 8080")

    async def cog_unload(self):
        if self.runner:
            await self.runner.cleanup()

    def _check_rate_limit(self, request: aiohttp.web.Request, limit: int = 5, window: int = 300) -> bool:
        # Prefer CF-Connecting-IP (set by Cloudflare, cannot be spoofed by clients)
        ip = request.headers.get('CF-Connecting-IP') \
            or request.headers.get('X-Forwarded-For', '').split(',')[0].strip() \
            or request.remote \
            or "unknown"
        now = time.time()
        self.rate_limits[ip] = [t for t in self.rate_limits.get(ip, []) if now - t < window]
        if len(self.rate_limits[ip]) >= limit:
            return False
        self.rate_limits[ip].append(now)
        return True

    async def handle_health(self, request: aiohttp.web.Request):
        return aiohttp.web.json_response({"status": "ok", "service": "shisho-api-v2"})

    async def handle_request_pin(self, request: aiohttp.web.Request):
        if not self._check_rate_limit(request):
            return aiohttp.web.json_response({"error": "Rate limit exceeded. Please try again later."}, status=429)

        # Require PocketBase auth
        token = self._extract_bearer_token(request)
        if not token:
            return aiohttp.web.json_response({"error": "Authorization required"}, status=401)

        user_record = await self.bot.loop.run_in_executor(None, self._validate_pb_token, token)
        if not user_record:
            return aiohttp.web.json_response({"error": "Invalid or expired token"}, status=401)

        try:
            data = await request.json()
            discord_id = data.get('discord_id')
            if not discord_id:
                return aiohttp.web.json_response({"error": "Missing discord_id"}, status=400)
            
            try:
                user = await self.bot.fetch_user(int(discord_id))
            except Exception:
                return aiohttp.web.json_response({"error": "Invalid discord_id or bot cannot access user"}, status=400)
                
            pin = f"{100000 + secrets.randbelow(900000)}"
            self.pending_pins[discord_id] = {
                "pin": pin,
                "expires_at": time.time() + 300,
                "attempts": 0
            }
            
            await user.send(f"Your Shisho Discord link PIN is: **{pin}**. This PIN expires in 5 minutes.")
            return aiohttp.web.json_response({"message": "PIN sent via DM"})
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return aiohttp.web.json_response({"error": "An internal error occurred"}, status=500)

    async def handle_link_discord(self, request: aiohttp.web.Request):
        if not self._check_rate_limit(request):
            return aiohttp.web.json_response({"error": "Rate limit exceeded. Please try again later."}, status=429)

        # Require PocketBase auth
        token = self._extract_bearer_token(request)
        if not token:
            return aiohttp.web.json_response({"error": "Authorization required"}, status=401)

        user_record = await self.bot.loop.run_in_executor(None, self._validate_pb_token, token)
        if not user_record:
            return aiohttp.web.json_response({"error": "Invalid or expired token"}, status=401)

        try:
            data = await request.json()
            discord_id = data.get('discord_id')
            pin = data.get('pin')
            
            if not discord_id or not pin:
                return aiohttp.web.json_response({"error": "Missing discord_id or pin"}, status=400)

            # Derive pb_user_id from the validated token — not from the request body
            pb_user_id = user_record.id

            pending = self.pending_pins.get(discord_id)
            if not pending:
                return aiohttp.web.json_response({"error": "No pending PIN found for this user"}, status=400)
                
            if time.time() > pending["expires_at"]:
                del self.pending_pins[discord_id]
                return aiohttp.web.json_response({"error": "PIN expired"}, status=400)
                
            if pending["pin"] != pin:
                pending["attempts"] = pending.get("attempts", 0) + 1
                if pending["attempts"] >= 5:
                    del self.pending_pins[discord_id]
                    return aiohttp.web.json_response({"error": "Too many failed attempts. PIN invalidated."}, status=400)
                return aiohttp.web.json_response({"error": "Invalid PIN"}, status=400)
                
            del self.pending_pins[discord_id]
            
            def _update():
                pb = self._get_pb()
                pb.collection("shisho_users").update(pb_user_id, {"discord_id": str(discord_id)})
            
            await self.bot.loop.run_in_executor(None, _update)
            
            try:
                user = await self.bot.fetch_user(int(discord_id))
                await user.send("Your Discord account has been successfully linked to your Shisho app! You will now receive reminders and can manage your notes here.")
            except Exception:
                pass
                
            return aiohttp.web.json_response({"success": True})
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return aiohttp.web.json_response({"error": "An internal error occurred"}, status=500)

    async def handle_unlink_discord(self, request: aiohttp.web.Request):
        if not self._check_rate_limit(request):
            return aiohttp.web.json_response({"error": "Rate limit exceeded. Please try again later."}, status=429)

        # Require PocketBase auth
        token = self._extract_bearer_token(request)
        if not token:
            return aiohttp.web.json_response({"error": "Authorization required"}, status=401)

        user_record = await self.bot.loop.run_in_executor(None, self._validate_pb_token, token)
        if not user_record:
            return aiohttp.web.json_response({"error": "Invalid or expired token"}, status=401)

        try:
            # Derive pb_user_id from the validated token — caller can only unlink themselves
            pb_user_id = user_record.id

            def _update():
                pb = self._get_pb()
                user = pb.collection("shisho_users").get_one(pb_user_id)
                discord_id = getattr(user, "discord_id", None)
                pb.collection("shisho_users").update(pb_user_id, {"discord_id": ""})
                return discord_id
                
            discord_id = await self.bot.loop.run_in_executor(None, _update)
            
            if discord_id:
                try:
                    user = await self.bot.fetch_user(int(discord_id))
                    await user.send("Your Discord account has been disconnected from your Shisho app.")
                except Exception:
                    pass
                    
            return aiohttp.web.json_response({"success": True})
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return aiohttp.web.json_response({"error": "An internal error occurred"}, status=500)

    async def handle_get_announcements(self, request: aiohttp.web.Request):
        try:
            if os.path.exists("announcements.json"):
                with open("announcements.json", "r") as f:
                    data = json.load(f)
                    return aiohttp.web.json_response(data)
            return aiohttp.web.json_response([])
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return aiohttp.web.json_response({"error": "An internal error occurred"}, status=500)

async def setup(bot):
    await bot.add_cog(API(bot))
