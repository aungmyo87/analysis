"""
SQLite Database Module - Async Support
=======================================

Provides async database connection management using aiosqlite.

ARCHITECTURE:
-------------
┌─────────────────────────────────────────────────────────────────┐
│                      Database Layer                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │  Connection  │  │    Schema    │  │     Migrations       │  │
│  │    Pool      │  │  Management  │  │     (JSON→SQLite)    │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘

WHY SQLITE + AIOSQLITE:
-----------------------
1. No external database server required
2. Fully async - integrates with FastAPI event loop
3. ACID compliant - safe concurrent writes
4. Single file - easy backup/deployment
5. Fast - in-process, no network latency

SCHEMA:
-------
api_keys:
  - id: INTEGER PRIMARY KEY AUTOINCREMENT
  - key: TEXT UNIQUE NOT NULL (the API key string)
  - balance: REAL DEFAULT 0.0
  - is_owner: INTEGER DEFAULT 0 (boolean)
  - created_at: TEXT (ISO format)
  - expires_at: TEXT (ISO format, nullable)

usage_logs:
  - id: INTEGER PRIMARY KEY AUTOINCREMENT
  - api_key_id: INTEGER (foreign key)
  - action: TEXT (solve, balance_check, etc.)
  - amount: REAL (cost deducted)
  - timestamp: TEXT (ISO format)
  - metadata: TEXT (JSON blob for extra data)
"""

import os
import logging
import asyncio
from typing import Optional, Dict, Any, List
from pathlib import Path
from datetime import datetime

import aiosqlite

logger = logging.getLogger(__name__)

# Database file path
DB_PATH = Path(__file__).parent.parent / "data" / "solver.db"

# Global connection pool (single connection for SQLite)
_db_connection: Optional[aiosqlite.Connection] = None
_db_lock = asyncio.Lock()


# =============================================================================
# SCHEMA DEFINITIONS
# =============================================================================

SCHEMA_SQL = """
-- API Keys table
CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT UNIQUE NOT NULL,
    balance REAL DEFAULT 0.0,
    is_owner INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    last_used_at TEXT,
    total_requests INTEGER DEFAULT 0,
    total_spent REAL DEFAULT 0.0
);

-- Usage logs for auditing
CREATE TABLE IF NOT EXISTS usage_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    api_key_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    amount REAL DEFAULT 0.0,
    success INTEGER DEFAULT 1,
    timestamp TEXT NOT NULL,
    metadata TEXT,
    FOREIGN KEY (api_key_id) REFERENCES api_keys(id)
);

-- Index for fast key lookups
CREATE INDEX IF NOT EXISTS idx_api_keys_key ON api_keys(key);

-- Index for usage log queries
CREATE INDEX IF NOT EXISTS idx_usage_logs_api_key_id ON usage_logs(api_key_id);
CREATE INDEX IF NOT EXISTS idx_usage_logs_timestamp ON usage_logs(timestamp);
"""


# =============================================================================
# DATABASE CONNECTION MANAGEMENT
# =============================================================================

async def get_db() -> aiosqlite.Connection:
    """
    Get the database connection (singleton pattern).
    
    Returns:
        aiosqlite.Connection instance
    
    Usage:
        db = await get_db()
        async with db.execute("SELECT * FROM api_keys") as cursor:
            rows = await cursor.fetchall()
    """
    global _db_connection
    
    if _db_connection is None:
        async with _db_lock:
            if _db_connection is None:
                await init_db()
    
    return _db_connection  # type: ignore


async def init_db(db_path: Optional[Path] = None) -> aiosqlite.Connection:
    """
    Initialize the database connection and create tables.
    
    This should be called ONCE during server startup (in main.py lifespan).
    
    Args:
        db_path: Optional custom path for database file
    
    Returns:
        aiosqlite.Connection instance
    """
    global _db_connection
    
    path = db_path or DB_PATH
    
    # Ensure directory exists
    path.parent.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"Initializing database at {path}")
    
    # Open connection with WAL mode for better concurrency
    conn = await aiosqlite.connect(str(path))
    _db_connection = conn
    
    # Enable WAL mode for better concurrent read/write performance
    await conn.execute("PRAGMA journal_mode=WAL")
    
    # Enable foreign keys
    await conn.execute("PRAGMA foreign_keys=ON")
    
    # Create tables
    await conn.executescript(SCHEMA_SQL)
    await conn.commit()
    
    # Check if we need to seed default data
    async with conn.execute("SELECT COUNT(*) FROM api_keys") as cursor:
        count = (await cursor.fetchone())[0]
        if count == 0:
            logger.info("Seeding default API keys...")
            await _seed_default_keys(conn)
    
    logger.info("Database initialized successfully")
    return conn


async def close_db():
    """Close the database connection gracefully."""
    global _db_connection
    
    if _db_connection:
        await _db_connection.close()
        _db_connection = None
        logger.info("Database connection closed")


