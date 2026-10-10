"""
tools/excel.py - AI Tool definitions and coroutine handlers for Excel (.xlsx) manipulation.

Provides Gemini function declarations and async handlers for:
- excel_create_workbook: Create new workbooks (blank, templated, or with headers/rows)
- excel_read_range: Read cell ranges or worksheets formatted as Markdown tables
- excel_update_cells: Write values, formulas, or matrices and clear ranges
- excel_manage_sheet: Lifecycle actions (list, add, rename, delete, set_active)
- excel_export_workbook: Attach .xlsx file to Discord chat responses via context['out_files']
- excel_list_workbooks: List stored workbooks in data/excel/ with metadata
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from pathlib import Path
from typing import Any

from google.genai import types
import sentry_sdk

from utils.excel_manager import (
    create_workbook,
    get_excel_dir,
    get_workbook_bytes,
    list_sheets,
    manage_sheet,
    read_range,
    write_cells,
)

logger = logging.getLogger(__name__)

# =========================================================================
# 1. Gemini Function Declarations
# =========================================================================

EXCEL_CREATE_WORKBOOK_TOOL = types.FunctionDeclaration(
    name="excel_create_workbook",
    description="Creates a new Excel (.xlsx) workbook in data/excel/. Can create a blank workbook, initialize with headers and rows, or apply predefined templates (blank, budget, project_tracker, reading_list).",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "filename": types.Schema(
                type=types.Type.STRING,
                description="Target filename (e.g. 'budget.xlsx', 'tracker.xlsx'). If extension is omitted, '.xlsx' is added automatically.",
            ),
            "sheet_name": types.Schema(
                type=types.Type.STRING,
                description="Title of the initial worksheet. Defaults to 'Sheet1' (or template default).",
            ),
            "headers": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(type=types.Type.STRING),
                description="Optional column header labels for row 1.",
            ),
            "rows": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(
                    type=types.Type.ARRAY,
                    items=types.Schema(type=types.Type.STRING),
                ),
                description="Optional 2D grid of rows (values or formulas like '=SUM(A1:A5)') to populate beneath headers.",
            ),
            "template": types.Schema(
                type=types.Type.STRING,
                description="Optional starter template name: 'blank', 'budget', 'project_tracker', or 'reading_list'.",
            ),
        },
        required=["filename"],
    ),
)

EXCEL_READ_RANGE_TOOL = types.FunctionDeclaration(
    name="excel_read_range",
    description="Reads cell values or ranges (e.g. 'A1:D10', 'B2', or full sheet) from an Excel workbook and returns an aligned Markdown table suitable for Discord chat.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "filename": types.Schema(
                type=types.Type.STRING,
                description="The filename of the workbook in data/excel/ to read.",
            ),
            "sheet_name": types.Schema(
                type=types.Type.STRING,
                description="Worksheet name to read. Defaults to the active sheet.",
            ),
            "range_address": types.Schema(
                type=types.Type.STRING,
                description="Cell coordinate (e.g. 'A1') or rectangular range (e.g. 'A1:F20'). If omitted, reads all populated data in the sheet.",
            ),
            "max_rows": types.Schema(
                type=types.Type.INTEGER,
                description="Maximum rows to display (default 50) to prevent chat overflow.",
            ),
            "show_formulas": types.Schema(
                type=types.Type.BOOLEAN,
                description="If true, displays formula expressions (e.g. '=SUM(...)') instead of cached calculated values.",
            ),
        },
        required=["filename"],
    ),
)

EXCEL_UPDATE_CELLS_TOOL = types.FunctionDeclaration(
    name="excel_update_cells",
    description="Writes or updates cell values and formulas in an Excel workbook. Supports individual cell updates (e.g. A1='Total') or tabular row writes starting from an anchor cell (e.g. A2).",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "filename": types.Schema(
                type=types.Type.STRING,
                description="Target workbook filename in data/excel/.",
            ),
            "sheet_name": types.Schema(
                type=types.Type.STRING,
                description="Worksheet to update. Defaults to the active sheet.",
            ),
            "updates": types.Schema(
                type=types.Type.ARRAY,
                description="List of individual cell updates with 'cell' (e.g. 'B10') and 'value' (e.g. 42.50 or '=SUM(B2:B9)').",
                items=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "cell": types.Schema(
                            type=types.Type.STRING,
                            description="Cell coordinate, e.g. 'A1', 'B10'.",
                        ),
                        "value": types.Schema(
                            type=types.Type.STRING,
                            description="Value to write, or formula string starting with '='.",
                        ),
                    },
                    required=["cell"],
                ),
            ),
            "start_cell": types.Schema(
                type=types.Type.STRING,
                description="Top-left anchor cell coordinate (e.g. 'A2') when writing tabular rows.",
            ),
            "rows": types.Schema(
                type=types.Type.ARRAY,
                description="2D array of row values to write sequentially starting at start_cell.",
                items=types.Schema(
                    type=types.Type.ARRAY,
                    items=types.Schema(type=types.Type.STRING),
                ),
            ),
            "clear_range": types.Schema(
                type=types.Type.STRING,
                description="Optional coordinate or range to clear before writing (e.g. 'A2:D50' or 'C1').",
            ),
        },
        required=["filename"],
    ),
)

EXCEL_MANAGE_SHEET_TOOL = types.FunctionDeclaration(
    name="excel_manage_sheet",
    description="Manages worksheets in an Excel workbook: add new sheets, rename sheets, delete sheets, list all sheet names, or set the active sheet.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "filename": types.Schema(
                type=types.Type.STRING,
                description="Target workbook filename in data/excel/.",
            ),
            "action": types.Schema(
                type=types.Type.STRING,
                description="Action to perform: 'list', 'add', 'rename', 'delete', or 'set_active'.",
                enum=["list", "add", "rename", "delete", "set_active"],
            ),
            "sheet_name": types.Schema(
                type=types.Type.STRING,
                description="Worksheet name to add, delete, set active, or current sheet name to rename.",
            ),
            "new_name": types.Schema(
                type=types.Type.STRING,
                description="New worksheet name when renaming a sheet.",
            ),
        },
        required=["filename", "action"],
    ),
)

EXCEL_EXPORT_WORKBOOK_TOOL = types.FunctionDeclaration(
    name="excel_export_workbook",
    description="Exports an Excel (.xlsx) workbook from data/excel/ and attaches it directly to the Discord chat response as a downloadable file.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "filename": types.Schema(
                type=types.Type.STRING,
                description="The filename of the workbook in data/excel/ to export and send.",
            ),
            "as_filename": types.Schema(
                type=types.Type.STRING,
                description="Optional custom attachment filename (e.g. 'Monthly_Report_Final.xlsx').",
            ),
        },
        required=["filename"],
    ),
)

EXCEL_LIST_WORKBOOKS_TOOL = types.FunctionDeclaration(
    name="excel_list_workbooks",
    description="Lists available Excel workbooks in data/excel/ with file sizes, modified dates, and sheet names.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "query": types.Schema(
                type=types.Type.STRING,
                description="Optional search filter to match against workbook filenames.",
            ),
        },
    ),
)


# =========================================================================
# 2. Async Coroutine Tool Handlers
# =========================================================================

async def handle_excel_create_workbook(
    bot: Any, args: dict, user_id: str, context: dict | None = None
) -> str:
    """Handler for excel_create_workbook AI tool."""
    if not isinstance(args, dict):
        args = {}

    filename = str(args.get("filename") or "").strip()
    if not filename:
        return "Error: 'filename' is required to create an Excel workbook."

    sheet_name = str(args.get("sheet_name") or "").strip() or None
    template = str(args.get("template") or "").strip() or None
    headers = args.get("headers")
    if headers is not None and not isinstance(headers, list):
        headers = [str(headers)]

    rows = args.get("rows")
    if rows is not None and not isinstance(rows, list):
        rows = None

    try:
        res = await asyncio.to_thread(
            create_workbook,
            filename=filename,
            sheet_name=sheet_name or "Sheet1",
            headers=headers,
            rows=rows,
            template=template,
        )
        return (
            f"Successfully created Excel workbook '{res['filename']}' "
            f"with sheet '{res['sheet_name']}' "
            f"({res['total_rows']} rows, {res['total_columns']} columns)."
        )
    except (ValueError, PermissionError, OSError) as e:
        return f"Error creating workbook: {e}"
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return f"Error creating workbook: {e}"


async def handle_excel_read_range(
    bot: Any, args: dict, user_id: str, context: dict | None = None
) -> str:
    """Handler for excel_read_range AI tool."""
    if not isinstance(args, dict):
        args = {}

    filename = str(args.get("filename") or "").strip()
    if not filename:
        return "Error: 'filename' is required to read an Excel workbook."

    sheet_name = str(args.get("sheet_name") or "").strip() or None
    range_address = (
        str(args.get("range_address") or args.get("cell_range") or args.get("range") or "").strip()
        or None
    )

    max_rows_raw = args.get("max_rows")
    max_rows = int(max_rows_raw) if str(max_rows_raw or "").isdigit() else 50

    show_formulas_raw = args.get("show_formulas")
    show_formulas = (
        bool(show_formulas_raw)
        if not isinstance(show_formulas_raw, str)
        else show_formulas_raw.lower() in ("true", "1", "yes")
    )

    try:
        res = await asyncio.to_thread(
            read_range,
            filename=filename,
            sheet_name=sheet_name,
            cell_range=range_address,
            max_rows=max_rows,
            show_formulas=show_formulas,
        )
        parts = [
            f"### Workbook: `{filename}` (Sheet: `{res['sheet_name']}`, Range: `{res['range']}`)",
            res["table_markdown"],
        ]
        if res.get("truncated"):
            parts.append("*Note: Table output was truncated for Discord display.*")
        return "\n\n".join(parts)
    except (FileNotFoundError, ValueError, PermissionError, OSError) as e:
        return f"Error reading range: {e}"
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return f"Error reading range: {e}"


async def handle_excel_update_cells(
    bot: Any, args: dict, user_id: str, context: dict | None = None
) -> str:
    """Handler for excel_update_cells AI tool."""
    if not isinstance(args, dict):
        args = {}

    filename = str(args.get("filename") or "").strip()
    if not filename:
        return "Error: 'filename' is required to update an Excel workbook."

    sheet_name = str(args.get("sheet_name") or "").strip() or None
    start_cell = str(args.get("start_cell") or "").strip() or None
    clear_range = (
        str(args.get("clear_range") or args.get("clear_range_str") or "").strip() or None
    )

    updates = args.get("updates")
    rows = args.get("rows")

    if not updates and not (start_cell and rows) and not clear_range:
        return "Error: No cell updates, rows, or clear_range provided."

    if updates is not None and not isinstance(updates, list):
        return "Error: 'updates' must be a list of cell update objects."

    if rows is not None and not isinstance(rows, list):
        return "Error: 'rows' must be a 2D list of row values."

    try:
        res = await asyncio.to_thread(
            write_cells,
            filename=filename,
            sheet_name=sheet_name,
            updates=updates,
            start_cell=start_cell,
            rows=rows,
            clear_range=clear_range,
        )
        return (
            f"Successfully updated '{filename}' (Sheet: '{res['sheet_name']}'): "
            f"{res['total_modifications']} modifications "
            f"({res['cleared_cells']} cells cleared, "
            f"{res['matrix_cells_written']} matrix cells written, "
            f"{res['individual_cells_updated']} individual cells updated)."
        )
    except (FileNotFoundError, ValueError, PermissionError, OSError) as e:
        return f"Error updating cells: {e}"
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return f"Error updating cells: {e}"


async def handle_excel_manage_sheet(
    bot: Any, args: dict, user_id: str, context: dict | None = None
) -> str:
    """Handler for excel_manage_sheet AI tool."""
    if not isinstance(args, dict):
        args = {}

    filename = str(args.get("filename") or "").strip()
    if not filename:
        return "Error: 'filename' is required to manage sheets."

    action = str(args.get("action") or "").strip().lower()
    if not action:
        return "Error: 'action' is required (valid actions: 'list', 'add', 'rename', 'delete', 'set_active')."

    sheet_name = str(args.get("sheet_name") or "").strip() or None
    new_name = str(args.get("new_name") or "").strip() or None

    try:
        res = await asyncio.to_thread(
            manage_sheet,
            filename=filename,
            action=action,
            sheet_name=sheet_name,
            new_name=new_name,
        )
        if action == "list":
            sheets_fmt = ", ".join(
                f"`{s}`" + (" (Active)" if s == res.get("active_sheet") else "")
                for s in res.get("sheets", [])
            )
            return f"Worksheets in '{filename}' ({res['total_sheets']}): {sheets_fmt}"
        else:
            return res.get("message") or f"Successfully performed sheet action '{action}' on '{filename}'."
    except (FileNotFoundError, ValueError, PermissionError, OSError) as e:
        return f"Error managing sheet: {e}"
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return f"Error managing sheet: {e}"


async def handle_excel_export_workbook(
    bot: Any, args: dict, user_id: str, context: dict | None = None
) -> str:
    """Handler for excel_export_workbook AI tool."""
    if not isinstance(args, dict):
        args = {}

    filename = str(args.get("filename") or "").strip()
    if not filename:
        return "Error: 'filename' is required to export workbook."

    as_filename = str(args.get("as_filename") or "").strip() or None

    try:
        actual_filename, file_bytes = await asyncio.to_thread(get_workbook_bytes, filename)
    except (FileNotFoundError, ValueError, PermissionError, OSError) as e:
        return f"Error exporting workbook: {e}"
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return f"Error exporting workbook: {e}"

    target_name = as_filename if as_filename else actual_filename
    if not target_name.lower().endswith(".xlsx"):
        target_name += ".xlsx"

    if context is not None and isinstance(context, dict):
        out_files = context.setdefault("out_files", [])
        out_files.append(
            {
                "filename": target_name,
                "bytes": file_bytes,
                "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            }
        )
        return f"Successfully exported '{actual_filename}' as '{target_name}' and attached it to the chat message."
    else:
        return (
            f"Workbook '{actual_filename}' is ready ({len(file_bytes)} bytes), "
            f"but output context was unavailable to attach the file."
        )


def _list_workbooks_sync(query: str | None = None) -> list[dict[str, Any]]:
    """Synchronously scans data/excel/ and retrieves workbook metadata."""
    excel_dir = get_excel_dir()
    if not excel_dir.exists():
        return []

    results: list[dict[str, Any]] = []
    q_lower = query.lower() if query else None

    for item in excel_dir.iterdir():
        if item.is_file() and item.suffix.lower() == ".xlsx" and not item.name.startswith("~$"):
            if q_lower and q_lower not in item.name.lower():
                continue
            try:
                stat = item.stat()
                size_kb = round(stat.st_size / 1024, 1)
                mtime_dt = datetime.datetime.fromtimestamp(stat.st_mtime, tz=datetime.timezone.utc)
                mtime_str = mtime_dt.strftime("%Y-%m-%d %H:%M UTC")
            except OSError:
                continue

            try:
                sheets = list_sheets(item)
            except Exception:
                sheets = []

            results.append(
                {
                    "filename": item.name,
                    "size_kb": size_kb,
                    "modified": mtime_str,
                    "sheets": sheets,
                }
            )

    results.sort(key=lambda x: x["filename"].lower())
    return results


async def handle_excel_list_workbooks(
    bot: Any, args: dict, user_id: str, context: dict | None = None
) -> str:
    """Handler for excel_list_workbooks AI tool."""
    if not isinstance(args, dict):
        args = {}

    query = str(args.get("query") or "").strip() or None

    try:
        files = await asyncio.to_thread(_list_workbooks_sync, query)
        if not files:
            return (
                f"No Excel workbooks found in storage matching '{query}'."
                if query
                else "No Excel workbooks found in storage."
            )

        lines = [f"### Available Excel Workbooks ({len(files)}):"]
        for f in files:
            sheets_str = (
                ", ".join(f"`{s}`" for s in f["sheets"]) if f["sheets"] else "*(unknown)*"
            )
            lines.append(
                f"- **`{f['filename']}`** ({f['size_kb']} KB, modified {f['modified']}) — Sheets: {sheets_str}"
            )
        return "\n".join(lines)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return f"Error listing workbooks: {e}"
