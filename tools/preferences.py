"""
tools/preferences.py - AI Tool definitions and Memory Manager subroutine for persistent user instructions and preferences.
"""

import json
import os
from typing import Any

from google.genai import types
import sentry_sdk

from utils.db import (
    get_discord_user_id,
    get_user_instructions,
    run_in_executor,
    update_user_instructions,
)
from utils.llm import generate_content_with_retry, get_gemini_client, get_gemini_model


UPDATE_USER_PREFERENCE_TOOL = types.FunctionDeclaration(
    name="update_user_preference",
    description=(
        "Saves, updates, or adjusts persistent behavioral preferences or rules for how Shisho "
        "interacts with the user (e.g. tone, formatting, language, persona tweaks, naming preferences). "
        "Call this tool whenever the user instructs Shisho on how they want Shisho to behave, reply, "
        "or speak in future messages."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "instruction": types.Schema(
                type=types.Type.STRING,
                description=(
                    "The preference or behavioral rule specified by the user "
                    "(e.g., 'always reply in lowercase', 'call me boss', 'keep answers brief', 'never use emojis')."
                ),
            ),
        },
        required=["instruction"],
    ),
)


MEMORY_MANAGER_SYSTEM_PROMPT = """You are the Memory Manager for Shisho, an AI Discord bot.
Your task is to maintain a structured JSON array of persistent behavioral preferences and rules for a user.

Rules for managing instructions:
1. CONTRADICTION: If the new instruction directly contradicts, opposes, or negates an existing rule (e.g. 'always be formal' vs 'never be formal, be casual'; 'use emojis' vs 'no emojis'), REPLACE that conflicting rule at its ID with the new instruction.
2. DEDUPLICATION: If the new instruction is already covered by or duplicates an existing rule, update or refine that existing rule instead of creating a duplicate.
3. COMPACTION: If the resulting number of rules is 10 or greater (>= 10), run a compaction pass: consolidate overlapping, redundant, or related rules into concise unified rules so the total count stays strictly under 10.
4. APPEND: If there is no contradiction and total rules < 10, append the new rule with an incremented integer ID.
5. RE-INDEXING: Ensure all rule IDs are strictly consecutive positive integers starting from 1 (1, 2, 3, ...). Keep rule text concise, actionable, and imperative (e.g. 'Reply in lowercase', 'Address the user as boss').

You MUST respond ONLY with a JSON object in this exact schema:
{
  "rules": [
    {"id": 1, "rule": "string description"}
  ],
  "action": "appended" | "replaced" | "compacted",
  "summary": "Brief 1-sentence description of what changed (e.g., 'Replaced conflicting rule ID 2 with new tone preference.')"
}"""


def _python_fallback_manager(
    existing_rules: list[dict], new_instruction: str
) -> tuple[list[dict], str]:
    """Deterministic pure-Python fallback if LLM memory manager is unreachable."""
    clean_inst = new_instruction.strip()
    if not clean_inst:
        return existing_rules, "No changes made."

    base = list(existing_rules)
    # Check simple contradiction/duplicate
    for idx, r in enumerate(base):
        rule_text = r.get("rule", "").lower()
        if clean_inst.lower() == rule_text:
            return existing_rules, "Rule already exists."

    # If count >= 10, compact by taking last 9
    if len(base) >= 10:
        base = base[-9:]
        action = "compacted"
    else:
        action = "appended"

    new_list = []
    for idx, r in enumerate(base, start=1):
        new_list.append({"id": idx, "rule": r.get("rule", "")})
    new_list.append({"id": len(new_list) + 1, "rule": clean_inst})

    return new_list, f"{action.capitalize()} rule ID {len(new_list)}: '{clean_inst}'"


async def run_memory_manager_llm(
    existing_rules: list[dict],
    new_instruction: str,
    client: Any = None,
) -> tuple[list[dict], str]:
    """
    Runs a rapid, background Gemini LLM call with structured JSON output and temperature=0.0
    to handle append, compaction, contradiction resolution, and sequential re-indexing.
    """
    clean_inst = new_instruction.strip()
    if not clean_inst:
        return existing_rules, "Empty instruction received."

    if client is None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return _python_fallback_manager(existing_rules, clean_inst)
        client = get_gemini_client(api_key)

    user_prompt = (
        f"Existing rules:\n{json.dumps(existing_rules, indent=2)}\n\n"
        f"New instruction:\n\"{clean_inst}\""
    )

    config = types.GenerateContentConfig(
        system_instruction=MEMORY_MANAGER_SYSTEM_PROMPT,
        response_mime_type="application/json",
        temperature=0.0,
    )

    try:
        model_name = get_gemini_model()
        response = await generate_content_with_retry(
            client,
            model=model_name,
            contents=user_prompt,
            config=config,
        )
        resp_text = (response.text or "").strip()
        if not resp_text:
            return _python_fallback_manager(existing_rules, clean_inst)

        data = json.loads(resp_text)
        if isinstance(data, list):
            raw_rules = data
            summary = f"Updated preferences: {len(raw_rules)} active rule(s)."
        elif isinstance(data, dict):
            raw_rules = data.get("rules", [])
            summary = data.get("summary", f"Updated preferences: {len(raw_rules)} active rule(s).")
        else:
            return _python_fallback_manager(existing_rules, clean_inst)

        # Validate and re-index sequentially 1..N
        valid_rules: list[dict] = []
        for idx, item in enumerate(raw_rules, start=1):
            if isinstance(item, dict) and "rule" in item:
                rule_str = str(item["rule"]).strip()
                if rule_str:
                    valid_rules.append({"id": idx, "rule": rule_str})

        if not valid_rules:
            return _python_fallback_manager(existing_rules, clean_inst)

        return valid_rules, summary
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return _python_fallback_manager(existing_rules, clean_inst)


async def handle_update_user_preference(
    bot: Any, args: dict, user_id: str
) -> str:
    """
    Handler for the update_user_preference AI tool call.
    """
    if not user_id:
        return "Error: Unable to identify user."

    clean_did = "".join(c for c in str(user_id) if c.isdigit())
    if not clean_did:
        return "Error: Invalid user ID."

    pb_user_id = await run_in_executor(get_discord_user_id, clean_did)
    if not pb_user_id:
        return (
            "You don't have a linked Shisho account yet. Please run `/register` "
            "to link your Discord account before saving persistent preferences."
        )

    instruction = str(args.get("instruction", "")).strip()
    if not instruction:
        return "Error: 'instruction' parameter is required."

    existing_rules = await run_in_executor(get_user_instructions, clean_did)

    ai_cog = bot.get_cog("AIChat") if hasattr(bot, "get_cog") else None
    client = getattr(ai_cog, "client", None) if ai_cog else None
    if not client:
        api_key = os.getenv("GEMINI_API_KEY")
        client = get_gemini_client(api_key)

    new_rules, summary = await run_memory_manager_llm(
        existing_rules, instruction, client=client
    )

    success = await run_in_executor(update_user_instructions, clean_did, new_rules)
    if not success:
        return "Error: Failed to save preferences to the database."

    # Instant Invalidation of in-memory cache
    if ai_cog and hasattr(ai_cog, "set_cached_user_instructions"):
        ai_cog.set_cached_user_instructions(clean_did, new_rules)

    return f"Successfully updated your AI preferences. {summary}"
