"""
Balance Routes - FastAPI
========================

Handles /getBalance, /addBalance endpoints.
"""

import logging
from typing import Optional
from pydantic import BaseModel

from fastapi import APIRouter

from ..middleware.auth import (
    validate_api_key, 
    add_balance as add_key_balance,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# =============================================================================
# PYDANTIC MODELS
# =============================================================================

class GetBalanceRequest(BaseModel):
    """Request body for /getBalance"""
    clientKey: str


class AddBalanceRequest(BaseModel):
    """Request body for /addBalance"""
    clientKey: str
    targetKey: str
    amount: float


# =============================================================================
# ROUTES
# =============================================================================

@router.post("/getBalance")
async def get_balance_route(request: GetBalanceRequest):
    """
    Get account balance.
    
    Request:
    ```json
    {
        "clientKey": "api-key"
    }
    ```
    
    Response:
    ```json
    {
        "errorId": 0,
        "balance": 10.5
    }
    ```
    """
    try:
        key_valid, key_error, key_data = await validate_api_key(request.clientKey)
        if not key_valid:
            return {
                "errorId": 1,
                "errorMessage": key_error
            }
        
        # Get balance from key_data (no extra DB call!)
        balance = key_data.get("balance", 0.0) if key_data else 0.0
        
        return {
            "errorId": 0,
            "balance": balance
        }
        
    except Exception as e:
        logger.error(f"Error getting balance: {e}")
        return {
            "errorId": 99,
            "errorMessage": str(e)
        }


@router.post("/addBalance")
async def add_balance(request: AddBalanceRequest):
    """
    Add balance to an account (admin only).
    
    Request:
    ```json
    {
        "clientKey": "admin-key",
        "targetKey": "user-key",
        "amount": 10.0
    }
    ```
    
    Response:
    ```json
    {
        "errorId": 0,
        "balance": 20.5
    }
    ```
    """
    try:
        # Validate admin key and get key_data
        key_valid, key_error, key_data = await validate_api_key(request.clientKey)
        if not key_valid:
            return {
                "errorId": 1,
                "errorMessage": key_error
            }
        
        # Check if admin from key_data (no extra DB call!)
        if not (key_data and key_data.get("is_owner", False)):
            return {
                "errorId": 2,
                "errorMessage": "Insufficient privileges"
            }
        
        # Validate target key exists
        target_valid, _, _ = await validate_api_key(request.targetKey)
        if not target_valid:
            return {
                "errorId": 1,
                "errorMessage": "Target key not found"
            }
        
        # Add balance
        new_balance = await add_key_balance(request.targetKey, request.amount)
        
        return {
            "errorId": 0,
            "balance": new_balance
        }
        
    except Exception as e:
        logger.error(f"Error adding balance: {e}")
        return {
            "errorId": 99,
            "errorMessage": str(e)
        }
