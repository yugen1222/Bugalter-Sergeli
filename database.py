from __future__ import annotations

from pathlib import Path
import json
from typing import Any

import aiosqlite

DB_PATH = Path(__file__).with_name("material_accountant.db")


async def _migrate_daily_checks(db: aiosqlite.Connection) -> None:
    """
    Старая версия разрешала только одну запись:
    employee_id + check_date.

    Новая версия хранит отдельно:
    employee_id + check_date + shift.

    Существующие записи и связанные minus_items сохраняются.
    """
    cursor = await db.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type='table' AND name='daily_checks'
        """
    )
    row = await cursor.fetchone()
    table_sql = (row[0] or "") if row else ""

    normalized = " ".join(table_sql.lower().split())
    old_unique = (
        "unique(employee_id, check_date)" in normalized
        and "unique(employee_id, check_date, shift)" not in normalized
    )

    if not old_unique:
        return

    await db.execute("PRAGMA foreign_keys = OFF")
    try:
        await db.executescript(
            """
            BEGIN IMMEDIATE;

            CREATE TABLE daily_checks_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_user_id INTEGER NOT NULL,
                employee_id INTEGER NOT NULL,
                employee_name TEXT NOT NULL,
                check_date TEXT NOT NULL,
                shift TEXT NOT NULL DEFAULT 'Общая проверка',
                alignment_done INTEGER NOT NULL DEFAULT 0,
                alignment_photo_file_id TEXT,
                has_minuses INTEGER,
                completed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(employee_id, check_date, shift)
            );

            INSERT INTO daily_checks_new(
                id,
                telegram_user_id,
                employee_id,
                employee_name,
                check_date,
                shift,
                alignment_done,
                alignment_photo_file_id,
                has_minuses,
                completed,
                created_at,
                updated_at
            )
            SELECT
                id,
                telegram_user_id,
                employee_id,
                employee_name,
                check_date,
                COALESCE(NULLIF(shift, ''), 'Общая проверка'),
                alignment_done,
                alignment_photo_file_id,
                has_minuses,
                completed,
                created_at,
                updated_at
            FROM daily_checks;

            DROP TABLE daily_checks;
            ALTER TABLE daily_checks_new RENAME TO daily_checks;

            COMMIT;
            """
        )
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.execute("PRAGMA foreign_keys = ON")


async def _migrate_minus_items(db: aiosqlite.Connection) -> None:
    """Добавляет новые поля без удаления старых данных."""
    cursor = await db.execute("PRAGMA table_info(minus_items)")
    columns = {row[1] for row in await cursor.fetchall()}
    if "countermeasures_json" not in columns:
        await db.execute(
            "ALTER TABLE minus_items "
            "ADD COLUMN countermeasures_json TEXT NOT NULL DEFAULT '[]'"
        )



async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys = ON")

        await db.executescript(
            """
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
                shift TEXT NOT NULL DEFAULT 'Общая проверка',
                alignment_done INTEGER NOT NULL DEFAULT 0,
                alignment_photo_file_id TEXT,
                has_minuses INTEGER,
                completed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(employee_id, check_date, shift)
            );

            CREATE TABLE IF NOT EXISTS minus_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                daily_check_id INTEGER NOT NULL,
                item_name TEXT NOT NULL,
                quantity REAL NOT NULL,
                reason_found INTEGER NOT NULL DEFAULT 0,
                reason TEXT NOT NULL,
                countermeasures_json TEXT NOT NULL DEFAULT '[]',
                responsible_name TEXT,
                comment TEXT,
                status TEXT NOT NULL DEFAULT 'unresolved',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(daily_check_id)
                    REFERENCES daily_checks(id)
                    ON DELETE CASCADE
            );
            """
        )

        await _migrate_daily_checks(db)
        await _migrate_minus_items(db)

        await db.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_checks_date
            ON daily_checks(check_date);

            CREATE INDEX IF NOT EXISTS idx_checks_employee
            ON daily_checks(employee_id);

            CREATE INDEX IF NOT EXISTS idx_checks_employee_date_shift
            ON daily_checks(employee_id, check_date, shift);

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
            "SELECT id, name FROM employees WHERE is_active=1 ORDER BY name"
        )
        return [dict(row) for row in await cur.fetchall()]


async def add_employee(name: str) -> int:
    clean = " ".join(name.strip().split())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO employees(name, is_active)
            VALUES (?, 1)
            ON CONFLICT(name) DO UPDATE SET is_active=1
            """,
            (clean,),
        )
        await db.commit()
        cur = await db.execute(
            "SELECT id FROM employees WHERE name=? COLLATE NOCASE",
            (clean,),
        )
        row = await cur.fetchone()
        if row is None:
            raise RuntimeError("Не удалось добавить сотрудника")
        return int(row[0])


