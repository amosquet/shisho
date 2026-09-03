"""
tools/anki.py - AI Tool definitions and handlers for Anki flashcards (.apkg) generation and Obsidian vault integration.
"""

import io
import json
import os
import re
from typing import Any, Dict, List, Optional
import discord
from google.genai import types

from utils.db import run_in_executor
from utils import anki as anki_utils
from utils import obsidian as vault_utils


async def _is_bot_owner(bot, user_id: str) -> bool:
    """Checks if the user ID belongs to the bot owner."""
    if not user_id:
        return False

    owner_id_env = os.getenv("OWNER_ID")
    if owner_id_env and str(user_id).strip() == owner_id_env.strip():
        return True

    try:
        uid_int = int(user_id)
        if hasattr(bot, "is_owner"):
            obj = discord.Object(id=uid_int)
            if await bot.is_owner(obj):
                return True
        if getattr(bot, "owner_id", None) == uid_int:
            return True
        if getattr(bot, "owner_ids", None) and uid_int in bot.owner_ids:
            return True
    except Exception:
        pass

    return False


# =========================================================================
# Tool Function Declarations
# =========================================================================

CREATE_ANKI_DECK_TOOL = types.FunctionDeclaration(
    name="create_anki_deck",
    description="Creates a downloadable Anki flashcard deck (.apkg file) and optional Obsidian flashcard note from a list of flashcards. Ideal for generating flashcards from uploaded PDFs, documents, Obsidian vault notes, lecture notes, or study topics.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "deck_name": types.Schema(
                type=types.Type.STRING,
                description="The name or title of the Anki deck (e.g. 'Biology 101 - Cell Division', 'French Irregular Verbs', 'Machine Learning Essentials').",
            ),
            "cards": types.Schema(
                type=types.Type.ARRAY,
                description="List of flashcard items to include in the deck.",
                items=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "front": types.Schema(
                            type=types.Type.STRING,
                            description="Front prompt, question, term, or cloze deletion sentence (e.g. 'What is the powerhouse of the cell?' or 'The {{c1::mitochondria}} generates cellular ATP.').",
                        ),
                        "back": types.Schema(
                            type=types.Type.STRING,
                            description="Back answer, definition, or explanation (e.g. 'Mitochondria'). Optional if using cloze deletion on the front.",
                        ),
                        "card_type": types.Schema(
                            type=types.Type.STRING,
                            description="Type of flashcard: 'basic' (Front -> Back), 'reversed' (Front <-> Back bidirectional), or 'cloze' (Fill-in-the-blank with {{c1::answer}}). Defaults to 'basic'.",
                        ),
                        "tags": types.Schema(
                            type=types.Type.ARRAY,
                            items=types.Schema(type=types.Type.STRING),
                            description="Optional list of tags for categorization in Anki (e.g. ['biology', 'cells']).",
                        ),
                        "extra": types.Schema(
                            type=types.Type.STRING,
                            description="Optional additional notes, mnemonics, explanation, or source reference displayed below the answer.",
                        ),
                    },
                    required=["front"],
                ),
            ),
            "save_to_vault": types.Schema(
                type=types.Type.BOOLEAN,
                description="Whether to also save a markdown version of these flashcards into the user's Obsidian vault (defaults to false). Only enabled for the bot owner.",
            ),
            "vault_path": types.Schema(
                type=types.Type.STRING,
                description="Optional relative path in the Obsidian vault to save the markdown flashcards (e.g. 'Flashcards/Biology.md'). Defaults to 'Flashcards/{deck_name}.md'.",
            ),
            "description": types.Schema(
                type=types.Type.STRING,
                description="Optional short summary or description of the deck.",
            ),
        },
        required=["deck_name", "cards"],
    ),
)

VAULT_EXPORT_ANKI_DECK_TOOL = types.FunctionDeclaration(
    name="vault_export_anki_deck",
    description="Extracts flashcards from an existing markdown note in the Obsidian vault and generates a downloadable Anki .apkg deck file.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "path": types.Schema(
                type=types.Type.STRING,
                description="The relative path to the markdown note in the Obsidian vault (e.g. 'Flashcards/French.md' or 'Projects/MachineLearning.md').",
            ),
            "deck_name": types.Schema(
                type=types.Type.STRING,
                description="Optional custom name for the Anki deck. Defaults to the note title.",
            ),
        },
        required=["path"],
    ),
)


# =========================================================================
# Tool Handlers
# =========================================================================

