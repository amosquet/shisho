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
            "start_date": types.Schema(
                type=types.Type.STRING,
                description="Start reading date (YYYY-MM-DD) (optional)",
            ),
            "end_date": types.Schema(
                type=types.Type.STRING,
                description="Finished reading date (YYYY-MM-DD) (optional)",
            ),
            "completed": types.Schema(
                type=types.Type.STRING,
                description="Completion details or notes (optional)",
            ),
            "description": types.Schema(
                type=types.Type.STRING,
                description="Synopsis, summary, or review notes (optional)",
            ),
        },
        required=["title"],
    ),
)

GET_READING_LIST_TOOL = types.FunctionDeclaration(
    name="get_reading_list",
    description="Retrieves the user's reading list from PocketBase, including book titles, authors, statuses, dates, completion info, and descriptions. Call this ONLY when the user explicitly asks about their reading list or asks for book recommendations.",
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

UPDATE_BOOK_TOOL = types.FunctionDeclaration(
    name="update_book",
    description="Updates an existing book on the user's reading list on PocketBase by title, author, ISBN, or ID (e.g. status, reading dates, completion progress, description, ISBN).",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "query": types.Schema(
                type=types.Type.STRING,
                description="The title, author, ISBN, or ID of the book to update",
            ),
            "title": types.Schema(
                type=types.Type.STRING,
                description="New title for the book (optional)",
            ),
            "author": types.Schema(
                type=types.Type.STRING,
                description="New author for the book (optional)",
            ),
            "status": types.Schema(
                type=types.Type.STRING,
                description="New reading status",
                enum=["planned", "reading", "read", "dropped"],
            ),
            "start_date": types.Schema(
                type=types.Type.STRING,
                description="Date started reading (YYYY-MM-DD) (optional)",
            ),
            "end_date": types.Schema(
                type=types.Type.STRING,
                description="Date finished reading (YYYY-MM-DD) (optional)",
            ),
            "completed": types.Schema(
                type=types.Type.STRING,
                description="Completion details or notes (optional)",
            ),
            "publish_date": types.Schema(
                type=types.Type.STRING,
                description="Publication date or year (optional)",
            ),
            "description": types.Schema(
                type=types.Type.STRING,
                description="Synopsis, summary, or review notes (optional)",
            ),
            "isbn": types.Schema(
                type=types.Type.STRING,
                description="ISBN of the book (optional)",
            ),
        },
        required=["query"],
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


async def handle_add_book(bot, args: dict, user_id: str = None, context: dict | None = None, **kwargs) -> str:
    """Handler for the add_book AI tool."""
    if isinstance(args, str) and isinstance(user_id, dict):
        args, user_id = user_id, args
    elif user_id is None and "user_id" in kwargs:
        user_id = kwargs["user_id"]

    reading_list_cog = bot.get_cog("ReadingList")
    if not reading_list_cog:
        return "Error: ReadingList cog is unavailable."

    title = str(args.get("title", "")).strip() if args else ""
    author = str(args.get("author", "")).strip() if args else ""
    status = str(args.get("status", "planned")).strip().lower() if args else "planned"
    publish_date = str(args.get("publish_date", args.get("publishDate", ""))).strip() if args else ""
    isbn = str(args.get("isbn", "")).strip() if args else ""
    start_date = str(args.get("start_date", args.get("startDate", ""))).strip() if args else ""
    end_date = str(args.get("end_date", args.get("endDate", ""))).strip() if args else ""
    completed = str(args.get("completed", "")).strip() if args else ""
    description = str(args.get("description", "")).strip() if args else ""

    if not title and not isbn:
        return "Error: Title or ISBN is required."

    api_key = os.getenv("GOOGLE_BOOKS_API_KEY")
    image_url = ""
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
                if not description and desc and desc != "No description available.":
                    description = desc

                if image_url:
                    cover_filename, cover_data = await google_books.download_image(
                        image_url
                    )

    today = datetime.now().strftime("%Y-%m-%d")
    final_start = start_date if start_date else (today if status in ["read", "reading"] else "")
    final_end = end_date if end_date else (today if status == "read" else "")

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
        completed=completed,
    )
    return f"Successfully added '{title or 'Unknown Title'}' by {author or 'Unknown Author'} (status: {status}) to the reading list."