async def _seed_default_keys(db: aiosqlite.Connection):
    """Seed the database with default API keys."""
    now = datetime.now().isoformat()
    
    default_keys = [
        ("owner_key_12345", 1000.0, 1, now, None),
        ("test_key_67890", 10.0, 0, now, None),
    ]
    
    await db.executemany(
        """
        INSERT INTO api_keys (key, balance, is_owner, created_at, expires_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        default_keys
    )
    await db.commit()
    logger.info(f"Seeded {len(default_keys)} default API keys")


# =============================================================================
# API KEY OPERATIONS (Used by auth middleware)
# =============================================================================

async def get_api_key(key: str) -> Optional[Dict[str, Any]]:
    """
    Get API key data by key string.
    
    Args:
        key: The API key string
    
    Returns:
        Dict with key data or None if not found
    """
    db = await get_db()
    
    async with db.execute(
        """
        SELECT id, key, balance, is_owner, created_at, expires_at,
               last_used_at, total_requests, total_spent
        FROM api_keys WHERE key = ?
        """,
        (key,)
    ) as cursor:
        row = await cursor.fetchone()
        
        if row is None:
            return None
        
        return {
            "id": row[0],
            "key": row[1],
            "balance": row[2],
            "is_owner": bool(row[3]),
            "created_at": row[4],
            "expires_at": row[5],
            "last_used_at": row[6],
            "total_requests": row[7],
            "total_spent": row[8],
        }


async def update_api_key_balance(key: str, new_balance: float) -> bool:
    """
    Update the balance for an API key.
    
    Args:
        key: The API key string
        new_balance: New balance value
    
    Returns:
        True if updated, False if key not found
    """
    db = await get_db()
    
    cursor = await db.execute(
        "UPDATE api_keys SET balance = ? WHERE key = ?",
        (new_balance, key)
    )
    await db.commit()
    
    return cursor.rowcount > 0


async def increment_api_key_stats(key: str, amount_spent: float):
    """
    Increment usage statistics for an API key.
    
    Args:
        key: The API key string
        amount_spent: Amount deducted for this request
    """
    db = await get_db()
    now = datetime.now().isoformat()
    
    await db.execute(
        """
        UPDATE api_keys 
        SET last_used_at = ?,
            total_requests = total_requests + 1,
            total_spent = total_spent + ?
        WHERE key = ?
        """,
        (now, amount_spent, key)
    )
    await db.commit()


async def create_api_key_record(
    key: str,
    balance: float = 0.0,
    is_owner: bool = False,
    expires_at: Optional[str] = None
) -> int:
    """
    Create a new API key record.
    
    Args:
        key: The API key string
        balance: Initial balance
        is_owner: Whether this is an owner key
        expires_at: Expiration datetime (ISO format)
    
    Returns:
        The new record ID
    """
    db = await get_db()
    now = datetime.now().isoformat()
    
    cursor = await db.execute(
        """
        INSERT INTO api_keys (key, balance, is_owner, created_at, expires_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (key, balance, int(is_owner), now, expires_at)
    )
    await db.commit()
    
    return cursor.lastrowid  # type: ignore


async def delete_api_key_record(key: str) -> bool:
    """
    Delete an API key record.
    
    Args:
        key: The API key string
    
    Returns:
        True if deleted, False if not found
    """
    db = await get_db()
    
    cursor = await db.execute("DELETE FROM api_keys WHERE key = ?", (key,))
    await db.commit()
    
    return cursor.rowcount > 0


async def list_all_api_keys() -> List[Dict[str, Any]]:
    """
    List all API keys (admin function).
    
    Returns:
        List of all API key records
    """
    db = await get_db()
    
    async with db.execute(
        """
        SELECT id, key, balance, is_owner, created_at, expires_at,
               last_used_at, total_requests, total_spent
        FROM api_keys
        ORDER BY created_at DESC
        """
    ) as cursor:
        rows = await cursor.fetchall()
        
        return [
            {
                "id": row[0],
                "key": row[1],
                "balance": row[2],
                "is_owner": bool(row[3]),
                "created_at": row[4],
                "expires_at": row[5],
                "last_used_at": row[6],
                "total_requests": row[7],
                "total_spent": row[8],
            }
            for row in rows
        ]


# =============================================================================
# USAGE LOGGING
# =============================================================================

async def log_usage(
    api_key: str,
    action: str,
    amount: float = 0.0,
    success: bool = True,
    metadata: Optional[Dict] = None
):
    """
    Log an API usage event.
    
    Args:
        api_key: The API key used
        action: Action type (solve, balance_check, etc.)
        amount: Cost deducted
        success: Whether the action succeeded
        metadata: Optional extra data (stored as JSON)
    """
    db = await get_db()
    
    # Get API key ID
    key_data = await get_api_key(api_key)
    if not key_data:
        return
    
    import json
    now = datetime.now().isoformat()
    
    await db.execute(
        """
        INSERT INTO usage_logs (api_key_id, action, amount, success, timestamp, metadata)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            key_data["id"],
            action,
            amount,
            int(success),
            now,
            json.dumps(metadata) if metadata else None
        )
    )
    await db.commit()


async def get_usage_stats(api_key: str, days: int = 30) -> Dict[str, Any]:
    """
    Get usage statistics for an API key.
    
    Args:
        api_key: The API key
        days: Number of days to look back
    
    Returns:
        Dict with usage statistics
    """
    db = await get_db()
    
    # Get API key ID
    key_data = await get_api_key(api_key)
    if not key_data:
        return {"error": "Key not found"}
    
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    
    async with db.execute(
        """
        SELECT 
            COUNT(*) as total_requests,
            SUM(amount) as total_spent,
            SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successful,
            SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as failed
        FROM usage_logs
        WHERE api_key_id = ? AND timestamp >= ?
        """,
        (key_data["id"], cutoff)
    ) as cursor:
        row = await cursor.fetchone()
        
        return {
            "period_days": days,
            "total_requests": row[0] or 0,
            "total_spent": row[1] or 0.0,
            "successful": row[2] or 0,
            "failed": row[3] or 0,
            "success_rate": (row[2] / row[0] * 100) if row[0] else 0,
        }
