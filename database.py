\
from __future__ import annotations

import aiosqlite
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).with_name("material_accountant.db")


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(
            """
            PRAGMA foreign_keys = ON;

            CREATE TABLE IF NOT EXISTS employees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS user_profiles (
                telegram_user_id INTEGER PRIMARY KEY,
                employee_id INTEGER NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(employee_id) REFERENCES employees(id)
            );

            CREATE TABLE IF NOT EXISTS daily_checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_user_id INTEGER NOT NULL,
                employee_id INTEGER NOT NULL,
                employee_name TEXT NOT NULL,
                check_date TEXT NOT NULL,
                shift TEXT,
                alignment_done INTEGER NOT NULL DEFAULT 0,
                alignment_photo_file_id TEXT,
                has_minuses INTEGER,
                completed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(employee_id, check_date)
            );

            CREATE TABLE IF NOT EXISTS minus_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                daily_check_id INTEGER NOT NULL,
                item_name TEXT NOT NULL,
                quantity REAL NOT NULL,
                reason_found INTEGER NOT NULL DEFAULT 0,
                reason TEXT NOT NULL,
                responsible_name TEXT,
                comment TEXT,
                status TEXT NOT NULL DEFAULT 'unresolved',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(daily_check_id) REFERENCES daily_checks(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_checks_date
            ON daily_checks(check_date);

            CREATE INDEX IF NOT EXISTS idx_checks_employee
            ON daily_checks(employee_id);

            CREATE INDEX IF NOT EXISTS idx_minus_daily
            ON minus_items(daily_check_id);

            CREATE INDEX IF NOT EXISTS idx_minus_name
            ON minus_items(item_name);
            """
        )
        await db.commit()


async def list_employees() -> list[dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, name FROM employees WHERE is_active = 1 ORDER BY name"
        )
        return [dict(row) for row in await cur.fetchall()]


async def add_employee(name: str) -> int:
    clean = " ".join(name.strip().split())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO employees(name, is_active)
            VALUES (?, 1)
            ON CONFLICT(name) DO UPDATE SET is_active = 1
            """,
            (clean,),
        )
        await db.commit()
        cur = await db.execute(
            "SELECT id FROM employees WHERE name = ? COLLATE NOCASE", (clean,)
        )
        row = await cur.fetchone()
        return int(row[0])


async def deactivate_employee(employee_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE employees SET is_active = 0 WHERE id = ?", (employee_id,)
        )
        await db.commit()


async def set_user_employee(telegram_user_id: int, employee_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO user_profiles(telegram_user_id, employee_id)
            VALUES (?, ?)
            ON CONFLICT(telegram_user_id)
            DO UPDATE SET employee_id = excluded.employee_id,
                          updated_at = CURRENT_TIMESTAMP
            """,
            (telegram_user_id, employee_id),
        )
        await db.commit()


async def get_user_employee(telegram_user_id: int) -> dict[str, Any] | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT e.id, e.name
            FROM user_profiles up
            JOIN employees e ON e.id = up.employee_id
            WHERE up.telegram_user_id = ? AND e.is_active = 1
            """,
            (telegram_user_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_or_create_daily_check(
    telegram_user_id: int,
    employee_id: int,
    employee_name: str,
    check_date: str,
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO daily_checks(
                telegram_user_id, employee_id, employee_name, check_date
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(employee_id, check_date)
            DO UPDATE SET telegram_user_id = excluded.telegram_user_id,
                          employee_name = excluded.employee_name,
                          updated_at = CURRENT_TIMESTAMP
            """,
            (telegram_user_id, employee_id, employee_name, check_date),
        )
        await db.commit()
        cur = await db.execute(
            """
            SELECT id FROM daily_checks
            WHERE employee_id = ? AND check_date = ?
            """,
            (employee_id, check_date),
        )
        row = await cur.fetchone()
        if not row:
            raise RuntimeError("Не удалось создать запись дня")
        return int(row[0])


