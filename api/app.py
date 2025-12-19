"""
FastAPI Application Factory
============================

Creates and configures the FastAPI app with async-native routes.

WHY FASTAPI OVER FLASK:
-----------------------
1. Native async/await support - no more RuntimeError: no running event loop
2. Automatic OpenAPI documentation at /docs
3. Pydantic validation for request/response models
4. Better performance with Starlette underneath
5. Proper lifecycle management with lifespan context

ARCHITECTURE:
------------
┌─────────────────────────────────────────────────────────────────┐
│                        FastAPI App                               │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐ │
│  │   /health   │  │  /api/v1/   │  │   Middleware            │ │
│  │   /status   │  │  createTask │  │   - CORS                │ │
│  │   /ready    │  │  getResult  │  │   - Exception Handler   │ │
│  │   /live     │  │  solve      │  │   - Request Logging     │ │
│  └─────────────┘  │  balance    │  └─────────────────────────┘ │
│                   └─────────────┘                               │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                    Lifespan Manager                       │  │
│  │  STARTUP:  Load YOLO Model → Init Browser Pool           │  │
│  │  SHUTDOWN: Close Browsers → Cleanup Resources            │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
"""

import logging
import time
from typing import Optional, Callable, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from ..core.config import get_config

logger = logging.getLogger(__name__)


def create_app(lifespan: Optional[Callable] = None) -> FastAPI:
    """
    Create and configure the FastAPI application.
    
    Args:
        lifespan: Async context manager for startup/shutdown events
    
    Returns:
        Configured FastAPI app
    """
    config = get_config()
    
    # Create FastAPI app with lifespan
    app = FastAPI(
        title="Unified reCAPTCHA Solver",
        description="High-performance async reCAPTCHA solving API",
        version="2.0.0",
        docs_url="/docs" if config.server.debug else None,
        redoc_url="/redoc" if config.server.debug else None,
        lifespan=lifespan,
    )
    
    # ==========================================================================
    # CORS Middleware - Allow all origins for API access
    # ==========================================================================
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # ==========================================================================
    # Request Logging Middleware
    # ==========================================================================
    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        """Log all incoming requests with timing"""
        start_time = time.time()
        
        # Process request
        response = await call_next(request)
        
        # Calculate duration
        duration = time.time() - start_time
        
        # Log (skip health checks for cleaner logs)
        if not request.url.path.startswith("/health"):
            logger.info(
                f"{request.method} {request.url.path} - "
                f"{response.status_code} - {duration:.3f}s"
            )
        
        return response
    
    # ==========================================================================
    # Exception Handlers
    # ==========================================================================
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        """Handle Pydantic validation errors"""
        return JSONResponse(
            status_code=400,
            content={
                "errorId": 15,
                "errorMessage": "Bad request parameters",
                "details": exc.errors()
            }
        )
    
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        """Handle HTTP exceptions"""
        error_map = {
            400: 15,  # BAD_PARAMETERS
            401: 1,   # KEY_DOES_NOT_EXIST
            404: 16,  # NOT_FOUND
            500: 99,  # INTERNAL_ERROR
        }
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "errorId": error_map.get(exc.status_code, 99),
                "errorMessage": exc.detail
            }
        )
    
    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        """Handle uncaught exceptions"""
        logger.error(f"Unhandled exception: {exc}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "errorId": 99,
                "errorMessage": "Internal server error"
            }
        )
    
    # ==========================================================================
    # Register Routers
    # ==========================================================================
    from .routes.tasks import router as tasks_router
    from .routes.balance import router as balance_router
    from .routes.health import router as health_router
    
    # Health routes at root level
    app.include_router(health_router, tags=["Health"])
    
    # API routes with /api/v1 prefix
    app.include_router(tasks_router, prefix="/api/v1", tags=["Tasks"])
    app.include_router(balance_router, prefix="/api/v1", tags=["Balance"])
    
    # Also mount at root for 2captcha compatibility
    app.include_router(tasks_router, tags=["Tasks (Root)"])
    app.include_router(balance_router, tags=["Balance (Root)"])
    
    logger.info(f"FastAPI app created, debug={config.server.debug}")
    
    return app
