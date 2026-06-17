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
        pb.collection("shisho_users").auth_with_password(self.pb_user or "", self.pb_password or "")
        return pb

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
        ip = request.headers.get('X-Forwarded-For', request.remote)
        if ip:
            ip = ip.split(',')[0].strip()
        else:
            ip = "unknown"
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
            return aiohttp.web.json_response({"error": str(e)}, status=500)

    async def handle_link_discord(self, request: aiohttp.web.Request):
        if not self._check_rate_limit(request):
            return aiohttp.web.json_response({"error": "Rate limit exceeded. Please try again later."}, status=429)
            
        try:
            data = await request.json()
            discord_id = data.get('discord_id')
            pin = data.get('pin')
            pb_user_id = data.get('pb_user_id')
            
            if not discord_id or not pin or not pb_user_id:
                return aiohttp.web.json_response({"error": "Missing discord_id, pin, or pb_user_id"}, status=400)
                
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
            return aiohttp.web.json_response({"success": True})
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return aiohttp.web.json_response({"error": str(e)}, status=500)

    async def handle_unlink_discord(self, request: aiohttp.web.Request):
        if not self._check_rate_limit(request):
            return aiohttp.web.json_response({"error": "Rate limit exceeded. Please try again later."}, status=429)
            
        try:
            data = await request.json()
            pb_user_id = data.get('pb_user_id')
            if not pb_user_id:
                return aiohttp.web.json_response({"error": "Missing pb_user_id"}, status=400)
                
            def _update():
                pb = self._get_pb()
                pb.collection("shisho_users").update(pb_user_id, {"discord_id": ""})
                
            await self.bot.loop.run_in_executor(None, _update)
            return aiohttp.web.json_response({"success": True})
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return aiohttp.web.json_response({"error": str(e)}, status=500)

    async def handle_get_announcements(self, request: aiohttp.web.Request):
        try:
            if os.path.exists("announcements.json"):
                with open("announcements.json", "r") as f:
                    data = json.load(f)
                    return aiohttp.web.json_response(data)
            return aiohttp.web.json_response([])
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return aiohttp.web.json_response({"error": str(e)}, status=500)

async def setup(bot):
    await bot.add_cog(API(bot))
