"""
utils/anki.py - Anki flashcard (.apkg) generator and Obsidian markdown parser for Shisho.
Supports Basic, Reversed, and Cloze card types with modern styling and Obsidian Spaced Repetition syntax.
"""

import hashlib
import html
import io
import os
import re
import tempfile
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import genanki


# =========================================================================
# Custom Anki Models & CSS Styling
# =========================================================================

ANKI_CARD_CSS = """
.card {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 19px;
    text-align: center;
    color: #1e293b;
    background-color: #ffffff;
    padding: 24px;
    line-height: 1.6;
}

.nightMode .card,
@media (prefers-color-scheme: dark) {
    .card {
        color: #f1f5f9;
        background-color: #0f172a;
    }
}

.card-title {
    font-size: 13px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: #64748b;
    margin-bottom: 16px;
}

.nightMode .card-title,
@media (prefers-color-scheme: dark) {
    .card-title {
        color: #94a3b8;
    }
}

hr#answer {
    border: none;
    border-top: 2px dashed #cbd5e1;
    margin: 24px 0;
}

.nightMode hr#answer,
@media (prefers-color-scheme: dark) {
    hr#answer {
        border-top-color: #334155;
    }
}

.answer {
    color: #0284c7;
    font-weight: 600;
}

.nightMode .answer,
@media (prefers-color-scheme: dark) {
    .answer {
        color: #38bdf8;
    }
}

.extra {
    margin-top: 18px;
    font-size: 15px;
    color: #475569;
    background: #f8fafc;
    border-left: 4px solid #3b82f6;
    border-radius: 4px;
    padding: 10px 14px;
    text-align: left;
}

.nightMode .extra,
@media (prefers-color-scheme: dark) {
    .extra {
        color: #cbd5e1;
        background: #1e293b;
        border-left-color: #60a5fa;
    }
}

code {
    font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
    font-size: 0.9em;
    background-color: #f1f5f9;
    color: #e11d48;
    padding: 2px 6px;
    border-radius: 4px;
}

.nightMode code,
@media (prefers-color-scheme: dark) {
    code {
        background-color: #1e293b;
        color: #fb7185;
    }
}

pre {
    text-align: left;
    background-color: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 6px;
    padding: 12px;
    overflow-x: auto;
}

.nightMode pre,
@media (prefers-color-scheme: dark) {
    pre {
        background-color: #1e293b;
        border-color: #334155;
    }
}

.cloze {
    font-weight: 700;
    color: #2563eb;
    background: #dbeafe;
    padding: 2px 6px;
    border-radius: 4px;
}

.nightMode .cloze,
@media (prefers-color-scheme: dark) {
    .cloze {
        color: #60a5fa;
        background: #1e3a8a;
    }
}

ul, ol {
    text-align: left;
    display: inline-block;
    margin: 8px 0;
}
"""

BASIC_MODEL_ID = 1607392319
REVERSED_MODEL_ID = 1607392320
CLOZE_MODEL_ID = 1607392321

SHISHO_BASIC_MODEL = genanki.Model(
    BASIC_MODEL_ID,
    "Shisho Basic Flashcard",
    fields=[
        {"name": "Front"},
        {"name": "Back"},
        {"name": "Extra"},
    ],
    templates=[
        {
            "name": "Card 1",
            "qfmt": '<div class="card"><div class="card-title">Prompt</div><div class="front">{{Front}}</div></div>',
            "afmt": '<div class="card"><div class="card-title">Prompt</div><div class="front">{{Front}}</div><hr id="answer"><div class="card-title">Answer</div><div class="answer">{{Back}}</div>{{#Extra}}<div class="extra">{{Extra}}</div>{{/Extra}}</div>',
        },
    ],
    css=ANKI_CARD_CSS,
)

