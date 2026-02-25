"""FastAPI dashboard for Crypto Alpha Hunter."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
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


def _safe_project_slug(name: str) -> str:
    return name.replace(" ", "-")


def _project_from_row(row: sqlite3.Row) -> dict[str, Any]:
    score = int(row["last_score"] or 0)
    project_name = row["project_name"]
    return {
        "project_name": project_name,
        "project_slug": _safe_project_slug(project_name),
        "action": row["action"] or "N/A",
        "vc_score": score,
        "investors": _safe_decode_investors(row["investors"]),
        "source": row["source"] or "N/A",
        "discovery_date": row["timestamp"] or "N/A",
        "score_class": "score-high" if score >= 18 else "score-medium" if score >= 8 else "score-low",
    }


def get_all_projects() -> list[dict[str, Any]]:
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
        query += ", source" if "source" in columns else ", NULL AS source"
        query += " FROM seen_projects ORDER BY timestamp DESC"

        rows = conn.execute(query).fetchall()

    return [_project_from_row(row) for row in rows]


def get_project(project_slug: str) -> dict[str, Any] | None:
    for project in get_all_projects():
        if project["project_slug"] == project_slug:
            return project
    return None


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    projects = get_all_projects()
    return templates.TemplateResponse("index.html", {"request": request, "projects": projects})


@app.get("/project/{project_slug}", response_class=HTMLResponse)
async def project_preview(request: Request, project_slug: str):
    project = get_project(project_slug)
    return templates.TemplateResponse(
        "project_preview.html",
        {"request": request, "project": project, "project_slug": project_slug},
    )


@app.get("/health", response_class=JSONResponse)
async def healthcheck():
    return {"status": "ok", "db_exists": Path(DB_PATH).exists()}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Crypto Alpha Hunter dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    import uvicorn

    args = _parse_args()
    uvicorn.run("web_dashboard:app", host=args.host, port=args.port, reload=args.reload)
