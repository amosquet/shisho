import os
from datetime import datetime

import discord
from discord import app_commands
import sentry_sdk
from discord.ext import commands

from utils.db import (
    get_pb_client,
    get_discord_user_id,
    prepare_file_upload_payload,
    run_in_executor,
)

class ReadingList(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.pb_url = os.getenv("POCKETBASE_URL")
        self.pb_user = os.getenv("POCKETBASE_USER")
        self.pb_password = os.getenv("POCKETBASE_PASSWORD")

    def get_pb_client(self):
        return get_pb_client()

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
        api_key = os.getenv("GOOGLE_BOOKS_API_KEY")
        if api_key and (not publish_date or not isbn or not cover_data or not (title and author)):
            from utils import google_books
            search_query = isbn if isbn else (f"{title} {author}".strip() if (title and author) else (title or ""))
            if search_query:
                book_data = await google_books.fetch_book_data(search_query, api_key)
                if isinstance(book_data, dict):
                    title = title or book_data.get("title", "Unknown Title")
                    authors = book_data.get("authors", [])
                    if not author and authors and authors != ["Unknown Author"]:
                        author = ", ".join(authors)
                    publish_date = publish_date or book_data.get("publishedDate", "")
                    isbn = isbn or book_data.get("isbn", "")
                    fetched_image_url = book_data.get("thumbnail", "")
                    desc = book_data.get("description", "")
                    if desc and desc != "No description available.":
                        fetched_desc = desc

                    if not cover_data and fetched_image_url:
                        cover_filename, cover_data = await google_books.download_image(fetched_image_url)

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
            pb_user_id = get_discord_user_id(pb, discord_id)
            if not pb_user_id:
                raise Exception("You have not linked your Discord account to Shisho. Please link it in the app.")

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
            
            files = {"cover": (cover_filename, cover_data)} if cover_filename and cover_data else None
            final_entry = prepare_file_upload_payload(new_book, files)
                
            pb.collection("shisho_books").create(final_entry)

        await run_in_executor(_add)

    async def fetch_reading_list(self, discord_id: str) -> list[dict]:
        def _fetch():
            try:
                pb = self.get_pb_client()
                pb_user_id = get_discord_user_id(pb, discord_id)
                if not pb_user_id:
                    return []

                records = pb.collection("shisho_books").get_full_list(query_params={"filter": f"owner='{pb_user_id}'"})
                result = []
                for r in records:
                    result.append({
                        "id": getattr(r, "id", ""),
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

        return await run_in_executor(_fetch)

    async def delete_book_from_pocketbase(self, discord_id: str, query_or_id: str) -> str:
        def _delete():
            pb = self.get_pb_client()
            pb_user_id = get_discord_user_id(pb, discord_id)
            if not pb_user_id:
                return "Error: You have not linked your Discord account to Shisho. Please link it in the app."

            clean_target = query_or_id.strip()
            if not clean_target:
                return "Error: Please specify a book title, ISBN, or ID to delete."

            # 1. Try exact ID
            try:
                record = pb.collection("shisho_books").get_one(clean_target)
                record_owner = getattr(record, "owner", "") or (record.get("owner", "") if hasattr(record, "get") else "")
                if record_owner == pb_user_id:
                    title = getattr(record, "title", "") or (record.get("title", "") if hasattr(record, "get") else "") or "Unknown Title"
                    author = getattr(record, "author", "") or (record.get("author", "") if hasattr(record, "get") else "")
                    pb.collection("shisho_books").delete(record.id)
                    author_str = f" by {author}" if author else ""
                    return f"Successfully removed **{title}**{author_str} from your reading list."
            except Exception:
                pass

            # 2. Search books owned by user
            clean_isbn = clean_target.replace("-", "").replace(" ", "").strip()
            safe_query = clean_target.replace("'", "\\'")
            filter_str = f"owner = '{pb_user_id}' && (title ~ '{safe_query}' || author ~ '{safe_query}' || isbn ~ '{clean_isbn}')"
            records = pb.collection("shisho_books").get_full_list(query_params={"filter": filter_str})
            if not records:
                return f"No book found matching '{clean_target}' on your reading list."

            # Prefer exact title or ISBN match
            matched = None
            for r in records:
                r_title = getattr(r, "title", "") or (r.get("title", "") if hasattr(r, "get") else "")
                r_isbn = getattr(r, "isbn", "") or (r.get("isbn", "") if hasattr(r, "get") else "")
                if r_title.lower() == clean_target.lower() or (clean_isbn and r_isbn == clean_isbn):
                    matched = r
                    break
            if not matched:
                matched = records[0]

            title = getattr(matched, "title", "") or (matched.get("title", "") if hasattr(matched, "get") else "") or "Unknown Title"
            author = getattr(matched, "author", "") or (matched.get("author", "") if hasattr(matched, "get") else "")
            pb.collection("shisho_books").delete(matched.id)
            author_str = f" by {author}" if author else ""
            return f"Successfully removed **{title}**{author_str} from your reading list."

        try:
            return await run_in_executor(_delete)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return f"Failed to delete book: {e}"

    async def book_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        try:
            books = await self.fetch_reading_list(str(interaction.user.id))
            if not books:
                return []
            choices = []
            clean_cur = current.lower().strip()
            for b in books:
                title = b.get("title", "Unknown Title")
                author = b.get("author", "")
                isbn = b.get("isbn", "")
                display = f"{title} by {author}" if author else title
                if clean_cur and clean_cur not in display.lower() and clean_cur not in isbn.lower():
                    continue
                name_preview = display[:100]
                book_id = b.get("id") or title
                choices.append(app_commands.Choice(name=name_preview, value=book_id))
            return choices[:25]
        except Exception:
            return []

    @app_commands.command(name="deletebook", description="Removes a book from your reading list.")
    @app_commands.describe(book="The book to remove (select from list or type title/ISBN/ID)")
    async def delete_book_cmd(self, interaction: discord.Interaction, book: str):
        await interaction.response.defer(ephemeral=True)
        res = await self.delete_book_from_pocketbase(str(interaction.user.id), book)
        await interaction.followup.send(res)

    @delete_book_cmd.autocomplete("book")
    async def delete_book_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self.book_autocomplete(interaction, current)

async def setup(bot):
    await bot.add_cog(ReadingList(bot))
