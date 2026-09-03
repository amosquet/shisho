"""
utils/obsidian.py - Core Obsidian / Markdown Vault management layer for Shisho.
Provides secure path resolution, frontmatter parsing/serialization, search, and CRUD primitives.
"""

import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import yaml


def get_vault_path() -> Optional[Path]:
    """Returns the configured Obsidian vault root path or None if not configured."""
    vault_str = os.getenv("OBSIDIAN_VAULT_PATH", "").strip()
    if not vault_str:
        return None
    path = Path(vault_str).expanduser().resolve()
    return path


def ensure_vault_accessible() -> Path:
    """
    Validates that the Obsidian vault path is configured and exists.
    Raises ValueError or FileNotFoundError if invalid.
    """
    vault_root = get_vault_path()
    if not vault_root:
        raise ValueError(
            "OBSIDIAN_VAULT_PATH environment variable is not configured on the server."
        )
    if not vault_root.exists() or not vault_root.is_dir():
        raise FileNotFoundError(
            f"Configured Obsidian vault directory does not exist: {vault_root}"
        )
    return vault_root


def resolve_vault_path(
    rel_path: str,
    allow_nonexistent: bool = False,
    auto_md: bool = True,
) -> Path:
    """
    Safely resolves a relative path within the vault root, preventing path traversal attacks.
    """
    vault_root = ensure_vault_accessible()
    clean_rel = rel_path.strip().lstrip("/\\")

    # If the path has no extension and is not intended to be a folder, optionally default to .md
    if auto_md and clean_rel and not os.path.splitext(clean_rel)[1]:
        clean_rel = f"{clean_rel}.md"

    target = (vault_root / clean_rel).resolve()

    try:
        target.relative_to(vault_root)
    except ValueError:
        raise PermissionError(
            f"Access Denied: Path '{rel_path}' escapes the configured vault root."
        )

    if not allow_nonexistent and not target.exists():
        raise FileNotFoundError(f"File or directory not found in vault: '{rel_path}'")

    return target


def get_relative_vault_path(abs_path: Path) -> str:
    """Converts an absolute path inside the vault into a relative POSIX path string."""
    vault_root = ensure_vault_accessible()
    return abs_path.resolve().relative_to(vault_root).as_posix()


# =========================================================================
# Frontmatter & Markdown Helpers
# =========================================================================

FRONTMATTER_REGEX = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_frontmatter(content: str) -> Tuple[Dict[str, Any], str]:
    """
    Splits markdown content into frontmatter metadata dictionary and body string.
    """
    match = FRONTMATTER_REGEX.match(content)
    if not match:
        return {}, content

    yaml_block = match.group(1)
    body = content[match.end() :]

    try:
        data = yaml.safe_load(yaml_block)
        if isinstance(data, dict):
            return data, body
        return {}, body
    except Exception:
        return {}, body


def serialize_note(frontmatter: Optional[Dict[str, Any]], body: str) -> str:
    """
    Combines frontmatter dictionary and markdown body into a formatted note string.
    """
    if not frontmatter:
        return body

    yaml_str = yaml.dump(
        frontmatter,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    ).strip()

    clean_body = body.lstrip("\n")
    return f"---\n{yaml_str}\n---\n\n{clean_body}"


# =========================================================================
# Vault Operations
# =========================================================================

IGNORED_DIRS = {".git", ".obsidian", ".trash", ".trashcan", ".smart-env", ".trash_bin"}


