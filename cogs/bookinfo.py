import os
import json
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
import sentry_sdk

CACHE_FILE = "book_cache.json"

class BookInfo(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.google_books_api_key = os.getenv("GOOGLE_BOOKS_API_KEY")
        self.cache = self.load_cache()

    def load_cache(self):
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"Failed to load book cache: {e}")
                return {}
        return {}

    def save_cache(self):
        try:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, indent=4)
        except Exception as e:
            print(f"Failed to save book cache: {e}")

    async def fetch_book_data(self, query: str):
        query_key = query.lower().strip()

        # 1. Check local JSON cache first
        if query_key in self.cache:
            return self.cache[query_key]

        # 2. If not in cache, fetch from Google Books API
        if not self.google_books_api_key:
            return "Google Books API key is not configured."

        url = f"https://www.googleapis.com/books/v1/volumes?q={query}&key={self.google_books_api_key}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("items"):
                            vol_info = data["items"][0].get("volumeInfo", {})
                            
                            book_data = {
                                "title": vol_info.get("title", "Unknown Title"),
                                "authors": vol_info.get("authors", ["Unknown Author"]),
                                "description": vol_info.get("description", "No description available."),
                                "pageCount": vol_info.get("pageCount", 0),
                                "averageRating": vol_info.get("averageRating", "N/A"),
                                "thumbnail": vol_info.get("imageLinks", {}).get("thumbnail", ""),
                                "publishedDate": vol_info.get("publishedDate", "Unknown")
                            }

                            # 3. Store inside local JSON cache
                            self.cache[query_key] = book_data
                            self.save_cache()

                            return book_data
                        else:
                            return f"No books found for query: `{query}`."
                    else:
                        return "Failed to fetch data from Google Books API."
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return f"An error occurred: {e}"

    @app_commands.command(name="bookinfo", description="Look up a book's details by title or ISBN.")
    @app_commands.describe(query="The title or ISBN of the book")
    @app_commands.checks.cooldown(1, 5, key=lambda i: i.user.id)
    async def bookinfo(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer()
        
        result = await self.fetch_book_data(query)
        if isinstance(result, str):
            await interaction.followup.send(result, ephemeral=True)
            return

        await self.send_book_embed(interaction, result)

    async def get_book_info_text(self, query: str) -> str:
        result = await self.fetch_book_data(query)
        if isinstance(result, str):
            return result
            
        title = result.get("title")
        authors = ", ".join(result.get("authors", []))
        description = result.get("description", "")
        if len(description) > 300:
            description = description[:300] + "..."
            
        return (
            f"Title: {title}\n"
            f"Author(s): {authors}\n"
            f"Published: {result.get('publishedDate')}\n"
            f"Pages: {result.get('pageCount')}\n"
            f"Rating: {result.get('averageRating')}\n\n"
            f"Synopsis: {description}"
        )

    async def send_book_embed(self, interaction: discord.Interaction, book_data: dict):
        title = book_data.get("title")
        authors = ", ".join(book_data.get("authors", []))
        description = book_data.get("description", "")
        
        # Truncate description so it doesn't blow up the embed
        if len(description) > 1000:
            description = description[:1000] + "..."

        embed = discord.Embed(title=title, description=description, color=discord.Color.blue())
        embed.add_field(name="Author(s)", value=authors, inline=True)
        embed.add_field(name="Published", value=book_data.get("publishedDate"), inline=True)
        embed.add_field(name="Pages", value=str(book_data.get("pageCount")), inline=True)
        embed.add_field(name="Rating", value=str(book_data.get("averageRating")), inline=True)

        thumbnail = book_data.get("thumbnail")
        if thumbnail:
            thumbnail = thumbnail.replace("http://", "https://")
            embed.set_thumbnail(url=thumbnail)

        await interaction.followup.send(embed=embed)


async def setup(bot):
    await bot.add_cog(BookInfo(bot))