async def handle_create_anki_deck(
    bot: Any, args: dict, user_id: str, context: dict | None = None
) -> str:
    """
    Handler for create_anki_deck AI tool.
    Generates .apkg bytes, optionally writes to Obsidian vault, and registers output file in context.
    """
    deck_name = str(args.get("deck_name") or "Shisho Flashcards").strip()
    raw_cards = args.get("cards")
    save_to_vault = bool(args.get("save_to_vault", False))
    vault_path = str(args.get("vault_path") or "").strip()
    description = str(args.get("description") or "").strip()

    if isinstance(raw_cards, str):
        try:
            raw_cards = json.loads(raw_cards)
        except Exception:
            raw_cards = []

    if not isinstance(raw_cards, list) or not raw_cards:
        return "Error: 'cards' must be a non-empty list of flashcard objects."

    # Parse and validate cards
    parsed_cards: List[Dict[str, Any]] = []
    for c in raw_cards:
        if not isinstance(c, dict):
            continue
        front = str(c.get("front") or c.get("question") or c.get("text") or "").strip()
        back = str(c.get("back") or c.get("answer") or "").strip()
        card_type = str(c.get("card_type") or "basic").strip().lower()
        extra = str(c.get("extra") or "").strip()
        tags = c.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]

        if not front:
            continue

        parsed_cards.append({
            "front": front,
            "back": back,
            "card_type": card_type,
            "tags": tags,
            "extra": extra,
        })

    if not parsed_cards:
        return "Error: No valid flashcards provided. Each card requires at least a 'front' field."

    # 1. Generate the .apkg binary package
    try:
        apkg_bytes = await run_in_executor(
            anki_utils.create_anki_deck_package,
            deck_name=deck_name,
            cards=parsed_cards,
            description=description,
        )
    except Exception as e:
        return f"Error creating Anki package: {str(e)}"

    # 2. Register output file in execution context for Discord attachment
    safe_filename = re.sub(r'[\\/*?:"<>| ]', "_", deck_name).strip("_") or "flashcards"
    apkg_filename = f"{safe_filename}.apkg"

    if context is not None and isinstance(context, dict):
        if "out_files" not in context:
            context["out_files"] = []
        context["out_files"].append({
            "filename": apkg_filename,
            "bytes": apkg_bytes,
            "content_type": "application/octet-stream",
        })

    # 3. Handle saving to Obsidian Vault if requested
    vault_status_msg = ""
    if save_to_vault:
        is_owner = await _is_bot_owner(bot, user_id)
        if is_owner:
            try:
                target_vault_path = vault_path or f"Flashcards/{safe_filename}.md"
                md_content = anki_utils.format_cards_for_obsidian(
                    deck_name=deck_name,
                    cards=parsed_cards,
                    source=description or "Generated via Shisho",
                )
                vault_res = await run_in_executor(
                    vault_utils.write_note,
                    rel_path=target_vault_path,
                    content=md_content,
                    overwrite=True,
                )
                vault_status_msg = f"\n- Also saved markdown cards to Obsidian vault: `{vault_res['path']}`"
            except Exception as ve:
                vault_status_msg = f"\n- (Warning: Could not save to Obsidian vault: {str(ve)})"
        else:
            vault_status_msg = "\n- (Note: Obsidian vault saving was skipped because it is restricted to the bot owner.)"

    # 4. Format preview summary
    preview_lines = [
        f"Successfully created Anki deck **{deck_name}** with **{len(parsed_cards)} flashcard(s)**!",
        f"- Attached download package: `{apkg_filename}` ({len(apkg_bytes):,} bytes){vault_status_msg}",
        "",
        "**Card Preview:**",
    ]

    for idx, card in enumerate(parsed_cards[:5], start=1):
        front_snip = card["front"] if len(card["front"]) <= 90 else card["front"][:87] + "..."
        back_snip = card["back"] if len(card["back"]) <= 90 else card["back"][:87] + "..."
        c_type = f" [{card['card_type']}]" if card['card_type'] != "basic" else ""
        if card["card_type"] == "cloze" or not card["back"]:
            preview_lines.append(f"{idx}. {front_snip}{c_type}")
        else:
            preview_lines.append(f"{idx}. **Q:** {front_snip} → **A:** {back_snip}{c_type}")

    if len(parsed_cards) > 5:
        preview_lines.append(f"... and {len(parsed_cards) - 5} more cards in `{apkg_filename}`.")

    return "\n".join(preview_lines)


async def handle_vault_export_anki_deck(
    bot: Any, args: dict, user_id: str, context: dict | None = None
) -> str:
    """
    Handler for vault_export_anki_deck AI tool.
    Extracts cards from an Obsidian note and generates .apkg.
    """
    if not await _is_bot_owner(bot, user_id):
        return "Permission Denied: Only the bot owner is permitted to access the Obsidian vault."

    path = str(args.get("path") or "").strip()
    if not path:
        return "Error: 'path' parameter is required."

    custom_deck_name = str(args.get("deck_name") or "").strip()

    try:
        note_data = await run_in_executor(vault_utils.read_note, rel_path=path)
        content = note_data.get("content", "")
        note_stem = os.path.splitext(note_data.get("filename", "Flashcards"))[0]
        deck_name = custom_deck_name or note_data.get("frontmatter", {}).get("cards-deck") or note_stem

        # Try parsing formatted flashcards directly
        parsed_cards = anki_utils.parse_flashcards_from_markdown(content)

        if not parsed_cards:
            return (
                f"Note `{note_data['path']}` was read, but no pre-formatted inline flashcards (e.g. `Q::A` or `{{c1::...}}`) "
                f"were found. Here is the note content so you can generate flashcards from it using `create_anki_deck`:\n\n"
                f"```markdown\n{content[:2500]}\n```"
            )

        # Generate .apkg file
        apkg_bytes = await run_in_executor(
            anki_utils.create_anki_deck_package,
            deck_name=deck_name,
            cards=parsed_cards,
            description=f"Exported from Obsidian vault note: {note_data['path']}",
        )

        safe_filename = re.sub(r'[\\/*?:"<>| ]', "_", deck_name).strip("_") or "flashcards"
        apkg_filename = f"{safe_filename}.apkg"

        if context is not None and isinstance(context, dict):
            if "out_files" not in context:
                context["out_files"] = []
            context["out_files"].append({
                "filename": apkg_filename,
                "bytes": apkg_bytes,
                "content_type": "application/octet-stream",
            })

        return (
            f"Successfully exported **{len(parsed_cards)} flashcard(s)** from `{note_data['path']}` "
            f"into Anki deck **{deck_name}** (`{apkg_filename}`)."
        )
    except Exception as e:
        return f"Error exporting flashcards from vault note '{path}': {str(e)}"
