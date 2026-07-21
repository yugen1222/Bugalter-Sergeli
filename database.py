\
from __future__ import annotations

import aiosqlite
from datetime import date
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).with_name("material_accountant.db")


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(
            """
            PRAGMA foreign_keys = ON;

            CREATE TABLE IF NOT EXISTS daily_checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                user_name TEXT,
                check_date TEXT NOT NULL,
                alignment_done INTEGER NOT NULL DEFAULT 0,
                has_minuses INTEGER,
                completed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, check_date)
            );

            CREATE TABLE IF NOT EXISTS minus_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                daily_check_id INTEGER NOT NULL,
                item_name TEXT NOT NULL,
                quantity REAL NOT NULL,
                reason_found INTEGER NOT NULL DEFAULT 0,
                reason TEXT NOT NULL,
                comment TEXT,
                status TEXT NOT NULL DEFAULT 'unresolved',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(daily_check_id) REFERENCES daily_checks(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_daily_checks_date
            ON daily_checks(check_date);

            CREATE INDEX IF NOT EXISTS idx_minus_items_daily_check
            ON minus_items(daily_check_id);

            CREATE INDEX IF NOT EXISTS idx_minus_items_name
            ON minus_items(item_name);
            """
        )
        await db.commit()


async def get_or_create_daily_check(
    user_id: int, user_name: str, check_date: str
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO daily_checks(user_id, user_name, check_date)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, check_date)
            DO UPDATE SET
                user_name = excluded.user_name,
                updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, user_name, check_date),
        )
        await db.commit()

        cursor = await db.execute(
            """
            SELECT id
            FROM daily_checks
            WHERE user_id = ? AND check_date = ?
            """,
            (user_id, check_date),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("Не удалось создать ежедневную запись.")
        return int(row[0])


async def reset_daily_check(daily_check_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM minus_items WHERE daily_check_id = ?",
            (daily_check_id,),
        )
        await db.execute(
            """
            UPDATE daily_checks
            SET alignment_done = 0,
                has_minuses = NULL,
                completed = 0,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (daily_check_id,),
        )
        await db.commit()


async def set_alignment(
    daily_check_id: int, alignment_done: bool, completed: bool = False
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE daily_checks
            SET alignment_done = ?,
                completed = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (int(alignment_done), int(completed), daily_check_id),
        )
        await db.commit()


async def set_has_minuses(
    daily_check_id: int, has_minuses: bool, completed: bool = False
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE daily_checks
            SET has_minuses = ?,
                completed = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (int(has_minuses), int(completed), daily_check_id),
        )
        await db.commit()


async def add_minus_item(
    daily_check_id: int,
    item_name: str,
    quantity: float,
    reason_found: bool,
    reason: str,
    comment: str | None,
    status: str,
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO minus_items(
                daily_check_id,
                item_name,
                quantity,
                reason_found,
                reason,
                comment,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                daily_check_id,
                item_name.strip(),
                quantity,
                int(reason_found),
                reason,
                comment.strip() if comment else None,
                status,
            ),
        )
        await db.commit()
        return int(cursor.lastrowid)


async def finish_daily_check(daily_check_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE daily_checks
            SET completed = 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (daily_check_id,),
        )
        await db.commit()


async def get_daily_report(user_id: int, check_date: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        cursor = await db.execute(
            """
            SELECT *
            FROM daily_checks
            WHERE user_id = ? AND check_date = ?
            """,
            (user_id, check_date),
        )
        check = await cursor.fetchone()
        if check is None:
            return None

        cursor = await db.execute(
            """
            SELECT *
            FROM minus_items
            WHERE daily_check_id = ?
            ORDER BY id
            """,
            (check["id"],),
        )
        items = await cursor.fetchall()

        return {
            "check": dict(check),
            "items": [dict(item) for item in items],
        }


async def get_month_summary(
    user_id: int, month_start: str, month_end: str
) -> dict[str, Any]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        cursor = await db.execute(
            """
            SELECT
                COUNT(*) AS total_days,
                SUM(CASE WHEN alignment_done = 1 THEN 1 ELSE 0 END) AS aligned_days,
                SUM(CASE WHEN completed = 1 THEN 1 ELSE 0 END) AS completed_days
            FROM daily_checks
            WHERE user_id = ?
              AND check_date BETWEEN ? AND ?
            """,
            (user_id, month_start, month_end),
        )
        totals = dict(await cursor.fetchone())

        cursor = await db.execute(
            """
            SELECT
                COUNT(mi.id) AS positions_count,
                COALESCE(SUM(mi.quantity), 0) AS quantity_sum,
                SUM(CASE WHEN mi.status = 'unresolved' THEN 1 ELSE 0 END)
                    AS unresolved_count
            FROM minus_items mi
            JOIN daily_checks dc ON dc.id = mi.daily_check_id
            WHERE dc.user_id = ?
              AND dc.check_date BETWEEN ? AND ?
            """,
            (user_id, month_start, month_end),
        )
        minus_totals = dict(await cursor.fetchone())

        cursor = await db.execute(
            """
            SELECT
                mi.item_name,
                COUNT(*) AS cases_count,
                SUM(mi.quantity) AS quantity_sum
            FROM minus_items mi
            JOIN daily_checks dc ON dc.id = mi.daily_check_id
            WHERE dc.user_id = ?
              AND dc.check_date BETWEEN ? AND ?
            GROUP BY LOWER(mi.item_name)
            ORDER BY quantity_sum DESC, cases_count DESC
            LIMIT 10
            """,
            (user_id, month_start, month_end),
        )
        top_items = [dict(row) for row in await cursor.fetchall()]

        cursor = await db.execute(
            """
            SELECT
                mi.reason,
                COUNT(*) AS cases_count,
                SUM(mi.quantity) AS quantity_sum
            FROM minus_items mi
            JOIN daily_checks dc ON dc.id = mi.daily_check_id
            WHERE dc.user_id = ?
              AND dc.check_date BETWEEN ? AND ?
            GROUP BY mi.reason
            ORDER BY cases_count DESC
            """,
            (user_id, month_start, month_end),
        )
        reasons = [dict(row) for row in await cursor.fetchall()]

        return {
            "totals": totals,
            "minus_totals": minus_totals,
            "top_items": top_items,
            "reasons": reasons,
        }
