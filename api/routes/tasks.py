"""
Task Routes - FastAPI
======================

Handles /createTask, /getTaskResult, /solve endpoints.

ALL ROUTES ARE ASYNC - This is critical for:
1. Non-blocking browser operations (Patchright/Playwright)
2. Concurrent request handling
3. Proper integration with asyncio event loop
"""

import logging
import time
import asyncio
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field

from fastapi import APIRouter, HTTPException, BackgroundTasks

from ...core.task_manager import get_task_manager, TaskStatus
from ...core.config import get_config
from ..middleware.auth import validate_api_key, deduct_balance
from ...solvers import solve_captcha

logger = logging.getLogger(__name__)

router = APIRouter()


# =============================================================================
# PYDANTIC MODELS - Request/Response validation
# =============================================================================

class ProxyConfig(BaseModel):
    """Proxy configuration"""
    type: str = "http"
    address: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None


class TaskData(BaseModel):
    """Task creation data"""
    type: str = "RecaptchaV2TaskProxyless"
    websiteURL: str
    websiteKey: str
    recaptchaType: Optional[str] = "normal"
    isInvisible: Optional[bool] = False
    proxy: Optional[ProxyConfig] = None
    userAgent: Optional[str] = None
    cookies: Optional[str] = None
    pageAction: Optional[str] = None
    enterprisePayload: Optional[Dict[str, Any]] = None
    apiDomain: Optional[str] = None


class CreateTaskRequest(BaseModel):
    """Request body for /createTask"""
    clientKey: str
    task: TaskData


class GetTaskResultRequest(BaseModel):
    """Request body for /getTaskResult"""
    clientKey: str
    taskId: str


class DirectSolveRequest(BaseModel):
    """Request body for /solve"""
    api_key: str
    url: str
    sitekey: str
    type: str = "normal"
    proxy: Optional[str] = None
    invisible: bool = False
    action: Optional[str] = None
    enterprise_payload: Optional[Dict[str, Any]] = None


# =============================================================================
# ERROR CODES - 2Captcha compatible
# =============================================================================

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


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def parse_proxy(proxy_data: Optional[ProxyConfig]) -> Optional[Dict]:
    """Parse proxy configuration from request"""
    if not proxy_data:
        return None
    
    proxy = {
        "server": f"{proxy_data.type}://{proxy_data.address}:{proxy_data.port}"
    }
    
    if proxy_data.username and proxy_data.password:
        proxy["username"] = proxy_data.username
        proxy["password"] = proxy_data.password
    
    return proxy


def parse_proxy_string(proxy_str: Optional[str]) -> Optional[Dict]:
    """Parse proxy from string format: host:port:user:pass"""
    if not proxy_str:
        return None
    
    parts = proxy_str.split(':')
    if len(parts) >= 2:
        proxy = {"server": f"http://{parts[0]}:{parts[1]}"}
        if len(parts) >= 4:
            proxy["username"] = parts[2]
            proxy["password"] = parts[3]
        return proxy
    
    return None


# =============================================================================
# BACKGROUND TASK PROCESSOR
# =============================================================================

async def process_task(task_id: str):
    """
    Process a task asynchronously.
    Called as a background task after task creation.
    
    This runs in the SAME event loop as the request handlers,
    which is why FastAPI + async is essential.
    """
    task_manager = get_task_manager()
    task = task_manager.get_task(task_id)
    
    if not task:
        return
    
    # Update status to processing
    task_manager.update_task_status(task_id, TaskStatus.PROCESSING)
    
    try:
        # Solve the captcha (fully async!)
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
            await deduct_balance(task.client_key, price)
            
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


# =============================================================================
# ROUTES - All async!
# =============================================================================

