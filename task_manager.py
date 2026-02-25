"""Airdrop Task Manager & Progress Tracker."""

from __future__ import annotations

import asyncio
import os
import sqlite3
from datetime import datetime, timedelta

DB_PATH = "alpha_hunter.db"


def init_task_db() -> None:
    """Initialize required tables and seed default recurring tasks."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_name TEXT NOT NULL,
                task_description TEXT NOT NULL,
                frequency_days INTEGER NOT NULL,
                last_completed TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS wallet_stats (
                wallet_address TEXT NOT NULL,
                network TEXT NOT NULL,
                balance REAL DEFAULT 0,
                points_accumulated REAL DEFAULT 0,
                PRIMARY KEY (wallet_address, network)
            )
            """
        )
        conn.commit()

    _seed_default_tasks()


def _seed_default_tasks() -> None:
    defaults = [
        ("MetaMask", "Perform 1 Swap", 7),
        ("Polymarket", "Place 1 Prediction", 1),
        ("Aztec", "Privacy Transfer", 14),
    ]

    with sqlite3.connect(DB_PATH) as conn:
        for project, description, frequency in defaults:
            exists = conn.execute(
                """
                SELECT 1 FROM tasks
                WHERE project_name = ? AND task_description = ? AND frequency_days = ?
                """,
                (project, description, frequency),
            ).fetchone()
            if not exists:
                conn.execute(
                    """
                    INSERT INTO tasks (project_name, task_description, frequency_days, last_completed)
                    VALUES (?, ?, ?, ?)
                    """,
                    (project, description, frequency, None),
                )
        conn.commit()


def add_task(project: str, description: str, frequency: int) -> int:
    """Manually add a recurring task and return created task id."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """
            INSERT INTO tasks (project_name, task_description, frequency_days, last_completed)
            VALUES (?, ?, ?, ?)
            """,
            (project, description, int(frequency), None),
        )
        conn.commit()
        return int(cursor.lastrowid)


def _parse_last_completed(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def check_pending_tasks() -> list[dict]:
    """Return tasks where now - last_completed exceeds frequency_days (or never completed)."""
    now = datetime.utcnow()

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, project_name, task_description, frequency_days, last_completed
            FROM tasks
            ORDER BY project_name ASC
            """
        ).fetchall()

    pending: list[dict] = []
    for row in rows:
        freq = int(row["frequency_days"])
        last_completed = _parse_last_completed(row["last_completed"])

        if last_completed is None:
            pending.append(dict(row))
            continue

        if now - last_completed > timedelta(days=freq):
            pending.append(dict(row))

    return pending


def mark_task_done(task_id: int) -> None:
    """Update the task's last_completed timestamp to now."""
    now_iso = datetime.utcnow().isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE tasks SET last_completed = ? WHERE id = ?", (now_iso, int(task_id)))
        conn.commit()


def _todo_message(tasks: list[dict]) -> str:
    if not tasks:
        return "📝 TODO TODAY: No pending tasks."

    lines = []
    for idx, task in enumerate(tasks, start=1):
        lines.append(f"{idx}. {task['task_description']} ({task['project_name']})")
    return "📝 TODO TODAY: " + " ".join(lines)


def _telegram_preview_only() -> bool:
    return os.getenv("TELEGRAM_PREVIEW_ONLY", "").strip().lower() in {"1", "true", "yes", "on"}


def _get_bot_and_chat_id():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("CHAT_ID")
    if not token or not chat_id:
        return None, None

    from aiogram import Bot

    return Bot(token=token), chat_id


async def send_daily_todo() -> None:
    """Send pending tasks to Telegram. Intended to run daily at 9:00 AM."""
    tasks = check_pending_tasks()
    message = _todo_message(tasks)

    if _telegram_preview_only():
        print("[WARN] TELEGRAM_PREVIEW_ONLY enabled. Preview only (no message sent):")
        print(message)
        return

    bot, chat_id = _get_bot_and_chat_id()
    if bot is None or chat_id is None:
        print("[WARN] TELEGRAM_BOT_TOKEN/CHAT_ID not set. Preview only (no message sent):")
        print(message)
        return

    try:
        await bot.send_message(chat_id=chat_id, text=message)
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] Failed to send daily TODO ({exc}). Preview output:")
        print(message)
    finally:
        await bot.session.close()


async def run_daily_scheduler() -> None:
    """Loop forever and send TODO list every day at 9:00 AM local time."""
    while True:
        now = datetime.now()
        target = now.replace(hour=9, minute=0, second=0, microsecond=0)
        if now >= target:
            target = target + timedelta(days=1)
        sleep_seconds = (target - now).total_seconds()
        await asyncio.sleep(sleep_seconds)
        await send_daily_todo()


async def main() -> None:
    init_task_db()

    if os.getenv("TASK_MANAGER_RUN_ONCE", "").strip().lower() in {"1", "true", "yes", "on"}:
        await send_daily_todo()
        return

    await run_daily_scheduler()


if __name__ == "__main__":
    asyncio.run(main())