async def deactivate_employee(employee_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE employees SET is_active=0 WHERE id=?",
            (employee_id,),
        )
        await db.commit()


async def set_user_employee(telegram_user_id: int, employee_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO user_profiles(telegram_user_id, employee_id)
            VALUES (?, ?)
            ON CONFLICT(telegram_user_id)
            DO UPDATE SET
                employee_id=excluded.employee_id,
                updated_at=CURRENT_TIMESTAMP
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
            JOIN employees e ON e.id=up.employee_id
            WHERE up.telegram_user_id=?
              AND e.is_active=1
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
    shift: str,
) -> tuple[int, bool]:
    """
    Возвращает (check_id, already_existed).

    Повторное открытие той же смены НЕ удаляет ранее внесённые позиции.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT id
            FROM daily_checks
            WHERE employee_id=? AND check_date=? AND shift=?
            """,
            (employee_id, check_date, shift),
        )
        existing = await cur.fetchone()

        if existing:
            await db.execute(
                """
                UPDATE daily_checks
                SET telegram_user_id=?,
                    employee_name=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (telegram_user_id, employee_name, existing[0]),
            )
            await db.commit()
            return int(existing[0]), True

        cur = await db.execute(
            """
            INSERT INTO daily_checks(
                telegram_user_id,
                employee_id,
                employee_name,
                check_date,
                shift
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                telegram_user_id,
                employee_id,
                employee_name,
                check_date,
                shift,
            ),
        )
        await db.commit()
        return int(cur.lastrowid), False


async def reset_daily_check(daily_check_id: int) -> None:
    """
    Используется только при явном подтверждении «Начать заново».
    Автоматически при открытии смены больше не вызывается.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM minus_items WHERE daily_check_id=?",
            (daily_check_id,),
        )
        await db.execute(
            """
            UPDATE daily_checks
            SET alignment_done=0,
                alignment_photo_file_id=NULL,
                has_minuses=NULL,
                completed=0,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (daily_check_id,),
        )
        await db.commit()


async def set_shift(daily_check_id: int, shift: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE daily_checks
            SET shift=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
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
            SET alignment_done=?,
                alignment_photo_file_id=COALESCE(?, alignment_photo_file_id),
                completed=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (int(done), photo_file_id, int(completed), daily_check_id),
        )
        await db.commit()


