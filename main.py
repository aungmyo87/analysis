"""
Unified reCAPTCHA Solver - Main Entry Point
============================================

FastAPI server with proper async lifecycle management.

STARTUP SEQUENCE:
1. Load configuration
2. Initialize SQLite database (async connection pool)
3. Initialize YOLO model (singleton - loaded ONCE)
4. Initialize Browser Pool (persistent browser processes)
5. Start accepting requests

This ensures NO cold-start latency on first request.

Run with:
    python main.py
    
Or for production:
    uvicorn main:app --host 0.0.0.0 --port 8080 --workers 1

NOTE: Use workers=1 because:
- Browser pool is process-local (not shared across workers)
- YOLO model is loaded per-process
- For scaling, use multiple containers/VMs instead
"""

import asyncio
import logging
import signal
import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from api.app import create_app
from core.config import get_config, load_config
from core.browser_pool import get_browser_pool, close_browser_pool
from challenges.image_solver import load_yolo_model, get_yolo_model
from database import init_db, close_db
from utils.logger import setup_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI Lifespan Context Manager
    
    Handles startup and shutdown events properly.
    All expensive initializations happen HERE, not on first request.
    """
    # ==========================================================================
    # STARTUP - Initialize all singletons BEFORE accepting requests
    # ==========================================================================
    logger.info("=" * 60)
    logger.info("STARTUP: Initializing reCAPTCHA Solver...")
    logger.info("=" * 60)
    
    # 1. Initialize SQLite database
    logger.info("[1/3] Initializing SQLite database...")
    try:
        await init_db()
        logger.info("[1/3] Database initialized successfully")
    except Exception as e:
        logger.error(f"[1/3] Failed to initialize database: {e}")
        raise  # Can't operate without database
    
    # 2. Load YOLO model (singleton - ~2-5 seconds, done ONCE)
    logger.info("[2/3] Loading YOLO model...")
    try:
        model = load_yolo_model()
        logger.info(f"[2/3] YOLO model loaded: {type(model).__name__}")
    except Exception as e:
        logger.error(f"[2/3] Failed to load YOLO model: {e}")
        # Continue without model - will fallback to audio solver
    
    # 3. Initialize Browser Pool (launches persistent browsers)
    logger.info("[3/3] Initializing browser pool...")
    try:
        pool = await get_browser_pool()
        stats = pool.get_stats()
        logger.info(f"[3/3] Browser pool ready: {stats['browser_count']} browsers, "
                   f"max {stats['max_total_capacity']} concurrent contexts")
    except Exception as e:
        logger.error(f"[3/3] Failed to initialize browser pool: {e}")
        raise  # Can't operate without browsers
    
    logger.info("=" * 60)
    logger.info("STARTUP COMPLETE - Server ready to accept requests")
    logger.info("=" * 60)
    
    # Yield control to the application
    yield
    
    # ==========================================================================
    # SHUTDOWN - Clean up resources
    # ==========================================================================
    logger.info("=" * 60)
    logger.info("SHUTDOWN: Cleaning up resources...")
    logger.info("=" * 60)
    
    # Close browser pool
    try:
        await close_browser_pool()
        logger.info("Browser pool closed")
    except Exception as e:
        logger.error(f"Error closing browser pool: {e}")
    
    # Close database connection
    try:
        await close_db()
        logger.info("Database connection closed")
    except Exception as e:
        logger.error(f"Error closing database: {e}")
    
    logger.info("Shutdown complete")


# Create the FastAPI application with lifespan
app = create_app(lifespan=lifespan)


def main():
    """Main entry point - starts the uvicorn server"""
    # Load config first
    config = load_config()
    
    # Setup logging
    setup_logging(
        level=config.logging.level,
        log_file=config.logging.file,
        format_type=config.logging.format
    )
    
    logger.info("Starting Unified reCAPTCHA Solver")
    logger.info(f"Server: {config.server.host}:{config.server.port}")
    logger.info(f"Browser pool size: {config.browser.pool_size}")
    logger.info(f"Primary solve method: {config.solver.primary_method}")
    
    # Run with uvicorn
    uvicorn.run(
        "main:app",
        host=config.server.host,
        port=config.server.port,
        reload=config.server.debug,
        workers=1,  # Single worker - see note above
        log_level="info" if not config.server.debug else "debug",
        access_log=True,
    )


if __name__ == "__main__":
    main()
