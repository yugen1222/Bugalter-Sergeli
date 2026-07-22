from __future__ import annotations

import calendar
import os
import sqlite3
from datetime import datetime
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-this-secret")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("RENDER", "").lower() == "true",
)

DB_PATH = Path(__file__).with_name("material_accountant.db")
WEB_USERNAME = os.getenv("WEB_USERNAME", "admin")
WEB_PASSWORD = os.getenv("WEB_PASSWORD", "change_me")


def query(sql: str, params=()):
    if not DB_PATH.exists():
        return []
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def safe_next_url(value: str | None) -> str:
    if not value:
        return url_for("dashboard")
    parsed = urlparse(value)
    if parsed.netloc or not parsed.path.startswith("/"):
        return url_for("dashboard")
    return value


def requires_auth(func):
    @wraps(func)
    def wrapped(*args, **kwargs):
        if not session.get("web_authenticated"):
            return redirect(
                url_for(
                    "login",
                    next=request.full_path if request.query_string else request.path,
                )
            )
        return func(*args, **kwargs)

    return wrapped


def month_dates(value: str | None) -> tuple[str, str, str]:
    now = datetime.now()
    month = value or f"{now.year:04d}-{now.month:02d}"
    try:
        year, month_number = [int(x) for x in month.split("-")]
        if not 1 <= month_number <= 12:
            raise ValueError
    except (ValueError, AttributeError):
        year, month_number = now.year, now.month
        month = f"{year:04d}-{month_number:02d}"

    last_day = calendar.monthrange(year, month_number)[1]
    return (
        month,
        f"{year:04d}-{month_number:02d}-01",
        f"{year:04d}-{month_number:02d}-{last_day:02d}",
    )


@app.route("/health")
def health():
    return {"status": "ok"}, 200


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("web_authenticated"):
        return redirect(url_for("dashboard"))

    next_url = safe_next_url(request.args.get("next") or request.form.get("next"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if username == WEB_USERNAME and password == WEB_PASSWORD:
            session.clear()
            session["web_authenticated"] = True
            session["web_username"] = username
            return redirect(next_url)

        flash("Неверный логин или пароль.", "error")

    return render_template("login.html", next_url=next_url)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@requires_auth
def dashboard():
    month, start, end = month_dates(request.args.get("month"))

    total_rows = query(
        """
        SELECT COUNT(DISTINCT dc.id) checks_count,
               COUNT(DISTINCT dc.check_date) days,
               SUM(CASE WHEN dc.alignment_done=1 THEN 1 ELSE 0 END)
                   aligned_checks,
               COUNT(mi.id) positions,
               COALESCE(SUM(mi.quantity),0) quantity,
               COALESCE(
                   SUM(CASE WHEN mi.status='unresolved' THEN 1 ELSE 0 END),
                   0
               ) unresolved
        FROM daily_checks dc
        LEFT JOIN minus_items mi ON mi.daily_check_id=dc.id
        WHERE dc.check_date BETWEEN ? AND ?
        """,
        (start, end),
    )
    totals = total_rows[0] if total_rows else {}

    products = query(
        """
        SELECT mi.item_name name,
               COUNT(*) cases,
               SUM(mi.quantity) quantity
        FROM daily_checks dc
        JOIN minus_items mi ON mi.daily_check_id=dc.id
        WHERE dc.check_date BETWEEN ? AND ?
        GROUP BY LOWER(mi.item_name)
        ORDER BY quantity DESC, cases DESC
        LIMIT 15
        """,
        (start, end),
    )

    reasons = query(
        """
        SELECT mi.reason name,
               COUNT(*) cases,
               SUM(mi.quantity) quantity
        FROM daily_checks dc
        JOIN minus_items mi ON mi.daily_check_id=dc.id
        WHERE dc.check_date BETWEEN ? AND ?
        GROUP BY mi.reason
        ORDER BY quantity DESC, cases DESC
        """,
        (start, end),
    )

    unresolved = query(
        """
        SELECT mi.id,
               dc.check_date,
               dc.employee_name,
               dc.shift,
               mi.item_name,
               mi.quantity,
               mi.comment
        FROM daily_checks dc
        JOIN minus_items mi ON mi.daily_check_id=dc.id
        WHERE dc.check_date BETWEEN ? AND ?
          AND mi.status='unresolved'
        ORDER BY dc.check_date DESC, mi.id DESC
        """,
        (start, end),
    )

    history = query(
        """
        SELECT dc.id,
               dc.check_date,
               dc.employee_name,
               dc.shift,
               dc.alignment_done,
               dc.completed,
               COUNT(mi.id) positions,
               COALESCE(SUM(mi.quantity),0) quantity,
               COALESCE(
                   SUM(CASE WHEN mi.status='unresolved' THEN 1 ELSE 0 END),
                   0
               ) unresolved
        FROM daily_checks dc
        LEFT JOIN minus_items mi ON mi.daily_check_id=dc.id
        WHERE dc.check_date BETWEEN ? AND ?
        GROUP BY dc.id
        ORDER BY dc.check_date DESC,
                 CASE dc.shift
                    WHEN '1 смена' THEN 1
                    WHEN '2 смена' THEN 2
                    WHEN '3 смена' THEN 3
                    ELSE 4
                 END,
                 dc.id DESC
        """,
        (start, end),
    )

    employees = query(
        """
        SELECT dc.employee_name name,
               COUNT(DISTINCT dc.id) checks,
               COUNT(mi.id) positions,
               COALESCE(SUM(mi.quantity),0) quantity
        FROM daily_checks dc
        LEFT JOIN minus_items mi ON mi.daily_check_id=dc.id
        WHERE dc.check_date BETWEEN ? AND ?
        GROUP BY dc.employee_name
        ORDER BY checks DESC, quantity DESC
        """,
        (start, end),
    )

    return render_template(
        "dashboard.html",
        month=month,
        totals=totals,
        products=products,
        reasons=reasons,
        unresolved=unresolved,
        history=history,
        employees=employees,
    )


@app.route("/check/<int:check_id>")
@requires_auth
def check_details(check_id: int):
    check_rows = query(
        "SELECT * FROM daily_checks WHERE id=?",
        (check_id,),
    )
    if not check_rows:
        return "Запись не найдена", 404

    items = query(
        """
        SELECT *
        FROM minus_items
        WHERE daily_check_id=?
        ORDER BY id
        """,
        (check_id,),
    )
    return render_template(
        "check_details.html",
        check=check_rows[0],
        items=items,
    )


@app.route("/product")
@requires_auth
def product_details():
    name = request.args.get("name", "").strip()
    if not name:
        return "Товар не указан", 400

    rows = query(
        """
        SELECT dc.check_date,
               dc.employee_name,
               dc.shift,
               mi.quantity,
               mi.reason,
               mi.responsible_name,
               mi.comment,
               mi.status
        FROM minus_items mi
        JOIN daily_checks dc ON dc.id=mi.daily_check_id
        WHERE LOWER(mi.item_name)=LOWER(?)
        ORDER BY dc.check_date DESC, mi.id DESC
        """,
        (name,),
    )
    return render_template(
        "product_details.html",
        product_name=name,
        rows=rows,
    )


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "10000")),
    )
