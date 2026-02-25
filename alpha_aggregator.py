"""Crypto Alpha Hunter: extraction, scoring, persistence, and Telegram alerts."""

import argparse
import asyncio
import json
import os
import sqlite3
from datetime import datetime, timezone

VC_TIERS = {
    "tier_1": {
        "score": 10,
        "investors": ["Paradigm", "a16z Crypto", "Polychain Capital"],
    },
    "tier_2": {
        "score": 8,
        "investors": ["Binance Labs", "Coinbase Ventures", "Multicoin Capital"],
    },
    "tier_3": {
        "score": 5,
        "investors": ["OKX Ventures", "Dragonfly", "Robot Ventures"],
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

    return {"project": project.strip(), "action": action.strip(), "investors": normalized_investors}


def _extract_json_text(model_text: str) -> str:
    cleaned = model_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()

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
            parsed = json.loads(_extract_json_text(getattr(response, "text", "") or ""))
            return _coerce_extraction(parsed)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
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
            parsed = json.loads(_extract_json_text(getattr(response, "text", "") or ""))
            return _coerce_extraction(parsed)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    raise RuntimeError(f"All Gemini model attempts failed: {last_error}")


def analyze_alpha_post(raw_text: str) -> dict:
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
    total_score = sum(lookup.get(str(i).strip().casefold(), 0) for i in investors)

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
        cursor.execute("SELECT 1 FROM seen_projects WHERE project_name = ?", (project_name,))
        return cursor.fetchone() is not None


def _insert_project(project_name: str, score: int) -> None:
    now_utc = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO seen_projects (project_name, last_score, timestamp) VALUES (?, ?, ?)",
            (project_name, score, now_utc),
        )
        conn.commit()


def _format_message(project_data: dict) -> str:
    name = str(project_data.get("project", "")).strip() or "Unknown"
    action = str(project_data.get("action", "")).strip() or "N/A"
    score = int(project_data.get("score", 0))
    investors = project_data.get("investors", [])
    investors_list = ", ".join(str(i) for i in investors) if investors else "None"
    source = str(project_data.get("source", "")).strip()
    immediate = bool(project_data.get("immediate_token"))

    header = "⚡ **IMMEDIATE TOKEN OPPORTUNITY** ⚡" if immediate else "🚀 **NEW ALPHA DETECTED** 🚀"
    source_line = f"\n🔗 **Source:** {source}" if source else ""

    return (
        f"{header}\n\n"
        f"🔹 **Project:** {name}\n"
        f"🛠 **Action:** {action}\n"
        f"💰 **VC Score:** {score}/10\n"
        f"👥 **Investors:** {investors_list}"
        f"{source_line}\n\n"
        "🔗 *Check source for details.*"
    )


async def send_telegram_test_message() -> None:
    """Send a direct Telegram test message (or preview if disabled)."""
    payload = {
        "project": "Nexus Alpha Test",
        "action": "Connectivity check",
        "investors": ["Paradigm"],
        "score": 10,
        "source": "local-test",
        "immediate_token": False,
        "force_send": True,
    }
    await process_and_notify(payload)


async def process_and_notify(project_data: dict) -> None:
    """Send a Telegram alert for unseen and valuable/immediate opportunities."""
    name = str(project_data.get("project", "")).strip()
    if not name:
        return

    score = int(project_data.get("score", 0))
    immediate = bool(project_data.get("immediate_token"))
    force_send = bool(project_data.get("force_send"))

    if _project_exists(name) and not force_send:
        return

    if score < 8 and not immediate and not force_send:
        return

    message = _format_message(project_data)

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
    return [
        {
            "project": "Monad",
            "action": "Join Testnet",
            "investors": ["Paradigm", "Coinbase Ventures"],
            "score": 18,
            "source": "https://nitter.net/Monad_xyz",
        },
        {
            "project": "Monad",
            "action": "Join Testnet",
            "investors": ["Paradigm"],
            "score": 18,
            "source": "https://nitter.net/Monad_xyz",
        },
    ]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Nexus Alpha aggregator runner")
    parser.add_argument("--telegram-test", action="store_true", help="send one Telegram test message (or preview)")
    parser.add_argument("--preview-only", action="store_true", help="force preview mode for this run")
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()
    init_db()

    if args.preview_only:
        os.environ["TELEGRAM_PREVIEW_ONLY"] = "1"

    env_test = os.getenv("TELEGRAM_SEND_TEST", "").strip().lower() in {"1", "true", "yes", "on"}
    if args.telegram_test or env_test:
        await send_telegram_test_message()
        return

    for project_data in _load_mock_data():
        await process_and_notify(project_data)


if __name__ == "__main__":
    asyncio.run(main())
