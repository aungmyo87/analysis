"""
Database Module
===============

SQLite database with async support via aiosqlite.
"""

from .db import (
    # Connection management
    get_db,
    init_db,
    close_db,
    
    # API Key operations
    get_api_key,
    update_api_key_balance,
    increment_api_key_stats,
    create_api_key_record,
    delete_api_key_record,
    list_all_api_keys,
    
    # Usage logging
    log_usage,
    get_usage_stats,
    
    # Constants
    DB_PATH,
)

# Aliases for backward compatibility with auth.py
create_api_key = create_api_key_record
get_all_api_keys = list_all_api_keys

__all__ = [
    # Connection management
    "get_db",
    "init_db",
    "close_db",
    
    # API Key operations
    "get_api_key",
    "update_api_key_balance",
    "increment_api_key_stats",
    "create_api_key_record",
    "delete_api_key_record",
    "list_all_api_keys",
    
    # Aliases
    "create_api_key",
    "get_all_api_keys",
    
    # Usage logging
    "log_usage",
    "get_usage_stats",
    
    # Constants
    "DB_PATH",
]