async def reset_daily_check(daily_check_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM minus_items WHERE daily_check_id = ?", (daily_check_id,))
        await db.execute(
            """
            UPDATE daily_checks
            SET shift = NULL,
                alignment_done = 0,
                alignment_photo_file_id = NULL,
                has_minuses = NULL,
                completed = 0,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (daily_check_id,),
        )
        await db.commit()


async def set_shift(daily_check_id: int, shift: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE daily_checks SET shift = ?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (shift, daily_check_id),
        )
        await db.commit()


async def set_alignment(
    daily_check_id: int,
    done: bool,
    photo_file_id: str | None = None,
    completed: bool = False,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE daily_checks
            SET alignment_done = ?,
                alignment_photo_file_id = COALESCE(?, alignment_photo_file_id),
                completed = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (int(done), photo_file_id, int(completed), daily_check_id),
        )
        await db.commit()


async def set_has_minuses(daily_check_id: int, value: bool, completed: bool = False) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE daily_checks
            SET has_minuses=?, completed=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (int(value), int(completed), daily_check_id),
        )
        await db.commit()


async def add_minus_item(
    daily_check_id: int,
    item_name: str,
    quantity: float,
    reason_found: bool,
    reason: str,
    responsible_name: str | None,
    comment: str | None,
    status: str,
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO minus_items(
                daily_check_id, item_name, quantity, reason_found,
                reason, responsible_name, comment, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                daily_check_id,
                item_name.strip(),
                quantity,
                int(reason_found),
                reason,
                responsible_name.strip() if responsible_name else None,
                comment.strip() if comment else None,
                status,
            ),
        )
        await db.commit()
        return int(cur.lastrowid)


async def finish_daily_check(daily_check_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE daily_checks SET completed=1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (daily_check_id,),
        )
        await db.commit()


async def get_daily_report(employee_id: int, check_date: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM daily_checks WHERE employee_id=? AND check_date=?",
            (employee_id, check_date),
        )
        check = await cur.fetchone()
        if not check:
            return None
        cur = await db.execute(
            "SELECT * FROM minus_items WHERE daily_check_id=? ORDER BY id",
            (check["id"],),
        )
        return {
            "check": dict(check),
            "items": [dict(row) for row in await cur.fetchall()],
        }


async def get_period_rows(start_date: str, end_date: str) -> list[dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT
                dc.check_date,
                dc.employee_name,
                dc.shift,
                dc.alignment_done,
                dc.has_minuses,
                dc.completed,
                mi.item_name,
                mi.quantity,
                mi.reason,
                mi.responsible_name,
                mi.comment,
                mi.status
            FROM daily_checks dc
            LEFT JOIN minus_items mi ON mi.daily_check_id = dc.id
            WHERE dc.check_date BETWEEN ? AND ?
            ORDER BY dc.check_date, dc.employee_name, mi.id
            """,
            (start_date, end_date),
        )
        return [dict(row) for row in await cur.fetchall()]


async def get_month_summary(
    start_date: str,
    end_date: str,
    employee_id: int | None = None,
) -> dict[str, Any]:
    employee_filter = ""
    params: list[Any] = [start_date, end_date]
    if employee_id is not None:
        employee_filter = " AND dc.employee_id = ?"
        params.append(employee_id)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        cur = await db.execute(
            f"""
            SELECT
                COUNT(DISTINCT dc.id) total_days,
                SUM(CASE WHEN dc.alignment_done=1 THEN 1 ELSE 0 END) aligned_days,
                SUM(CASE WHEN dc.completed=1 THEN 1 ELSE 0 END) completed_days
            FROM daily_checks dc
            WHERE dc.check_date BETWEEN ? AND ? {employee_filter}
            """,
            params,
        )
        totals = dict(await cur.fetchone())

        cur = await db.execute(
            f"""
            SELECT
                COUNT(mi.id) positions_count,
                COALESCE(SUM(mi.quantity),0) quantity_sum,
                SUM(CASE WHEN mi.status='unresolved' THEN 1 ELSE 0 END) unresolved_count
            FROM daily_checks dc
            LEFT JOIN minus_items mi ON mi.daily_check_id=dc.id
            WHERE dc.check_date BETWEEN ? AND ? {employee_filter}
            """,
            params,
        )
        minus_totals = dict(await cur.fetchone())

        cur = await db.execute(
            f"""
            SELECT mi.item_name, COUNT(*) cases_count, SUM(mi.quantity) quantity_sum
            FROM daily_checks dc
            JOIN minus_items mi ON mi.daily_check_id=dc.id
            WHERE dc.check_date BETWEEN ? AND ? {employee_filter}
            GROUP BY LOWER(mi.item_name)
            ORDER BY quantity_sum DESC, cases_count DESC
            LIMIT 15
            """,
            params,
        )
        top_items = [dict(row) for row in await cur.fetchall()]

        cur = await db.execute(
            f"""
            SELECT mi.reason, COUNT(*) cases_count, SUM(mi.quantity) quantity_sum
            FROM daily_checks dc
            JOIN minus_items mi ON mi.daily_check_id=dc.id
            WHERE dc.check_date BETWEEN ? AND ? {employee_filter}
            GROUP BY mi.reason
            ORDER BY cases_count DESC
            """,
            params,
        )
        reasons = [dict(row) for row in await cur.fetchall()]

        cur = await db.execute(
            f"""
            SELECT dc.shift, COUNT(mi.id) cases_count, COALESCE(SUM(mi.quantity),0) quantity_sum
            FROM daily_checks dc
            LEFT JOIN minus_items mi ON mi.daily_check_id=dc.id
            WHERE dc.check_date BETWEEN ? AND ? {employee_filter}
            GROUP BY dc.shift
            ORDER BY cases_count DESC
            """,
            params,
        )
        shifts = [dict(row) for row in await cur.fetchall()]

        return {
            "totals": totals,
            "minus_totals": minus_totals,
            "top_items": top_items,
            "reasons": reasons,
            "shifts": shifts,
        }


async def get_missing_today(check_date: str) -> list[dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT e.id, e.name
            FROM employees e
            LEFT JOIN daily_checks dc
              ON dc.employee_id=e.id AND dc.check_date=?
            WHERE e.is_active=1
              AND (dc.id IS NULL OR dc.completed=0)
            ORDER BY e.name
            """,
            (check_date,),
        )
        return [dict(row) for row in await cur.fetchall()]
