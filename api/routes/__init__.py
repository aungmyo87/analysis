"""
API Routes Module
"""

from .tasks import tasks_bp
from .balance import balance_bp
from .health import health_bp

__all__ = ['tasks_bp', 'balance_bp', 'health_bp']
