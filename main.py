"""
Unified reCAPTCHA Solver - Main Entry Point
Run this file to start the API server
"""

import asyncio
import logging
import signal
import sys

from api.app import create_app, run_app
from core.config import get_config, load_config
from core.browser_pool import close_browser_pool
from utils.logger import setup_logging


def main():
    """Main entry point"""
    # Load config
    config = load_config()
    
    # Setup logging
    setup_logging(
        level=config.logging.level,
        log_file=config.logging.file,
        format_type=config.logging.format
    )
    
    logger = logging.getLogger(__name__)
    logger.info("Starting Unified reCAPTCHA Solver")
    logger.info(f"Server: {config.server.host}:{config.server.port}")
    logger.info(f"Browser pool size: {config.browser.pool_size}")
    logger.info(f"Primary solve method: {config.solver.primary_method}")
    
    # Setup signal handlers for graceful shutdown
    def signal_handler(sig, frame):
        logger.info("Shutting down...")
        asyncio.get_event_loop().run_until_complete(close_browser_pool())
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Run the app
    run_app()


if __name__ == "__main__":
    main()
