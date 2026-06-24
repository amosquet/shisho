import os
import json
import aiohttp
import sentry_sdk

CACHE_FILE = "book_cache.json"
_cache = None

def _load_cache():
    global _cache
    if _cache is not None:
        return _cache
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
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
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_cache, f, indent=4)
    except Exception as e:
        print(f"Failed to save book cache: {e}")

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

    # 1. Check local JSON cache first
    if query_key in cache:
        return cache[query_key]

    # 2. If not in cache, fetch from Google Books API
    if not api_key:
        return "Google Books API key is not configured."

    api_query = query
    clean_query = query.replace("-", "").replace(" ", "").strip()
    if clean_query.isdigit() and len(clean_query) in (10, 13):
        api_query = f"isbn:{clean_query}"

    url = "https://www.googleapis.com/books/v1/volumes"
    params = {"q": api_query, "key": api_key}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("items"):
                        vol_info = data["items"][0].get("volumeInfo", {})
                        
                        book_data = {
                            "title": vol_info.get("title", "Unknown Title"),
                            "authors": vol_info.get("authors", ["Unknown Author"]),
                            "description": vol_info.get("description", "No description available."),
                            "pageCount": vol_info.get("pageCount", 0),
                            "averageRating": vol_info.get("averageRating", "N/A"),
                            "thumbnail": vol_info.get("imageLinks", {}).get("thumbnail", ""),
                            "publishedDate": vol_info.get("publishedDate", "Unknown"),
                            "isbn": _extract_isbn(vol_info.get("industryIdentifiers", []))
                        }

                        # 3. Store inside local JSON cache
                        cache[query_key] = book_data
                        _save_cache()

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
