import asyncio
import json
import os
import re

import aiohttp
import sentry_sdk

CACHE_FILE = os.path.join("data", "book_cache.json")
_cache = None

def _load_cache():
    global _cache
    if _cache is not None:
        return _cache
    target_file = CACHE_FILE
    if not os.path.exists(target_file) and os.path.exists("book_cache.json"):
        target_file = "book_cache.json"
    if os.path.exists(target_file):
        try:
            with open(target_file, "r", encoding="utf-8") as f:
                _cache = json.load(f)
                return _cache
        except Exception as e:
            print(f"Failed to load book cache: {e}")
            _cache = {}
            return _cache
    _cache = {}
    return _cache

def _save_cache():
    global _cache
    if _cache is None:
        return
    try:
        os.makedirs(os.path.dirname(CACHE_FILE) or ".", exist_ok=True)
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_cache, f, indent=4)
    except Exception as e:
        print(f"Failed to save book cache: {e}")

def clean_thumbnail_url(url: str) -> str:
    """Removes the edge curl/fold parameter and ensures https for Google Books cover images."""
    if not url:
        return ""
    # Ensure https protocol
    cleaned = url.replace("http://", "https://")
    # Remove edge parameter (e.g. edge=curl)
    cleaned = re.sub(r"([?&])edge=[^&]*(&|$)", r"\1", cleaned)
    # Clean up trailing ? or &
    cleaned = re.sub(r"[?&]$", "", cleaned)
    # Fix ?& if edge was first parameter
    cleaned = cleaned.replace("?&", "?")
    return cleaned

async def download_image(url: str) -> tuple[str | None, bytes | None]:
    """Downloads an image from a URL, cleaning the URL first, and returns filename and bytes."""
    if not url:
        return None, None
    clean_url = clean_thumbnail_url(url)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(clean_url) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    return "cover.jpg", data
    except Exception as e:
        print(f"Failed to download image from {clean_url}: {e}")
    return None, None

def _extract_isbn(identifiers: list) -> str:
    for identifier in identifiers:
        if identifier.get("type") in ("ISBN_13", "ISBN_10"):
            return identifier.get("identifier", "")
    return ""

async def fetch_book_data(query: str, api_key: str):
    """
    Fetches detailed book data from Google Books API.
    Returns a dictionary of book details or an error string.
    """
    if not query:
        return "Query is empty."

    query_key = query.lower().strip()
    cache = _load_cache()

    # 1. Check local JSON cache first (only if it has valid publication date)
    if query_key in cache:
        cached = cache[query_key]
        if isinstance(cached, dict) and cached.get("publishedDate") and cached.get("publishedDate") != "Unknown":
            # Ensure cached thumbnail has clean URL
            if cached.get("thumbnail"):
                cached["thumbnail"] = clean_thumbnail_url(cached["thumbnail"])
            return cached

    # 2. If not in cache, fetch from Google Books API
    if not api_key:
        return "Google Books API key is not configured."

    raw_query = query.strip()
    isbn_clean = raw_query.lower()
    if isbn_clean.startswith("isbn:"):
        isbn_clean = isbn_clean[5:].strip()
    isbn_clean = isbn_clean.replace("-", "").replace(" ", "").strip()

    if isbn_clean.isdigit() and len(isbn_clean) in (10, 13):
        api_query = f"isbn:{isbn_clean}"
    else:
        api_query = raw_query

    url = "https://www.googleapis.com/books/v1/volumes"
    params = {"q": api_query, "key": api_key}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("items"):
                        vol_info = data["items"][0].get("volumeInfo", {})
                        
                        image_links = vol_info.get("imageLinks", {})
                        raw_thumbnail = (
                            image_links.get("extraLarge")
                            or image_links.get("large")
                            or image_links.get("medium")
                            or image_links.get("small")
                            or image_links.get("thumbnail")
                            or image_links.get("smallThumbnail")
                            or ""
                        )
                        thumbnail = clean_thumbnail_url(raw_thumbnail)

                        book_data = {
                            "title": vol_info.get("title", "Unknown Title"),
                            "authors": vol_info.get("authors", ["Unknown Author"]),
                            "description": vol_info.get("description", "No description available."),
                            "pageCount": vol_info.get("pageCount", 0),
                            "averageRating": vol_info.get("averageRating", "N/A"),
                            "thumbnail": thumbnail,
                            "publishedDate": vol_info.get("publishedDate", "Unknown"),
                            "isbn": _extract_isbn(vol_info.get("industryIdentifiers", []))
                        }

                        # 3. Store inside local JSON cache if meaningful data was found
                        if book_data.get("publishedDate") != "Unknown":
                            cache[query_key] = book_data
                            await asyncio.to_thread(_save_cache)

                        return book_data
                    else:
                        return f"No books found for query: `{query}`."
                else:
                    return "Failed to fetch data from Google Books API."
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return "An internal error occurred while fetching book data."

async def search_books(query: str, api_key: str, order_by: str = None) -> list:
    """
    Performs a broader search on Google Books API and returns a list of items.
    """
    if not api_key:
        return []

    url = "https://www.googleapis.com/books/v1/volumes"
    params = {"q": query, "key": api_key}
    if order_by:
        params["orderBy"] = order_by

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("items", [])
    except Exception as e:
        sentry_sdk.capture_exception(e)
    return []
