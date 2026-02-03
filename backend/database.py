import os
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional

import psycopg2
from psycopg2.extras import RealDictCursor


def get_connection():
    """Create a new database connection."""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is missing. Set DATABASE_URL=postgresql://user:password@host:port/dbname")
    return psycopg2.connect(database_url, cursor_factory=RealDictCursor)


@contextmanager
def get_db():
    """Context manager for database connections."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# --- User CRUD ---

def create_user(email: str, password_hash: str, full_name: Optional[str] = None) -> Dict[str, Any]:
    """Create a new user and return the user record."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (email, password_hash, full_name)
                VALUES (%s, %s, %s)
                RETURNING id, email, full_name, created_at, is_active
                """,
                (email.lower().strip(), password_hash, full_name)
            )
            return dict(cur.fetchone())


def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    """Get user by email address."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, email, password_hash, full_name, created_at, is_active
                FROM users
                WHERE email = %s
                """,
                (email.lower().strip(),)
            )
            row = cur.fetchone()
            return dict(row) if row else None


def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    """Get user by ID."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, email, full_name, created_at, is_active
                FROM users
                WHERE id = %s
                """,
                (user_id,)
            )
            row = cur.fetchone()
            return dict(row) if row else None


# --- Generation History CRUD ---

def save_generation(
    user_id: int,
    lang: str,
    hint: Optional[str],
    image_count: int,
    image_filenames: List[str],
    result_json: Dict[str, Any],
    product_type: Optional[str] = None,
    brand: Optional[str] = None,
    generation_time_ms: Optional[int] = None
) -> Dict[str, Any]:
    """Save a generation to history and return the record."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO generation_history
                (user_id, lang, hint, image_count, image_filenames, result_json, product_type, brand, generation_time_ms)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, user_id, lang, hint, image_count, image_filenames, product_type, brand, created_at, generation_time_ms
                """,
                (
                    user_id,
                    lang,
                    hint,
                    image_count,
                    image_filenames,
                    psycopg2.extras.Json(result_json),
                    product_type,
                    brand,
                    generation_time_ms
                )
            )
            return dict(cur.fetchone())


def get_user_history(
    user_id: int,
    limit: int = 20,
    offset: int = 0
) -> tuple[List[Dict[str, Any]], int]:
    """Get paginated generation history for a user. Returns (items, total_count)."""
    with get_db() as conn:
        with conn.cursor() as cur:
            # Get total count
            cur.execute(
                "SELECT COUNT(*) as count FROM generation_history WHERE user_id = %s",
                (user_id,)
            )
            total = cur.fetchone()["count"]

            # Get paginated items
            cur.execute(
                """
                SELECT id, user_id, lang, hint, image_count, image_filenames, product_type, brand, created_at, generation_time_ms
                FROM generation_history
                WHERE user_id = %s
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
                """,
                (user_id, limit, offset)
            )
            items = [dict(row) for row in cur.fetchall()]

            return items, total


def get_generation_by_id(generation_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    """Get a single generation by ID, ensuring it belongs to the user."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, user_id, lang, hint, image_count, image_filenames, result_json, product_type, brand, created_at, generation_time_ms
                FROM generation_history
                WHERE id = %s AND user_id = %s
                """,
                (generation_id, user_id)
            )
            row = cur.fetchone()
            return dict(row) if row else None


def delete_generation(generation_id: int, user_id: int) -> bool:
    """Delete a generation, ensuring it belongs to the user. Returns True if deleted."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM generation_history
                WHERE id = %s AND user_id = %s
                RETURNING id
                """,
                (generation_id, user_id)
            )
            return cur.fetchone() is not None
