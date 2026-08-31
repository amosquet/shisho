import os
import aiohttp
from datetime import datetime

import discord
from discord import app_commands
import sentry_sdk
from discord.ext import commands

from utils import google_books
from utils.db import get_pb_client, run_in_executor


class SuggestedBooks(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.pb_url = os.getenv("POCKETBASE_URL")
        self.pb_user = os.getenv("POCKETBASE_USER")
        self.pb_password = os.getenv("POCKETBASE_PASSWORD")
        self.google_books_api_key = os.getenv("GOOGLE_BOOKS_API_KEY")

    @app_commands.command(name="suggest", description="Suggests a book.")
    @app_commands.describe(
        title="Title of the book",
        author="Author of the book",
        isbn="ISBN of the book (optional, fetches details if provided alone)"
    )
    @app_commands.checks.cooldown(1, 5, key=lambda i: i.user.id)
    async def suggest_book(
        self, 
        interaction: discord.Interaction, 
        title: str | None = None, 
        author: str | None = None, 
        isbn: str | None = None
    ):
        if not title and not isbn:
            await interaction.response.send_message("Please provide a title or an ISBN.", ephemeral=True)
            return
            
        await interaction.response.defer()

        title = title or ""
        author = author or ""
        isbn = isbn or ""

        try:
            display_name = await self.add_suggestion(title, author, isbn, str(interaction.user), "Discord")
            await interaction.followup.send(
                f"Thanks {interaction.user.mention}! {display_name} has been added to the suggested books list."
            )

        except Exception as e:
            sentry_sdk.capture_exception(e)
            await interaction.followup.send(f"An error occurred: {e}")

    async def add_suggestion(self, title: str, author: str, isbn: str, suggested_by: str, suggested_from: str) -> str:
        title = title or ""
        author = author or ""
        isbn = (isbn or "").replace("-", "")

        if isbn and not (title and author):
            try:
                book_data = await google_books.fetch_book_data(isbn, self.google_books_api_key)
                if isinstance(book_data, dict):
                    fetched_title = book_data.get("title")
                    fetched_authors = book_data.get("authors")
                    if fetched_title and fetched_title != "Unknown Title":
                        title = fetched_title
                    if fetched_authors and fetched_authors != ["Unknown Author"]:
                        author = ", ".join(fetched_authors)
            except Exception as e:
                sentry_sdk.capture_exception(e)
                # Fail gracefully and proceed with whatever we had parsed originally

        display_name = ""
        if title and author:
            display_name = f"**{title}** by {author}"
        elif title:
            display_name = f"**{title}**"
        elif isbn:
            display_name = f"ISBN: {isbn}"
        else:
            display_name = "Unknown Book"

        def add_to_pocketbase():
            pb = get_pb_client()

            entry = {
                "title": title,
                "author": author,
                "isbn": isbn,
                "suggestedBy": suggested_by,
                "suggestedFrom": suggested_from,
                "dateSuggested": datetime.now().strftime("%Y-%m-%d"),
            }

            pb.collection("suggested_books").create(entry)

        await run_in_executor(add_to_pocketbase)
        return display_name

    @app_commands.command(name="suggestions", description="Lists the latest suggested books.")
    async def list_suggestions(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        try:
            response = await self.get_suggestions_text()
            await interaction.followup.send(response)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            await interaction.followup.send(f"An error occurred: {e}")

    async def get_suggestions_text(self) -> str:
        def get_from_pocketbase():
            pb = get_pb_client()
            return pb.collection("suggested_books").get_list(1, 10, query_params={"sort": "-dateSuggested"})

        result = await run_in_executor(get_from_pocketbase)

        if not result.items:
            return "No books have been suggested yet!"

        response = "**Latest Suggested Books:**\n"
        for idx, record in enumerate(result.items, 1):
            title = getattr(record, "title", "")
            isbn = getattr(record, "isbn", "")
            author = getattr(record, "author", "")
            
            display_title = title if title else (f"ISBN: {isbn}" if isbn else "Unknown Book")
            
            if author:
                response += f"{idx}. **{display_title}** by {author}\n"
            else:
                response += f"{idx}. **{display_title}**\n"

        return response

    async def delete_suggestion(self, query_or_id: str, user_name: str = None, is_owner: bool = False) -> str:
        try:
            def _delete():
                pb = get_pb_client()
                clean_target = query_or_id.strip()
                if not clean_target:
                    return "Error: Please specify a book title, ISBN, or ID to delete."

                # 1. Try finding by exact ID first
                try:
                    record = pb.collection("suggested_books").get_one(clean_target)
                    s_by = getattr(record, "suggestedBy", "") or (record.get("suggestedBy", "") if hasattr(record, "get") else "")
                    if is_owner or (user_name and s_by == user_name):
                        title = getattr(record, "title", "") or getattr(record, "isbn", "") or "Unknown Book"
                        pb.collection("suggested_books").delete(record.id)
                        return f"Successfully removed **{title}** from the suggested books list."
                    else:
                        return "You can only delete suggestions that you created."
                except Exception:
                    pass

                # 2. Search suggestions
                clean_isbn = clean_target.replace("-", "").replace(" ", "").strip()
                safe_query = clean_target.replace("'", "\\'")
                filter_str = f"title ~ '{safe_query}' || author ~ '{safe_query}' || isbn ~ '{clean_isbn}'"
                records = pb.collection("suggested_books").get_full_list(query_params={"filter": filter_str})
                if not records:
                    return f"No book suggestion found matching '{clean_target}'."

                # If caller is not owner, filter to caller's suggestions
                if not is_owner and user_name:
                    user_records = [r for r in records if (getattr(r, "suggestedBy", "") or (r.get("suggestedBy", "") if hasattr(r, "get") else "")) == user_name]
                    if not user_records:
                        return "You can only delete suggestions that you created."
                    records = user_records

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

                title = getattr(matched, "title", "") or getattr(matched, "isbn", "") or "Unknown Book"
                pb.collection("suggested_books").delete(matched.id)
                return f"Successfully removed **{title}** from the suggested books list."

            return await run_in_executor(_delete)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return f"Failed to delete suggestion: {e}"

    async def suggestion_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        try:
            owner_id_str = os.getenv("OWNER_ID", "0")
            is_owner = str(interaction.user.id) == owner_id_str

            def _fetch():
                pb = get_pb_client()
                return pb.collection("suggested_books").get_full_list(query_params={"sort": "-dateSuggested"})

            records = await run_in_executor(_fetch)
            if not records:
                return []

            user_name = str(interaction.user)
            clean_cur = current.lower().strip()
            choices = []
            for r in records:
                s_by = getattr(r, "suggestedBy", "") or (r.get("suggestedBy", "") if hasattr(r, "get") else "")
                if not is_owner and s_by != user_name:
                    continue
                title = getattr(r, "title", "") or (r.get("title", "") if hasattr(r, "get") else "")
                author = getattr(r, "author", "") or (r.get("author", "") if hasattr(r, "get") else "")
                isbn = getattr(r, "isbn", "") or (r.get("isbn", "") if hasattr(r, "get") else "")
                display = f"{title} by {author}" if (title and author) else (title or f"ISBN: {isbn}")
                if clean_cur and clean_cur not in display.lower() and clean_cur not in isbn.lower():
                    continue
                name_preview = display[:100]
                choices.append(app_commands.Choice(name=name_preview, value=r.id))
            return choices[:25]
        except Exception:
            return []

    @app_commands.command(name="deletesuggestion", description="Removes a book from the suggested books list.")
    @app_commands.describe(suggestion="The suggestion to delete (select from list or type title/ISBN/ID)")
    async def slash_delete_suggestion(self, interaction: discord.Interaction, suggestion: str):
        await interaction.response.defer()
        owner_id_str = os.getenv("OWNER_ID", "0")
        is_owner = str(interaction.user.id) == owner_id_str
        response = await self.delete_suggestion(suggestion, user_name=str(interaction.user), is_owner=is_owner)
        await interaction.followup.send(response)

    @slash_delete_suggestion.autocomplete("suggestion")
    async def slash_delete_suggestion_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self.suggestion_autocomplete(interaction, current)


async def setup(bot):
    await bot.add_cog(SuggestedBooks(bot))
