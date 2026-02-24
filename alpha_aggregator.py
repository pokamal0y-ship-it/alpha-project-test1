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
DEFAULT_MODEL_CANDIDATES = [
    "gemini-1.5-flash",
    "gemini-1.5-flash-latest",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
]


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


def _extract_json_text(model_text: str) -> str:
    cleaned = model_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()

    # Rescue JSON when model wraps it with extra words
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end >= start:
        cleaned = cleaned[start : end + 1]

    return cleaned


def _gemini_models_to_try() -> list[str]:
    forced = os.getenv("GEMINI_MODEL", "").strip()
    if forced:
        return [forced]
    return DEFAULT_MODEL_CANDIDATES


def _analyze_with_google_genai(raw_text: str, api_key: str) -> dict:
    from google import genai
    from google.genai import types

    system_instruction = (
        "You are a crypto data extractor. Extract the Project Name, "
        "a 1-sentence description of the required action, and a list of Venture Capital "
        "investors mentioned. Output ONLY in valid JSON format."
    )
    prompt = (
        "Extract from the following raw post and return valid JSON with exactly these keys: "
        '{"project": str, "action": str, "investors": list}. '
        "Do not include markdown or extra commentary.\n\n"
        f"RAW_POST:\n{raw_text}"
    )

    client = genai.Client(api_key=api_key)
    last_error = None
    for model_name in _gemini_models_to_try():
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(system_instruction=system_instruction),
            )
            text = getattr(response, "text", "") or ""
            parsed = json.loads(_extract_json_text(text))
            return _coerce_extraction(parsed)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue

    raise RuntimeError(f"All Gemini model attempts failed: {last_error}")


def _analyze_with_google_generativeai(raw_text: str, api_key: str) -> dict:
    import google.generativeai as genai

    system_instruction = (
        "You are a crypto data extractor. Extract the Project Name, "
        "a 1-sentence description of the required action, and a list of Venture Capital "
        "investors mentioned. Output ONLY in valid JSON format."
    )
    prompt = (
        "Extract from the following raw post and return valid JSON with exactly these keys: "
        '{"project": str, "action": str, "investors": list}. '
        "Do not include markdown or extra commentary.\n\n"
        f"RAW_POST:\n{raw_text}"
    )

    genai.configure(api_key=api_key)

    last_error = None
    for model_name in _gemini_models_to_try():
        try:
            model = genai.GenerativeModel(model_name=model_name, system_instruction=system_instruction)
            response = model.generate_content(prompt)
            text = getattr(response, "text", "") or ""
            parsed = json.loads(_extract_json_text(text))
            return _coerce_extraction(parsed)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue

    raise RuntimeError(f"All Gemini model attempts failed: {last_error}")


def analyze_alpha_post(raw_text: str) -> dict:
    """Extract project, action, investors from raw text using Gemini."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable is required.")

    try:
        return _analyze_with_google_genai(raw_text, api_key)
    except ModuleNotFoundError:
        return _analyze_with_google_generativeai(raw_text, api_key)


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
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("CHAT_ID")
    if not token or not chat_id:
        return None, None

    from aiogram import Bot

    return Bot(token=token), chat_id


def _telegram_preview_only() -> bool:
    return os.getenv("TELEGRAM_PREVIEW_ONLY", "").strip().lower() in {"1", "true", "yes", "on"}


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

    if _telegram_preview_only():
        print("[WARN] TELEGRAM_PREVIEW_ONLY enabled. Preview only (no message sent):")
        print(message)
        _insert_project(name, score)
        return

    try:
        bot, chat_id = _get_bot_and_chat_id()
        if bot is None or chat_id is None:
            print("[WARN] TELEGRAM_BOT_TOKEN/CHAT_ID not set. Preview only (no message sent):")
            print(message)
            _insert_project(name, score)
            return

        try:
            await bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown")
        except Exception as exc:
            print(f"[WARN] Telegram send failed ({exc}). Falling back to preview mode:")
            print(message)
        finally:
            await bot.session.close()
    except Exception as exc:
        print(f"[WARN] Telegram subsystem failure ({exc}). Falling back to preview mode:")
        print(message)

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