SHISHO_REVERSED_MODEL = genanki.Model(
    REVERSED_MODEL_ID,
    "Shisho Basic & Reversed Flashcard",
    fields=[
        {"name": "Front"},
        {"name": "Back"},
        {"name": "Extra"},
    ],
    templates=[
        {
            "name": "Card 1 (Forward)",
            "qfmt": '<div class="card"><div class="card-title">Prompt</div><div class="front">{{Front}}</div></div>',
            "afmt": '<div class="card"><div class="card-title">Prompt</div><div class="front">{{Front}}</div><hr id="answer"><div class="card-title">Answer</div><div class="answer">{{Back}}</div>{{#Extra}}<div class="extra">{{Extra}}</div>{{/Extra}}</div>',
        },
        {
            "name": "Card 2 (Reverse)",
            "qfmt": '<div class="card"><div class="card-title">Prompt (Reverse)</div><div class="front">{{Back}}</div></div>',
            "afmt": '<div class="card"><div class="card-title">Prompt (Reverse)</div><div class="front">{{Back}}</div><hr id="answer"><div class="card-title">Answer</div><div class="answer">{{Front}}</div>{{#Extra}}<div class="extra">{{Extra}}</div>{{/Extra}}</div>',
        },
    ],
    css=ANKI_CARD_CSS,
)

SHISHO_CLOZE_MODEL = genanki.Model(
    CLOZE_MODEL_ID,
    "Shisho Cloze Flashcard",
    model_type=genanki.Model.CLOZE,
    fields=[
        {"name": "Text"},
        {"name": "Extra"},
    ],
    templates=[
        {
            "name": "Cloze Card",
            "qfmt": '<div class="card"><div class="card-title">Fill in the Blank</div><div class="front">{{cloze:Text}}</div></div>',
            "afmt": '<div class="card"><div class="card-title">Fill in the Blank</div><div class="front">{{cloze:Text}}</div><hr id="answer">{{#Extra}}<div class="extra">{{Extra}}</div>{{/Extra}}</div>',
        },
    ],
    css=ANKI_CARD_CSS,
)


def _generate_deck_id(deck_name: str) -> int:
    """Generates a stable, deterministic positive 31-bit integer ID for an Anki deck."""
    clean = deck_name.strip().lower()
    hash_digest = hashlib.sha256(clean.encode("utf-8")).hexdigest()
    deck_id = int(hash_digest[:8], 16) & 0x7FFFFFFF
    if deck_id < 1000000000:
        deck_id += 1000000000
    return deck_id


