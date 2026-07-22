import json
import os
import sqlite3
import time

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS updates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    container TEXT NOT NULL,
    portainer_stack_name TEXT,
    image_name TEXT,
    current_version TEXT,
    new_version TEXT,
    version_note TEXT,
    kind TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    risk TEXT,
    summary TEXT,
    compose_change_needed INTEGER DEFAULT 0,
    compose_change_explanation TEXT,
    compose_patch TEXT,
    raw_analysis TEXT,
    created_at REAL,
    updated_at REAL,
    UNIQUE(container, current_version, new_version)
);
"""


def _connect():
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = _connect()
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def get_existing(container: str, current_version: str, new_version: str):
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM updates WHERE container=? AND current_version=? AND new_version=?",
            (container, current_version, new_version),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def upsert_pending(container: str, portainer_stack_name: str, image_name: str,
                    current_version: str, new_version: str, kind: str,
                    version_note: str = ""):
    """Legt einen Eintrag an, falls er noch nicht existiert (idempotent), ohne
    eine bestehende Analyse zu ueberschreiben."""
    now = time.time()
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO updates
                (container, portainer_stack_name, image_name, current_version,
                 new_version, version_note, kind, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            ON CONFLICT(container, current_version, new_version) DO NOTHING
            """,
            (container, portainer_stack_name, image_name, current_version,
             new_version, version_note, kind, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def save_analysis(container: str, current_version: str, new_version: str, analysis: dict):
    conn = _connect()
    try:
        conn.execute(
            """
            UPDATE updates
            SET risk=?, summary=?, compose_change_needed=?, compose_change_explanation=?,
                compose_patch=?, raw_analysis=?, updated_at=?
            WHERE container=? AND current_version=? AND new_version=?
            """,
            (
                analysis.get("risk"),
                analysis.get("summary"),
                1 if analysis.get("compose_change_needed") else 0,
                analysis.get("compose_change_explanation"),
                analysis.get("compose_patch"),
                analysis.get("raw"),
                time.time(),
                container,
                current_version,
                new_version,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def set_status(container: str, current_version: str, new_version: str, status: str):
    conn = _connect()
    try:
        conn.execute(
            "UPDATE updates SET status=?, updated_at=? WHERE container=? AND current_version=? AND new_version=?",
            (status, time.time(), container, current_version, new_version),
        )
        conn.commit()
    finally:
        conn.close()


def list_all():
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM updates ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_latest_pending(container: str):
    conn = _connect()
    try:
        row = conn.execute(
            """
            SELECT * FROM updates
            WHERE container=? AND status='pending'
            ORDER BY created_at DESC LIMIT 1
            """,
            (container,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