def list_vault_files(
    dir_path: str = "",
    recursive: bool = False,
    include_hidden: bool = False,
) -> List[Dict[str, Any]]:
    """
    Lists files and directories inside a vault directory.
    """
    vault_root = ensure_vault_accessible()
    if dir_path:
        target_dir = resolve_vault_path(dir_path, allow_nonexistent=False, auto_md=False)
    else:
        target_dir = vault_root

    if not target_dir.is_dir():
        raise NotADirectoryError(f"'{dir_path}' is not a directory.")

    results: List[Dict[str, Any]] = []

    if recursive:
        for root, dirs, files in os.walk(target_dir):
            # Exclude hidden and ignored directories in-place
            if not include_hidden:
                dirs[:] = [
                    d
                    for d in dirs
                    if not d.startswith(".") and d.lower() not in IGNORED_DIRS
                ]
            else:
                dirs[:] = [d for d in dirs if d.lower() not in IGNORED_DIRS]

            for d in dirs:
                full_path = Path(root) / d
                rel = get_relative_vault_path(full_path)
                results.append({"path": rel, "is_dir": True, "size": 0})

            for f in files:
                if not include_hidden and f.startswith("."):
                    continue
                full_path = Path(root) / f
                rel = get_relative_vault_path(full_path)
                results.append({
                    "path": rel,
                    "is_dir": False,
                    "size": full_path.stat().st_size,
                })
    else:
        for entry in target_dir.iterdir():
            if not include_hidden and entry.name.startswith("."):
                continue
            if entry.name.lower() in IGNORED_DIRS:
                continue

            rel = get_relative_vault_path(entry)
            results.append({
                "path": rel,
                "is_dir": entry.is_dir(),
                "size": entry.stat().st_size if entry.is_file() else 0,
            })

    results.sort(key=lambda x: (not x["is_dir"], x["path"].lower()))
    return results


def read_note(rel_path: str) -> Dict[str, Any]:
    """
    Reads a note from the vault, parsing frontmatter and body.
    """
    target = resolve_vault_path(rel_path, allow_nonexistent=False)
    if not target.is_file():
        raise IsADirectoryError(f"'{rel_path}' is a directory, not a note file.")

    raw_text = target.read_text(encoding="utf-8", errors="replace")
    frontmatter, body = parse_frontmatter(raw_text)

    return {
        "path": get_relative_vault_path(target),
        "filename": target.name,
        "frontmatter": frontmatter,
        "content": raw_text,
        "body": body,
        "size": target.stat().st_size,
    }


