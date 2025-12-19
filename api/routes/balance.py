"""
Balance Routes
Handles /getBalance, /addBalance endpoints
"""

import logging
from flask import Blueprint, request, jsonify

from ..middleware.auth import (
    validate_api_key, 
    get_balance, 
    add_balance as add_key_balance,
    is_owner_key
)

logger = logging.getLogger(__name__)

balance_bp = Blueprint('balance', __name__)


@balance_bp.route('/getBalance', methods=['POST'])
def get_balance_route():
    """
    Get account balance.
    
    Request:
    {
        "clientKey": "api-key"
    }
    
    Response:
    {
        "errorId": 0,
        "balance": 10.5
    }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({
                "errorId": 15,
                "errorMessage": "Missing request body"
            }), 400
        
        client_key = data.get('clientKey')
        
        if not client_key:
            return jsonify({
                "errorId": 1,
                "errorMessage": "Missing clientKey"
            }), 400
        
        key_valid, key_error = validate_api_key(client_key)
        if not key_valid:
            return jsonify({
                "errorId": 1,
                "errorMessage": key_error
            }), 401
        
        balance = get_balance(client_key)
        
        return jsonify({
            "errorId": 0,
            "balance": balance
        })
        
    except Exception as e:
        logger.error(f"Error getting balance: {e}")
        return jsonify({
            "errorId": 99,
            "errorMessage": str(e)
        }), 500


@balance_bp.route('/addBalance', methods=['POST'])
def add_balance():
    """
    Add balance to an account (admin only).
    
    Request:
    {
        "clientKey": "admin-key",
        "targetKey": "user-key",
        "amount": 10.0
    }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({
                "errorId": 15,
                "errorMessage": "Missing request body"
            }), 400
        
        client_key = data.get('clientKey')
        target_key = data.get('targetKey')
        amount = data.get('amount', 0)
        
        # Validate admin key
        if not client_key or not is_owner_key(client_key):
            return jsonify({
                "errorId": 1,
                "errorMessage": "Unauthorized"
            }), 401
        
        if not target_key:
            return jsonify({
                "errorId": 15,
                "errorMessage": "Missing targetKey"
            }), 400
        
        if amount <= 0:
            return jsonify({
                "errorId": 15,
                "errorMessage": "Invalid amount"
            }), 400
        
        # Add balance
        new_balance = add_key_balance(target_key, amount)
        
        return jsonify({
            "errorId": 0,
            "balance": new_balance,
            "message": f"Added {amount} to {target_key}"
        })
        
    except Exception as e:
        logger.error(f"Error adding balance: {e}")
        return jsonify({
            "errorId": 99,
            "errorMessage": str(e)
        }), 500


@balance_bp.route('/checkBalance', methods=['GET'])
def check_balance():
    """
    Simple balance check (GET request).
    
    Query params:
    - key: API key
    """
    try:
        api_key = request.args.get('key')
        
        if not api_key:
            return jsonify({
                "status": "error",
                "message": "Missing key parameter"
            }), 400
        
        key_valid, key_error = validate_api_key(api_key)
        if not key_valid:
            return jsonify({
                "status": "error",
                "message": key_error
            }), 401
        
        balance = get_balance(api_key)
        
        return jsonify({
            "status": "success",
            "balance": balance
        })
        
    except Exception as e:
        logger.error(f"Error checking balance: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500
