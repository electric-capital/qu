"""REST API endpoints for inspecting project SQLite databases."""

import logging

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query

from chat.auth import get_current_user_cookie_or_apikey_checked
from chat.conversation_access import require_owned_project
from chat.storage import ChatStorage

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/app/api",
    tags=["project_db"],
)


@router.get("/projects/{project_id}/tables")
async def list_project_tables(
    project_id: str,
    user: dict = Depends(get_current_user_cookie_or_apikey_checked),
):
    """List all tables in a project's SQLite database."""
    user_id = user["id"]

    await require_owned_project(user_id, project_id)
    db_path = ChatStorage.get_project_db_path(project_id)

    # DB is created lazily; if it doesn't exist yet, return empty list
    if not db_path.exists():
        return {"tables": []}

    try:
        async with aiosqlite.connect(str(db_path), timeout=10) as conn:
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA busy_timeout=5000")

            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
            rows = await cursor.fetchall()

        return {"tables": [{"name": row[0]} for row in rows]}
    except aiosqlite.Error as e:
        logger.error("Failed to list tables for project %s: %s", project_id, e)
        raise HTTPException(
            status_code=500,
            detail={"error": "db_error", "message": f"Database error: {e}"},
        )


@router.get("/projects/{project_id}/tables/{table_name}")
async def get_project_table_data(
    project_id: str,
    table_name: str,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    sort_by: str | None = Query(default=None),
    sort_dir: str = Query(default="asc", pattern="^(asc|desc)$"),
    user: dict = Depends(get_current_user_cookie_or_apikey_checked),
):
    """Get rows from a table in a project's SQLite database.

    Optional ``sort_by`` is a column name validated against the table's
    schema via ``PRAGMA table_info``; unknown columns return HTTP 400.
    ``sort_dir`` accepts only ``asc`` or ``desc`` (default ``asc``); SQLite's
    default NULL ordering applies (NULLs first for ASC, last for DESC).
    """
    user_id = user["id"]

    await require_owned_project(user_id, project_id)
    db_path = ChatStorage.get_project_db_path(project_id)

    if not db_path.exists():
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": "Project database does not exist"},
        )

    try:
        async with aiosqlite.connect(str(db_path), timeout=10) as conn:
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA busy_timeout=5000")

            # Validate table name exists in sqlite_master (prevents SQL injection)
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
                (table_name,),
            )
            if not await cursor.fetchone():
                raise HTTPException(
                    status_code=404,
                    detail={"error": "not_found", "message": f"Table '{table_name}' not found"},
                )

            # Build optional ORDER BY clause. sort_by is whitelisted against
            # the live table schema before being interpolated into SQL.
            order_clause = ""
            if sort_by is not None:
                # PRAGMA table_info does not accept ? placeholders, but
                # table_name has already been validated above.
                pragma_cursor = await conn.execute(
                    f'PRAGMA table_info("{table_name}")'
                )
                valid_columns = {row[1] for row in await pragma_cursor.fetchall()}
                if sort_by not in valid_columns:
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "error": "invalid_sort",
                            "message": f"Unknown column '{sort_by}'",
                        },
                    )
                direction_sql = "DESC" if sort_dir == "desc" else "ASC"
                order_clause = f' ORDER BY "{sort_by}" {direction_sql}'

            # Get total row count (independent of ordering)
            count_cursor = await conn.execute(
                f'SELECT COUNT(*) FROM "{table_name}"'
            )
            total_rows = (await count_cursor.fetchone())[0]

            # Fetch rows
            data_cursor = await conn.execute(
                f'SELECT * FROM "{table_name}"{order_clause} LIMIT ? OFFSET ?',
                (limit, offset),
            )
            columns = [desc[0] for desc in data_cursor.description] if data_cursor.description else []
            rows = await data_cursor.fetchall()

        # Convert rows to lists and handle special types
        serialized_rows = []
        for row in rows:
            serialized_row = []
            for cell in row:
                if isinstance(cell, bytes):
                    serialized_row.append(f"[BLOB, {len(cell)} bytes]")
                else:
                    serialized_row.append(cell)
            serialized_rows.append(serialized_row)

        return {
            "table_name": table_name,
            "columns": columns,
            "rows": serialized_rows,
            "total_rows": total_rows,
            "limit": limit,
            "offset": offset,
            "sort_by": sort_by,
            "sort_dir": sort_dir if sort_by is not None else None,
        }
    except HTTPException:
        raise
    except aiosqlite.Error as e:
        logger.error("Failed to read table %s for project %s: %s", table_name, project_id, e)
        raise HTTPException(
            status_code=500,
            detail={"error": "db_error", "message": f"Database error: {e}"},
        )


@router.delete("/projects/{project_id}/tables/{table_name}")
async def delete_project_table(
    project_id: str,
    table_name: str,
    user: dict = Depends(get_current_user_cookie_or_apikey_checked),
):
    """Drop a table from a project's SQLite database."""
    user_id = user["id"]

    await require_owned_project(user_id, project_id)
    db_path = ChatStorage.get_project_db_path(project_id)

    if not db_path.exists():
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": "Project database does not exist"},
        )

    try:
        async with aiosqlite.connect(str(db_path), timeout=10) as conn:
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA busy_timeout=5000")

            # Validate table name exists in sqlite_master (prevents SQL injection)
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
                (table_name,),
            )
            if not await cursor.fetchone():
                raise HTTPException(
                    status_code=404,
                    detail={"error": "not_found", "message": f"Table '{table_name}' not found"},
                )

            await conn.execute(f'DROP TABLE IF EXISTS "{table_name}"')
            await conn.commit()

        return {"status": "ok", "table_name": table_name}
    except HTTPException:
        raise
    except aiosqlite.Error as e:
        logger.error("Failed to delete table %s for project %s: %s", table_name, project_id, e)
        raise HTTPException(
            status_code=500,
            detail={"error": "db_error", "message": f"Database error: {e}"},
        )
