"""
tools/recommendations.py - AI Tool definitions and handlers for Friend & Public Book Recommendations.
"""

import os
from google.genai import types


GET_RECOMMENDATIONS_TOOL = types.FunctionDeclaration(
    name="get_recommendations",
    description="Retrieves books recommended to or by the user, or public recommendations from friends, from the recommendations database (shisho_books_recommendations). Use this whenever the user asks what books are on their recommended list or what friends have suggested to them.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "filter": types.Schema(
                type=types.Type.STRING,
                description="Filter scope: 'for_me' (books recommended to the user), 'from_me' (books the user recommended), 'public' (public recommendations), or 'all' (all relevant recommendations)",
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
        },
        required=["title"],
    ),
)

DELETE_RECOMMENDATION_TOOL = types.FunctionDeclaration(
    name="delete_recommendation",
    description="Deletes a book recommendation.",
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


async def handle_get_recommendations(bot, args: dict, user_id: str) -> str:
    """Handler for the get_recommendations AI tool."""
    suggested_cog = bot.get_cog("SuggestedBooks")
    if not suggested_cog:
        return "Error: SuggestedBooks cog is unavailable."
    filter_type = str(args.get("filter", "all")).strip().lower()
    res = await suggested_cog.get_suggestions_text(
        user_discord_id=user_id, filter_type=filter_type
    )
    return res or "No recommendations found."


async def handle_add_recommendation(bot, args: dict, user_id: str) -> str:
    """Handler for the add_recommendation AI tool."""
    suggested_cog = bot.get_cog("SuggestedBooks")
    if not suggested_cog:
        return "Error: SuggestedBooks cog is unavailable."
    title = str(args.get("title", "")).strip()
    author = str(args.get("author", "")).strip()
    isbn = str(args.get("isbn", "")).strip()
    rec_raw = str(
        args.get("recipient_discord_id", args.get("recipient", ""))
    ).strip()
    msg = str(args.get("message", "")).strip()
    is_pub = args.get("is_public")

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
        sender_discord_id=user_id,
        recipient_discord_id=rec_did,
        message=msg,
        is_public=is_pub,
        suggested_from="Discord AI",
    )
    disp = res.get("display_name", title or "Book")
    return f"Successfully added recommendation for {disp}."


async def handle_delete_recommendation(bot, args: dict, user_id: str) -> str:
    """Handler for the delete_recommendation AI tool."""
    suggested_cog = bot.get_cog("SuggestedBooks")
    if not suggested_cog:
        return "Error: SuggestedBooks cog is unavailable."
    query = str(args.get("query", args.get("title", ""))).strip()
    if not query:
        return "Error: Query is required."
    owner_id_str = os.getenv("OWNER_ID", "0")
    is_owner = str(user_id) == owner_id_str
    res = await suggested_cog.delete_suggestion(
        query, user_discord_id=user_id, is_owner=is_owner
    )
    return res