def write_note(
    rel_path: str,
    content: str,
    overwrite: bool = True,
    frontmatter: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Creates or overwrites a note in the vault.
    """
    target = resolve_vault_path(rel_path, allow_nonexistent=True)

    existed_before = target.exists()
    if existed_before and not overwrite:
        raise FileExistsError(
            f"Note '{rel_path}' already exists and overwrite is set to False."
        )

    target.parent.mkdir(parents=True, exist_ok=True)

    if frontmatter is not None:
        final_content = serialize_note(frontmatter, content)
    else:
        final_content = content

    target.write_text(final_content, encoding="utf-8")

    return {
        "path": get_relative_vault_path(target),
        "status": "updated" if existed_before else "created",
        "size": len(final_content.encode("utf-8")),
    }


def patch_note(
    rel_path: str,
    target_snippet: str,
    replacement_snippet: str,
    replace_all: bool = False,
) -> Dict[str, Any]:
    """
    Surgically replaces a snippet of text within an existing note.
    """
    target = resolve_vault_path(rel_path, allow_nonexistent=False)
    if not target.is_file():
        raise IsADirectoryError(f"'{rel_path}' is a directory.")

    raw_text = target.read_text(encoding="utf-8", errors="replace")

    if target_snippet not in raw_text:
        raise ValueError(
            f"Target snippet was not found in '{rel_path}'. Please inspect the file content first."
        )

    count = raw_text.count(target_snippet)
    if count > 1 and not replace_all:
        raise ValueError(
            f"Target snippet appears {count} times in '{rel_path}'. Please provide a more specific unique snippet or set replace_all=True."
        )

    if replace_all:
        new_text = raw_text.replace(target_snippet, replacement_snippet)
    else:
        new_text = raw_text.replace(target_snippet, replacement_snippet, 1)

    target.write_text(new_text, encoding="utf-8")

    return {
        "path": get_relative_vault_path(target),
        "replaced_occurrences": count if replace_all else 1,
        "status": "patched",
    }


def append_note(
    rel_path: str,
    content: str,
    heading: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Appends content to the end of a note or under a specific markdown heading.
    """
    target = resolve_vault_path(rel_path, allow_nonexistent=False)
    if not target.is_file():
        raise IsADirectoryError(f"'{rel_path}' is a directory.")

    raw_text = target.read_text(encoding="utf-8", errors="replace")
    clean_append = content.strip()

    if not heading:
        new_text = raw_text.rstrip() + "\n\n" + clean_append + "\n"
    else:
        # Search for heading (e.g. ## Heading or # Heading)
        pattern = re.compile(
            rf"^(#+\s+{re.escape(heading.strip('# '))}\s*)$",
            re.MULTILINE | re.IGNORECASE,
        )
        match = pattern.search(raw_text)
        if match:
            # Insert content right after heading line
            idx = match.end()
            new_text = raw_text[:idx] + "\n" + clean_append + "\n" + raw_text[idx:]
        else:
            # Heading doesn't exist, append new heading at the bottom
            heading_title = heading.strip()
            if not heading_title.startswith("#"):
                heading_title = f"## {heading_title}"
            new_text = raw_text.rstrip() + f"\n\n{heading_title}\n{clean_append}\n"

    target.write_text(new_text, encoding="utf-8")

    return {
        "path": get_relative_vault_path(target),
        "status": "appended",
        "heading": heading,
    }


def delete_note(
    rel_path: str,
    permanent: bool = False,
) -> Dict[str, Any]:
    """
    Deletes a note or folder. By default, safely moves it into vault's `.trash/`.
    """
    target = resolve_vault_path(rel_path, allow_nonexistent=False, auto_md=False)
    vault_root = ensure_vault_accessible()

    if permanent:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return {"path": rel_path, "status": "permanently_deleted"}

    # Safe Trash mode (Obsidian standard)
    trash_dir = vault_root / ".trash"
    trash_dir.mkdir(exist_ok=True)
    rel = get_relative_vault_path(target)
    trash_dest = trash_dir / rel

    trash_dest.parent.mkdir(parents=True, exist_ok=True)
    if trash_dest.exists():
        if trash_dest.is_dir():
            shutil.rmtree(trash_dest)
        else:
            trash_dest.unlink()

    shutil.move(str(target), str(trash_dest))
    return {"path": rel_path, "status": "moved_to_trash", "trash_path": f".trash/{rel}"}


def move_note(
    source_rel: str,
    target_rel: str,
) -> Dict[str, Any]:
    """
    Renames or moves a note or folder within the vault.
    """
    src = resolve_vault_path(source_rel, allow_nonexistent=False, auto_md=False)
    dst = resolve_vault_path(target_rel, allow_nonexistent=True, auto_md=False)

    if dst.exists():
        raise FileExistsError(f"Destination path '{target_rel}' already exists.")

    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))

    return {
        "from": get_relative_vault_path(src) if src.exists() else source_rel,
        "to": get_relative_vault_path(dst),
        "status": "moved",
    }


