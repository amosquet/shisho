"""
tools/recommendations.py - AI Tool definitions and handlers for Friend & Public Book Recommendations.
"""

import os
from google.genai import types


GET_RECOMMENDATIONS_TOOL = types.FunctionDeclaration(
    name="get_recommendations",
    description="Retrieves books recommended to or by the user, or public recommendations from friends, from the recommendations database (shisho_books_recommendations). Use this whenever the user asks what books are on their recommended list, what friends have suggested to them, or wants to view book recommendations.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "filter": types.Schema(
                type=types.Type.STRING,
                description="Filter scope: 'for_me' (books recommended to the user), 'from_me' (books the user recommended), 'public' (public recommendations), or 'all' (all relevant recommendations). Defaults to 'all'.",
                enum=["for_me", "from_me", "public", "all"],
            ),
        },
    ),
)

ADD_RECOMMENDATION_TOOL = types.FunctionDeclaration(
    name="add_recommendation",
    description="Recommends a book to another user or adds a public suggestion to the recommendations list.",
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
            "isbn": types.Schema(
                type=types.Type.STRING,
                description="ISBN of the book (optional)",
            ),
            "recipient_discord_id": types.Schema(
                type=types.Type.STRING,
                description="Discord User ID (numeric ID snowflake like '634903926495510569') or username/mention of the recipient",
            ),
            "message": types.Schema(
                type=types.Type.STRING,
                description="A personal note or reason for recommending this book (optional)",
            ),
            "is_public": types.Schema(
                type=types.Type.BOOLEAN,
                description="Whether this recommendation is public for everyone (optional)",
            ),
            "publish_date": types.Schema(
                type=types.Type.STRING,
                description="The publication date or year of the book (optional)",
            ),
            "description": types.Schema(
                type=types.Type.STRING,
                description="Synopsis, summary, or detailed description of the book (optional)",
            ),
        },
        required=["title"],
    ),
)

DELETE_RECOMMENDATION_TOOL = types.FunctionDeclaration(
    name="delete_recommendation",
    description="Deletes a book recommendation by title, ISBN, or record ID.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "query": types.Schema(
                type=types.Type.STRING,
                description="The title, ISBN, or ID of the recommendation to remove",
            ),
        },
        required=["query"],
    ),
)


async def handle_get_recommendations(
    bot=None,
    args: dict | None = None,
    user_id: str = "",
    context: dict | None = None,
    **kwargs,
) -> str:
    """Handler for the get_recommendations AI tool."""
    if bot is None:
        return "Error: Bot instance is required."
    suggested_cog = bot.get_cog("SuggestedBooks")
    if not suggested_cog:
        return "Error: SuggestedBooks cog is unavailable."

    if args is None:
        args = {}
    combined_args = {**args, **kwargs}
    uid = user_id or str(combined_args.get("user_id") or "").strip()
    raw_filter = combined_args.get("filter")
    filter_type = str(raw_filter).strip().lower() if raw_filter is not None else "all"
    if filter_type not in ("for_me", "from_me", "public", "all"):
        filter_type = "all"

    res = await suggested_cog.get_suggestions_text(
        user_discord_id=uid, filter_type=filter_type
    )
    return res or "No recommendations found."


async def handle_add_recommendation(
    bot=None,
    args: dict | None = None,
    user_id: str = "",
    context: dict | None = None,
    **kwargs,
) -> str:
    """Handler for the add_recommendation AI tool."""
    if bot is None:
        return "Error: Bot instance is required."
    suggested_cog = bot.get_cog("SuggestedBooks")
    if not suggested_cog:
        return "Error: SuggestedBooks cog is unavailable."

    if args is None:
        args = {}
    combined_args = {**args, **kwargs}
    uid = user_id or str(combined_args.get("user_id") or "").strip()

    title = str(combined_args.get("title") or "").strip()
    author = str(combined_args.get("author") or "").strip()
    isbn = str(combined_args.get("isbn") or "").strip()
    publish_date = str(
        combined_args.get("publish_date") or combined_args.get("publishDate") or ""
    ).strip()
    description = str(combined_args.get("description") or "").strip()
    rec_raw = str(
        combined_args.get("recipient_discord_id") or combined_args.get("recipient") or ""
    ).strip()
    msg = str(combined_args.get("message") or "").strip()
    is_pub = combined_args.get("is_public")
    if isinstance(is_pub, str):
        is_pub = is_pub.strip().lower() in ("true", "1", "yes")

    if not title and not isbn:
        return "Error: Book title or ISBN is required."

    rec_did = ""
    rec_digits = "".join(c for c in rec_raw if c.isdigit())
    if len(rec_digits) >= 15:
        rec_did = rec_digits
    elif rec_raw:
        # Look up user by name or username in bot cache
        clean_name = rec_raw.lstrip("@").lower().strip()
        if hasattr(bot, "users"):
            for u in bot.users:
                u_name = getattr(u, "name", "").lower()
                u_display = getattr(u, "display_name", "").lower()
                if (
                    u_name == clean_name
                    or u_display == clean_name
                    or clean_name in u_name
                    or str(u.id) == clean_name
                ):
                    rec_did = str(u.id)
                    break
        if not rec_did and rec_digits:
            rec_did = rec_digits

    if is_pub is None:
        is_pub = not bool(rec_did)

    res = await suggested_cog.add_suggestion(
        title=title,
        author=author,
        isbn=isbn,
        publish_date=publish_date,
        description=description,
        sender_discord_id=uid,
        recipient_discord_id=rec_did,
        message=msg,
        is_public=is_pub,
        suggested_from="Discord AI",
    )
    disp = res.get("display_name", title or "Book")
    return f"Successfully added recommendation for {disp}."


async def handle_delete_recommendation(
    bot=None,
    args: dict | None = None,
    user_id: str = "",
    context: dict | None = None,
    **kwargs,
) -> str:
    """Handler for the delete_recommendation AI tool."""
    if bot is None:
        return "Error: Bot instance is required."
    suggested_cog = bot.get_cog("SuggestedBooks")
    if not suggested_cog:
        return "Error: SuggestedBooks cog is unavailable."

    if args is None:
        args = {}
    combined_args = {**args, **kwargs}
    uid = user_id or str(combined_args.get("user_id") or "").strip()
    query = str(
        combined_args.get("query")
        or combined_args.get("title")
        or combined_args.get("isbn")
        or ""
    ).strip()

    if not query:
        return "Error: Query is required."
    owner_id_str = os.getenv("OWNER_ID", "0")
    is_owner = str(uid) == owner_id_str
    res = await suggested_cog.delete_suggestion(
        query, user_discord_id=uid, is_owner=is_owner
    )
    return res
