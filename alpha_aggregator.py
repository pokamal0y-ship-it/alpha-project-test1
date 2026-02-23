"""Crypto Alpha Hunter: extraction, scoring, persistence, and Telegram alerts."""

import asyncio
import json
import os
import sqlite3
from datetime import datetime, timezone

VC_TIERS = {
    "tier_1": {
        "score": 10,
        "investors": [
            "Paradigm",
            "a16z Crypto",
            "Polychain Capital",
        ],
    },
    "tier_2": {
        "score": 8,
        "investors": [
            "Binance Labs",
            "Coinbase Ventures",
            "Multicoin Capital",
        ],
    },
    "tier_3": {
        "score": 5,
        "investors": [
            "OKX Ventures",
            "Dragonfly",
            "Robot Ventures",
        ],
    },
}

DB_PATH = "alpha_hunter.db"


def init_db() -> None:
    """Initialize SQLite persistence for seen projects."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS seen_projects (
                project_name TEXT PRIMARY KEY,
                last_score INT,
                timestamp DATETIME
            )
            """
        )
        conn.commit()


def _investor_score_lookup() -> dict[str, int]:
    lookup: dict[str, int] = {}
    for tier_data in VC_TIERS.values():
        score = tier_data["score"]
        for name in tier_data["investors"]:
            lookup[name.casefold()] = score
    return lookup


def _coerce_extraction(payload: dict) -> dict:
    project = payload.get("project")
    action = payload.get("action")
    investors = payload.get("investors", [])

    if not isinstance(project, str):
        project = ""
    if not isinstance(action, str):
        action = ""
    if not isinstance(investors, list):
        investors = []

    normalized_investors = [str(item).strip() for item in investors if str(item).strip()]

    return {
        "project": project.strip(),
        "action": action.strip(),
        "investors": normalized_investors,
    }


def analyze_alpha_post(raw_text: str) -> dict:
    """Extract project, action, investors from raw text using Gemini 1.5 Flash."""
    import google.generativeai as genai

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable is required.")

    genai.configure(api_key=api_key)

    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        system_instruction=(
            "You are a crypto data extractor. Extract the Project Name, "
            "a 1-sentence description of the required action, and a list of Venture Capital "
            "investors mentioned. Output ONLY in valid JSON format."
        ),
    )

    prompt = (
        "Extract from the following raw post and return valid JSON with exactly these keys: "
        '{"project": str, "action": str, "investors": list}. '
        "Do not include markdown or extra commentary.\n\n"
        f"RAW_POST:\n{raw_text}"
    )

    response = model.generate_content(prompt)
    text = (response.text or "").strip()

    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()

    parsed = json.loads(text)
    return _coerce_extraction(parsed)


def calculate_score(extracted_json: dict) -> tuple[int, str]:
    investors = extracted_json.get("investors", [])
    if not isinstance(investors, list):
        investors = []

    lookup = _investor_score_lookup()
    total_score = 0

    for investor in investors:
        normalized = str(investor).strip().casefold()
        total_score += lookup.get(normalized, 0)

    if total_score >= 18:
        label = "🔥 HIGH PRIORITY"
    elif total_score >= 8:
        label = "✅ MEDIUM"
    else:
        label = "👀 LOW"

    return total_score, label


def _get_bot_and_chat_id():
    from aiogram import Bot

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("CHAT_ID")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN environment variable is required.")
    if not chat_id:
        raise ValueError("CHAT_ID environment variable is required.")
    return Bot(token=token), chat_id


def _project_exists(project_name: str) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM seen_projects WHERE project_name = ?",
            (project_name,),
        )
        return cursor.fetchone() is not None


def _insert_project(project_name: str, score: int) -> None:
    now_utc = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO seen_projects (project_name, last_score, timestamp)
            VALUES (?, ?, ?)
            """,
            (project_name, score, now_utc),
        )
        conn.commit()


async def process_and_notify(project_data: dict) -> None:
    """Send a Telegram alert only for unseen projects with score >= 8."""
    name = str(project_data.get("project", "")).strip()
    if not name:
        return

    score = int(project_data.get("score", 0))
    if _project_exists(name):
        return

    if score < 8:
        return

    action = str(project_data.get("action", "")).strip() or "N/A"
    investors = project_data.get("investors", [])
    investors_list = ", ".join(str(i) for i in investors) if investors else "None"

    message = (
        "🚀 **NEW ALPHA DETECTED** 🚀\n\n"
        f"🔹 **Project:** {name}\n"
        f"🛠 **Action:** {action}\n"
        f"💰 **VC Score:** {score}/10\n"
        f"👥 **Investors:** {investors_list}\n\n"
        "🔗 *Check source for details.*"
    )

    bot, chat_id = _get_bot_and_chat_id()
    try:
        await bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown")
    finally:
        await bot.session.close()

    _insert_project(name, score)


def _load_mock_data() -> list[dict]:
    """Mock pipeline input for local simulation."""
    return [
        {
            "project": "Monad",
            "action": "Join Testnet",
            "investors": ["Paradigm", "Coinbase Ventures"],
            "score": 18,
        },
        {
            "project": "Monad",
            "action": "Join Testnet",
            "investors": ["Paradigm"],
            "score": 18,
        },
    ]


async def main() -> None:
    init_db()
    for project_data in _load_mock_data():
        await process_and_notify(project_data)


if __name__ == "__main__":
    asyncio.run(main())
