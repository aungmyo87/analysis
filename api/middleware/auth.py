"""
Authentication Middleware
=========================

Async API key validation and balance management using SQLite.

This module provides async functions for:
- API key validation
- Balance checking/modification
- Owner privilege verification
- API key creation

All operations use the database module with aiosqlite for
concurrent-safe async database access.
"""

import logging
import uuid
from typing import Tuple, Optional, Dict, Any
from datetime import datetime, timedelta

from database import (
    get_api_key,
    update_api_key_balance,
    create_api_key as db_create_api_key,
    get_all_api_keys,
    log_usage,
)

logger = logging.getLogger(__name__)


async def validate_api_key(api_key: str) -> Tuple[bool, Optional[str]]:
    """
    Validate an API key (async).
    
    Args:
        api_key: The API key to validate
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not api_key:
        return False, "API key is required"
    
    key_data = await get_api_key(api_key)
    
    if not key_data:
        return False, "Invalid API key"
    
    # Check expiry
    if key_data.get('expires_at'):
        try:
            expires = datetime.fromisoformat(key_data['expires_at'])
            if datetime.now() > expires:
                return False, "API key has expired"
        except (ValueError, TypeError):
            pass  # Invalid date format, ignore expiry check
    
    # Check balance
    if key_data.get('balance', 0) <= 0:
        return False, "Insufficient balance"
    
    return True, None


async def get_balance(api_key: str) -> float:
    """Get the balance for an API key (async)"""
    key_data = await get_api_key(api_key)
    if key_data:
        return key_data.get('balance', 0.0)
    return 0.0


async def deduct_balance(api_key: str, amount: float, action: str = "solve") -> float:
    """
    Deduct balance from an API key (async).
    
    Args:
        api_key: The API key
        amount: Amount to deduct
        action: Action description for logging
    
    Returns:
        New balance
    """
    key_data = await get_api_key(api_key)
    
    if not key_data:
        return 0.0
    
    current_balance = key_data.get('balance', 0.0)
    new_balance = max(0, current_balance - amount)
    
    # Update balance in database
    success = await update_api_key_balance(api_key, new_balance)
    
    if success:
        # Log the usage
        await log_usage(
            api_key=api_key,
            action=action,
            amount=amount,
            success=True,
            metadata={"previous_balance": current_balance}
        )
        logger.debug(f"Deducted {amount} from {api_key[:20]}..., new balance: {new_balance}")
    
    return new_balance


async def add_balance(api_key: str, amount: float) -> float:
    """
    Add balance to an API key (async).
    
    Args:
        api_key: The API key
        amount: Amount to add
    
    Returns:
        New balance
    """
    key_data = await get_api_key(api_key)
    
    if not key_data:
        # Create new key with the specified balance
        new_key = await db_create_api_key(
            key=api_key,
            balance=amount,
            is_owner=False
        )
        if new_key:
            return amount
        return 0.0
    
    current_balance = key_data.get('balance', 0.0)
    new_balance = current_balance + amount
    
    success = await update_api_key_balance(api_key, new_balance)
    
    if success:
        # Log the balance addition
        await log_usage(
            api_key=api_key,
            action="add_balance",
            amount=amount,
            success=True,
            metadata={"previous_balance": current_balance}
        )
    
    return new_balance if success else current_balance


async def is_owner_key(api_key: str) -> bool:
    """Check if an API key has owner privileges (async)"""
    key_data = await get_api_key(api_key)
    if key_data:
        return bool(key_data.get('is_owner', False))
    return False


async def create_api_key(
    balance: float = 0.0,
    is_owner: bool = False,
    expires_days: Optional[int] = None
) -> str:
    """
    Create a new API key (async).
    
    Args:
        balance: Initial balance
        is_owner: Whether key has owner privileges
        expires_days: Days until expiry (None for no expiry)
    
    Returns:
        New API key string
    """
    new_key = f"sk_{uuid.uuid4().hex}"
    
    expires_at = None
    if expires_days:
        expires_at = (datetime.now() + timedelta(days=expires_days)).isoformat()
    
    success = await db_create_api_key(
        key=new_key,
        balance=balance,
        is_owner=is_owner,
        expires_at=expires_at
    )
    
    if success:
        logger.info(f"Created new API key: {new_key[:20]}... (owner: {is_owner})")
        return new_key
    
    return ""


async def list_api_keys() -> Dict[str, Any]:
    """
    List all API keys (admin function, async).
    
    Returns:
        Dict mapping key -> {balance, is_owner, created_at, expires_at, ...}
    """
    keys = await get_all_api_keys()
    
    # Convert to dict format for backward compatibility
    result = {}
    for key_data in keys:
        result[key_data['key']] = {
            "balance": key_data['balance'],
            "is_owner": bool(key_data['is_owner']),
            "created": key_data['created_at'],
            "expires": key_data['expires_at'],
            "total_requests": key_data.get('total_requests', 0),
            "total_spent": key_data.get('total_spent', 0.0),
        }
    
    return result


async def get_key_stats(api_key: str) -> Optional[Dict[str, Any]]:
    """
    Get detailed statistics for an API key.
    
    Args:
        api_key: The API key to get stats for
    
    Returns:
        Dict with key statistics or None if not found
    """
    key_data = await get_api_key(api_key)
    
    if not key_data:
        return None
    
    return {
        "key": api_key,
        "balance": key_data['balance'],
        "is_owner": bool(key_data['is_owner']),
        "created_at": key_data['created_at'],
        "expires_at": key_data['expires_at'],
        "last_used_at": key_data.get('last_used_at'),
        "total_requests": key_data.get('total_requests', 0),
        "total_spent": key_data.get('total_spent', 0.0),
    }
