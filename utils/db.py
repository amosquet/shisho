"""
Centralized PocketBase Database Layer for Shisho.
Provides connection pooling/singleton with admin authentication,
user identity mapping, multipart file upload helpers, and async wrappers.
"""

import asyncio
import functools
import os
from typing import Any, Callable, TypeVar, Union

from pocketbase import PocketBase
from pocketbase.client import FileUpload

T = TypeVar("T")

_pb_client: PocketBase | None = None


def get_pb_url(url: str | None = None) -> str:
    """
    Normalize and return the PocketBase base URL.
    Prepends 'https://' if scheme is missing.
    """
    base_url = url if url is not None else os.getenv("POCKETBASE_URL", "")
    if not base_url:
        return ""
    if "://" not in base_url:
        return f"https://{base_url}"
    return base_url


def get_pb_client(
    url: str | None = None,
    user: str | None = None,
    password: str | None = None,
    refresh: bool = False,
) -> PocketBase:
    """
    Get or create an authenticated PocketBase client instance.
    Uses admin credentials from environment variables if not provided.
    Reuses existing valid authentication token if available unless refresh=True.
    """
    global _pb_client

    target_url = get_pb_url(url)
    target_user = user if user is not None else os.getenv("POCKETBASE_USER", "")
    target_password = (
        password if password is not None else os.getenv("POCKETBASE_PASSWORD", "")
    )

    if not target_url or not target_user or not target_password:
        raise ValueError("PocketBase configuration missing in environment variables.")

    if not refresh and _pb_client is not None:
        if getattr(_pb_client.auth_store, "is_valid", False):
            return _pb_client

    client = PocketBase(target_url)
    client.collection("users").auth_with_password(target_user, target_password)
    _pb_client = client
    return _pb_client


def validate_pb_token(token: str, url: str | None = None) -> Any:
    """
    Validate a user Bearer token using auth_refresh on shisho_users.
    Uses an isolated client instance to avoid mutating the admin auth state.
    Returns the user record dict/object on success, or None on failure.
    """
    if not token:
        return None
    try:
        target_url = get_pb_url(url)
        client = PocketBase(target_url)
        client.auth_store.save(token, None)
        result = client.collection("shisho_users").auth_refresh()
        return getattr(result, "record", result)
    except Exception:
        return None


def get_discord_user_id(
    pb: Union[PocketBase, Any, str, int, None] = None,
    discord_id: Union[str, int, None] = None,
) -> str | None:
    """
    Look up the PocketBase user ID in `shisho_users` corresponding to the given Discord ID.
    Returns the PocketBase user record ID string if found, otherwise None.

    Supports both get_discord_user_id(pb, discord_id) and get_discord_user_id(discord_id).
    """
    # Allow (discord_id) positional call
    if isinstance(pb, (str, int)) and discord_id is None:
        discord_id = pb
        client = None
    else:
        client = pb

    if not discord_id:
        return None

    clean_did = "".join(c for c in str(discord_id) if c.isdigit())
    if not clean_did:
        return None

    try:
        if client is None:
            client = get_pb_client()
        records = client.collection("shisho_users").get_full_list(
            query_params={"filter": f"discord_id='{clean_did}'"}
        )
        if records:
            return records[0].id
        return None
    except Exception:
        return None


def get_pb_user_discord_id(
    pb: Union[PocketBase, Any, str, None] = None,
    pb_user_id: str | None = None,
) -> str | None:
    """
    Reverse lookup: Get the Discord ID string from a PocketBase `shisho_users` record ID.

    Supports both get_pb_user_discord_id(pb, pb_user_id) and get_pb_user_discord_id(pb_user_id).
    """
    # Allow (pb_user_id) positional call
    if isinstance(pb, str) and pb_user_id is None and not hasattr(pb, "collection"):
        pb_user_id = pb
        client = None
    else:
        client = pb

    if not pb_user_id:
        return None

    try:
        if client is None:
            client = get_pb_client()
        user_record = client.collection("shisho_users").get_one(pb_user_id)
        return getattr(user_record, "discord_id", None)
    except Exception:
        return None


class MultiFileUpload(FileUpload):
    """FileUpload wrapper supporting a list of (filename, bytes) for repeated form fields."""

    def __init__(self, file_data_list: list[tuple[str, bytes]]):
        self.file_data_list = file_data_list

    def get(self, key: str):
        return tuple((key, data) for data in self.file_data_list)


class BodyDict(dict):
    """Dictionary wrapper that yields regular fields and FileUpload instances on .items()."""

    def __init__(self, regular_data: dict[str, Any], file_uploads: dict[str, Any]):
        super().__init__(regular_data)
        self.regular_data = regular_data
        self.file_uploads = file_uploads

    def items(self):
        for k, v in self.regular_data.items():
            yield k, v
        for k, v in self.file_uploads.items():
            yield k, v


def prepare_file_upload_payload(
    data: dict[str, Any],
    files: dict[str, tuple[str, bytes] | list[tuple[str, bytes]] | FileUpload]
    | None = None,
) -> dict[str, Any]:
    """
    Combines regular dictionary data with file attachments for PocketBase create/update.
    """
    if not files:
        return data

    file_uploads: dict[str, Any] = {}
    for field_name, file_item in files.items():
        if not file_item:
            continue
        if isinstance(file_item, FileUpload):
            file_uploads[field_name] = file_item
        elif isinstance(file_item, list):
            file_uploads[field_name] = MultiFileUpload(file_item)
        elif isinstance(file_item, tuple):
            file_uploads[field_name] = FileUpload(file_item)
        else:
            raise ValueError(
                f"Unsupported file upload format for field '{field_name}': {type(file_item)}"
            )

    if not file_uploads:
        return data

    return BodyDict(data, file_uploads)


async def run_in_executor(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """
    Run a blocking/synchronous callable in a background thread.
    """
    if kwargs:
        return await asyncio.to_thread(functools.partial(func, *args, **kwargs))
    return await asyncio.to_thread(func, *args)
