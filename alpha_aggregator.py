"""Crypto Alpha Hunter: extraction, scoring, persistence, and Telegram alerts."""

import argparse
import asyncio
import json
import os
import sqlite3
import logging
from datetime import datetime, timezone

# Suppress verbose logs from external libraries
logging.getLogger("google_genai").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("google.generativeai").setLevel(logging.ERROR)

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
                timestamp DATETIME,
                action TEXT,
                investors TEXT,
                source TEXT,
                frequency TEXT
            )
            """
        )

        existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(seen_projects)").fetchall()}
        columns_to_add = {
            "action": "TEXT",
            "investors": "TEXT",
            "source": "TEXT",
            "frequency": "TEXT"
        }
        for col_name, col_type in columns_to_add.items():
            if col_name not in existing_columns:
                conn.execute(f"ALTER TABLE seen_projects ADD COLUMN {col_name} {col_type}")

        conn.commit()


def seed_initial_projects() -> None:
    """Automatically add initial projects to seen_projects if not exists."""
    initial_projects = [
        {
            "project": "Sentio",
            "action": "Binance Web3 Quest",
            "investors": ["Binance Labs"],
            "score": 18,  # HIGH
            "frequency": "daily_scan"
        },
        {
            "project": "Monad",
            "action": "Weekly Testnet Swaps",
            "investors": ["Paradigm"],
            "score": 20,  # Tier-0
            "frequency": "daily_scan"
        },
        {
            "project": "DataHaven",
            "action": "Daily Check-in",
            "investors": ["Coinbase Ventures"], # Tier-2
            "score": 8,   # MEDIUM
            "frequency": "daily_scan"
        }
    ]
    for p in initial_projects:
        if not _project_exists(p["project"]):
            _insert_project(p)
            print(f"[INFO] Seeded project: {p['project']}")


def _investor_score_lookup() -> dict[str, int]:
    lookup: dict[str, int] = {}
    for tier_data in VC_TIERS.values():
        score = tier_data["score"]
        for name in tier_data["investors"]:
            lookup[name.casefold()] = score
    return lookup


def _coerce_extraction(payload: dict) -> dict:
    project = payload.get("project", "")
    action = payload.get("action", "")
    investors = payload.get("investors", [])

    if not isinstance(project, str):
        project = str(project)
    if not isinstance(action, str):
        action = str(action)
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
            continue

    raise RuntimeError(f"All Gemini model attempts failed: {last_error}")


def _rule_based_extraction(raw_text: str) -> dict:
    """Fallback extractor using regex and keyword matching."""
    text = raw_text.strip()
    lines = text.split('\n')
    
    # 1. Try to find a project name (usually first capitalized word or "Project: ...")
    project = "Unknown Project"
    import re
    project_match = re.search(r"(?:Project|Protocol|App):\s*(\w+)", text, re.I)
    if project_match:
        project = project_match.group(1)
    elif lines:
        # Just take the first few words of the first line as a guess
        first_line_words = lines[0].split()
        if first_line_words:
            project = first_line_words[0].strip(":#*")

    # 2. Match known investors from VC_TIERS
    found_investors = []
    lookup = _investor_score_lookup()
    normalized_text = text.lower()
    for investor_name in lookup.keys():
        if investor_name in normalized_text:
            # Re-capitalize for display if possible, or just use the key
            found_investors.append(investor_name.title())

    return {
        "project": project,
        "action": "Rule-based extraction (Manual review recommended)",
        "investors": list(set(found_investors))
    }


def analyze_alpha_post(raw_text: str) -> dict:
    """Extract project, action, investors from raw text using Gemini or fallback."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("[INFO] GEMINI_API_KEY not found. Using rule-based fallback.")
        return _rule_based_extraction(raw_text)

    try:
        return _analyze_with_google_genai(raw_text, api_key)
    except (ModuleNotFoundError, ImportError):
        try:
            return _analyze_with_google_generativeai(raw_text, api_key)
        except Exception as exc:
            print(f"[WARN] Gemini extraction failed ({exc}). Using rule-based fallback.")
            return _rule_based_extraction(raw_text)
    except Exception as exc:
        print(f"[WARN] Gemini extraction failed ({exc}). Using rule-based fallback.")
        return _rule_based_extraction(raw_text)


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
        cursor.execute("SELECT 1 FROM seen_projects WHERE project_name = ?", (project_name,))
        return cursor.fetchone() is not None