def search_vault(
    query: str,
    search_in: str = "all",
    max_results: int = 15,
) -> List[Dict[str, Any]]:
    """
    Searches across the vault for notes matching query, tags, or frontmatter.
    search_in options: 'all', 'content', 'filename', 'tag', 'frontmatter'
    """
    vault_root = ensure_vault_accessible()
    clean_query = query.strip().lower()
    if not clean_query:
        return []

    results: List[Dict[str, Any]] = []

    for root, dirs, files in os.walk(vault_root):
        dirs[:] = [
            d for d in dirs if not d.startswith(".") and d.lower() not in IGNORED_DIRS
        ]

        for file in files:
            if file.startswith(".") or not file.endswith((".md", ".markdown", ".txt")):
                continue

            file_path = Path(root) / file
            rel_path = get_relative_vault_path(file_path)
            note_title = file_path.stem

            matched = False
            matches_info: List[str] = []

            # 1. Filename match
            if search_in in ("all", "filename"):
                if clean_query in rel_path.lower() or clean_query in note_title.lower():
                    matched = True
                    matches_info.append(f"Title/Path matched: '{rel_path}'")
                else:
                    tokens = [t for t in re.findall(r"\w+", clean_query) if len(t) > 1]
                    if tokens and all(t in rel_path.lower() or t in note_title.lower() for t in tokens):
                        matched = True
                        matches_info.append(f"Title/Path matched all tokens: '{rel_path}'")

            try:
                raw_text = file_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            frontmatter, body = parse_frontmatter(raw_text)

            # 2. Tag match (#tag or frontmatter tags)
            if search_in in ("all", "tag"):
                tag_query = clean_query.lstrip("#")
                # Check frontmatter tags
                fm_tags = frontmatter.get("tags", [])
                if isinstance(fm_tags, str):
                    fm_tags = [t.strip().lstrip("#") for t in fm_tags.split(",")]
                elif isinstance(fm_tags, list):
                    fm_tags = [str(t).strip().lstrip("#") for t in fm_tags]

                if any(tag_query in t.lower() for t in fm_tags):
                    matched = True
                    matches_info.append(f"Frontmatter tag: {fm_tags}")

                # Check body hashtags
                body_tags = re.findall(r"#([\w-]+)", body)
                if any(tag_query == t.lower() for t in body_tags):
                    matched = True
                    matches_info.append(f"Inline tag: #{tag_query}")

            # 3. Frontmatter match
            if search_in in ("all", "frontmatter") and frontmatter:
                fm_str = yaml.dump(frontmatter).lower()
                if clean_query in fm_str:
                    matched = True
                    matches_info.append("Frontmatter property match")

            # 4. Content / Body match
            if search_in in ("all", "content"):
                lines = raw_text.splitlines()
                for line_no, line in enumerate(lines, start=1):
                    if clean_query in line.lower():
                        matched = True
                        snippet = line.strip()
                        if len(snippet) > 120:
                            snippet = snippet[:120] + "..."
                        matches_info.append(f"Line {line_no}: {snippet}")
                        if len(matches_info) >= 4:
                            break
                if not matched:
                    tokens = [t for t in re.findall(r"\w+", clean_query) if len(t) > 2 and t not in ("note", "notes", "today", "yesterday", "from", "with")]
                    if tokens and all(t in raw_text.lower() or t in rel_path.lower() for t in tokens):
                        matched = True
                        matches_info.append(f"Matched keywords: {', '.join(tokens)}")

            if matched:
                results.append({
                    "path": rel_path,
                    "title": note_title,
                    "matches": matches_info,
                })

            if len(results) >= max_results:
                break

    return results


def get_backlinks(target_name_or_path: str) -> List[Dict[str, Any]]:
    """
    Finds all notes in the vault that link to the target note via [[wikilinks]] or markdown links.
    """
    vault_root = ensure_vault_accessible()
    clean_target = target_name_or_path.strip()
    target_stem = Path(clean_target).stem.lower()

    results: List[Dict[str, Any]] = []

    # Regex to match [[Note Name]], [[Note Name|Alias]], [[Note Name#Heading]]
    wikilink_pattern = re.compile(
        r"\[\[([^\]\|#]+)(?:#[^\]\|]*)?(?:\|[^\]]*)?\]\]",
        re.IGNORECASE,
    )

    for root, dirs, files in os.walk(vault_root):
        dirs[:] = [
            d for d in dirs if not d.startswith(".") and d.lower() not in IGNORED_DIRS
        ]

        for file in files:
            if file.startswith(".") or not file.endswith((".md", ".markdown")):
                continue

            file_path = Path(root) / file
            rel_path = get_relative_vault_path(file_path)

            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            links = wikilink_pattern.findall(content)
            matched_links = []
            for link in links:
                link_stem = Path(link.strip()).stem.lower()
                if link_stem == target_stem:
                    matched_links.append(link.strip())

            if matched_links:
                results.append({
                    "source_path": rel_path,
                    "matched_links": matched_links,
                })

    return results
