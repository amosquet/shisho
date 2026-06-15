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
import smtplib
from email.message import EmailMessage

class API(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.app = aiohttp.web.Application()
        
        # Setup routes matching quick-curie's requests
        self.app.add_routes([
            aiohttp.web.post('/api/auth/request-pin', self.handle_request_pin),
            aiohttp.web.post('/api/auth/verify-pin', self.handle_verify_pin),
            
            aiohttp.web.post('/api/auth/request-email-link', self.handle_request_email_link),
            aiohttp.web.post('/api/auth/verify-email-link', self.handle_verify_email_link),
            aiohttp.web.post('/api/auth/unlink-email', self.handle_unlink_email),
            
            aiohttp.web.get('/api/users/me', self.handle_get_me),
            aiohttp.web.patch('/api/users/me/preferences', self.handle_patch_preferences),
            
            aiohttp.web.get('/api/collections/books/records', self.handle_get_books),
            aiohttp.web.post('/api/collections/books/records', self.handle_post_book),
            aiohttp.web.patch('/api/collections/books/records/{id}', self.handle_patch_book),
            
            aiohttp.web.get('/api/collections/notes/records', self.handle_get_notes),
            aiohttp.web.post('/api/collections/notes/records', self.handle_post_note),
            aiohttp.web.patch('/api/collections/notes/records/{id}', self.handle_patch_note),
            aiohttp.web.delete('/api/collections/notes/records/{id}', self.handle_delete_record),
            
            aiohttp.web.get('/api/collections/reminders/records', self.handle_get_reminders),
            aiohttp.web.post('/api/collections/reminders/records', self.handle_post_reminder),
            aiohttp.web.patch('/api/collections/reminders/records/{id}', self.handle_patch_reminder),
            aiohttp.web.delete('/api/collections/reminders/records/{id}', self.handle_delete_record),
            
            aiohttp.web.get('/api/files/{collection}/{record_id}/{filename}', self.handle_get_file)
        ])
        self.runner = None
        self.site = None
        
        self.pending_pins = {}
        self.pending_email_pins = {}
        self.active_tokens = {}
        
        self.pb_url = os.getenv("POCKETBASE_URL")
        self.pb_user = os.getenv("POCKETBASE_USER")
        self.pb_password = os.getenv("POCKETBASE_PASSWORD")
        
        self.email_smtp_host = os.getenv("EMAIL_SMTP_HOST")
        self.email_smtp_port = int(os.getenv("EMAIL_SMTP_PORT", 465))
        self.email_user = os.getenv("EMAIL_USER")
        self.email_pass = os.getenv("EMAIL_PASS")

    def _get_pb(self):
        pb = PocketBase(self.pb_url or "")
        pb.collection("users").auth_with_password(self.pb_user or "", self.pb_password or "")
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

    async def _get_user_id(self, request: aiohttp.web.Request):
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return None
        token = auth_header.split(' ')[1]
        
        def _lookup():
            pb = self._get_pb()
            try:
                records = pb.collection("shisho_users").get_full_list(query_params={"filter": f"session_token='{token}'"})
                if records:
                    # In python SDK, record fields are attributes or dictionary keys
                    return getattr(records[0], "discord_id", None) or records[0].get("discord_id")
            except Exception as e:
                print(f"Error looking up token: {e}")
            return None
            
        return await self.bot.loop.run_in_executor(None, _lookup)

    async def handle_request_pin(self, request: aiohttp.web.Request):
        try:
            data = await request.json()
            discord_id = data.get('discord_id')
            if not discord_id:
                return aiohttp.web.json_response({"error": "Missing discord_id"}, status=400)
            
            try:
                user = await self.bot.fetch_user(int(discord_id))
            except Exception:
                return aiohttp.web.json_response({"error": "Invalid discord_id or bot cannot access user"}, status=400)
                
            pin = f"{random.randint(100000, 999999)}"
            self.pending_pins[discord_id] = {
                "pin": pin,
                "expires_at": time.time() + 300 # 5 minutes
            }
            
            await user.send(f"Your Shisho login PIN is: **{pin}**. This PIN expires in 5 minutes.")
            return aiohttp.web.json_response({"message": "PIN sent via DM"})
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return aiohttp.web.json_response({"error": str(e)}, status=500)

    async def handle_verify_pin(self, request: aiohttp.web.Request):
        try:
            data = await request.json()
            discord_id = data.get('discord_id')
            pin = data.get('pin')
            
            if not discord_id or not pin:
                return aiohttp.web.json_response({"error": "Missing discord_id or pin"}, status=400)
                
            pending = self.pending_pins.get(discord_id)
            if not pending:
                return aiohttp.web.json_response({"error": "No pending PIN found for this user"}, status=400)
                
            if time.time() > pending["expires_at"]:
                del self.pending_pins[discord_id]
                return aiohttp.web.json_response({"error": "PIN expired"}, status=400)
                
            if pending["pin"] != pin:
                return aiohttp.web.json_response({"error": "Invalid PIN"}, status=400)
                
            # PIN verified!
            del self.pending_pins[discord_id]
            
            # Generate a token
            token = secrets.token_hex(32)
            
            def _save_session():
                pb = self._get_pb()
                try:
                    records = pb.collection("shisho_users").get_full_list(query_params={"filter": f"discord_id='{discord_id}'"})
                    if records:
                        pb.collection("shisho_users").update(records[0].id, {"session_token": token})
                    else:
                        pb.collection("shisho_users").create({
                            "discord_id": str(discord_id),
                            "session_token": token
                        })
                except Exception as e:
                    print(f"Failed to save session to PB: {e}")
            
            await self.bot.loop.run_in_executor(None, _save_session)
            
            return aiohttp.web.json_response({
                "token": token,
                "record": {
                    "id": str(discord_id)
                }
            })
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return aiohttp.web.json_response({"error": str(e)}, status=500)

    async def handle_request_email_link(self, request: aiohttp.web.Request):
        user_id = await self._get_user_id(request)
        if not user_id:
            return aiohttp.web.json_response({"error": "Unauthorized"}, status=401)
            
        try:
            data = await request.json()
            email_address = data.get('email')
            if not email_address:
                return aiohttp.web.json_response({"error": "Missing email"}, status=400)
                
            pin = f"{random.randint(100000, 999999)}"
            self.pending_email_pins[user_id] = {
                "pin": pin,
                "email": email_address,
                "expires_at": time.time() + 300 # 5 minutes
            }
            
            def _send_email():
                if not self.email_user or not self.email_pass:
                    print("Email credentials not configured.")
                    return
                msg = EmailMessage()
                msg.set_content(f"Your Shisho email verification PIN is: {pin}. This PIN expires in 5 minutes.")
                msg["Subject"] = "Shisho Email Verification"
                msg["From"] = self.email_user
                msg["To"] = email_address
                
                try:
                    server = smtplib.SMTP_SSL(self.email_smtp_host, self.email_smtp_port)
                    server.login(self.email_user, self.email_pass)
                    server.send_message(msg)
                    server.quit()
                except Exception as e:
                    print(f"Failed to send email link pin: {e}")

            await self.bot.loop.run_in_executor(None, _send_email)
            return aiohttp.web.json_response({"message": "PIN sent via email"})
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return aiohttp.web.json_response({"error": str(e)}, status=500)

    async def handle_verify_email_link(self, request: aiohttp.web.Request):
        user_id = await self._get_user_id(request)
        if not user_id:
            return aiohttp.web.json_response({"error": "Unauthorized"}, status=401)
            
        try:
            data = await request.json()
            pin = data.get('pin')
            
            if not pin:
                return aiohttp.web.json_response({"error": "Missing pin"}, status=400)
                
            pending = self.pending_email_pins.get(user_id)
            if not pending:
                return aiohttp.web.json_response({"error": "No pending PIN found"}, status=400)
                
            if time.time() > pending["expires_at"]:
                del self.pending_email_pins[user_id]
                return aiohttp.web.json_response({"error": "PIN expired"}, status=400)
                
            if pending["pin"] != pin:
                return aiohttp.web.json_response({"error": "Invalid PIN"}, status=400)
                
            # PIN verified
            email_address = pending["email"]
            del self.pending_email_pins[user_id]
            
            def _link_email():
                pb = self._get_pb()
                records = pb.collection("shisho_users").get_full_list(query_params={"filter": f"discord_id='{user_id}'"})
                if records:
                    pb.collection("shisho_users").update(records[0].id, {"email": email_address})
                else:
                    raise Exception("User not found in pocketbase")
            
            await self.bot.loop.run_in_executor(None, _link_email)
            
            return aiohttp.web.json_response({
                "message": "Email linked successfully",
                "email": email_address
            })
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return aiohttp.web.json_response({"error": str(e)}, status=500)

    async def handle_unlink_email(self, request: aiohttp.web.Request):
        user_id = await self._get_user_id(request)
        if not user_id:
            return aiohttp.web.json_response({"error": "Unauthorized"}, status=401)
            
        try:
            def _unlink_email():
                pb = self._get_pb()
                records = pb.collection("shisho_users").get_full_list(query_params={"filter": f"discord_id='{user_id}'"})
                if records:
                    pb.collection("shisho_users").update(records[0].id, {"email": ""})
                else:
                    raise Exception("User not found in pocketbase")
            
            await self.bot.loop.run_in_executor(None, _unlink_email)
            return aiohttp.web.json_response({"message": "Email unlinked successfully"})
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return aiohttp.web.json_response({"error": str(e)}, status=500)

    async def handle_get_me(self, request: aiohttp.web.Request):
        user_id = await self._get_user_id(request)
        if not user_id:
            return aiohttp.web.json_response({"error": "Unauthorized"}, status=401)
            
        try:
            def _fetch():
                pb = self._get_pb()
                records = pb.collection("shisho_users").get_full_list(query_params={"filter": f"discord_id='{user_id}'"})
                if records:
                    record = records[0]
                    def get_field(rec, field_name, default=None):
                        val = getattr(rec, field_name, None)
                        if val is None and hasattr(rec, "get"):
                            val = rec.get(field_name)
                        return val if val is not None else default

                    return {
                        "discord_id": get_field(record, "discord_id"),
                        "email": get_field(record, "email", ""),
                        "preferences": get_field(record, "preferences", {})
                    }
                return {"discord_id": user_id, "email": "", "preferences": {}}
                
            data = await self.bot.loop.run_in_executor(None, _fetch)
            return aiohttp.web.json_response(data)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return aiohttp.web.json_response({"error": str(e)}, status=500)

    async def handle_patch_preferences(self, request: aiohttp.web.Request):
        user_id = await self._get_user_id(request)
        if not user_id:
            return aiohttp.web.json_response({"error": "Unauthorized"}, status=401)
            
        try:
            body = await request.json()
            
            def _update():
                pb = self._get_pb()
                records = pb.collection("shisho_users").get_full_list(query_params={"filter": f"discord_id='{user_id}'"})
                if records:
                    record = records[0]
                    current_prefs = getattr(record, "preferences", None) or record.get("preferences") or {}
                    current_prefs.update(body)
                    updated = pb.collection("shisho_users").update(record.id, {"preferences": current_prefs})
                    return {
                        "discord_id": getattr(updated, "discord_id", None) or updated.get("discord_id"),
                        "preferences": getattr(updated, "preferences", None) or updated.get("preferences") or {}
                    }
                return {"error": "User not found"}
                
            data = await self.bot.loop.run_in_executor(None, _update)
            if "error" in data:
                return aiohttp.web.json_response(data, status=404)
            return aiohttp.web.json_response(data)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return aiohttp.web.json_response({"error": str(e)}, status=500)

    async def _handle_get_collection(self, request: aiohttp.web.Request, collection_name: str):
        user_id = await self._get_user_id(request)
        if not user_id:
            return aiohttp.web.json_response({"error": "Unauthorized"}, status=401)
            
        try:
            def _fetch():
                pb = self._get_pb()
                query_params = {}
                if collection_name in ["notes", "reminders"]:
                    query_params["filter"] = f"user_id='{user_id}'"
                
                records = pb.collection(collection_name).get_full_list(query_params=query_params)
                
                import datetime
                def sanitize_dict(d):
                    res = {}
                    for k, v in d.items():
                        if isinstance(v, datetime.datetime):
                            res[k] = v.strftime("%Y-%m-%d %H:%M:%S.000Z")
                        else:
                            res[k] = v
                    return res
                            
                # Format to match what pocketbase returns (quick-curie expects this)
                items = []
                for record in records:
                    # In python sdk, fields are stored in the object dict, but private or reserved ones might need cleaning
                    # Let's safely extract fields to a dict
                    record_dict = {}
                    for k, v in record.__dict__.items():
                        if not k.startswith('_'):
                            record_dict[k] = v
                    # ensure we include standard fields if they are missing
                    if not hasattr(record, "id") and getattr(record, "id", None) is not None:
                        record_dict["id"] = record.id
                    if getattr(record, "created", None) is not None:
                        record_dict["created"] = record.created
                    if getattr(record, "updated", None) is not None:
                        record_dict["updated"] = record.updated
                    items.append(sanitize_dict(record_dict))
                    
                return {
                    "page": 1,
                    "perPage": len(items) if items else 30,
                    "totalItems": len(items),
                    "totalPages": 1,
                    "items": items
                }
                
            data = await self.bot.loop.run_in_executor(None, _fetch)
            return aiohttp.web.json_response(data)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return aiohttp.web.json_response({"error": str(e)}, status=500)

    async def _handle_post_collection(self, request: aiohttp.web.Request, collection_name: str):
        user_id = await self._get_user_id(request)
        if not user_id:
            return aiohttp.web.json_response({"error": "Unauthorized"}, status=401)
            
        try:
            body = await request.json()
            if collection_name in ["notes", "reminders"]:
                body["user_id"] = user_id # forcefully associate with this user
                
            if body.get("attachment") == "":
                del body["attachment"]
            
            def _create():
                pb = self._get_pb()
                record = pb.collection(collection_name).create(body)
                import datetime
                def sanitize_dict(d):
                    res = {}
                    for k, v in d.items():
                        if isinstance(v, datetime.datetime):
                            res[k] = v.strftime("%Y-%m-%d %H:%M:%S.000Z")
                        else:
                            res[k] = v
                    return res
                return sanitize_dict(record.__dict__)
                
            data = await self.bot.loop.run_in_executor(None, _create)
            return aiohttp.web.json_response(data)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return aiohttp.web.json_response({"error": str(e)}, status=500)

    async def _handle_patch_collection(self, request: aiohttp.web.Request, collection_name: str):
        user_id = await self._get_user_id(request)
        if not user_id:
            return aiohttp.web.json_response({"error": "Unauthorized"}, status=401)
            
        record_id = request.match_info.get('id')
        if not record_id:
            return aiohttp.web.json_response({"error": "Missing record ID"}, status=400)
            
        try:
            body = await request.json()
            
            def _update():
                pb = self._get_pb()
                # Security check: verify this record actually belongs to the user if it's a user-owned collection
                if collection_name in ["notes", "reminders"]:
                    existing = pb.collection(collection_name).get_one(record_id)
                    if getattr(existing, "user_id", None) != user_id:
                        raise Exception("Forbidden")
                    
                    if "user_id" in body:
                        del body["user_id"] # don't let them change ownership
                        
                if body.get("attachment") == "":
                    del body["attachment"]
                    
                record = pb.collection(collection_name).update(record_id, body)
                import datetime
                def sanitize_dict(d):
                    res = {}
                    for k, v in d.items():
                        if isinstance(v, datetime.datetime):
                            res[k] = v.strftime("%Y-%m-%d %H:%M:%S.000Z")
                        else:
                            res[k] = v
                    return res
                return sanitize_dict(record.__dict__)
                
            data = await self.bot.loop.run_in_executor(None, _update)
            return aiohttp.web.json_response(data)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return aiohttp.web.json_response({"error": str(e)}, status=500)

    async def handle_delete_record(self, request: aiohttp.web.Request):
        user_id = await self._get_user_id(request)
        if not user_id:
            return aiohttp.web.json_response({"error": "Unauthorized"}, status=401)
            
        record_id = request.match_info.get('id')
        collection_name = request.path.split('/')[3] # /api/collections/notes/records/id -> index 3 is 'notes'
        
        try:
            def _delete():
                pb = self._get_pb()
                if collection_name in ["notes", "reminders"]:
                    existing = pb.collection(collection_name).get_one(record_id)
                    if getattr(existing, "user_id", None) != user_id:
                        raise Exception("Forbidden")
                pb.collection(collection_name).delete(record_id)
                
            await self.bot.loop.run_in_executor(None, _delete)
            return aiohttp.web.json_response({"success": True})
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return aiohttp.web.json_response({"error": str(e)}, status=500)

    # Book handlers
    async def handle_get_books(self, request: aiohttp.web.Request): return await self._handle_get_collection(request, "books")
    async def handle_post_book(self, request: aiohttp.web.Request): return await self._handle_post_collection(request, "books")
    async def handle_patch_book(self, request: aiohttp.web.Request): return await self._handle_patch_collection(request, "books")

    # Notes handlers
    async def handle_get_notes(self, request: aiohttp.web.Request): return await self._handle_get_collection(request, "notes")
    async def handle_post_note(self, request: aiohttp.web.Request): return await self._handle_post_collection(request, "notes")
    async def handle_patch_note(self, request: aiohttp.web.Request): return await self._handle_patch_collection(request, "notes")

    # Reminders handlers
    async def handle_get_reminders(self, request: aiohttp.web.Request): return await self._handle_get_collection(request, "reminders")
    async def handle_post_reminder(self, request: aiohttp.web.Request): return await self._handle_post_collection(request, "reminders")
    async def handle_patch_reminder(self, request: aiohttp.web.Request): return await self._handle_patch_collection(request, "reminders")

    async def handle_get_file(self, request: aiohttp.web.Request):
        collection = request.match_info.get('collection')
        record_id = request.match_info.get('record_id')
        filename = request.match_info.get('filename')
        
        query_string = request.query_string
        target_url = f"{self.pb_url}/api/files/{collection}/{record_id}/{filename}"
        if query_string:
            target_url += f"?{query_string}"
            
        raise aiohttp.web.HTTPFound(target_url)

async def setup(bot):
    await bot.add_cog(API(bot))
