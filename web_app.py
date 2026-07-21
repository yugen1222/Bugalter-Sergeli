\
from __future__ import annotations

import calendar
import os
import sqlite3
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import Flask, Response, render_template, request
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-this")
DB_PATH = Path(__file__).with_name("material_accountant.db")
WEB_USERNAME = os.getenv("WEB_USERNAME", "admin")
WEB_PASSWORD = os.getenv("WEB_PASSWORD", "change_me")


def check_auth(username: str, password: str) -> bool:
    return username == WEB_USERNAME and password == WEB_PASSWORD


def authenticate() -> Response:
    return Response(
        "Требуется авторизация",
        401,
        {"WWW-Authenticate": 'Basic realm="Material Dashboard"'},
    )


def requires_auth(func):
    @wraps(func)
    def wrapped(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return func(*args, **kwargs)
    return wrapped


def query(sql: str, params=()):
    if not DB_PATH.exists():
        return []
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(x) for x in conn.execute(sql, params).fetchall()]


@app.route("/")
@requires_auth
def dashboard():
    now = datetime.now()
    month = request.args.get("month", f"{now.year:04d}-{now.month:02d}")
    year, month_number = [int(x) for x in month.split("-")]
    last_day = calendar.monthrange(year, month_number)[1]
    start = f"{year:04d}-{month_number:02d}-01"
    end = f"{year:04d}-{month_number:02d}-{last_day:02d}"

    totals = query(
        """
        SELECT COUNT(DISTINCT dc.id) days,
               COUNT(mi.id) positions,
               COALESCE(SUM(mi.quantity),0) quantity,
               SUM(CASE WHEN mi.status='unresolved' THEN 1 ELSE 0 END) unresolved
        FROM daily_checks dc
        LEFT JOIN minus_items mi ON mi.daily_check_id=dc.id
        WHERE dc.check_date BETWEEN ? AND ?
        """,
        (start, end),
    )[0] if DB_PATH.exists() else {}

    products = query(
        """
        SELECT mi.item_name name, COUNT(*) cases, SUM(mi.quantity) quantity
        FROM daily_checks dc
        JOIN minus_items mi ON mi.daily_check_id=dc.id
        WHERE dc.check_date BETWEEN ? AND ?
        GROUP BY LOWER(mi.item_name)
        ORDER BY quantity DESC LIMIT 15
        """,
        (start, end),
    )

    reasons = query(
        """
        SELECT mi.reason name, COUNT(*) cases, SUM(mi.quantity) quantity
        FROM daily_checks dc
        JOIN minus_items mi ON mi.daily_check_id=dc.id
        WHERE dc.check_date BETWEEN ? AND ?
        GROUP BY mi.reason
        ORDER BY cases DESC
        """,
        (start, end),
    )

    employees = query(
        """
        SELECT dc.employee_name name, COUNT(mi.id) positions,
               COALESCE(SUM(mi.quantity),0) quantity
        FROM daily_checks dc
        LEFT JOIN minus_items mi ON mi.daily_check_id=dc.id
        WHERE dc.check_date BETWEEN ? AND ?
        GROUP BY dc.employee_name
        ORDER BY quantity DESC
        """,
        (start, end),
    )

    return render_template(
        "dashboard.html",
        month=month,
        totals=totals,
        products=products,
        reasons=reasons,
        employees=employees,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
