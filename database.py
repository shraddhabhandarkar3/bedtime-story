"""Small SQLite persistence layer for users and story messages."""

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Dict, List, Optional


DB_PATH = Path(__file__).with_name("stories.db")
VALID_ROLES = {"user", "assistant", "judge"}


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database() -> None:
    """Create the two application tables if they do not exist."""
    with connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_name TEXT NOT NULL UNIQUE
            );

            CREATE TABLE IF NOT EXISTS messages (
                message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                story_id TEXT NOT NULL,
                role TEXT NOT NULL
                    CHECK (role IN ('user', 'assistant', 'judge')),
                content TEXT NOT NULL,
                category TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );

            CREATE INDEX IF NOT EXISTS idx_messages_user_story
                ON messages (user_id, story_id, message_id);
            """
        )


def get_or_create_user(user_name: str) -> int:
    user_name = user_name.strip()
    if not user_name:
        raise ValueError("User name cannot be empty.")

    with connect() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO users (user_name) VALUES (?)",
            (user_name,),
        )
        user = connection.execute(
            "SELECT user_id FROM users WHERE user_name = ?",
            (user_name,),
        ).fetchone()

    if user is None:
        raise RuntimeError("Could not create or find the user.")
    return int(user["user_id"])


def new_story_id() -> str:
    return str(uuid.uuid4())


def save_message(
    user_id: int,
    story_id: str,
    role: str,
    content: str,
    category: Optional[str] = None,
) -> int:
    """Append one user, assistant, or judge message to the history."""
    if role not in VALID_ROLES:
        raise ValueError(f"Role must be one of: {sorted(VALID_ROLES)}")
    if not content.strip():
        raise ValueError("Message content cannot be empty.")

    with connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO messages (user_id, story_id, role, content, category)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, story_id, role, content.strip(), category),
        )
        return int(cursor.lastrowid)


def get_story_history(user_id: int, story_id: str) -> List[Dict]:
    with connect() as connection:
        messages = connection.execute(
            """
            SELECT message_id, role, content, category, created_at
            FROM messages
            WHERE user_id = ? AND story_id = ?
            ORDER BY message_id
            """,
            (user_id, story_id),
        ).fetchall()
    return [dict(message) for message in messages]


def get_recent_stories(user_id: int, limit: int = 3) -> List[Dict]:
    """Return the latest assistant version from each previous story."""
    with connect() as connection:
        stories = connection.execute(
            """
            SELECT message.story_id, message.content, message.category,
                   message.created_at
            FROM messages AS message
            JOIN (
                SELECT story_id, MAX(message_id) AS latest_message_id
                FROM messages
                WHERE user_id = ?
                  AND role = 'assistant'
                  AND category != 'conversation'
                GROUP BY story_id
            ) AS latest
              ON message.message_id = latest.latest_message_id
            ORDER BY message.message_id DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
    return [dict(story) for story in stories]


def save_child_profile(user_id: int, story_id: str, profile: Dict) -> int:
    """Append profile memory inside messages, preserving the two-table design."""
    return save_message(
        user_id,
        story_id,
        "user",
        json.dumps(profile, ensure_ascii=False),
        "child_profile",
    )


def get_child_profile(user_id: int) -> Dict:
    """Return the latest remembered child profile for a returning account."""
    with connect() as connection:
        row = connection.execute(
            """
            SELECT content
            FROM messages
            WHERE user_id = ? AND category = 'child_profile'
            ORDER BY message_id DESC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
    if row is None:
        return {}
    try:
        profile = json.loads(row["content"])
    except (json.JSONDecodeError, TypeError):
        return {}
    return profile if isinstance(profile, dict) else {}


def get_latest_initial_request(user_id: int) -> Optional[str]:
    """Return the opening request from the user's most recent prior story."""
    with connect() as connection:
        row = connection.execute(
            """
            SELECT message.content
            FROM messages AS message
            JOIN (
                SELECT story_id, MIN(message_id) AS first_message_id
                FROM messages
                WHERE user_id = ?
                  AND role = 'user'
                  AND category != 'child_profile'
                GROUP BY story_id
            ) AS first_message
              ON message.message_id = first_message.first_message_id
            ORDER BY message.message_id DESC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
    return str(row["content"]) if row is not None else None
