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
from utils.discord_helpers import UNLINKED_ACCOUNT_MESSAGE

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
        completed="Completion details or notes",
        description="Book synopsis or review",
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
        completed: str | None = None,
        description: str | None = None,
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
        final_desc = description if description is not None else fetched_desc
        final_completed = completed or ""

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
                final_desc,
                cover_filename, 
                cover_data,
                completed=final_completed,
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
        status_val: str = "planned",
        publish_date: str = "",
        isbn: str = "",
        final_start_date: str = "",
        final_end_date: str = "",
        image_url: str = "",
        description: str = "",
        cover_filename: str | None = None,
        cover_data: bytes | None = None,
        completed: str = "",
        **kwargs,
    ):
        isbn = isbn.replace("-", "").replace(" ", "").strip() if isbn else ""
        status_val = kwargs.get("status", status_val)
        final_start_date = kwargs.get("startDate", kwargs.get("start_date", final_start_date))
        final_end_date = kwargs.get("endDate", kwargs.get("end_date", final_end_date))
        publish_date = kwargs.get("publishDate", publish_date)
        completed = kwargs.get("completed", completed)
        description = kwargs.get("description", description)

        def _add():
            pb = self.get_pb_client()
            pb_user_id = get_discord_user_id(pb, discord_id)
            if not pb_user_id:
                raise Exception(UNLINKED_ACCOUNT_MESSAGE)

            new_book = {
                "owner": str(pb_user_id),
                "title": title,
                "author": author,
                "status": status_val,
                "publishDate": publish_date,
                "isbn": isbn,
                "startDate": final_start_date,
                "endDate": final_end_date,
                "completed": str(completed) if completed is not None else "",
                "description": description,
            }
            
            files = {"cover": (cover_filename, cover_data)} if cover_filename and cover_data else None
            final_entry = prepare_file_upload_payload(new_book, files)
                
            return pb.collection("shisho_books").create(final_entry)

        return await run_in_executor(_add)

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
                    def _g(obj, attr, fallback=""):
                        val = getattr(obj, attr, None)
                        if val is None and isinstance(obj, dict):
                            val = obj.get(attr)
                        if val is None and hasattr(obj, "get"):
                            val = obj.get(attr)
                        return val if val is not None else fallback

                    result.append({
                        "id": _g(r, "id"),
                        "title": _g(r, "title"),
                        "author": _g(r, "author"),
                        "status": _g(r, "status"),
                        "publishDate": _g(r, "publishDate", _g(r, "publish_date")),
                        "isbn": _g(r, "isbn"),
                        "startDate": _g(r, "startDate", _g(r, "start_date")),
                        "endDate": _g(r, "endDate", _g(r, "end_date")),
                        "completed": _g(r, "completed"),
                        "cover": _g(r, "cover"),
                        "description": _g(r, "description"),
                    })
                return result
            except Exception as e:
                sentry_sdk.capture_exception(e)
                return []

        return await run_in_executor(_fetch)

    async def update_book_in_pocketbase(
        self,
        user_id: str,
        book_query: str,
        update_data: dict,
        cover_file: tuple[str, bytes] | None = None,
    ) -> dict:
        def _update():
            pb = self.get_pb_client()
            pb_user_id = get_discord_user_id(pb, str(user_id))
            if not pb_user_id:
                raise Exception(UNLINKED_ACCOUNT_MESSAGE)

            clean_target = str(book_query).strip()
            if not clean_target:
                raise ValueError("Book query is required.")

            def _g(obj, attr, fallback=""):
                val = getattr(obj, attr, None)
                if val is None and isinstance(obj, dict):
                    val = obj.get(attr)
                if val is None and hasattr(obj, "get"):
                    val = obj.get(attr)
                return val if val is not None else fallback

            # 1. Try exact ID lookup
            matched = None
            try:
                rec = pb.collection("shisho_books").get_one(clean_target)
                rec_owner = _g(rec, "owner")
                if rec_owner == pb_user_id:
                    matched = rec
            except Exception:
                matched = None

            # 2. If not found by ID, search by owner and fuzzy filter
            if not matched:
                clean_isbn = clean_target.replace("-", "").replace(" ", "").strip()
                safe_query = clean_target.replace("'", "\\'")
                filter_str = f"owner = '{pb_user_id}' && (title ~ '{safe_query}' || author ~ '{safe_query}' || isbn ~ '{clean_isbn}')"
                records = pb.collection("shisho_books").get_full_list(query_params={"filter": filter_str})
                if not records:
                    raise ValueError(f"No book found matching '{clean_target}' on your reading list.")

                # Prefer exact title or ISBN match
                for r in records:
                    r_title = _g(r, "title")
                    r_isbn = _g(r, "isbn")
                    if r_title.lower() == clean_target.lower() or (clean_isbn and r_isbn == clean_isbn):
                        matched = r
                        break
                if not matched:
                    matched = records[0]

            # Build fields to update
            payload = {}
            if "title" in update_data and update_data["title"] is not None:
                payload["title"] = str(update_data["title"]).strip()

            if "author" in update_data and update_data["author"] is not None:
                payload["author"] = str(update_data["author"]).strip()

            if "status" in update_data and update_data["status"] is not None:
                status_val = str(update_data["status"]).strip().lower()
                valid_statuses = ["planned", "reading", "read", "dropped"]
                if status_val not in valid_statuses:
                    raise ValueError(f"Invalid status '{status_val}'. Must be one of: {', '.join(valid_statuses)}")
                payload["status"] = status_val

            if "startDate" in update_data and update_data["startDate"] is not None:
                payload["startDate"] = str(update_data["startDate"]).strip()
            elif "start_date" in update_data and update_data["start_date"] is not None:
                payload["startDate"] = str(update_data["start_date"]).strip()

            if "endDate" in update_data and update_data["endDate"] is not None:
                payload["endDate"] = str(update_data["endDate"]).strip()
            elif "end_date" in update_data and update_data["end_date"] is not None:
                payload["endDate"] = str(update_data["end_date"]).strip()

            if "completed" in update_data and update_data["completed"] is not None:
                payload["completed"] = str(update_data["completed"]).strip()

            if "publishDate" in update_data and update_data["publishDate"] is not None:
                payload["publishDate"] = str(update_data["publishDate"]).strip()
            elif "publish_date" in update_data and update_data["publish_date"] is not None:
                payload["publishDate"] = str(update_data["publish_date"]).strip()

            if "description" in update_data and update_data["description"] is not None:
                payload["description"] = str(update_data["description"]).strip()

            if "isbn" in update_data and update_data["isbn"] is not None:
                payload["isbn"] = str(update_data["isbn"]).replace("-", "").replace(" ", "").strip()

            # Handle cover
            files = None
            if cover_file and isinstance(cover_file, tuple) and len(cover_file) == 2:
                cover_filename, cover_data = cover_file
                if cover_filename and cover_data:
                    files = {"cover": (cover_filename, cover_data)}
            elif "cover" in update_data and isinstance(update_data["cover"], tuple):
                files = {"cover": update_data["cover"]}

            if not payload and not files:
                raise ValueError("No fields provided to update.")

            matched_id = getattr(matched, "id", None) or (matched.get("id") if hasattr(matched, "get") else None)

            if files:
                final_entry = prepare_file_upload_payload(payload, files)
                updated_rec = pb.collection("shisho_books").update(matched_id, final_entry)
            else:
                updated_rec = pb.collection("shisho_books").update(matched_id, payload)

            # Return updated dictionary
            return {
                "id": matched_id,
                "title": _g(updated_rec, "title", payload.get("title", _g(matched, "title"))),
                "author": _g(updated_rec, "author", payload.get("author", _g(matched, "author"))),
                "status": _g(updated_rec, "status", payload.get("status", _g(matched, "status"))),
                "publishDate": _g(updated_rec, "publishDate", _g(updated_rec, "publish_date", payload.get("publishDate", _g(matched, "publishDate", _g(matched, "publish_date"))))),
                "isbn": _g(updated_rec, "isbn", payload.get("isbn", _g(matched, "isbn"))),
                "startDate": _g(updated_rec, "startDate", _g(updated_rec, "start_date", payload.get("startDate", _g(matched, "startDate", _g(matched, "start_date"))))),
                "endDate": _g(updated_rec, "endDate", _g(updated_rec, "end_date", payload.get("endDate", _g(matched, "endDate", _g(matched, "end_date"))))),
                "completed": _g(updated_rec, "completed", payload.get("completed", _g(matched, "completed"))),
                "cover": _g(updated_rec, "cover", _g(matched, "cover")),
                "description": _g(updated_rec, "description", payload.get("description", _g(matched, "description"))),
            }

        return await run_in_executor(_update)

    async def delete_book_from_pocketbase(self, discord_id: str, query_or_id: str) -> str:
        def _delete():
            pb = self.get_pb_client()
            pb_user_id = get_discord_user_id(pb, discord_id)
            if not pb_user_id:
                return f"Error: {UNLINKED_ACCOUNT_MESSAGE}"

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

    @app_commands.command(name="editbook", description="Edits an existing book on your reading list.")
    @app_commands.describe(
        book="The book to edit (select from list or type title/ISBN/ID)",
        status="New status of the book",
        start_date="Start reading date (YYYY-MM-DD)",
        end_date="Finished reading date (YYYY-MM-DD)",
        completed="Completion details or notes",
        description="Book synopsis or notes",
        publish_date="Publish date or year",
        isbn="ISBN of the book",
        cover_image="Optional cover image to upload"
    )
    @app_commands.choices(status=[
        app_commands.Choice(name="Planned", value="planned"),
        app_commands.Choice(name="Reading", value="reading"),
        app_commands.Choice(name="Read", value="read"),
        app_commands.Choice(name="Dropped", value="dropped"),
    ])
    async def edit_book(
        self,
        interaction: discord.Interaction,
        book: str,
        status: app_commands.Choice[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        completed: str | None = None,
        description: str | None = None,
        publish_date: str | None = None,
        isbn: str | None = None,
        cover_image: discord.Attachment | None = None,
    ):
        await interaction.response.defer(ephemeral=True)

        update_data = {}
        if status is not None:
            update_data["status"] = status.value
        if start_date is not None:
            update_data["startDate"] = start_date
        if end_date is not None:
            update_data["endDate"] = end_date
        if completed is not None:
            update_data["completed"] = completed
        if description is not None:
            update_data["description"] = description
        if publish_date is not None:
            update_data["publishDate"] = publish_date
        if isbn is not None:
            update_data["isbn"] = isbn

        cover_file = None
        if cover_image:
            cover_filename = cover_image.filename
            cover_data = await cover_image.read()
            cover_file = (cover_filename, cover_data)

        if not update_data and not cover_file:
            await interaction.followup.send("Please specify at least one field to update.")
            return

        try:
            updated_record = await self.update_book_in_pocketbase(
                user_id=str(interaction.user.id),
                book_query=book,
                update_data=update_data,
                cover_file=cover_file,
            )
            title = updated_record.get("title", "book")
            await interaction.followup.send(f"Successfully updated **{title}**!")
        except Exception as e:
            sentry_sdk.capture_exception(e)
            await interaction.followup.send(f"Failed to update book: {e}")

    @edit_book.autocomplete("book")
    async def edit_book_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self.book_autocomplete(interaction, current)

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
