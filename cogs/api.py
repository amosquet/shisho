import ipaddress
import re
import aiohttp.web
import asyncio
import os
import random
import time
import secrets
import json
import discord
from discord.ext import commands, tasks
import sentry_sdk

from utils.db import get_pb_client, validate_pb_token, run_in_executor

@aiohttp.web.middleware
async def cors_middleware(request: aiohttp.web.Request, handler):
    if request.method == "OPTIONS":
        response = aiohttp.web.Response(status=204)
    else:
        try:
            response = await handler(request)
        except aiohttp.web.HTTPException as ex:
            response = ex

    # Configurable CORS origins with fallback to '*'
    raw_origins = os.getenv("CORS_ALLOWED_ORIGINS", "*").strip()
    if raw_origins == "*":
        response.headers["Access-Control-Allow-Origin"] = "*"
    else:
        allowed = [o.strip() for o in raw_origins.split(",") if o.strip()]
        origin = request.headers.get("Origin")
        if origin and origin in allowed:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"

    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    
    # Security headers
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response

class API(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.app = aiohttp.web.Application(middlewares=[cors_middleware])
        
        self.app.add_routes([
            aiohttp.web.get('/api/health', self.handle_health),
            aiohttp.web.post('/api/auth/request-pin', self.handle_request_pin),
            aiohttp.web.post('/api/auth/link-discord', self.handle_link_discord),
            aiohttp.web.post('/api/auth/unlink-discord', self.handle_unlink_discord),
            aiohttp.web.get('/api/announcements', self.handle_get_announcements),
            aiohttp.web.post('/api/books/suggest', self.handle_book_suggest),
            aiohttp.web.get('/api/books/suggestions', self.handle_get_suggestions),
            aiohttp.web.get('/', self.handle_serve_index),
            aiohttp.web.get('/index.html', self.handle_serve_index),
            aiohttp.web.get('/suggestions', self.handle_serve_suggestions),
            aiohttp.web.get('/suggestions.html', self.handle_serve_suggestions),
        ])
        
        self.runner = None
        self.site = None
        self.pending_pins = {}
        self.rate_limits = {}
        
        self.pb_url = os.getenv("POCKETBASE_URL")
        self.pb_user = os.getenv("POCKETBASE_USER")
        self.pb_password = os.getenv("POCKETBASE_PASSWORD")

    def _get_pb(self):
        return get_pb_client()

    def _validate_pb_token(self, token: str):
        return validate_pb_token(token)

    def _extract_bearer_token(self, request: aiohttp.web.Request) -> str | None:
        """Extract the Bearer token from the Authorization header robustly."""
        auth_header = request.headers.get('Authorization', '').strip()
        parts = auth_header.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == 'bearer':
            token = parts[1].strip()
            if token and len(token) >= 10:
                return token
        return None

    async def cog_load(self):
        self.runner = aiohttp.web.AppRunner(self.app)
        await self.runner.setup()
        self.site = aiohttp.web.TCPSite(self.runner, '0.0.0.0', 8080)
        await self.site.start()
        self.cleanup_task.start()
        print("Shisho API server started on port 8080")

    async def cog_unload(self):
        self.cleanup_task.cancel()
        if self.runner:
            await self.runner.cleanup()

    def _get_client_ip(self, request: aiohttp.web.Request) -> str:
        """Extract and validate client IP with proxy verification."""
        trust_proxy = os.getenv("TRUST_PROXIES", "true").lower() in ("true", "1", "yes")
        if trust_proxy:
            cf_ip = request.headers.get('CF-Connecting-IP', '').strip()
            if cf_ip:
                try:
                    ipaddress.ip_address(cf_ip)
                    return cf_ip
                except ValueError:
                    pass
            xff = request.headers.get('X-Forwarded-For', '').strip()
            if xff:
                candidate = xff.split(',')[0].strip()
                try:
                    ipaddress.ip_address(candidate)
                    return candidate
                except ValueError:
                    pass
        return request.remote or "unknown"

    def _check_rate_limit(self, request: aiohttp.web.Request, limit: int = 5, window: int = 300) -> bool:
        ip = self._get_client_ip(request)
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

        user_record = await run_in_executor(validate_pb_token, token)
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

        user_record = await run_in_executor(validate_pb_token, token)
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
            
            await run_in_executor(_update)
            
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

        user_record = await run_in_executor(validate_pb_token, token)
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
                
            discord_id = await run_in_executor(_update)
            
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
        def _read_announcements():
            filename = os.path.join("data", "announcements.json")
            if not os.path.exists(filename) and os.path.exists("announcements.json"):
                filename = "announcements.json"

            if os.path.exists(filename):
                with open(filename, "r", encoding="utf-8") as f:
                    return json.load(f)
            return []

        try:
            data = await run_in_executor(_read_announcements)
            return aiohttp.web.json_response(data)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return aiohttp.web.json_response({"error": "An internal error occurred"}, status=500)

    @staticmethod
    def _validate_and_sanitize_suggestion_payload(data: dict) -> dict:
        if not isinstance(data, dict):
            raise ValueError("Payload must be a JSON object.")

        def clean_str(val: str, max_len: int, allow_newlines: bool = False) -> str:
            s = str(val or "")
            if allow_newlines:
                s = "".join(ch for ch in s if ch in "\r\n\t" or not (0 <= ord(ch) < 32 or ord(ch) == 127))
            else:
                s = "".join(ch for ch in s if not (0 <= ord(ch) < 32 or ord(ch) == 127))
            return s.strip()[:max_len].strip()

        title = clean_str(data.get("title", ""), max_len=200)
        author = clean_str(data.get("author", ""), max_len=150)
        reason = clean_str(data.get("reason", ""), max_len=1000, allow_newlines=True)
        submitter = clean_str(data.get("submitter", ""), max_len=100) or "Anonymous"

        raw_isbn = str(data.get("isbn", "") or "").strip()
        clean_isbn = re.sub(r"[\s\-]", "", raw_isbn)
        if clean_isbn:
            if not (re.fullmatch(r"^\d{13}$", clean_isbn) or re.fullmatch(r"^\d{9}[\dXx]$", clean_isbn)):
                raise ValueError("Invalid ISBN format. ISBN must be a valid 10 or 13-digit number.")

        if not title and not clean_isbn:
            raise ValueError("Please provide a Book Title or an ISBN.")

        return {
            "title": title,
            "author": author,
            "isbn": clean_isbn,
            "reason": reason,
            "submitter": submitter
        }

    async def handle_book_suggest(self, request: aiohttp.web.Request):
        if not self._check_rate_limit(request, limit=15, window=300):
            return aiohttp.web.json_response({"error": "Rate limit exceeded. Please try again later."}, status=429)

        try:
            data = await request.json()
        except Exception:
            return aiohttp.web.json_response({"error": "Invalid JSON body."}, status=400)

        try:
            validated = self._validate_and_sanitize_suggestion_payload(data)
        except ValueError as ve:
            return aiohttp.web.json_response({"error": str(ve)}, status=400)

        suggested_books_cog = self.bot.get_cog("SuggestedBooks")
        if not suggested_books_cog:
            return aiohttp.web.json_response({"error": "Suggestion service is temporarily unavailable."}, status=503)

        try:
            res_data = await suggested_books_cog.add_suggestion(
                title=validated["title"],
                author=validated["author"],
                isbn=validated["isbn"],
                sender_name=validated["submitter"],
                message=validated["reason"],
                is_public=True,
                suggested_from="Web Form",
            )
            display_name = res_data.get("display_name", validated["title"] or validated["isbn"] or "Unknown Book")
            return aiohttp.web.json_response({
                "success": True,
                "display_name": display_name,
                "book": {
                    "id": res_data.get("id", ""),
                    "title": res_data.get("title", validated["title"]),
                    "author": res_data.get("author", validated["author"]),
                    "isbn": res_data.get("isbn", validated["isbn"]),
                }
            })
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return aiohttp.web.json_response({"error": "Failed to save suggestion. Please try again later."}, status=500)

    async def handle_get_suggestions(self, request: aiohttp.web.Request):
        def _fetch():
            pb = get_pb_client()
            records = pb.collection("shisho_books_recommendations").get_list(
                1, 50, query_params={
                    "sort": "-date_suggested,-created",
                    "filter": "is_public = true"
                }
            )
            books = []
            for r in records.items:
                cover_name = getattr(r, "cover", "") or (r.get("cover", "") if hasattr(r, "get") else "")
                coll_id = getattr(r, "collection_id", "") or (r.get("collection_id", "") if hasattr(r, "get") else "shisho_books_recommendations")
                rec_id = getattr(r, "id", "") or (r.get("id", "") if hasattr(r, "get") else "")

                cover_url = ""
                if cover_name and self.pb_url and rec_id:
                    cover_url = f"{self.pb_url.rstrip('/')}/api/files/{coll_id}/{rec_id}/{cover_name}"

                books.append({
                    "id": rec_id,
                    "title": getattr(r, "title", "") or (r.get("title", "") if hasattr(r, "get") else ""),
                    "author": getattr(r, "author", "") or (r.get("author", "") if hasattr(r, "get") else ""),
                    "isbn": getattr(r, "isbn", "") or (r.get("isbn", "") if hasattr(r, "get") else ""),
                    "message": getattr(r, "message", "") or (r.get("message", "") if hasattr(r, "get") else ""),
                    "date_suggested": getattr(r, "date_suggested", "") or (r.get("date_suggested", "") if hasattr(r, "get") else ""),
                    "suggested_from": getattr(r, "suggested_from", "") or (r.get("suggested_from", "") if hasattr(r, "get") else ""),
                    "cover_url": cover_url,
                })
            return books

        try:
            books = await run_in_executor(_fetch)
            return aiohttp.web.json_response({"books": books})
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return aiohttp.web.json_response({"error": "Failed to fetch suggestions"}, status=500)

    async def handle_serve_index(self, request: aiohttp.web.Request):
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        index_path = os.path.join(base_dir, "templates", "index.html")
        if os.path.exists(index_path):
            return aiohttp.web.FileResponse(index_path)
        return aiohttp.web.Response(text="Index page not found", status=404)

    async def handle_serve_suggestions(self, request: aiohttp.web.Request):
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        suggestions_path = os.path.join(base_dir, "templates", "suggestions.html")
        if os.path.exists(suggestions_path):
            return aiohttp.web.FileResponse(suggestions_path)
        return aiohttp.web.Response(text="Suggestions page not found", status=404)

    @tasks.loop(minutes=5.0)
    async def cleanup_task(self):
        now = time.time()
        
        # Clean pending_pins
        expired_pins = [
            did for did, data in self.pending_pins.items()
            if now > data.get("expires_at", 0)
        ]
        for did in expired_pins:
            del self.pending_pins[did]
            
        # Clean rate_limits (300s window is used in _check_rate_limit)
        empty_ips = []
        for ip, times in self.rate_limits.items():
            valid_times = [t for t in times if now - t < 300]
            if not valid_times:
                empty_ips.append(ip)
            else:
                self.rate_limits[ip] = valid_times
                
        for ip in empty_ips:
            del self.rate_limits[ip]

    @cleanup_task.before_loop
    async def before_cleanup_task(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(API(bot))