async def set_has_minuses(
    daily_check_id: int,
    value: bool,
    completed: bool = False,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE daily_checks
            SET has_minuses=?,
                completed=?,
                updated_at=CURRENT_TIMESTAMP
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
    countermeasures: list[str],
    responsible_name: str | None,
    comment: str | None,
    status: str,
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO minus_items(
                daily_check_id,
                item_name,
                quantity,
                reason_found,
                reason,
                countermeasures_json,
                responsible_name,
                comment,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                daily_check_id,
                item_name.strip(),
                quantity,
                int(reason_found),
                reason,
                json.dumps(countermeasures, ensure_ascii=False),
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
            """
            UPDATE daily_checks
            SET completed=1, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (daily_check_id,),
        )
        await db.commit()


async def get_check_summary(check_id: int) -> dict[str, Any] | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT dc.*,
                   COUNT(mi.id) AS positions_count,
                   COALESCE(SUM(mi.quantity), 0) AS quantity_sum
            FROM daily_checks dc
            LEFT JOIN minus_items mi ON mi.daily_check_id=dc.id
            WHERE dc.id=?
            GROUP BY dc.id
            """,
            (check_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_daily_reports(
    employee_id: int,
    check_date: str,
) -> list[dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT *
            FROM daily_checks
            WHERE employee_id=? AND check_date=?
            ORDER BY CASE shift
                WHEN '1 смена' THEN 1
                WHEN '2 смена' THEN 2
                WHEN '3 смена' THEN 3
                ELSE 4
            END,
            id
            """,
            (employee_id, check_date),
        )
        checks = [dict(row) for row in await cur.fetchall()]

        result: list[dict[str, Any]] = []
        for check in checks:
            cur = await db.execute(
                """
                SELECT *
                FROM minus_items
                WHERE daily_check_id=?
                ORDER BY id
                """,
                (check["id"],),
            )
            result.append(
                {
                    "check": check,
                    "items": [dict(row) for row in await cur.fetchall()],
                }
            )
        return result


async def get_daily_report(
    employee_id: int,
    check_date: str,
) -> dict[str, Any] | None:
    reports = await get_daily_reports(employee_id, check_date)
    return reports[0] if reports else None


async def get_period_rows(
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
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
                mi.countermeasures_json,
                mi.responsible_name,
                mi.comment,
                mi.status
            FROM daily_checks dc
            LEFT JOIN minus_items mi ON mi.daily_check_id=dc.id
            WHERE dc.check_date BETWEEN ? AND ?
            ORDER BY dc.check_date, dc.employee_name, dc.shift, mi.id
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
        employee_filter = " AND dc.employee_id=?"
        params.append(employee_id)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        cur = await db.execute(
            f"""
            SELECT
                COUNT(DISTINCT dc.id) total_days,
                SUM(CASE WHEN dc.alignment_done=1 THEN 1 ELSE 0 END)
                    aligned_days,
                SUM(CASE WHEN dc.completed=1 THEN 1 ELSE 0 END)
                    completed_days
            FROM daily_checks dc
            WHERE dc.check_date BETWEEN ? AND ?
            {employee_filter}
            """,
            params,
        )
        totals = dict(await cur.fetchone())

        cur = await db.execute(
            f"""
            SELECT
                COUNT(mi.id) positions_count,
                COALESCE(SUM(mi.quantity), 0) quantity_sum,
                COALESCE(
                    SUM(CASE WHEN mi.status='unresolved' THEN 1 ELSE 0 END),
                    0
                ) unresolved_count
            FROM daily_checks dc
            LEFT JOIN minus_items mi ON mi.daily_check_id=dc.id
            WHERE dc.check_date BETWEEN ? AND ?
            {employee_filter}
            """,
            params,
        )
        minus_totals = dict(await cur.fetchone())

        cur = await db.execute(
            f"""
            SELECT
                mi.item_name,
                COUNT(*) cases_count,
                SUM(mi.quantity) quantity_sum
            FROM daily_checks dc
            JOIN minus_items mi ON mi.daily_check_id=dc.id
            WHERE dc.check_date BETWEEN ? AND ?
            {employee_filter}
            GROUP BY LOWER(mi.item_name)
            ORDER BY quantity_sum DESC, cases_count DESC
            LIMIT 15
            """,
            params,
        )
        top_items = [dict(row) for row in await cur.fetchall()]

        cur = await db.execute(
            f"""
            SELECT
                mi.reason,
                COUNT(*) cases_count,
                SUM(mi.quantity) quantity_sum
            FROM daily_checks dc
            JOIN minus_items mi ON mi.daily_check_id=dc.id
            WHERE dc.check_date BETWEEN ? AND ?
            {employee_filter}
            GROUP BY mi.reason
            ORDER BY cases_count DESC
            """,
            params,
        )
        reasons = [dict(row) for row in await cur.fetchall()]

        cur = await db.execute(
            f"""
            SELECT
                dc.shift,
                COUNT(mi.id) cases_count,
                COALESCE(SUM(mi.quantity), 0) quantity_sum
            FROM daily_checks dc
            LEFT JOIN minus_items mi ON mi.daily_check_id=dc.id
            WHERE dc.check_date BETWEEN ? AND ?
            {employee_filter}
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
            WHERE e.is_active=1
              AND NOT EXISTS (
                  SELECT 1
                  FROM daily_checks dc
                  WHERE dc.employee_id=e.id
                    AND dc.check_date=?
                    AND dc.completed=1
              )
            ORDER BY e.name
            """,
            (check_date,),
        )
        return [dict(row) for row in await cur.fetchall()]