async def handle_get_reading_list(bot, args: dict, user_id: str = None, context: dict | None = None, **kwargs) -> str:
    """Handler for the get_reading_list AI tool."""
    if isinstance(args, str) and isinstance(user_id, dict):
        args, user_id = user_id, args
    elif user_id is None and "user_id" in kwargs:
        user_id = kwargs["user_id"]

    reading_list_cog = bot.get_cog("ReadingList")
    if not reading_list_cog:
        return "Error: ReadingList cog is unavailable."
    status_filter = str(args.get("status", "")).strip().lower() if args else ""
    books = await reading_list_cog.fetch_reading_list(user_id)
    if not books:
        return "No books found in reading list."
    if status_filter and status_filter != "all":
        filtered_books = [
            b for b in books if b.get("status", "").lower() == status_filter
        ]
        if filtered_books:
            books = filtered_books
        else:
            return f"No books found with status '{status_filter}' in reading list."

    formatted = []
    for b in books:
        title = b.get("title", "Unknown")
        author = b.get("author", "Unknown")
        status = b.get("status", "unknown")
        line = f"- **{title}** by {author} (Status: {status})"
        extras = []
        if b.get("isbn"):
            extras.append(f"ISBN: {b['isbn']}")
        if b.get("publishDate"):
            extras.append(f"Published: {b['publishDate']}")
        if b.get("startDate"):
            extras.append(f"Started: {b['startDate']}")
        if b.get("endDate"):
            extras.append(f"Finished: {b['endDate']}")
        if b.get("completed"):
            extras.append(f"Completed: {b['completed']}")
        if b.get("description"):
            desc = b["description"]
            desc_snippet = desc[:120] + "..." if len(desc) > 120 else desc
            extras.append(f"Description: {desc_snippet}")
        if extras:
            line += f" [{', '.join(extras)}]"
        formatted.append(line)

    return "\n".join(formatted)


async def handle_update_book(bot, args: dict, user_id: str = None, context: dict | None = None, **kwargs) -> str:
    """Handler for the update_book AI tool."""
    if isinstance(args, str) and isinstance(user_id, dict):
        args, user_id = user_id, args
    elif user_id is None and "user_id" in kwargs:
        user_id = kwargs["user_id"]

    reading_list_cog = bot.get_cog("ReadingList")
    if not reading_list_cog:
        return "Error: ReadingList cog is unavailable."

    query = str(args.get("query", args.get("book", args.get("title", "")))).strip() if args else ""
    if not query:
        return "Error: Book query (title, author, ISBN, or ID) is required."

    update_data = {}
    if "title" in args and args["title"] is not None:
        update_data["title"] = args["title"]
    if "author" in args and args["author"] is not None:
        update_data["author"] = args["author"]
    if "status" in args and args["status"] is not None:
        update_data["status"] = args["status"]
    if "start_date" in args and args["start_date"] is not None:
        update_data["startDate"] = args["start_date"]
    elif "startDate" in args and args["startDate"] is not None:
        update_data["startDate"] = args["startDate"]
    if "end_date" in args and args["end_date"] is not None:
        update_data["endDate"] = args["end_date"]
    elif "endDate" in args and args["endDate"] is not None:
        update_data["endDate"] = args["endDate"]
    if "completed" in args and args["completed"] is not None:
        update_data["completed"] = args["completed"]
    if "publish_date" in args and args["publish_date"] is not None:
        update_data["publishDate"] = args["publish_date"]
    elif "publishDate" in args and args["publishDate"] is not None:
        update_data["publishDate"] = args["publishDate"]
    if "description" in args and args["description"] is not None:
        update_data["description"] = args["description"]
    if "isbn" in args and args["isbn"] is not None:
        update_data["isbn"] = args["isbn"]

    if not update_data:
        return "Error: No update parameters provided."

    try:
        updated_rec = await reading_list_cog.update_book_in_pocketbase(
            user_id=user_id,
            book_query=query,
            update_data=update_data,
        )
        title = updated_rec.get("title", query)
        status = updated_rec.get("status", "unknown")
        return f"Successfully updated '{title}' (Status: {status})."
    except Exception as e:
        return f"Error updating book: {e}"


async def handle_delete_book(bot, args: dict, user_id: str = None, context: dict | None = None, **kwargs) -> str:
    """Handler for the delete_book AI tool."""
    if isinstance(args, str) and isinstance(user_id, dict):
        args, user_id = user_id, args
    elif user_id is None and "user_id" in kwargs:
        user_id = kwargs["user_id"]

    reading_list_cog = bot.get_cog("ReadingList")
    if not reading_list_cog:
        return "Error: ReadingList cog is unavailable."
    query = str(args.get("query", args.get("title", ""))).strip() if args else ""
    if not query:
        return "Error: Book title, ISBN, or ID is required."
    res = await reading_list_cog.delete_book_from_pocketbase(user_id, query)
    return res
