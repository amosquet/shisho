"""
tools/reading_list.py - AI Tool definitions and handlers for the Reading List domain.
"""

from datetime import datetime
import os
from google.genai import types


ADD_BOOK_TOOL = types.FunctionDeclaration(
    name="add_book",
    description="Adds a book to the user's reading list on PocketBase.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "title": types.Schema(
                type=types.Type.STRING,
                description="The title of the book",
            ),
            "author": types.Schema(
                type=types.Type.STRING,
                description="The author of the book (optional)",
            ),
            "status": types.Schema(
                type=types.Type.STRING,
                description="Reading status of the book",
                enum=["planned", "reading", "read", "dropped"],
            ),
            "publish_date": types.Schema(
                type=types.Type.STRING,
                description="The publication date or year (optional)",
            ),
            "isbn": types.Schema(
                type=types.Type.STRING,
                description="ISBN of the book (optional)",
            ),
        },
        required=["title"],
    ),
)

GET_READING_LIST_TOOL = types.FunctionDeclaration(
    name="get_reading_list",
    description="Retrieves the user's reading list from PocketBase, including book titles, authors, and statuses (planned, reading, read, dropped). Call this ONLY when the user explicitly asks about their reading list or asks for book recommendations.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "status": types.Schema(
                type=types.Type.STRING,
                description="Optional filter by status: 'planned', 'reading', 'read', 'dropped', or 'all'",
            ),
        },
    ),
)

DELETE_BOOK_TOOL = types.FunctionDeclaration(
    name="delete_book",
    description="Removes a book from the user's reading list on PocketBase by title, author, ISBN, or ID.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "query": types.Schema(
                type=types.Type.STRING,
                description="The title, author, ISBN, or ID of the book to remove",
            ),
        },
        required=["query"],
    ),
)


async def handle_add_book(bot, args: dict, user_id: str) -> str:
    """Handler for the add_book AI tool."""
    reading_list_cog = bot.get_cog("ReadingList")
    if not reading_list_cog:
        return "Error: ReadingList cog is unavailable."

    title = str(args.get("title", "")).strip()
    author = str(args.get("author", "")).strip()
    status = str(args.get("status", "planned")).strip().lower()
    publish_date = str(args.get("publish_date", "")).strip()
    isbn = str(args.get("isbn", "")).strip()

    if not title and not isbn:
        return "Error: Title or ISBN is required."

    api_key = os.getenv("GOOGLE_BOOKS_API_KEY")
    image_url = ""
    description = ""
    cover_filename = None
    cover_data = None

    # Automatically fetch book data from Google Books API to populate missing details and cover
    if api_key:
        from utils import google_books

        search_query = (
            isbn
            if isbn
            else (
                f"{title} {author}".strip()
                if (title and author)
                else (title or "")
            )
        )
        if search_query:
            book_data = await google_books.fetch_book_data(search_query, api_key)
            if isinstance(book_data, dict):
                title = title or book_data.get("title", "")
                authors = book_data.get("authors", [])
                if not author and authors and authors != ["Unknown Author"]:
                    author = ", ".join(authors)
                if (
                    not publish_date
                    and book_data.get("publishedDate")
                    and book_data.get("publishedDate") != "Unknown"
                ):
                    publish_date = book_data.get("publishedDate")
                if not isbn and book_data.get("isbn"):
                    isbn = book_data.get("isbn")
                image_url = book_data.get("thumbnail", "")
                desc = book_data.get("description", "")
                if desc and desc != "No description available.":
                    description = desc

                if image_url:
                    cover_filename, cover_data = await google_books.download_image(
                        image_url
                    )

    today = datetime.now().strftime("%Y-%m-%d")
    final_start = today if status in ["read", "reading"] else ""
    final_end = today if status == "read" else ""

    await reading_list_cog.add_book_to_pocketbase(
        discord_id=user_id,
        title=title or "Unknown Title",
        author=author or "Unknown Author",
        status_val=(
            status
            if status in ["planned", "reading", "read", "dropped"]
            else "planned"
        ),
        publish_date=publish_date if publish_date != "Unknown" else "",
        isbn=isbn,
        final_start_date=final_start,
        final_end_date=final_end,
        image_url=image_url,
        description=description,
        cover_filename=cover_filename,
        cover_data=cover_data,
    )
    return f"Successfully added '{title or 'Unknown Title'}' by {author or 'Unknown Author'} (status: {status}) to the reading list."


async def handle_get_reading_list(bot, args: dict, user_id: str) -> str:
    """Handler for the get_reading_list AI tool."""
    reading_list_cog = bot.get_cog("ReadingList")
    if not reading_list_cog:
        return "Error: ReadingList cog is unavailable."
    status_filter = str(args.get("status", "")).strip().lower()
    books = await reading_list_cog.fetch_reading_list(user_id)
    if not books:
        return "No books found in reading list."
    if status_filter and status_filter != "all":
        filtered_books = [
            b for b in books if b.get("status", "").lower() == status_filter
        ]
        if filtered_books:
            books = filtered_books
    formatted = [
        f"- {b.get('title', 'Unknown')} by {b.get('author', 'Unknown')} (Status: {b.get('status', 'unknown')})"
        for b in books
    ]
    return "\n".join(formatted)


async def handle_delete_book(bot, args: dict, user_id: str) -> str:
    """Handler for the delete_book AI tool."""
    reading_list_cog = bot.get_cog("ReadingList")
    if not reading_list_cog:
        return "Error: ReadingList cog is unavailable."
    query = str(args.get("query", args.get("title", ""))).strip()
    if not query:
        return "Error: Book title, ISBN, or ID is required."
    res = await reading_list_cog.delete_book_from_pocketbase(user_id, query)
    return res
