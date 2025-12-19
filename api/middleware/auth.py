"""
Authentication Middleware
Handles API key validation and balance management
"""

import json
import logging
import threading
from typing import Tuple, Optional
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

# Lock for thread-safe file operations
_lock = threading.Lock()

# Path to API keys file
KEYS_FILE = Path(__file__).parent.parent.parent / "data" / "api_keys.json"


def _load_keys() -> dict:
    """Load API keys from file"""
    if not KEYS_FILE.exists():
        # Create default keys file
        KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
        default_keys = {
            "owner_key_12345": {
                "balance": 1000.0,
                "is_owner": True,
                "created": datetime.now().isoformat(),
                "expires": None
            },
            "test_key_67890": {
                "balance": 10.0,
                "is_owner": False,
                "created": datetime.now().isoformat(),
                "expires": None
            }
        }
        with open(KEYS_FILE, 'w') as f:
            json.dump(default_keys, f, indent=2)
        return default_keys
    
    with open(KEYS_FILE, 'r') as f:
        return json.load(f)


def _save_keys(keys: dict):
    """Save API keys to file"""
    KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(KEYS_FILE, 'w') as f:
        json.dump(keys, f, indent=2)


def validate_api_key(api_key: str) -> Tuple[bool, Optional[str]]:
    """
    Validate an API key.
    
    Args:
        api_key: The API key to validate
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not api_key:
        return False, "API key is required"
    
    with _lock:
        keys = _load_keys()
        
        if api_key not in keys:
            return False, "Invalid API key"
        
        key_data = keys[api_key]
        
        # Check expiry
        if key_data.get('expires'):
            expires = datetime.fromisoformat(key_data['expires'])
            if datetime.now() > expires:
                return False, "API key has expired"
        
        # Check balance
        if key_data.get('balance', 0) <= 0:
            return False, "Insufficient balance"
        
        return True, None


def get_balance(api_key: str) -> float:
    """Get the balance for an API key"""
    with _lock:
        keys = _load_keys()
        if api_key in keys:
            return keys[api_key].get('balance', 0.0)
        return 0.0


def deduct_balance(api_key: str, amount: float) -> float:
    """
    Deduct balance from an API key.
    
    Args:
        api_key: The API key
        amount: Amount to deduct
    
    Returns:
        New balance
    """
    with _lock:
        keys = _load_keys()
        
        if api_key not in keys:
            return 0.0
        
        current_balance = keys[api_key].get('balance', 0.0)
        new_balance = max(0, current_balance - amount)
        keys[api_key]['balance'] = new_balance
        
        _save_keys(keys)
        
        logger.debug(f"Deducted {amount} from {api_key}, new balance: {new_balance}")
        return new_balance


def add_balance(api_key: str, amount: float) -> float:
    """
    Add balance to an API key.
    
    Args:
        api_key: The API key
        amount: Amount to add
    
    Returns:
        New balance
    """
    with _lock:
        keys = _load_keys()
        
        if api_key not in keys:
            # Create new key
            keys[api_key] = {
                "balance": amount,
                "is_owner": False,
                "created": datetime.now().isoformat(),
                "expires": None
            }
        else:
            keys[api_key]['balance'] = keys[api_key].get('balance', 0) + amount
        
        _save_keys(keys)
        
        return keys[api_key]['balance']


def is_owner_key(api_key: str) -> bool:
    """Check if an API key has owner privileges"""
    with _lock:
        keys = _load_keys()
        if api_key in keys:
            return keys[api_key].get('is_owner', False)
        return False


def create_api_key(balance: float = 0.0, is_owner: bool = False, expires_days: Optional[int] = None) -> str:
    """
    Create a new API key.
    
    Args:
        balance: Initial balance
        is_owner: Whether key has owner privileges
        expires_days: Days until expiry (None for no expiry)
    
    Returns:
        New API key
    """
    import uuid
    
    new_key = f"sk_{uuid.uuid4().hex}"
    
    expires = None
    if expires_days:
        from datetime import timedelta
        expires = (datetime.now() + timedelta(days=expires_days)).isoformat()
    
    with _lock:
        keys = _load_keys()
        keys[new_key] = {
            "balance": balance,
            "is_owner": is_owner,
            "created": datetime.now().isoformat(),
            "expires": expires
        }
        _save_keys(keys)
    
    return new_key


def list_api_keys() -> dict:
    """List all API keys (admin function)"""
    with _lock:
        return _load_keys()
