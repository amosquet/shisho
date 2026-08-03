import os
import asyncio
import aiohttp
import discord
from discord.ext import commands, tasks
from discord import app_commands
import sentry_sdk
from pocketbase import PocketBase
from pocketbase.client import FileUpload

from utils import google_books

class ReadingListSync(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.pb_url = os.getenv("POCKETBASE_URL")
        self.pb_user = os.getenv("POCKETBASE_USER")
        self.pb_password = os.getenv("POCKETBASE_PASSWORD")
        self.google_books_api_key = os.getenv("GOOGLE_BOOKS_API_KEY")
        self.owner_id = int(os.getenv("OWNER_ID", "0"))
        
        if self.pb_url and self.pb_user and self.pb_password and self.google_books_api_key and self.owner_id:
            self.sync_reading_list.start()

    def get_pb_client(self):
        if not self.pb_url or not self.pb_user or not self.pb_password:
            raise Exception("PocketBase configuration missing in environment variables.")
        url = self.pb_url if "://" in self.pb_url else f"https://{self.pb_url}"
        pb = PocketBase(url)
        pb.collection("users").auth_with_password(self.pb_user or "", self.pb_password or "")
        return pb

    def cog_unload(self):
        self.sync_reading_list.cancel()

    async def _sync_logic(self):
        def _fetch_books():
            pb = self.get_pb_client()
            user_records = pb.collection("shisho_users").get_full_list(query_params={"filter": "discord_id='{:discord_id}'", "discord_id": self.owner_id})
            if not user_records:
                return []
            pb_user_id = user_records[0].id
            return pb.collection("shisho_books").get_full_list(query_params={"filter": "user_id='{:pb_user_id}'", "pb_user_id": pb_user_id})

        try:
            records = await self.bot.loop.run_in_executor(None, _fetch_books)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return

        for record in records:
            title = getattr(record, "title", "")
            author = getattr(record, "author", "")
            isbn = getattr(record, "isbn", "")
            publish_date = getattr(record, "publishDate", "")
            cover = getattr(record, "cover", "")
            description = getattr(record, "description", "")
            
            # Check if any important metadata is missing
            if not isbn or not publish_date or not cover or not title or not author or not description:
                query = ""
                if isbn:
                    query = f"isbn:{isbn}"
                elif title and author:
                    query = f"{title} {author}"
                elif title:
                    query = title
                
                if not query:
                    continue
                    
                try:
                    book_data = await google_books.fetch_book_data(query, self.google_books_api_key)
                    if isinstance(book_data, dict):
                        update_data = {}
                        if not isbn and book_data.get("isbn"):
                            update_data["isbn"] = book_data["isbn"]
                        if not publish_date and book_data.get("publishedDate") and book_data["publishedDate"] != "Unknown":
                            update_data["publishDate"] = book_data["publishedDate"]
                        if not title and book_data.get("title") and book_data["title"] != "Unknown Title":
                            update_data["title"] = book_data["title"]
                        if not author and book_data.get("authors") and book_data["authors"] != ["Unknown Author"]:
                            update_data["author"] = ", ".join(book_data["authors"])
                        if not description and book_data.get("description") and book_data["description"] != "No description available.":
                            update_data["description"] = book_data["description"]
                            
                        cover_data = None
                        cover_filename = None
                        thumbnail_url = book_data.get("thumbnail")
                        
                        if not cover and thumbnail_url:
                            thumbnail_url = thumbnail_url.replace("http://", "https://")
                            async with aiohttp.ClientSession() as session:
                                async with session.get(thumbnail_url) as resp:
                                    if resp.status == 200:
                                        cover_data = await resp.read()
                                        cover_filename = "cover.jpg"
                        
                        if update_data or (cover_data and cover_filename):
                            def _update():
                                pb = self.get_pb_client()
                                if cover_data and cover_filename:
                                    class BodyDict(dict):
                                        def __init__(self, regular_data, file_uploads):
                                            super().__init__(regular_data)
                                            self.regular_data = regular_data
                                            self.file_uploads = file_uploads
                                        def items(self):
                                            for k, v in self.regular_data.items():
                                                yield k, v
                                            for k, v in self.file_uploads.items():
                                                yield k, v
                                    file_uploads = {"cover": FileUpload((cover_filename, cover_data))}
                                    final_entry = BodyDict(update_data, file_uploads)
                                else:
                                    final_entry = update_data
                                    
                                pb.collection("shisho_books").update(record.id, final_entry)
                                
                            await self.bot.loop.run_in_executor(None, _update)
                except Exception as e:
                    sentry_sdk.capture_exception(e)
                
                await asyncio.sleep(2) # rate limiting
                
    @tasks.loop(hours=12.0)
    async def sync_reading_list(self):
        try:
            await self._sync_logic()
        except Exception as e:
            sentry_sdk.capture_exception(e)
            print(f"Error in background reading list sync task: {e}")

    @sync_reading_list.before_loop
    async def before_sync_reading_list(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="force_sync", description="Force sync missing reading list data from Google Books API.")
    async def force_sync(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            await self._sync_logic()
            await interaction.followup.send("Sync completed successfully.")
        except Exception as e:
            sentry_sdk.capture_exception(e)
            await interaction.followup.send(f"An error occurred during sync: {e}")

async def setup(bot):
    await bot.add_cog(ReadingListSync(bot))
