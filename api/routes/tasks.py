"""
Task Routes
Handles /createTask, /getTaskResult endpoints
"""

import logging
import asyncio
from flask import Blueprint, request, jsonify
from typing import Dict, Any, Optional

from ...core.task_manager import get_task_manager, TaskStatus
from ...core.config import get_config
from ..middleware.auth import validate_api_key, deduct_balance
from ...solvers import solve_captcha

logger = logging.getLogger(__name__)

tasks_bp = Blueprint('tasks', __name__)


# Error codes
ERROR_CODES = {
    "SUCCESS": 0,
    "ERROR_KEY_DOES_NOT_EXIST": 1,
    "ERROR_NO_SLOT_AVAILABLE": 2,
    "ERROR_ZERO_BALANCE": 3,
    "ERROR_WRONG_CAPTCHA_ID": 10,
    "ERROR_TIMEOUT": 11,
    "ERROR_RECAPTCHA_BLOCKED": 12,
    "ERROR_PROXY_CONNECT_REFUSED": 13,
    "ERROR_CAPTCHA_UNSOLVABLE": 14,
    "ERROR_BAD_PARAMETERS": 15,
}


def parse_proxy(proxy_data: Optional[Dict]) -> Optional[Dict]:
    """Parse proxy configuration from request"""
    if not proxy_data:
        return None
    
    proxy = {
        "server": f"{proxy_data.get('type', 'http')}://{proxy_data.get('address')}:{proxy_data.get('port')}"
    }
    
    if proxy_data.get('username') and proxy_data.get('password'):
        proxy["username"] = proxy_data['username']
        proxy["password"] = proxy_data['password']
    
    return proxy


@tasks_bp.route('/createTask', methods=['POST'])
def create_task():
    """
    Create a new captcha solving task.
    
    Request:
    {
        "clientKey": "api-key",
        "task": {
            "type": "RecaptchaV2Task",
            "websiteURL": "https://example.com",
            "websiteKey": "6Le-xxxxx",
            "recaptchaType": "normal",
            "isInvisible": false,
            "proxy": {...}
        }
    }
    
    Response:
    {
        "errorId": 0,
        "taskId": "uuid"
    }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({
                "errorId": ERROR_CODES["ERROR_BAD_PARAMETERS"],
                "errorMessage": "Missing request body"
            }), 400
        
        client_key = data.get('clientKey')
        task_data = data.get('task', {})
        
        # Validate API key
        if not client_key:
            return jsonify({
                "errorId": ERROR_CODES["ERROR_KEY_DOES_NOT_EXIST"],
                "errorMessage": "Missing clientKey"
            }), 400
        
        key_valid, key_error = validate_api_key(client_key)
        if not key_valid:
            return jsonify({
                "errorId": ERROR_CODES["ERROR_KEY_DOES_NOT_EXIST"],
                "errorMessage": key_error
            }), 401
        
        # Validate required fields
        website_url = task_data.get('websiteURL')
        website_key = task_data.get('websiteKey')
        task_type = task_data.get('type', 'RecaptchaV2TaskProxyless')
        
        if not website_url or not website_key:
            return jsonify({
                "errorId": ERROR_CODES["ERROR_BAD_PARAMETERS"],
                "errorMessage": "Missing websiteURL or websiteKey"
            }), 400
        
        # Determine reCAPTCHA type
        recaptcha_type = task_data.get('recaptchaType', 'normal')
        if task_data.get('isInvisible'):
            recaptcha_type = 'invisible'
        if 'Enterprise' in task_type:
            recaptcha_type = 'enterprise'
        
        # Parse proxy
        proxy = parse_proxy(task_data.get('proxy'))
        
        # Create task
        task_manager = get_task_manager()
        task = task_manager.create_task(
            task_type=task_type,
            website_url=website_url,
            website_key=website_key,
            recaptcha_type=recaptcha_type,
            client_key=client_key,
            proxy=proxy,
            user_agent=task_data.get('userAgent'),
            cookies=task_data.get('cookies'),
            is_invisible=task_data.get('isInvisible', False),
            page_action=task_data.get('pageAction'),
            enterprise_payload=task_data.get('enterprisePayload'),
            api_domain=task_data.get('apiDomain'),
        )
        
        # Start async solving in background
        asyncio.create_task(process_task(task.id))
        
        return jsonify({
            "errorId": 0,
            "taskId": task.id
        })
        
    except Exception as e:
        logger.error(f"Error creating task: {e}")
        return jsonify({
            "errorId": 99,
            "errorMessage": str(e)
        }), 500


@tasks_bp.route('/getTaskResult', methods=['POST'])
def get_task_result():
    """
    Get the result of a task.
    
    Request:
    {
        "clientKey": "api-key",
        "taskId": "uuid"
    }
    
    Response (processing):
    {
        "errorId": 0,
        "status": "processing"
    }
    
    Response (ready):
    {
        "errorId": 0,
        "status": "ready",
        "solution": {
            "gRecaptchaResponse": "03AGdBq26..."
        }
    }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({
                "errorId": ERROR_CODES["ERROR_BAD_PARAMETERS"],
                "errorMessage": "Missing request body"
            }), 400
        
        client_key = data.get('clientKey')
        task_id = data.get('taskId')
        
        # Validate API key
        if not client_key:
            return jsonify({
                "errorId": ERROR_CODES["ERROR_KEY_DOES_NOT_EXIST"],
                "errorMessage": "Missing clientKey"
            }), 400
        
        key_valid, _ = validate_api_key(client_key)
        if not key_valid:
            return jsonify({
                "errorId": ERROR_CODES["ERROR_KEY_DOES_NOT_EXIST"],
                "errorMessage": "Invalid API key"
            }), 401
        
        # Get task
        task_manager = get_task_manager()
        task = task_manager.get_task(task_id)
        
        if not task:
            return jsonify({
                "errorId": ERROR_CODES["ERROR_WRONG_CAPTCHA_ID"],
                "errorMessage": "Task not found"
            }), 404
        
        # Verify ownership
        if task.client_key != client_key:
            return jsonify({
                "errorId": ERROR_CODES["ERROR_WRONG_CAPTCHA_ID"],
                "errorMessage": "Task not found"
            }), 404
        
        return jsonify(task.get_result())
        
    except Exception as e:
        logger.error(f"Error getting task result: {e}")
        return jsonify({
            "errorId": 99,
            "errorMessage": str(e)
        }), 500


