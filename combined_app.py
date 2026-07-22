from __future__ import annotations

import asyncio
import logging
import os
import threading

from waitress import serve

from main import main as run_telegram_bot
from web_app import app


def run_website() -> None:
    """
    Запускает сайт в отдельном потоке.
    Render передаёт порт через переменную PORT.
    """
    port = int(os.getenv("PORT", "10000"))
    logging.info("Website is starting on port %s", port)
    serve(
        app,
        host="0.0.0.0",
        port=port,
        threads=6,
        channel_timeout=120,
    )


async def run_all() -> None:
    website_thread = threading.Thread(
        target=run_website,
        name="material-accountant-website",
        daemon=True,
    )
    website_thread.start()

    # Telegram polling работает в основном потоке.
    await run_telegram_bot()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    try:
        asyncio.run(run_all())
    except KeyboardInterrupt:
        logging.info("Service stopped.")