def markdown_to_anki_html(text: str) -> str:
    """
    Converts basic markdown formatting to HTML suitable for Anki cards,
    while carefully preserving Anki Cloze syntax {{c1::word}}.
    """
    if not text:
        return ""

    # 1. Temporarily replace Anki Cloze deletions to protect them from regexes
    cloze_patterns: List[str] = []

    def _cloze_sub(m):
        cloze_patterns.append(m.group(0))
        return f"SHISHOCLOZETOKEN{len(cloze_patterns) - 1}ENDTOKEN"

    protected_text = re.sub(r"\{\{c\d+::.*?\}\}", _cloze_sub, text, flags=re.DOTALL)

    # 2. Convert code blocks
    def _code_block_sub(m):
        code_content = html.escape(m.group(1).strip())
        return f"<pre><code>{code_content}</code></pre>"

    formatted = re.sub(r"```(?:\w+)?\n?(.*?)```", _code_block_sub, protected_text, flags=re.DOTALL)

    # 3. Convert inline code
    def _inline_code_sub(m):
        return f"<code>{html.escape(m.group(1))}</code>"

    formatted = re.sub(r"`([^`]+)`", _inline_code_sub, formatted)

    # 4. Bold and Italics
    formatted = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", formatted)
    formatted = re.sub(r"__(.+?)__", r"<strong>\1</strong>", formatted)
    formatted = re.sub(r"\*(.+?)\*", r"<em>\1</em>", formatted)
    formatted = re.sub(r"_(.+?)_", r"<em>\1</em>", formatted)

    # 5. Process lines for lists and paragraphs
    raw_lines = formatted.split("\n")
    processed_lines: List[str] = []
    in_list = False

    for line in raw_lines:
        stripped = line.strip()
        if stripped.startswith(("- ", "* ", "• ")):
            item_text = stripped[2:].strip()
            if not in_list:
                processed_lines.append("<ul>")
                in_list = True
            processed_lines.append(f"<li>{item_text}</li>")
        else:
            if in_list:
                processed_lines.append("</ul>")
                in_list = False
            processed_lines.append(line)

    if in_list:
        processed_lines.append("</ul>")

    # 6. Join lines converting consecutive newlines to <br><br> and single to <br> outside <ul> and <pre>
    result_blocks = []
    current_block = []
    inside_tag = False

    for line in processed_lines:
        if line.startswith("<ul>") or line.startswith("<pre>"):
            if current_block:
                result_blocks.append("<br>".join(current_block))
                current_block = []
            result_blocks.append(line)
            if not (line.endswith("</ul>") or line.endswith("</pre>")):
                inside_tag = True
        elif line.endswith("</ul>") or line.endswith("</pre>"):
            result_blocks.append(line)
            inside_tag = False
        elif inside_tag:
            result_blocks.append(line)
        elif not line.strip():
            if current_block:
                result_blocks.append("<br>".join(current_block))
                current_block = []
            result_blocks.append("<br>")
        else:
            current_block.append(line)

    if current_block:
        result_blocks.append("<br>".join(current_block))

    final_formatted = "\n".join(result_blocks)
    # Clean up redundant <br> around <ul> and <pre>
    final_formatted = re.sub(r"(?:<br>\s*)+(<ul>|<pre>)", r"\1", final_formatted)
    final_formatted = re.sub(r"(</ul>|</pre>)(?:\s*<br>)+", r"\1", final_formatted)

    # 7. Restore protected cloze tokens
    for idx, orig_cloze in enumerate(cloze_patterns):
        final_formatted = final_formatted.replace(f"SHISHOCLOZETOKEN{idx}ENDTOKEN", orig_cloze)

    return final_formatted.strip()


# =========================================================================
# Deck Generator
# =========================================================================

