"""
Health Check Routes - FastAPI
=============================

Provides health, status, and readiness endpoints.
"""

import logging
from fastapi import APIRouter

from ...core.task_manager import get_task_manager
from ...core.browser_pool import get_browser_pool
from ...challenges.image_solver import get_yolo_model

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
async def health_check():
    """Basic health check endpoint"""
    return {
        "status": "healthy",
        "service": "unified-recaptcha-solver",
        "version": "2.0.0"
    }


@router.get("/status")
async def status():
    """
    Detailed status endpoint.
    
    Returns information about:
    - Task manager statistics
    - Browser pool statistics
    - YOLO model status
    """
    try:
        task_manager = get_task_manager()
        browser_pool = await get_browser_pool()
        
        # Check if YOLO model is loaded
        yolo_model = get_yolo_model()
        yolo_status = "loaded" if yolo_model is not None else "not_loaded"
        
        return {
            "status": "healthy",
            "service": "unified-recaptcha-solver",
            "version": "2.0.0",
            "tasks": task_manager.get_stats(),
            "browsers": browser_pool.get_stats(),
            "yolo_model": yolo_status
        }
        
    except Exception as e:
        logger.error(f"Error getting status: {e}")
        return {
            "status": "degraded",
            "error": str(e)
        }


@router.get("/ready")
async def readiness():
    """
    Kubernetes readiness probe.
    
    Returns 200 only if the service is ready to accept requests.
    """
    try:
        # Check if browser pool is initialized
        browser_pool = await get_browser_pool()
        if browser_pool.browser_count_actual == 0:
            return {"ready": False, "reason": "No browsers available"}
        
        return {"ready": True}
        
    except Exception as e:
        return {"ready": False, "reason": str(e)}


@router.get("/live")
async def liveness():
    """Kubernetes liveness probe"""
    return {"alive": True}
