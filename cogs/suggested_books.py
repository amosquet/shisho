import os
import aiohttp
from datetime import datetime

import sentry_sdk
from discord.ext import commands
from pocketbase import PocketBase


class SuggestedBooks(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.pb_url = os.getenv("POCKETBASE_URL")
        self.pb_user = os.getenv("POCKETBASE_USER")
        self.pb_password = os.getenv("POCKETBASE_PASSWORD")
        self.google_books_api_key = os.getenv("GOOGLE_BOOKS_API_KEY")

    @commands.command(name="suggest")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def suggest_book(self, ctx, *, query: str):
        """Suggests a book. Provide "Title" "Author" and/or an ISBN.
        Usage: !suggest "Project Hail Mary" "Andy Weir"
        Usage: !suggest 9780593135211
        """
        title = ""
        author = ""
        isbn = ""

        parts = query.split('"')
        clean_parts = [p.strip() for p in parts if p.strip()]

        if len(clean_parts) == 1:
            if clean_parts[0].isdigit():
                isbn = clean_parts[0]
                display_name = f"ISBN: {isbn}"
            else:
                title = clean_parts[0]
                display_name = f"**{title}**"
        elif len(clean_parts) >= 2:
            title = clean_parts[0]
            author = clean_parts[1]
            display_name = f"**{title}** by {author}"
            if len(clean_parts) >= 3 and clean_parts[2].isdigit():
                isbn = clean_parts[2]
        else:
            await ctx.send("Please provide a title, author, or ISBN.")
            return

        if isbn:
            if not self.google_books_api_key:
                await ctx.send("Error: Google Books API configuration missing. Cannot fetch book details.")
                return
            try:
                url = f"https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn}&key={self.google_books_api_key}"

                async with aiohttp.ClientSession() as session:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if data.get("items"):
                                vol_info = data["items"][0].get("volumeInfo", {})
                                fetched_title = vol_info.get("title")
                                fetched_authors = vol_info.get("authors")
                                if fetched_title:
                                    title = fetched_title
                                if fetched_authors:
                                    author = ", ".join(fetched_authors)
                                
                                display_name = f"**{title}** by {author}" if author else f"**{title}**"
            except Exception as e:
                sentry_sdk.capture_exception(e)
                # Fail gracefully and proceed with whatever we had parsed originally

        if not self.pb_url or not self.pb_user or not self.pb_password:
            await ctx.send("Error: PocketBase configuration missing.")
            return

        try:
            # We run the synchronous pocketbase calls in an executor to avoid blocking the bot's event loop
            def add_to_pocketbase():
                pb = PocketBase(self.pb_url or "")
                pb.collection("users").auth_with_password(self.pb_user or "", self.pb_password or "")

                entry = {
                    "title": title,
                    "author": author,
                    "isbn": isbn,
                    "suggestedBy": str(ctx.author),
                    "suggestedFrom": "Discord",
                    "dateSuggested": datetime.now().strftime("%Y-%m-%d"),
                }

                pb.collection("suggested_books").create(entry)

            await self.bot.loop.run_in_executor(None, add_to_pocketbase)

            await ctx.send(
                f"Thanks {ctx.author.mention}! {display_name} has been added to the suggested books list."
            )

        except Exception as e:
            sentry_sdk.capture_exception(e)
            await ctx.send(f"An error occurred: {e}")

    @commands.command(name="suggestions")
    async def list_suggestions(self, ctx):
        """Lists the latest suggested books."""
        if not self.pb_url or not self.pb_user or not self.pb_password:
            await ctx.send("Error: PocketBase configuration missing.")
            return

        try:
            def get_from_pocketbase():
                pb = PocketBase(self.pb_url or "")
                pb.collection("users").auth_with_password(self.pb_user or "", self.pb_password or "")
                return pb.collection("suggested_books").get_list(1, 10, query_params={"sort": "-dateSuggested"})

            result = await self.bot.loop.run_in_executor(None, get_from_pocketbase)

            if not result.items:
                await ctx.send("No books have been suggested yet!")
                return

            response = "**Latest Suggested Books:**\n"
            for idx, record in enumerate(result.items, 1):
                # PocketBase Record fields are accessible via getattr
                title = getattr(record, "title", "")
                isbn = getattr(record, "isbn", "")
                author = getattr(record, "author", "")
                
                display_title = title if title else (f"ISBN: {isbn}" if isbn else "Unknown Book")
                
                if author:
                    response += f"{idx}. **{display_title}** by {author}\n"
                else:
                    response += f"{idx}. **{display_title}**\n"

            await ctx.send(response)

        except Exception as e:
            sentry_sdk.capture_exception(e)
            await ctx.send(f"An error occurred: {e}")


async def setup(bot):
    await bot.add_cog(SuggestedBooks(bot))