@router.post("/createTask")
async def create_task(
    request: CreateTaskRequest,
    background_tasks: BackgroundTasks
):
    """
    Create a new captcha solving task.
    
    The task is processed in the background. Use /getTaskResult to poll for results.
    
    Request:
    ```json
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
    ```
    
    Response:
    ```json
    {
        "errorId": 0,
        "taskId": "uuid"
    }
    ```
    """
    try:
        # Validate API key
        key_valid, key_error = await validate_api_key(request.clientKey)
        if not key_valid:
            return {
                "errorId": ERROR_CODES["ERROR_KEY_DOES_NOT_EXIST"],
                "errorMessage": key_error
            }
        
        task_data = request.task
        
        # Determine reCAPTCHA type
        recaptcha_type = task_data.recaptchaType or "normal"
        if task_data.isInvisible:
            recaptcha_type = "invisible"
        if "Enterprise" in task_data.type:
            recaptcha_type = "enterprise"
        
        # Parse proxy
        proxy = parse_proxy(task_data.proxy)
        
        # Create task
        task_manager = get_task_manager()
        task = task_manager.create_task(
            task_type=task_data.type,
            website_url=task_data.websiteURL,
            website_key=task_data.websiteKey,
            recaptcha_type=recaptcha_type,
            client_key=request.clientKey,
            proxy=proxy,
            user_agent=task_data.userAgent,
            cookies=task_data.cookies,
            is_invisible=task_data.isInvisible or False,
            page_action=task_data.pageAction,
            enterprise_payload=task_data.enterprisePayload,
            api_domain=task_data.apiDomain,
        )
        
        # Start background processing
        # FastAPI's BackgroundTasks integrates properly with asyncio
        background_tasks.add_task(process_task, task.id)
        
        return {
            "errorId": 0,
            "taskId": task.id
        }
        
    except Exception as e:
        logger.error(f"Error creating task: {e}")
        return {
            "errorId": 99,
            "errorMessage": str(e)
        }


@router.post("/getTaskResult")
async def get_task_result(request: GetTaskResultRequest):
    """
    Get the result of a task.
    
    Poll this endpoint until status is "ready" or "failed".
    
    Request:
    ```json
    {
        "clientKey": "api-key",
        "taskId": "uuid"
    }
    ```
    
    Response (processing):
    ```json
    {
        "errorId": 0,
        "status": "processing"
    }
    ```
    
    Response (ready):
    ```json
    {
        "errorId": 0,
        "status": "ready",
        "solution": {
            "gRecaptchaResponse": "03AGdBq26..."
        }
    }
    ```
    """
    try:
        # Validate API key
        key_valid, _ = await validate_api_key(request.clientKey)
        if not key_valid:
            return {
                "errorId": ERROR_CODES["ERROR_KEY_DOES_NOT_EXIST"],
                "errorMessage": "Invalid API key"
            }
        
        # Get task
        task_manager = get_task_manager()
        task = task_manager.get_task(request.taskId)
        
        if not task:
            return {
                "errorId": ERROR_CODES["ERROR_WRONG_CAPTCHA_ID"],
                "errorMessage": "Task not found"
            }
        
        # Verify ownership
        if task.client_key != request.clientKey:
            return {
                "errorId": ERROR_CODES["ERROR_WRONG_CAPTCHA_ID"],
                "errorMessage": "Task not found"
            }
        
        return task.get_result()
        
    except Exception as e:
        logger.error(f"Error getting task result: {e}")
        return {
            "errorId": 99,
            "errorMessage": str(e)
        }


@router.post("/solve")
async def solve_direct(request: DirectSolveRequest):
    """
    Direct/simple solve endpoint - blocks until solution is ready.
    
    This is a convenience endpoint that combines createTask + getTaskResult.
    
    Request:
    ```json
    {
        "api_key": "key",
        "url": "https://example.com",
        "sitekey": "6Le-xxxxx",
        "type": "normal",
        "proxy": "host:port:user:pass"
    }
    ```
    
    Response:
    ```json
    {
        "success": true,
        "token": "03AGdBq26...",
        "elapsed_time": 12.5
    }
    ```
    """
    try:
        # Validate API key
        key_valid, key_error = await validate_api_key(request.api_key)
        if not key_valid:
            return {"success": False, "error": key_error}
        
        # Parse proxy string
        proxy = parse_proxy_string(request.proxy)
        
        # Solve - this is now properly async!
        start_time = time.time()
        
        result = await solve_captcha(
            url=request.url,
            sitekey=request.sitekey,
            captcha_type=request.type,
            proxy=proxy,
            is_invisible=request.invisible,
            action=request.action,
            enterprise_payload=request.enterprise_payload,
        )
        
        elapsed = time.time() - start_time
        
        if result.get('success'):
            # Deduct balance
            config = get_config()
            price = getattr(config.pricing, f"{request.type}_v2", 0.001)
            await deduct_balance(request.api_key, price)
            
            return {
                "success": True,
                "token": result.get('token'),
                "elapsed_time": round(elapsed, 2),
                "method": result.get('method', 'unknown'),
                "cost": price
            }
        else:
            return {
                "success": False,
                "error": result.get('error', 'Unknown error'),
                "elapsed_time": round(elapsed, 2)
            }
        
    except Exception as e:
        logger.error(f"Error in direct solve: {e}")
        return {"success": False, "error": str(e)}