@tasks_bp.route('/solve', methods=['POST'])
def solve_direct():
    """
    Direct/simple solve endpoint.
    Blocks until solution is ready.
    
    Request:
    {
        "api_key": "key",
        "url": "https://example.com",
        "sitekey": "6Le-xxxxx",
        "type": "normal",
        "proxy": "host:port:user:pass"
    }
    
    Response:
    {
        "success": true,
        "token": "03AGdBq26...",
        "elapsed_time": 12.5
    }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"success": False, "error": "Missing request body"}), 400
        
        api_key = data.get('api_key')
        url = data.get('url')
        sitekey = data.get('sitekey')
        captcha_type = data.get('type', 'normal')
        proxy_str = data.get('proxy')
        
        # Validate
        if not api_key:
            return jsonify({"success": False, "error": "Missing api_key"}), 400
        
        key_valid, key_error = validate_api_key(api_key)
        if not key_valid:
            return jsonify({"success": False, "error": key_error}), 401
        
        if not url or not sitekey:
            return jsonify({"success": False, "error": "Missing url or sitekey"}), 400
        
        # Parse proxy string
        proxy = None
        if proxy_str:
            parts = proxy_str.split(':')
            if len(parts) >= 2:
                proxy = {"server": f"http://{parts[0]}:{parts[1]}"}
                if len(parts) >= 4:
                    proxy["username"] = parts[2]
                    proxy["password"] = parts[3]
        
        # Solve synchronously
        import time
        start_time = time.time()
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            result = loop.run_until_complete(
                solve_captcha(
                    url=url,
                    sitekey=sitekey,
                    captcha_type=captcha_type,
                    proxy=proxy,
                    is_invisible=data.get('invisible', False),
                    action=data.get('action'),
                    enterprise_payload=data.get('enterprise_payload'),
                )
            )
        finally:
            loop.close()
        
        elapsed = time.time() - start_time
        
        if result.get('success'):
            # Deduct balance
            config = get_config()
            price = getattr(config.pricing, f"{captcha_type}_v2", 0.001)
            deduct_balance(api_key, price)
            
            return jsonify({
                "success": True,
                "token": result.get('token'),
                "elapsed_time": round(elapsed, 2),
                "method": result.get('method', 'unknown'),
                "cost": price
            })
        else:
            return jsonify({
                "success": False,
                "error": result.get('error', 'Unknown error'),
                "elapsed_time": round(elapsed, 2)
            })
        
    except Exception as e:
        logger.error(f"Error in direct solve: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


async def process_task(task_id: str):
    """
    Process a task asynchronously.
    Called after task creation.
    """
    task_manager = get_task_manager()
    task = task_manager.get_task(task_id)
    
    if not task:
        return
    
    # Update status to processing
    task_manager.update_task_status(task_id, TaskStatus.PROCESSING)
    
    try:
        # Solve the captcha
        result = await solve_captcha(
            url=task.website_url,
            sitekey=task.website_key,
            captcha_type=task.recaptcha_type,
            proxy=task.proxy,
            is_invisible=task.is_invisible,
            action=task.page_action,
            enterprise_payload=task.enterprise_payload,
        )
        
        if result.get('success'):
            # Get pricing
            config = get_config()
            price = getattr(config.pricing, f"{task.recaptcha_type}_v2", 0.001)
            
            # Deduct balance
            deduct_balance(task.client_key, price)
            
            # Update task with solution
            task_manager.update_task_status(
                task_id,
                TaskStatus.READY,
                solution={
                    "gRecaptchaResponse": result['token'],
                    "token": result['token'],
                },
                cost=price
            )
        else:
            task_manager.update_task_status(
                task_id,
                TaskStatus.FAILED,
                error_id=ERROR_CODES.get("ERROR_CAPTCHA_UNSOLVABLE", 14),
                error_message=result.get('error', 'Failed to solve')
            )
            
    except Exception as e:
        logger.error(f"Error processing task {task_id}: {e}")
        task_manager.update_task_status(
            task_id,
            TaskStatus.FAILED,
            error_id=99,
            error_message=str(e)
        )
