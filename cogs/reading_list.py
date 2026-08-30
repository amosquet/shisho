import os
from datetime import datetime

import discord
from discord import app_commands
import sentry_sdk
from discord.ext import commands
from pocketbase import PocketBase
from pocketbase.client import FileUpload

class ReadingList(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.pb_url = os.getenv("POCKETBASE_URL")
        self.pb_user = os.getenv("POCKETBASE_USER")
        self.pb_password = os.getenv("POCKETBASE_PASSWORD")

    def get_pb_client(self):
        if not self.pb_url or not self.pb_user or not self.pb_password:
            raise Exception("PocketBase configuration missing in environment variables.")
        url = self.pb_url if "://" in self.pb_url else f"https://{self.pb_url}"
        pb = PocketBase(url)
        pb.collection("users").auth_with_password(self.pb_user or "", self.pb_password or "")
        return pb

    @app_commands.command(name="addbook", description="Adds a book to the reading list on PocketBase.")
    @app_commands.describe(
        title="Title of the book",
        author="Author of the book",
        publish_date="Publish date",
        isbn="ISBN of the book",
        status="Status of the book",
        start_date="Start reading date (YYYY-MM-DD)",
        end_date="Finished reading date (YYYY-MM-DD)",
        cover_image="Optional cover image for the book"
    )
    @app_commands.choices(status=[
        app_commands.Choice(name="Planned", value="planned"),
        app_commands.Choice(name="Reading", value="reading"),
        app_commands.Choice(name="Read", value="read"),
        app_commands.Choice(name="Dropped", value="dropped"),
    ])
    async def add_book(
        self,
        interaction: discord.Interaction,
        status: app_commands.Choice[str],
        title: str | None = None,
        author: str | None = None,
        isbn: str | None = None,
        publish_date: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        cover_image: discord.Attachment = None,
    ):
        if not title and not isbn:
            await interaction.response.send_message("You must provide either a title or an ISBN.", ephemeral=True)
            return
            
        await interaction.response.defer()
        status_val = status.value
        today = datetime.now().strftime("%Y-%m-%d")

        final_start_date = (
            start_date
            if start_date
            else (today if status_val in ["read", "reading"] else "")
        )
        final_end_date = end_date if end_date else (today if status_val == "read" else "")

        cover_filename = None
        cover_data = None
        if cover_image:
            cover_filename = cover_image.filename
            cover_data = await cover_image.read()

        fetched_image_url = ""
        fetched_desc = ""
        if isbn and (not title or not author):
            api_key = os.getenv("GOOGLE_BOOKS_API_KEY")
            if api_key:
                from utils import google_books
                book_data = await google_books.fetch_book_data(isbn, api_key)
                if isinstance(book_data, dict):
                    title = title or book_data.get("title", "Unknown Title")
                    author = author or ", ".join(book_data.get("authors", ["Unknown Author"]))
                    publish_date = publish_date or book_data.get("publishedDate", "")
                    fetched_image_url = book_data.get("thumbnail", "")
                    fetched_desc = book_data.get("description", "")
        
        title = title or "Unknown Title"
        author = author or "Unknown Author"
        publish_date = publish_date or ""
        isbn = isbn or ""

        try:
            await self.add_book_to_pocketbase(
                str(interaction.user.id), 
                title, 
                author, 
                status_val, 
                publish_date, 
                isbn, 
                final_start_date, 
                final_end_date, 
                fetched_image_url,
                fetched_desc,
                cover_filename, 
                cover_data
            )
            await interaction.followup.send(
                f"Successfully added **{title}** by {author} to the reading list!"
            )
        except Exception as e:
            sentry_sdk.capture_exception(e)
            await interaction.followup.send(f"An error occurred: {e}")

    async def add_book_to_pocketbase(
        self,
        discord_id: str,
        title: str,
        author: str,
        status_val: str,
        publish_date: str,
        isbn: str,
        final_start_date: str,
        final_end_date: str,
        image_url: str = "",
        description: str = "",
        cover_filename: str = None,
        cover_data: bytes = None
    ):
        isbn = isbn.replace("-", "")

        def _add():
            pb = self.get_pb_client()
            user_records = pb.collection("shisho_users").get_full_list(query_params={"filter": f"discord_id='{discord_id}'"})
            if not user_records:
                raise Exception("You have not linked your Discord account to Shisho. Please link it in the app.")
            pb_user_id = user_records[0].id

            new_book = {
                "owner": str(pb_user_id),
                "title": title,
                "author": author,
                "status": status_val,
                "publishDate": publish_date,
                "isbn": isbn,
                "startDate": final_start_date,
                "endDate": final_end_date,
                "imageUrl": image_url,
                "description": description,
            }
            
            if cover_filename and cover_data:
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
                final_entry = BodyDict(new_book, file_uploads)
            else:
                final_entry = new_book
                
            pb.collection("shisho_books").create(final_entry)

        await self.bot.loop.run_in_executor(None, _add)

    async def fetch_reading_list(self, discord_id: str) -> list[dict]:
        def _fetch():
            try:
                if not self.pb_url or not self.pb_user or not self.pb_password:
                    return []
                pb = self.get_pb_client()
                user_records = pb.collection("shisho_users").get_full_list(query_params={"filter": f"discord_id='{discord_id}'"})
                if not user_records:
                    return []
                pb_user_id = user_records[0].id

                records = pb.collection("shisho_books").get_full_list(query_params={"filter": f"owner='{pb_user_id}'"})
                result = []
                for r in records:
                    result.append({
                        "title": getattr(r, "title", ""),
                        "author": getattr(r, "author", ""),
                        "status": getattr(r, "status", ""),
                        "publishDate": getattr(r, "publish_date", getattr(r, "publishDate", "")),
                        "isbn": getattr(r, "isbn", ""),
                        "startDate": getattr(r, "start_date", getattr(r, "startDate", "")),
                        "endDate": getattr(r, "end_date", getattr(r, "endDate", "")),
                    })
                return result
            except Exception as e:
                sentry_sdk.capture_exception(e)
                return []

        return await self.bot.loop.run_in_executor(None, _fetch)

async def setup(bot):
    await bot.add_cog(ReadingList(bot))
