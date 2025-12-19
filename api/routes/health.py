"""
Health Check Routes
"""

import logging
from flask import Blueprint, jsonify

from ...core.task_manager import get_task_manager
from ...core.browser_pool import get_browser_pool

logger = logging.getLogger(__name__)

health_bp = Blueprint('health', __name__)


@health_bp.route('/health', methods=['GET'])
def health_check():
    """Basic health check endpoint"""
    return jsonify({
        "status": "healthy",
        "service": "unified-recaptcha-solver",
        "version": "1.0.0"
    })


@health_bp.route('/status', methods=['GET'])
async def status():
    """Detailed status endpoint"""
    try:
        task_manager = get_task_manager()
        browser_pool = await get_browser_pool()
        
        return jsonify({
            "status": "healthy",
            "service": "unified-recaptcha-solver",
            "version": "1.0.0",
            "tasks": task_manager.get_stats(),
            "browsers": browser_pool.get_stats()
        })
        
    except Exception as e:
        logger.error(f"Error getting status: {e}")
        return jsonify({
            "status": "degraded",
            "error": str(e)
        }), 500


@health_bp.route('/ready', methods=['GET'])
def readiness():
    """Kubernetes readiness probe"""
    return jsonify({"ready": True})


@health_bp.route('/live', methods=['GET'])
def liveness():
    """Kubernetes liveness probe"""
    return jsonify({"alive": True})
