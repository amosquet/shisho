import os
from datetime import datetime

import discord
from discord import app_commands
import sentry_sdk
from discord.ext import commands

from utils import google_books
from utils.db import (
    get_pb_client,
    get_discord_user_id,
    prepare_file_upload_payload,
    run_in_executor,
)


class AddToReadingListView(discord.ui.View):
    """Interactive button view allowing users to quickly add a recommended book to their reading list."""

    def __init__(
        self,
        title: str,
        author: str,
        isbn: str = "",
        publish_date: str = "",
        description: str = "",
        image_url: str = "",
        cover_filename: str | None = None,
        cover_data: bytes | None = None,
        target_discord_id: str | None = None,
    ):
        super().__init__(timeout=86400)  # 24 hours
        self.title = title
        self.author = author
        self.isbn = isbn
        self.publish_date = publish_date
        self.description = description
        self.image_url = image_url
        self.cover_filename = cover_filename
        self.cover_data = cover_data
        self.target_discord_id = target_discord_id

    @discord.ui.button(
        label="Add to Reading List",
        style=discord.ButtonStyle.primary,
        emoji="📚",
    )
    async def add_to_reading_list_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.defer(ephemeral=True)

        user_id_str = str(interaction.user.id)
        reading_list_cog = interaction.client.get_cog("ReadingList")
        if not reading_list_cog:
            await interaction.followup.send(
                "Reading list service is currently unavailable.", ephemeral=True
            )
            return

        try:
            await reading_list_cog.add_book_to_pocketbase(
                discord_id=user_id_str,
                title=self.title,
                author=self.author,
                status_val="planned",
                publish_date=self.publish_date,
                isbn=self.isbn,
                final_start_date="",
                final_end_date="",
                image_url=self.image_url,
                description=self.description,
                cover_filename=self.cover_filename,
                cover_data=self.cover_data,
            )
            display = f"**{self.title}**" + (f" by {self.author}" if self.author else "")
            await interaction.followup.send(
                f"✅ Added {display} to your Planned reading list!", ephemeral=True
            )
        except Exception as e:
            sentry_sdk.capture_exception(e)
            await interaction.followup.send(
                f"❌ Failed to add to reading list: {e}", ephemeral=True
            )


