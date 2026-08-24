import os
import json
import base64
import asyncio
from datetime import datetime, timedelta

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
import sentry_sdk

from utils import google_books

NOTIFIED_BOOKS_FILE = "notified_books.json"

class AuthorTracking(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.pb_url = os.getenv("POCKETBASE_URL")
        self.pb_user = os.getenv("POCKETBASE_USER")
        self.pb_password = os.getenv("POCKETBASE_PASSWORD")
        self.google_books_api_key = os.getenv("GOOGLE_BOOKS_API_KEY")
        self.owner_id = int(os.getenv("OWNER_ID", "0"))
        
        if self.pb_url and self.pb_user and self.pb_password and self.google_books_api_key and self.owner_id:
            self.check_new_releases.start()

    def get_pb_client(self):
        from pocketbase import PocketBase
        if not self.pb_url or not self.pb_user or not self.pb_password:
            raise Exception("PocketBase configuration missing in environment variables.")
        url = self.pb_url if "://" in self.pb_url else f"https://{self.pb_url}"
        pb = PocketBase(url)
        pb.collection("users").auth_with_password(self.pb_user or "", self.pb_password or "")
        return pb

    def cog_unload(self):
        self.check_new_releases.cancel()

    def load_notified_books(self) -> set:
        if os.path.exists(NOTIFIED_BOOKS_FILE):
            try:
                with open(NOTIFIED_BOOKS_FILE, "r", encoding="utf-8") as f:
                    return set(json.load(f))
            except Exception as e:
                print(f"Failed to load {NOTIFIED_BOOKS_FILE}: {e}")
                return set()
        return set()

    def save_notified_books(self, notified_books: set):
        try:
            with open(NOTIFIED_BOOKS_FILE, "w", encoding="utf-8") as f:
                json.dump(list(notified_books), f, indent=4)
        except Exception as e:
            print(f"Failed to save {NOTIFIED_BOOKS_FILE}: {e}")

    async def get_unique_authors(self) -> set:
        if not self.pb_url or not self.pb_user or not self.pb_password:
            return set()

        try:
            def _fetch():
                pb = self.get_pb_client()
                user_records = pb.collection("shisho_users").get_full_list(query_params={"filter": "discord_id='{:discord_id}'", "discord_id": self.owner_id})
                if not user_records:
                    return []
                pb_user_id = user_records[0].id
                return pb.collection("shisho_books").get_full_list(query_params={"filter": "owner='{:pb_user_id}'", "pb_user_id": pb_user_id})
            
            records = await self.bot.loop.run_in_executor(None, _fetch)
            
            authors = set()
            for record in records:
                author = getattr(record, "author", "")
                if author:
                    authors.add(author.strip())
            return authors
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return set()

    def is_recently_published(self, published_date_str: str) -> bool:
        if not published_date_str:
            return False
            
        try:
            # Parse dates like "2026", "2026-05", "2026-05-24"
            parts = published_date_str.split("-")
            year = int(parts[0])
            month = int(parts[1]) if len(parts) > 1 else 1
            day = int(parts[2]) if len(parts) > 2 else 1
            
            pub_date = datetime(year, month, day)
            
            # Consider "new" if published within the last 6 months or in the future
            six_months_ago = datetime.now() - timedelta(days=180)
            return pub_date >= six_months_ago
        except ValueError:
            return False

    async def check_authors_logic(self):
        authors = await self.get_unique_authors()
        if not authors:
            return 0

        notified_books = self.load_notified_books()
        new_books_found = []

        for author in authors:
            query = f'inauthor:"{author}"'
            
            try:
                items = await google_books.search_books(query, self.google_books_api_key, order_by="newest")
                
                for item in items:
                    book_id = item.get("id")
                    if not book_id or book_id in notified_books:
                        continue
                    
                    vol_info = item.get("volumeInfo", {})
                    pub_date = vol_info.get("publishedDate", "")
                    
                    # Skip if not a recent or upcoming book
                    if not self.is_recently_published(pub_date):
                        continue
                        
                    title = vol_info.get("title", "Unknown Title")
                    book_authors = ", ".join(vol_info.get("authors", ["Unknown Author"]))
                    thumbnail = vol_info.get("imageLinks", {}).get("thumbnail", "")
                    
                    # Only notify if our author is actually in the author list (Google search can be fuzzy)
                    if author.lower() not in book_authors.lower():
                        continue

                    new_books_found.append({
                        "id": book_id,
                        "title": title,
                        "authors": book_authors,
                        "publishedDate": pub_date,
                        "thumbnail": thumbnail.replace("http://", "https://")
                    })
                    notified_books.add(book_id)
            except Exception as e:
                sentry_sdk.capture_exception(e)
                print(f"Error fetching data for author {author}: {e}")
            
            # Small delay to respect rate limits
            await asyncio.sleep(1)

        self.save_notified_books(notified_books)
        
        if new_books_found:
            owner = self.bot.get_user(self.owner_id)
            if not owner:
                try:
                    owner = await self.bot.fetch_user(self.owner_id)
                except Exception:
                    owner = None
                    
            if owner:
                for book in new_books_found:
                    embed = discord.Embed(
                        title="New Book Release!",
                        description=f"A new book by an author you follow has been released or announced.",
                        color=discord.Color.green()
                    )
                    embed.add_field(name="Title", value=book["title"], inline=False)
                    embed.add_field(name="Author(s)", value=book["authors"], inline=True)
                    embed.add_field(name="Published", value=book["publishedDate"], inline=True)
                    if book["thumbnail"]:
                        embed.set_thumbnail(url=book["thumbnail"])
                        
                    try:
                        await owner.send(embed=embed)
                    except discord.Forbidden:
                        print("Could not send DM to owner.")
                        
        return len(new_books_found)

    @tasks.loop(hours=24.0)
    async def check_new_releases(self):
        try:
            await self.check_authors_logic()
        except Exception as e:
            sentry_sdk.capture_exception(e)
            print(f"Error in background author tracking task: {e}")

    @check_new_releases.before_loop
    async def before_check_new_releases(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="check_authors", description="Manually trigger a check for new books by authors in your reading list.")
    async def check_authors_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            count = await self.check_authors_logic()
            await interaction.followup.send(f"Check complete. Found {count} new release(s).")
        except Exception as e:
            sentry_sdk.capture_exception(e)
            await interaction.followup.send(f"An error occurred during the check: {e}")


async def setup(bot):
    await bot.add_cog(AuthorTracking(bot))