def create_anki_deck_package(
    deck_name: str,
    cards: List[Dict[str, Any]],
    description: str = "",
) -> bytes:
    """
    Builds a complete, valid Anki .apkg deck package in memory and returns the bytes.

    Args:
        deck_name: Title of the Anki deck.
        cards: List of card dictionaries with fields:
               - 'front' / 'question' / 'text': Question or cloze prompt.
               - 'back' / 'answer': Answer text.
               - 'card_type': 'basic', 'reversed', or 'cloze' (defaults to 'basic').
               - 'tags': Optional list of strings or comma-separated string.
               - 'extra': Optional supplementary explanation or context.
        description: Optional deck description.

    Returns:
        Bytes of the generated .apkg file.
    """
    clean_name = deck_name.strip() or "Shisho Flashcards"
    deck_id = _generate_deck_id(clean_name)
    deck = genanki.Deck(deck_id, clean_name, description=description)

    for c in cards:
        card_type = str(c.get("card_type") or "basic").lower().strip()
        front_raw = str(c.get("front") or c.get("question") or c.get("text") or "").strip()
        back_raw = str(c.get("back") or c.get("answer") or "").strip()
        extra_raw = str(c.get("extra") or "").strip()

        raw_tags = c.get("tags") or []
        if isinstance(raw_tags, str):
            tags = [t.strip().replace(" ", "_") for t in raw_tags.split(",") if t.strip()]
        elif isinstance(raw_tags, list):
            tags = [str(t).strip().replace(" ", "_") for t in raw_tags if str(t).strip()]
        else:
            tags = []

        # Auto-detect Cloze type if cloze deletion syntax exists in front
        has_cloze_syntax = bool(re.search(r"\{\{c\d+::.*?\}\}", front_raw))
        if card_type == "cloze" or has_cloze_syntax:
            cloze_text = front_raw
            if not has_cloze_syntax:
                # Convert ==highlighted== to {{c1::highlighted}}
                cloze_text = re.sub(r"==([^=]+)==", r"{{c1::\1}}", cloze_text)
                if not re.search(r"\{\{c\d+::.*?\}\}", cloze_text):
                    cloze_text = re.sub(r"\{([^{}]+)\}", r"{{c1::\1}}", cloze_text)

            formatted_text = markdown_to_anki_html(cloze_text)
            formatted_extra = markdown_to_anki_html(extra_raw or back_raw)

            note = genanki.Note(
                model=SHISHO_CLOZE_MODEL,
                fields=[formatted_text, formatted_extra],
                tags=tags,
            )
            deck.add_note(note)

        elif card_type in ("reversed", "both", "bidirectional"):
            formatted_front = markdown_to_anki_html(front_raw)
            formatted_back = markdown_to_anki_html(back_raw)
            formatted_extra = markdown_to_anki_html(extra_raw)

            note = genanki.Note(
                model=SHISHO_REVERSED_MODEL,
                fields=[formatted_front, formatted_back, formatted_extra],
                tags=tags,
            )
            deck.add_note(note)

        else:
            # Standard Basic Front -> Back
            formatted_front = markdown_to_anki_html(front_raw)
            formatted_back = markdown_to_anki_html(back_raw)
            formatted_extra = markdown_to_anki_html(extra_raw)

            note = genanki.Note(
                model=SHISHO_BASIC_MODEL,
                fields=[formatted_front, formatted_back, formatted_extra],
                tags=tags,
            )
            deck.add_note(note)

    package = genanki.Package(deck)

    with tempfile.NamedTemporaryFile(suffix=".apkg", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        package.write_to_file(tmp_path)
        with open(tmp_path, "rb") as f:
            apkg_bytes = f.read()
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass

    return apkg_bytes


# =========================================================================
# Obsidian Markdown Formatter & Parser
# =========================================================================

def format_cards_for_obsidian(
    deck_name: str,
    cards: List[Dict[str, Any]],
    source: str = "",
) -> str:
    """
    Formats a list of flashcards into an Obsidian Spaced Repetition compatible markdown note.
    """
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    clean_deck = deck_name.strip() or "Flashcards"

    lines = [
        "---",
        f"cards-deck: {clean_deck}",
        "tags:",
        "  - flashcards",
        "  - anki",
        f"created: {now_str}",
    ]
    if source:
        lines.append(f"source: \"{source}\"")
    lines.extend([
        "---",
        "",
        f"# {clean_deck}",
        "",
        f"> [!info] Anki Deck Export",
        f"> Generated with Shisho on {now_str}. Compatible with **Anki** (.apkg) and the **Obsidian Spaced Repetition** plugin.",
        "",
        "## Flashcards",
        "",
    ])

    for idx, c in enumerate(cards, start=1):
        card_type = str(c.get("card_type") or "basic").lower().strip()
        front = str(c.get("front") or c.get("question") or c.get("text") or "").strip()
        back = str(c.get("back") or c.get("answer") or "").strip()
        extra = str(c.get("extra") or "").strip()
        raw_tags = c.get("tags") or []
        if isinstance(raw_tags, str):
            tags = [f"#{t.strip()}" for t in raw_tags.split(",") if t.strip()]
        elif isinstance(raw_tags, list):
            tags = [f"#{str(t).strip()}" for t in raw_tags if str(t).strip()]
        else:
            tags = []
        tags_str = " " + " ".join(tags) if tags else ""

        if card_type == "cloze" or re.search(r"\{\{c\d+::.*?\}\}", front):
            lines.append(f"{front}{tags_str}")
            if extra:
                lines.append(f"> [!note] Extra\n> {extra}")
            lines.append("")

        elif card_type in ("reversed", "both"):
            clean_front = front.split(":::")[0].strip()
            clean_back = back or (front.split(":::")[1].strip() if ":::" in front else "")
            if "\n" in clean_front or "\n" in clean_back:
                lines.append(f"{clean_front}{tags_str}")
                lines.append("???")
                lines.append(clean_back)
            else:
                lines.append(f"{clean_front}:::{clean_back}{tags_str}")
            if extra:
                lines.append(f"> [!note] Extra\n> {extra}")
            lines.append("")

        else:
            # Basic Front::Back
            clean_front = front.split("::")[0].strip()
            clean_back = back or (front.split("::")[1].strip() if "::" in front else "")
            if "\n" in clean_front or "\n" in clean_back:
                lines.append(f"{clean_front}{tags_str}")
                lines.append("?")
                lines.append(clean_back)
            else:
                lines.append(f"{clean_front}::{clean_back}{tags_str}")
            if extra:
                lines.append(f"> [!note] Extra\n> {extra}")
            lines.append("")

    return "\n".join(lines).strip() + "\n"


def parse_flashcards_from_markdown(content: str) -> List[Dict[str, Any]]:
    """
    Parses a markdown string or Obsidian note to extract flashcard items.
    Supports:
    - Single-line basic: `Question::Answer`
    - Single-line reversed: `Question:::Answer`
    - Multi-line basic: `Question\n?\nAnswer`
    - Multi-line reversed: `Question\n???\nAnswer`
    - Cloze deletions: `{{c1::word}}` or `==highlighted==` or `{word}` in flashcard blocks
    - Tagged lines: `#flashcards`, `#card`
    """
    cards: List[Dict[str, Any]] = []
    lines = content.splitlines()
    i = 0
    total_lines = len(lines)

    while i < total_lines:
        line = lines[i].strip()

        # Skip YAML frontmatter
        if i == 0 and line == "---":
            i += 1
            while i < total_lines and lines[i].strip() != "---":
                i += 1
            i += 1
            continue

        if not line or line.startswith("# ") or line.startswith(">"):
            i += 1
            continue

        # 1. Check for Cloze deletion: {{c1::...}}
        if re.search(r"\{\{c\d+::.*?\}\}", line):
            clean_line, tags = _extract_line_tags(line)
            cards.append({
                "front": clean_line,
                "back": "",
                "card_type": "cloze",
                "tags": tags,
                "extra": "",
            })
            i += 1
            continue

        # 2. Check for Single-line reversed: Front:::Back
        if ":::" in line:
            parts = line.split(":::", 1)
            front, f_tags = _extract_line_tags(parts[0].strip())
            back, b_tags = _extract_line_tags(parts[1].strip())
            cards.append({
                "front": front,
                "back": back,
                "card_type": "reversed",
                "tags": list(dict.fromkeys(f_tags + b_tags)),
                "extra": "",
            })
            i += 1
            continue

        # 3. Check for Single-line basic: Front::Back
        if "::" in line:
            parts = line.split("::", 1)
            front, f_tags = _extract_line_tags(parts[0].strip())
            back, b_tags = _extract_line_tags(parts[1].strip())
            cards.append({
                "front": front,
                "back": back,
                "card_type": "basic",
                "tags": list(dict.fromkeys(f_tags + b_tags)),
                "extra": "",
            })
            i += 1
            continue

        # 4. Check for Multi-line basic / reversed: Question\n?\nAnswer or Question\n???\nAnswer
        if i + 2 < total_lines:
            next_line = lines[i + 1].strip()
            if next_line in ("?", "???"):
                front, f_tags = _extract_line_tags(line)
                card_type = "reversed" if next_line == "???" else "basic"
                ans_lines = []
                j = i + 2
                while j < total_lines and lines[j].strip() and not lines[j].startswith(("#", ">")):
                    ans_lines.append(lines[j])
                    j += 1
                back_raw = "\n".join(ans_lines).strip()
                back, b_tags = _extract_line_tags(back_raw)
                cards.append({
                    "front": front,
                    "back": back,
                    "card_type": card_type,
                    "tags": list(dict.fromkeys(f_tags + b_tags)),
                    "extra": "",
                })
                i = j
                continue

        i += 1

    return cards


def _extract_line_tags(line: str) -> Tuple[str, List[str]]:
    """Extracts trailing #hashtags from a flashcard line."""
    tags = re.findall(r"#([\w-]+)", line)
    clean = re.sub(r"\s*#[\w-]+", "", line).strip()
    return clean, tags