class SuggestedBooks(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.pb_url = os.getenv("POCKETBASE_URL")
        self.pb_user = os.getenv("POCKETBASE_USER")
        self.pb_password = os.getenv("POCKETBASE_PASSWORD")
        self.google_books_api_key = os.getenv("GOOGLE_BOOKS_API_KEY")

    @app_commands.command(
        name="suggest",
        description="Recommends or suggests a book to another user or to everyone.",
    )
    @app_commands.describe(
        title="Title of the book",
        author="Author of the book",
        isbn="ISBN of the book (optional, fetches details if provided alone)",
        recipient="The user you want to recommend this book to (optional, defaults to public)",
        message="A personal note or reason for recommending this book",
        is_public="Whether this recommendation is public (defaults to False if recipient is set)",
        publish_date="Publication date or year of the book (optional)",
        description="Description, synopsis, or review of the book (optional)",
        cover_image="Cover image file for the book (optional)",
    )
    @app_commands.checks.cooldown(1, 5, key=lambda i: i.user.id)
    async def suggest_book(
        self,
        interaction: discord.Interaction,
        title: str | None = None,
        author: str | None = None,
        isbn: str | None = None,
        recipient: discord.User | discord.Member | None = None,
        message: str | None = None,
        is_public: bool | None = None,
        publish_date: str | None = None,
        description: str | None = None,
        cover_image: discord.Attachment | None = None,
    ):
        if not title and not isbn:
            await interaction.response.send_message(
                "Please provide a title or an ISBN.", ephemeral=True
            )
            return

        await interaction.response.defer()

        clean_title = (title or "").strip()
        clean_author = (author or "").strip()
        clean_isbn = (isbn or "").strip()
        clean_message = (message or "").strip()
        clean_publish_date = (publish_date or "").strip()
        clean_description = (description or "").strip()

        # Handle cover image attachment if provided
        cover_filename = None
        cover_data = None
        if cover_image:
            max_size = 5 * 1024 * 1024  # 5 MB
            allowed_exts = (".png", ".jpg", ".jpeg", ".webp")
            if cover_image.size and cover_image.size > max_size:
                await interaction.followup.send(
                    "❌ Cover image file is too large (maximum size is 5 MB).", ephemeral=True
                )
                return
            if not any((cover_image.filename or "").lower().endswith(ext) for ext in allowed_exts):
                await interaction.followup.send(
                    "❌ Invalid cover image format. Allowed formats: PNG, JPG, JPEG, WEBP.", ephemeral=True
                )
                return
            try:
                cover_data = await cover_image.read()
                cover_filename = cover_image.filename
            except Exception as e:
                sentry_sdk.capture_exception(e)

        # Determine public flag
        if is_public is None:
            final_is_public = recipient is None
        else:
            final_is_public = is_public

        sender_discord_id = str(interaction.user.id)
        sender_name = str(interaction.user)
        recipient_discord_id = str(recipient.id) if recipient else ""
        recipient_name = str(recipient) if recipient else ""

        try:
            res_data = await self.add_suggestion(
                title=clean_title,
                author=clean_author,
                isbn=clean_isbn,
                sender_discord_id=sender_discord_id,
                sender_name=sender_name,
                recipient_discord_id=recipient_discord_id,
                recipient_name=recipient_name,
                message=clean_message,
                is_public=final_is_public,
                publish_date=clean_publish_date,
                description=clean_description,
                cover_filename=cover_filename,
                cover_data=cover_data,
                suggested_from="Discord",
            )

            display_name = res_data.get("display_name", "Unknown Book")
            view = AddToReadingListView(
                title=res_data.get("title", clean_title),
                author=res_data.get("author", clean_author),
                isbn=res_data.get("isbn", clean_isbn),
                publish_date=res_data.get("publish_date", clean_publish_date),
                description=res_data.get("description", clean_description),
                image_url=res_data.get("image_url", ""),
                cover_filename=res_data.get("cover_filename", cover_filename),
                cover_data=res_data.get("cover_data", cover_data),
                target_discord_id=recipient_discord_id or None,
            )

            if recipient and recipient.id != interaction.user.id:
                msg_text = (
                    f"📚 {interaction.user.mention} recommended {display_name} to {recipient.mention}!"
                )
            else:
                msg_text = (
                    f"📚 {interaction.user.mention} suggested {display_name}!"
                )

            if clean_message:
                msg_text += f"\n> *\"{clean_message}\"*"

            await interaction.followup.send(msg_text, view=view)

        except Exception as e:
            sentry_sdk.capture_exception(e)
            await interaction.followup.send(f"An error occurred: {e}")

    async def add_suggestion(
        self,
        title: str = "",
        author: str = "",
        isbn: str = "",
        sender_discord_id: str = "",
        sender_name: str = "",
        recipient_discord_id: str = "",
        recipient_name: str = "",
        message: str = "",
        is_public: bool = True,
        date_suggested: str = "",
        publish_date: str = "",
        description: str = "",
        cover_filename: str | None = None,
        cover_data: bytes | None = None,
        suggested_from: str = "Discord",
        # Legacy positional compatibility (suggested_by, suggested_from)
        suggested_by: str | None = None,
        **kwargs,
    ) -> dict:
        title = (kwargs.get("title", title) or "").strip()
        raw_isbn = (kwargs.get("isbn", isbn) or "").replace("-", "").replace(" ", "").strip()
        # Validate ISBN: must be 10 or 13 alphanumeric chars if provided
        if raw_isbn:
            clean_digits = "".join(c for c in raw_isbn if c.isdigit() or c.upper() == 'X')
            if len(clean_digits) in (10, 13):
                isbn = clean_digits
            else:
                isbn = ""
        else:
            isbn = ""
        publish_date = (kwargs.get("publishDate", kwargs.get("publish_date", publish_date)) or "").strip()
        description = (kwargs.get("description", description) or "").strip()
        date_suggested = (kwargs.get("date_suggested", date_suggested) or "").strip()
        if not date_suggested:
            date_suggested = datetime.now().strftime("%Y-%m-%d")

        # Handle legacy positional calls if needed
        if suggested_by and not sender_name and not sender_discord_id:
            sender_name = suggested_by
            if suggested_by.isdigit():
                sender_discord_id = suggested_by

        sender_discord_id = kwargs.get("sender_discord_id", sender_discord_id)
        recipient_discord_id = kwargs.get("recipient_discord_id", recipient_discord_id)
        image_url = kwargs.get("image_url", "")
        cover_filename = kwargs.get("cover_filename", cover_filename)
        cover_data = kwargs.get("cover_data", cover_data)

        # Fetch metadata from Google Books if needed
        if self.google_books_api_key and (not (title and author) or not isbn or not description or not publish_date or not (cover_filename and cover_data)):
            search_query = isbn if isbn else (f"{title} {author}".strip() if (title and author) else title)
            if search_query:
                try:
                    book_data = await google_books.fetch_book_data(
                        search_query, self.google_books_api_key
                    )
                    if isinstance(book_data, dict):
                        fetched_title = book_data.get("title")
                        fetched_authors = book_data.get("authors")
                        if fetched_title and fetched_title != "Unknown Title" and not title:
                            title = fetched_title
                        if fetched_authors and fetched_authors != ["Unknown Author"] and not author:
                            author = ", ".join(fetched_authors)
                        if not isbn:
                            isbn = book_data.get("isbn", "")
                        if not publish_date:
                            publish_date = book_data.get("publishedDate", "")
                        if not image_url:
                            image_url = book_data.get("thumbnail", "")
                        desc = book_data.get("description", "")
                        if desc and desc != "No description available." and not description:
                            description = desc

                        if image_url and not (cover_filename and cover_data):
                            cov_name, cov_bytes = await google_books.download_image(image_url)
                            if cov_name and cov_bytes:
                                cover_filename, cover_data = cov_name, cov_bytes
                except Exception as e:
                    sentry_sdk.capture_exception(e)

        display_name = ""
        if title and author:
            display_name = f"**{title}** by {author}"
        elif title:
            display_name = f"**{title}**"
        elif isbn:
            display_name = f"ISBN: {isbn}"
        else:
            display_name = "Unknown Book"

        created_record_id = ""

        def add_to_pocketbase():
            nonlocal created_record_id
            pb = get_pb_client()

            clean_sender_did = "".join(c for c in str(sender_discord_id) if c.isdigit())
            if not clean_sender_did and sender_discord_id:
                clean_sender_did = str(sender_discord_id).strip()

            clean_recipient_did = "".join(c for c in str(recipient_discord_id) if c.isdigit())
            if not clean_recipient_did and recipient_discord_id:
                clean_recipient_did = str(recipient_discord_id).strip()

            # If recipient_discord_id wasn't a valid snowflake ID (e.g. username was passed)
            if len(clean_recipient_did) < 15 and (recipient_discord_id or recipient_name):
                target_str = str(recipient_discord_id or recipient_name).lstrip("@").lower().strip()
                if hasattr(self.bot, "users"):
                    for u in self.bot.users:
                        u_name = getattr(u, "name", "").lower()
                        u_disp = getattr(u, "display_name", "").lower()
                        if u_name == target_str or u_disp == target_str or target_str in u_name:
                            clean_recipient_did = str(u.id)
                            break

            sender_pb_id = kwargs.get("sender")
            if not sender_pb_id and clean_sender_did:
                sender_pb_id = get_discord_user_id(pb, clean_sender_did)

            recipient_pb_id = kwargs.get("recipient")
            if not recipient_pb_id and clean_recipient_did:
                recipient_pb_id = get_discord_user_id(pb, clean_recipient_did)

            entry = {
                "sender_discord_id": clean_sender_did,
                "recipient_discord_id": clean_recipient_did,
                "is_public": is_public,
                "message": message,
                "date_suggested": date_suggested,
                "title": title,
                "author": author,
                "isbn": isbn,
                "publish_date": publish_date,
                "description": description,
            }

            if sender_pb_id:
                entry["sender"] = str(sender_pb_id)
            if recipient_pb_id:
                entry["recipient"] = str(recipient_pb_id)

            files = (
                {"cover": (cover_filename, cover_data)}
                if cover_filename and cover_data
                else None
            )
            final_entry = prepare_file_upload_payload(entry, files)
            rec = pb.collection("shisho_books_recommendations").create(final_entry)
            if rec and hasattr(rec, "id"):
                created_record_id = rec.id

        await run_in_executor(add_to_pocketbase)

        clean_sender_did = "".join(c for c in str(sender_discord_id) if c.isdigit())
        if not clean_sender_did and sender_discord_id:
            clean_sender_did = str(sender_discord_id).strip()

        clean_recipient_did = "".join(c for c in str(recipient_discord_id) if c.isdigit())
        if not clean_recipient_did and recipient_discord_id:
            clean_recipient_did = str(recipient_discord_id).strip()

        return {
            "id": created_record_id,
            "display_name": display_name,
            "title": title,
            "author": author,
            "isbn": isbn,
            "publish_date": publish_date,
            "description": description,
            "image_url": image_url,
            "cover_filename": cover_filename,
            "cover_data": cover_data,
            "sender_discord_id": clean_sender_did,
            "recipient_discord_id": clean_recipient_did,
            "is_public": is_public,
            "message": message,
            "date_suggested": date_suggested,
        }

    @app_commands.command(
        name="suggestions",
        description="Lists latest suggested and recommended books.",
    )
    @app_commands.describe(
        filter="Filter recommendations by scope"
    )
    @app_commands.choices(
        filter=[
            app_commands.Choice(name="All (Relevant to you & Public)", value="all"),
            app_commands.Choice(name="For Me (Received)", value="for_me"),
            app_commands.Choice(name="From Me (Sent)", value="from_me"),
            app_commands.Choice(name="Public Recommendations", value="public"),
        ]
    )
    async def list_suggestions(
        self,
        interaction: discord.Interaction,
        filter: app_commands.Choice[str] | None = None,
    ):
        await interaction.response.defer()

        filter_val = filter.value if filter else "all"
        user_discord_id = str(interaction.user.id)

        try:
            response = await self.get_suggestions_text(
                user_discord_id=user_discord_id, filter_type=filter_val
            )
            await interaction.followup.send(response)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            await interaction.followup.send(f"An error occurred: {e}")

    async def get_suggestions_text(
        self,
        user_discord_id: str | None = None,
        filter_type: str = "all",
        user_id: str | None = None,
        **kwargs,
    ) -> str:
        uid = user_discord_id or user_id or kwargs.get("user_id") or kwargs.get("user_discord_id")

        def get_from_pocketbase():
            pb = get_pb_client()
            if filter_type == "for_me" and uid:
                filter_expr = f"recipient_discord_id = '{uid}'"
            elif filter_type == "from_me" and uid:
                filter_expr = f"sender_discord_id = '{uid}'"
            elif filter_type == "public":
                filter_expr = "is_public = true"
            else:
                if uid:
                    filter_expr = f"is_public = true || recipient_discord_id = '{uid}' || sender_discord_id = '{uid}'"
                else:
                    filter_expr = "is_public = true"

            query_params = {"sort": "-date_suggested,-created"}
            if filter_expr:
                query_params["filter"] = filter_expr

            return pb.collection("shisho_books_recommendations").get_list(
                1, 15, query_params=query_params
            )

        result = await run_in_executor(get_from_pocketbase)

        if not result or not getattr(result, "items", None):
            filter_labels = {
                "for_me": "recommended to you",
                "from_me": "recommended by you",
                "public": "in public recommendations",
                "all": "yet",
            }
            return f"No books found {filter_labels.get(filter_type, 'yet')}!"

        header_labels = {
            "for_me": "Books Recommended to You",
            "from_me": "Books You Recommended",
            "public": "Public Book Recommendations",
            "all": "Latest Book Recommendations",
        }
        response = f"**{header_labels.get(filter_type, 'Latest Book Recommendations')}:**\n\n"

        for idx, record in enumerate(result.items, 1):
            title = getattr(record, "title", "") or (record.get("title", "") if hasattr(record, "get") else "")
            isbn = getattr(record, "isbn", "") or (record.get("isbn", "") if hasattr(record, "get") else "")
            author = getattr(record, "author", "") or (record.get("author", "") if hasattr(record, "get") else "")
            publish_date = getattr(record, "publish_date", "") or (record.get("publish_date", "") if hasattr(record, "get") else "") or getattr(record, "publishDate", "") or (record.get("publishDate", "") if hasattr(record, "get") else "")
            description = getattr(record, "description", "") or (record.get("description", "") if hasattr(record, "get") else "")
            cover = getattr(record, "cover", "") or (record.get("cover", "") if hasattr(record, "get") else "")
            sender_id = getattr(record, "sender_discord_id", "") or (record.get("sender_discord_id", "") if hasattr(record, "get") else "")
            recipient_id = getattr(record, "recipient_discord_id", "") or (record.get("recipient_discord_id", "") if hasattr(record, "get") else "")
            msg = getattr(record, "message", "") or (record.get("message", "") if hasattr(record, "get") else "")
            date_sug = getattr(record, "date_suggested", "") or (record.get("date_suggested", "") if hasattr(record, "get") else "")
            is_public = getattr(record, "is_public", True) if hasattr(record, "is_public") else (record.get("is_public", True) if hasattr(record, "get") else True)

            display_title = title if title else (f"ISBN: {isbn}" if isbn else "Unknown Book")
            author_str = f" by {author}" if author else ""
            response += f"{idx}. **{display_title}**{author_str}\n"

            details = []
            if isbn:
                details.append(f"ISBN: {isbn}")
            if publish_date:
                details.append(f"Published: {publish_date}")
            if date_sug:
                details.append(f"Suggested: {date_sug}")
            if sender_id:
                details.append(f"From: <@{sender_id}>")
            if recipient_id:
                details.append(f"To: <@{recipient_id}>")
            elif is_public:
                details.append("To: Everyone (Public)")
            if cover:
                details.append("Cover: [Attached]")

            if details:
                response += f"   • {' | '.join(details)}\n"

            if msg:
                response += f"   • Note: *\"{msg}\"*\n"

            if description:
                clean_desc = str(description).strip()
                if len(clean_desc) > 200:
                    clean_desc = clean_desc[:197] + "..."
                response += f"   • Description: {clean_desc}\n"

        return response

    async def delete_suggestion(
        self,
        query_or_id: str,
        user_discord_id: str | None = None,
        user_name: str | None = None,
        is_owner: bool = False,
        user_id: str | None = None,
        **kwargs,
    ) -> str:
        uid = user_discord_id or user_id or kwargs.get("user_discord_id") or kwargs.get("user_id")

        def can_delete_record(r):
            if is_owner:
                return True
            s_did = getattr(r, "sender_discord_id", "") or (r.get("sender_discord_id", "") if hasattr(r, "get") else "")
            r_did = getattr(r, "recipient_discord_id", "") or (r.get("recipient_discord_id", "") if hasattr(r, "get") else "")
            clean_uid = str(uid).strip()
            if clean_uid and clean_uid.isdigit() and (str(s_did) == clean_uid or str(r_did) == clean_uid):
                return True
            if user_name and str(s_by) == str(user_name):
                return True
            return False

        try:
            def _delete():
                pb = get_pb_client()
                clean_target = query_or_id.strip()
                if not clean_target:
                    return "Error: Please specify a book title, ISBN, or ID to delete."

                # 1. Try finding by exact ID first
                try:
                    record = pb.collection("shisho_books_recommendations").get_one(clean_target)
                    if record:
                        if can_delete_record(record):
                            title = getattr(record, "title", "") or getattr(record, "isbn", "") or "Unknown Book"
                            pb.collection("shisho_books_recommendations").delete(record.id)
                            return f"Successfully removed **{title}** from recommendations."
                        else:
                            return "You can only delete recommendations that you created or received."
                except Exception:
                    pass

                # 2. Search suggestions
                clean_isbn = clean_target.replace("-", "").replace(" ", "").strip()
                safe_query = clean_target.replace("\\", "\\\\").replace("'", "\\'")
                filter_str = f"title ~ '{safe_query}' || author ~ '{safe_query}' || isbn ~ '{clean_isbn}'"
                records = pb.collection("shisho_books_recommendations").get_full_list(
                    query_params={"filter": filter_str}
                )
                if not records:
                    return f"No book recommendation found matching '{clean_target}'."

                # Filter records to allowed user if not owner
                permitted_records = [r for r in records if can_delete_record(r)]
                if not permitted_records:
                    return "You can only delete recommendations that you created or received."

                # Prefer exact title or ISBN match
                matched = None
                for r in permitted_records:
                    r_title = getattr(r, "title", "") or (r.get("title", "") if hasattr(r, "get") else "")
                    r_isbn = getattr(r, "isbn", "") or (r.get("isbn", "") if hasattr(r, "get") else "")
                    if r_title.lower() == clean_target.lower() or (clean_isbn and r_isbn == clean_isbn):
                        matched = r
                        break
                if not matched:
                    matched = permitted_records[0]

                title = getattr(matched, "title", "") or getattr(matched, "isbn", "") or "Unknown Book"
                pb.collection("shisho_books_recommendations").delete(matched.id)
                return f"Successfully removed **{title}** from recommendations."

            return await run_in_executor(_delete)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            return f"Failed to delete recommendation: {e}"

    async def suggestion_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        try:
            owner_id_str = os.getenv("OWNER_ID", "0")
            is_owner = str(interaction.user.id) == owner_id_str
            user_discord_id = str(interaction.user.id)
            user_name = str(interaction.user)

            def _fetch():
                pb = get_pb_client()
                return pb.collection("shisho_books_recommendations").get_full_list(
                    query_params={"sort": "-date_suggested,-created"}
                )

            records = await run_in_executor(_fetch)
            if not records:
                return []

            clean_cur = current.lower().strip()
            choices = []
            for r in records:
                s_did = getattr(r, "sender_discord_id", "") or (r.get("sender_discord_id", "") if hasattr(r, "get") else "")
                r_did = getattr(r, "recipient_discord_id", "") or (r.get("recipient_discord_id", "") if hasattr(r, "get") else "")
                s_by = getattr(r, "suggestedBy", "") or (r.get("suggestedBy", "") if hasattr(r, "get") else "")

                if not is_owner and s_did != user_discord_id and r_did != user_discord_id and s_by != user_name:
                    continue

                title = getattr(r, "title", "") or (r.get("title", "") if hasattr(r, "get") else "")
                author = getattr(r, "author", "") or (r.get("author", "") if hasattr(r, "get") else "")
                isbn = getattr(r, "isbn", "") or (r.get("isbn", "") if hasattr(r, "get") else "")
                display = f"{title} by {author}" if (title and author) else (title or f"ISBN: {isbn}")

                if clean_cur and clean_cur not in display.lower() and clean_cur not in isbn.lower():
                    continue

                name_preview = display[:100]
                choices.append(app_commands.Choice(name=name_preview, value=r.id))
            return choices[:25]
        except Exception:
            return []

    @app_commands.command(
        name="deletesuggestion",
        description="Removes a book from the recommendations list.",
    )
    @app_commands.describe(
        suggestion="The recommendation to delete (select from list or type title/ISBN/ID)"
    )
    async def slash_delete_suggestion(
        self, interaction: discord.Interaction, suggestion: str
    ):
        await interaction.response.defer()
        owner_id_str = os.getenv("OWNER_ID", "0")
        is_owner = str(interaction.user.id) == owner_id_str
        response = await self.delete_suggestion(
            suggestion,
            user_discord_id=str(interaction.user.id),
            user_name=str(interaction.user),
            is_owner=is_owner,
        )
        await interaction.followup.send(response)

    @slash_delete_suggestion.autocomplete("suggestion")
    async def slash_delete_suggestion_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self.suggestion_autocomplete(interaction, current)


async def setup(bot):
    await bot.add_cog(SuggestedBooks(bot))

