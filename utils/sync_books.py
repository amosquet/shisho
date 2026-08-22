import os
import requests
from pocketbase import PocketBase
from pocketbase.client import FileUpload
from dotenv import load_dotenv

load_dotenv()

pb_url = os.getenv("POCKETBASE_URL")
pb_user = os.getenv("POCKETBASE_USER")
pb_password = os.getenv("POCKETBASE_PASSWORD")
owner_id = os.getenv("OWNER_ID", "0")

if not pb_url or not pb_user or not pb_password:
    print("Missing PocketBase credentials in .env")
    exit(1)

url = pb_url if "://" in pb_url else f"https://{pb_url}"
pb = PocketBase(url)
pb.collection("users").auth_with_password(pb_user, pb_password)

def sync_books():
    print(f"Fetching owner discord user: {owner_id}")
    user_records = pb.collection("shisho_users").get_full_list(query_params={"filter": f"discord_id='{owner_id}'"})
    if not user_records:
        print(f"Owner not found in shisho_users for discord_id={owner_id}.")
        return
    pb_user_id = user_records[0].id

    print(f"Fetching owner's shisho_books for user_id={pb_user_id}...")
    try:
        shisho_books = pb.collection("shisho_books").get_full_list(query_params={"filter": f"user_id='{pb_user_id}'"})
        print(f"Found {len(shisho_books)} book(s) in shisho_books.")
    except Exception as e:
        print(f"Error fetching 'shisho_books': {e}")
        shisho_books = []
    
    print("Fetching global books collection...")
    try:
        global_books = pb.collection("books").get_full_list()
        print(f"Found {len(global_books)} book(s) in global books collection.")
    except Exception as e:
        print(f"Error fetching 'books' collection: {e}")
        global_books = []

    def clean_isbn_str(val):
        return (val or "").replace("-", "").replace(" ", "").strip()

    # Create maps for quick lookup
    global_books_by_isbn = {clean_isbn_str(getattr(b, "isbn", "")): b for b in global_books if clean_isbn_str(getattr(b, "isbn", ""))}
    global_books_by_title_author = {f"{getattr(b, 'title', '').strip().lower()}::{getattr(b, 'author', '').strip().lower()}": b for b in global_books}

    processed_books_ids = set()

    def download_cover(record, filename, isbn_val):
        if not filename:
            return None
        cover_url = pb.get_file_url(record, filename)
        try:
            resp = requests.get(cover_url, timeout=10)
            if resp.status_code == 200:
                upload_name = filename
                if isbn_val:
                    ext = os.path.splitext(filename)[1] or ".jpg"
                    upload_name = f"{isbn_val}{ext}"
                return FileUpload((upload_name, resp.content))
        except Exception as e:
            print(f"Failed to download cover from {cover_url}: {e}")
        return None

    for s_book in shisho_books:
        title = getattr(s_book, "title", "")
        author = getattr(s_book, "author", "")
        isbn = getattr(s_book, "isbn", "")
        clean_s_isbn = clean_isbn_str(isbn)
        
        target_book = None
        if clean_s_isbn and clean_s_isbn in global_books_by_isbn:
            target_book = global_books_by_isbn[clean_s_isbn]
        else:
            title_author_key = f"{title.strip().lower()}::{author.strip().lower()}"
            if title_author_key in global_books_by_title_author:
                target_book = global_books_by_title_author[title_author_key]

        # PocketBase python SDK translates camelCase schema fields (like startDate) 
        # into snake_case properties (like start_date) on the Record object.
        # We map them to camelCase keys for PocketBase payload.
        data = {
            "title": getattr(s_book, "title", ""),
            "author": getattr(s_book, "author", ""),
            "status": getattr(s_book, "status", ""),
            "publishDate": getattr(s_book, "publish_date", getattr(s_book, "publishDate", "")),
            "isbn": getattr(s_book, "isbn", ""),
            "startDate": getattr(s_book, "start_date", getattr(s_book, "startDate", "")),
            "endDate": getattr(s_book, "end_date", getattr(s_book, "endDate", "")),
            "description": getattr(s_book, "description", ""),
            "completed": getattr(s_book, "completed", ""),
        }
        
        cover_filename = getattr(s_book, "cover", "")
        target_cover = getattr(target_book, "cover", "") if target_book else ""
        file_uploads = {}

        if target_book:
            processed_books_ids.add(target_book.id)
            
            t_data = {
                "title": getattr(target_book, "title", ""),
                "author": getattr(target_book, "author", ""),
                "status": getattr(target_book, "status", ""),
                "publishDate": getattr(target_book, "publish_date", getattr(target_book, "publishDate", "")),
                "isbn": getattr(target_book, "isbn", ""),
                "startDate": getattr(target_book, "start_date", getattr(target_book, "startDate", "")),
                "endDate": getattr(target_book, "end_date", getattr(target_book, "endDate", "")),
                "description": getattr(target_book, "description", ""),
                "completed": getattr(target_book, "completed", ""),
            }
            
            # One-way sync: if data differs or cover is missing in target, update the global books collection
            needs_cover_sync = bool(cover_filename and not target_cover)
            if data != t_data or needs_cover_sync:
                print(f"shisho_books -> books: Updating '{title}' (differences found)...")
                if needs_cover_sync:
                    cover_upload = download_cover(s_book, cover_filename, isbn)
                    if cover_upload:
                        file_uploads["cover"] = cover_upload
                
                final_entry = {**data, **file_uploads} if file_uploads else data
                try:
                    pb.collection("books").update(target_book.id, final_entry)
                    print(f"shisho_books -> books: Successfully updated '{title}'")
                except Exception as e:
                    print(f"Failed to update '{title}' in books: {e}")
            else:
                print(f"shisho_books -> books: '{title}' is already up-to-date in books.")
        else:
            print(f"shisho_books -> books: Creating '{title}'...")
            if cover_filename:
                cover_upload = download_cover(s_book, cover_filename, isbn)
                if cover_upload:
                    file_uploads["cover"] = cover_upload
            
            final_entry = {**data, **file_uploads} if file_uploads else data
            try:
                pb.collection("books").create(final_entry)
                print(f"shisho_books -> books: Successfully created '{title}'")
            except Exception as e:
                print(f"Failed to create '{title}' in books: {e}")

if __name__ == "__main__":
    sync_books()
    print("Sync complete.")
