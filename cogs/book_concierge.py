import os
import re
import json
import discord
from discord.ext import commands
from discord import app_commands
from google import genai
from google.genai import errors, types
from datetime import datetime
import sentry_sdk

CONCIERGE_PROMPT = """You are an expert Book Concierge. You provide highly specific and thoughtful book recommendations based on the user's queries.
You can answer questions about books, summarize plots, and suggest reading orders.

CRITICAL INSTRUCTION:
If your response includes book recommendations or mentions specific books that the user might want to read, you MUST append a JSON block at the very end of your response containing a list of those book titles.

The JSON block must be formatted EXACTLY like this:
```json
[
  "Book Title 1",
  "Book Title 2"
]
```
Ensure it is a valid JSON array of strings. Do not include authors or any other information in this JSON array, just the plain titles.
If you are NOT recommending any books (e.g., just answering a general question), do NOT include the JSON block.
"""

class BookSelect(discord.ui.Select):
    def __init__(self, book_titles: list[str]):
        # Options must be limited to 25 items and labels max 100 chars
        options = []
        for title in book_titles[:25]:
            label = title[:100]
            options.append(discord.SelectOption(label=label, description="Add to Reading List"))
            
        super().__init__(placeholder="Select a book to instantly add it to your Planned list...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        selected_title = self.values[0]
        
        # Get necessary cogs
        bot = interaction.client
        bookinfo_cog = bot.get_cog("BookInfo")
        readinglist_cog = bot.get_cog("ReadingList")
        
        if not bookinfo_cog or not readinglist_cog:
            await interaction.followup.send("Required systems (BookInfo or ReadingList) are currently offline.", ephemeral=True)
            return

        await interaction.followup.send(f"Looking up details for **{selected_title}**...", ephemeral=True)
        
        try:
            # Fetch book details
            book_data = await bookinfo_cog.fetch_book_data(selected_title)
            
            if isinstance(book_data, str):
                await interaction.followup.send(f"Could not find details for **{selected_title}**: {book_data}", ephemeral=True)
                return
                
            # Prepare data for reading list
            title = book_data.get("title", selected_title)
            author = ", ".join(book_data.get("authors", ["Unknown Author"]))
            publish_date = book_data.get("publishedDate", "Unknown")
            isbn = "0000000000" # Placeholder if not found
            # Attempt to extract a valid ISBN if possible, but fetch_book_data doesn't return it currently.
            # That's fine, reading_list.py accepts any string for ISBN.
            
            status_val = "planned"
            today = datetime.now().strftime("%Y-%m-%d")
            
            # Add to reading list
            await readinglist_cog.add_book_to_github(
                title=title,
                author=author,
                status_val=status_val,
                publish_date=publish_date,
                isbn=isbn,
                final_start_date="",
                final_end_date=""
            )
            
            await interaction.followup.send(f"✅ Successfully added **{title}** by {author} to your Planned reading list!", ephemeral=True)
            
        except Exception as e:
            sentry_sdk.capture_exception(e)
            await interaction.followup.send(f"❌ An error occurred while adding the book: {e}", ephemeral=True)


class ConciergeView(discord.ui.View):
    def __init__(self, book_titles: list[str]):
        super().__init__(timeout=3600) # 1 hour timeout
        self.add_item(BookSelect(book_titles))


class BookConcierge(commands.Cog):
    """AI Book Concierge for recommendations."""

    def __init__(self, bot):
        self.bot = bot
        self.api_key = os.getenv("GEMINI_API_KEY")
        if self.api_key:
            self.client = genai.Client(api_key=self.api_key)
        else:
            self.client = None

    def extract_book_titles(self, text: str) -> tuple[str, list[str]]:
        """Extracts the JSON block of book titles from the response if present."""
        titles = []
        clean_text = text
        
        # Look for the last JSON code block
        match = re.search(r'```json\s*(\[.*?\])\s*```\s*$', text, re.DOTALL | re.IGNORECASE)
        if match:
            json_str = match.group(1)
            try:
                parsed = json.loads(json_str)
                if isinstance(parsed, list):
                    titles = [str(item) for item in parsed if isinstance(item, str)]
                # Remove the JSON block from the text shown to the user
                clean_text = text[:match.start()].strip()
            except json.JSONDecodeError:
                pass
                
        return clean_text, titles

    async def process_query(self, query: str) -> tuple[str, list[str]]:
        if not self.client:
            return "Gemini API key is not configured. Please set GEMINI_API_KEY in the environment.", []

        actual_query = query.strip() if query else ""
        if not actual_query:
            reading_list_cog = self.bot.get_cog("ReadingList")
            if reading_list_cog:
                books = await reading_list_cog.fetch_reading_list()
                past_books = [f"- {b['title']} by {b['author']} (Status: {b['status']})" for b in books if b.get('status') in ['read', 'reading']]
                if past_books:
                    books_str = "\n".join(past_books[:30]) # limit to 30 recent books
                    actual_query = f"I am looking for book recommendations tailored to my tastes. Here are some books I have read or am currently reading:\n{books_str}\n\nPlease recommend some new books for me that I might like based on this list!"
                else:
                    actual_query = "Please recommend some good books for me!"
            else:
                actual_query = "Please recommend some good books for me!"

        try:
            config = types.GenerateContentConfig(
                system_instruction=CONCIERGE_PROMPT,
                tools=[{"google_search": {}}]
            )
            
            response = await self.client.aio.models.generate_content(
                model='gemini-2.5-flash',
                contents=actual_query,
                config=config
            )
            
            text = response.text
            if not text:
                return "Received empty response from the Concierge.", []
                
            clean_text, titles = self.extract_book_titles(text)
            return clean_text, titles
            
        except errors.APIError as e:
            return f"API Error: {str(e)}", []
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return f"An unexpected error occurred: {str(e)}", []

    @commands.command(name="recommend", help="Ask the AI Book Concierge for book recommendations.")
    async def recommend_prefix(self, ctx, *, query: str = ""):
        async with ctx.typing():
            text, titles = await self.process_query(query)
            
            view = ConciergeView(titles) if titles else None
            
            chunks = [text[i:i+1990] for i in range(0, len(text), 1990)]
            if not chunks:
                return
                
            for i, chunk in enumerate(chunks):
                if i == len(chunks) - 1 and view:
                    await ctx.send(chunk, view=view)
                else:
                    await ctx.send(chunk)

    @app_commands.command(name="recommend", description="Ask the AI Book Concierge for recommendations")
    @app_commands.describe(query="Your query (leave blank for personalized recommendations)")
    async def recommend_slash(self, interaction: discord.Interaction, query: str | None = None):
        await self._handle_slash_command(interaction, query)
        
    async def _handle_slash_command(self, interaction: discord.Interaction, query: str | None):
        if not interaction.response.is_done():
            await interaction.response.defer()
            
        actual_query = query or ""
        text, titles = await self.process_query(actual_query)
        view = ConciergeView(titles) if titles else None
        
        chunks = [text[i:i+1990] for i in range(0, len(text), 1990)]
        if not chunks:
            await interaction.followup.send("No response generated.")
            return
            
        for i, chunk in enumerate(chunks):
            if i == len(chunks) - 1 and view:
                await interaction.followup.send(chunk, view=view)
            else:
                await interaction.followup.send(chunk)


async def setup(bot):
    await bot.add_cog(BookConcierge(bot))