def _insert_project(project_data: dict) -> None:
    now_utc = datetime.now(timezone.utc).isoformat()
    project_name = str(project_data.get("project", "")).strip()
    if not project_name:
        return

    score = int(project_data.get("score", 0))
    action = str(project_data.get("action", "")).strip() or None
    investors_raw = project_data.get("investors", [])
    if isinstance(investors_raw, list):
        investors = json.dumps([str(item) for item in investors_raw if str(item).strip()])
    else:
        investors = json.dumps([])
    source = str(project_data.get("source", "")).strip() or None
    frequency = str(project_data.get("frequency", "")).strip() or None

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO seen_projects (project_name, last_score, timestamp, action, investors, source, frequency)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (project_name, score, now_utc, action, investors, source, frequency),
        )
        conn.commit()


async def _safe_send_message(bot, chat_id: str, message: str) -> bool:
    try:
        await bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] Telegram send failed ({exc}). Falling back to preview mode:")
        print(message)
        return False


async def _safe_close_bot(bot) -> None:
    try:
        await bot.session.close()
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] Telegram bot session close failed: {exc}")


def _format_message(project_data: dict) -> str:
    name = str(project_data.get("project", "")).strip() or "Unknown"
    action = str(project_data.get("action", "")).strip() or "N/A"
    score = int(project_data.get("score", 0))
    investors = project_data.get("investors", [])
    investors_list = ", ".join(str(i) for i in investors) if investors else "None"
    source = str(project_data.get("source", "")).strip()
    frequency = str(project_data.get("frequency", "")).strip()
    immediate = bool(project_data.get("immediate_token"))

    header = "⚡ **IMMEDIATE TOKEN OPPORTUNITY** ⚡" if immediate else "🚀 **NEW ALPHA DETECTED** 🚀"
    source_line = f"\n🔗 **Source:** {source}" if source else ""
    tag = frequency.replace("_scan", "").replace("_research", "").replace("_alpha", "").upper()
    tag_line = f"\n🏷 **Tag:** #{tag}" if tag else ""

    return (
        f"{header}\n\n"
        f"🔹 **Project:** {name}\n"
        f"🛠 **Action:** {action}\n"
        f"💰 **VC Score:** {score}/10\n"
        f"👥 **Investors:** {investors_list}"
        f"{source_line}"
        f"{tag_line}\n\n"
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
        "frequency": "daily_scan"
    }
    await process_and_notify(payload)


async def process_and_notify(project_data: dict) -> None:
    """Send a Telegram alert only for unseen projects with score >= 8 or immediate token."""
    name = str(project_data.get("project", "")).strip()
    if not name:
        return

    score = int(project_data.get("score", 0))
    immediate = bool(project_data.get("immediate_token"))
    force_send = bool(project_data.get("force_send"))

    if _project_exists(name) and not force_send:
        return

    # Filter by value
    if score < 8 and not immediate and not force_send:
        _insert_project(project_data) # Still save it even if no alert
        return

    message = _format_message(project_data)

    if _telegram_preview_only():
        print("[WARN] TELEGRAM_PREVIEW_ONLY enabled. Preview only (no message sent):")
        print(message)
        _insert_project(project_data)
        return

    bot, chat_id = _get_bot_and_chat_id()
    if bot is None or chat_id is None:
        print("[WARN] TELEGRAM_BOT_TOKEN/CHAT_ID not set. Preview only (no message sent):")
        print(message)
        _insert_project(project_data)
        return

    try:
        await _safe_send_message(bot, chat_id, message)
        await _safe_close_bot(bot)
    except Exception as exc:
        print(f"[WARN] Telegram subsystem failure ({exc}). Falling back to preview mode:")
        print(message)

    _insert_project(project_data)


def _load_mock_data() -> list[dict]:
    """Mock pipeline input for local simulation."""
    return [
        {
            "project": "Monad",
            "action": "Join Testnet",
            "investors": ["Paradigm", "Coinbase Ventures"],
            "score": 18,
            "source": "https://nitter.net/Monad_xyz",
            "frequency": "daily_scan"
        }
    ]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Nexus Alpha aggregator runner")
    parser.add_argument("--telegram-test", action="store_true", help="send one Telegram test message (or preview)")
    parser.add_argument("--preview-only", action="store_true", help="force preview mode for this run")
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()
    init_db()
    seed_initial_projects()

    if args.preview_only:
        os.environ["TELEGRAM_PREVIEW_ONLY"] = "1"

    env_test = os.getenv("TELEGRAM_SEND_TEST", "").strip().lower() in {"1", "true", "yes", "on"}
    if args.telegram_test or env_test:
        try:
            await send_telegram_test_message()
        except Exception as exc:
            print(f"[WARN] Telegram test flow failed safely: {exc}")
        return

    for project_data in _load_mock_data():
        try:
            await process_and_notify(project_data)
        except Exception as exc:
            print(f"[WARN] Processing failed safely for mock data: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
