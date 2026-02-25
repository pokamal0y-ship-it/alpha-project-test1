"""FastAPI dashboard for Crypto Alpha Hunter."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

DB_PATH = "alpha_hunter.db"

app = FastAPI(title="Crypto Alpha Hunter Dashboard")
templates = Jinja2Templates(directory="templates")


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1] for row in rows}


def _safe_decode_investors(raw: object) -> str:
    if raw is None:
        return "N/A"
    text = str(raw).strip()
    if not text:
        return "N/A"
    try:
        loaded = json.loads(text)
        if isinstance(loaded, list):
            return ", ".join(str(item) for item in loaded) if loaded else "N/A"
    except Exception:  # noqa: BLE001
        pass
    return text


def get_all_projects() -> list[dict]:
    """Fetch all seen projects sorted by timestamp descending."""
    if not Path(DB_PATH).exists():
        return []

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        columns = _table_columns(conn, "seen_projects")
        if not columns:
            return []

        query = "SELECT project_name, last_score, timestamp"
        query += ", action" if "action" in columns else ", NULL AS action"
        query += ", investors" if "investors" in columns else ", NULL AS investors"
        query += " FROM seen_projects ORDER BY timestamp DESC"

        rows = conn.execute(query).fetchall()

    projects = []
    for row in rows:
        score = int(row["last_score"] or 0)
        projects.append(
            {
                "project_name": row["project_name"],
                "action": row["action"] or "N/A",
                "vc_score": score,
                "investors": _safe_decode_investors(row["investors"]),
                "discovery_date": row["timestamp"] or "N/A",
                "score_class": "score-high" if score >= 18 else "score-medium" if score >= 8 else "score-low",
            }
        )

    return projects


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    projects = get_all_projects()
    return templates.TemplateResponse("index.html", {"request": request, "projects": projects})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("web_dashboard:app", host="127.0.0.1", port=8000, reload=False)
