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
        print("Owner not found in shisho_users.")
        return
    pb_user_id = user_records[0].id

    print("Fetching owner's shisho_books...")
    try:
        shisho_books = pb.collection("shisho_books").get_full_list(query_params={"filter": f"user_id='{pb_user_id}'"})
    except Exception as e:
        print(f"Error fetching 'shisho_books': {e}")
        shisho_books = []
    
    print("Fetching global books collection...")
    try:
        global_books = pb.collection("books").get_full_list()
    except Exception as e:
        print(f"Error fetching 'books' collection: {e}")
        global_books = []

    # Create maps for quick lookup
    global_books_by_isbn = {b.isbn: b for b in global_books if getattr(b, "isbn", None)}
    global_books_by_title_author = {f"{getattr(b, 'title', '')}::{getattr(b, 'author', '')}": b for b in global_books}

    processed_books_ids = set()

    for s_book in shisho_books:
        title = getattr(s_book, "title", "")
        author = getattr(s_book, "author", "")
        isbn = getattr(s_book, "isbn", "")
        
        target_book = None
        if isbn and isbn in global_books_by_isbn:
            target_book = global_books_by_isbn[isbn]
        else:
            title_author_key = f"{title}::{author}"
            if title_author_key in global_books_by_title_author:
                target_book = global_books_by_title_author[title_author_key]

        # PocketBase python SDK translates camelCase schema fields (like startDate) 
        # into snake_case properties (like start_date) on the Record object.
        # We need to map them back to the exact camelCase keys for the update payload.
        data = {
            "title": getattr(s_book, "title", ""),
            "author": getattr(s_book, "author", ""),
            "status": getattr(s_book, "status", ""),
            "publishDate": getattr(s_book, "publish_date", ""),
            "isbn": getattr(s_book, "isbn", ""),
            "startDate": getattr(s_book, "start_date", ""),
            "endDate": getattr(s_book, "end_date", ""),
            "description": getattr(s_book, "description", ""),
            "completed": getattr(s_book, "completed", ""),
        }
        
        cover_filename = getattr(s_book, "cover", "")
        
        # Download cover if present (for uploading to the other side)
        file_uploads = {}
        target_cover = getattr(target_book, "cover", "") if target_book else ""
        
        def download_cover(record, filename, isbn_val):
            if not filename: return None
            cover_url = pb.get_file_url(record, filename)
            try:
                resp = requests.get(cover_url)
                if resp.status_code == 200:
                    upload_name = filename
                    if isbn_val:
                        ext = os.path.splitext(filename)[1]
                        upload_name = f"{isbn_val}{ext}"
                    return FileUpload((upload_name, resp.content))
            except Exception as e:
                print(f"Failed to download cover: {e}")
            return None

        class BodyDict(dict):
            def __init__(self, regular_data, file_uploads):
                super().__init__(regular_data)
                self.regular_data = regular_data
                self.file_uploads = file_uploads
            def items(self):
                for k, v in self.regular_data.items():
                    yield k, v
                for k, v in self.file_uploads.items():
                    yield k, v

        if target_book:
            processed_books_ids.add(target_book.id)
            
            t_data = {
                "title": getattr(target_book, "title", ""),
                "author": getattr(target_book, "author", ""),
                "status": getattr(target_book, "status", ""),
                "publishDate": getattr(target_book, "publish_date", ""),
                "isbn": getattr(target_book, "isbn", ""),
                "startDate": getattr(target_book, "start_date", ""),
                "endDate": getattr(target_book, "end_date", ""),
                "description": getattr(target_book, "description", ""),
                "completed": getattr(target_book, "completed", ""),
            }
            
            # One-way sync: if data differs, update the global books collection
            if data != t_data or (cover_filename and cover_filename != target_cover):
                print(f"shisho_books -> books: Updating '{title}' (differences found)...")
                if cover_filename and cover_filename != target_cover:
                    cover_upload = download_cover(s_book, cover_filename, isbn)
                    if cover_upload: file_uploads["cover"] = cover_upload
                
                final_entry = BodyDict(data, file_uploads) if file_uploads else data
                try:
                    pb.collection("books").update(target_book.id, final_entry)
                except Exception as e:
                    print(f"Failed to update '{title}' in books: {e}")
        else:
            print(f"shisho_books -> books: Creating '{title}'...")
            if cover_filename:
                cover_upload = download_cover(s_book, cover_filename, isbn)
                if cover_upload: file_uploads["cover"] = cover_upload
            
            final_entry = BodyDict(data, file_uploads) if file_uploads else data
            try:
                pb.collection("books").create(final_entry)
            except Exception as e:
                print(f"Failed to create '{title}' in books: {e}")

if __name__ == "__main__":
    sync_books()
    print("Sync complete.")
