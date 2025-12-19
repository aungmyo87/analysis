"""
Browser Pool Manager - Context-Based Proxy Rotation Strategy
============================================================

Manages a pool of PERSISTENT Patchright Browser processes with lightweight
BrowserContext rotation for efficient proxy switching.

ARCHITECTURE (Optimized for 12-Core CPU / 24GB RAM):
----------------------------------------------------
┌─────────────────────────────────────────────────────────────────────┐
│                        BrowserPool                                   │
│  ┌──────────────┐  ┌──────────────┐       ┌──────────────┐         │
│  │  Browser #1  │  │  Browser #2  │  ...  │  Browser #N  │         │
│  │  (Process)   │  │  (Process)   │       │  (Process)   │         │
│  │              │  │              │       │              │         │
│  │  Context A   │  │  Context C   │       │  Context E   │         │
│  │  (Proxy 1)   │  │  (Proxy 3)   │       │  (Proxy 5)   │         │
│  │              │  │              │       │              │         │
│  │  Context B   │  │  Context D   │       │  Context F   │         │
│  │  (Proxy 2)   │  │  (Proxy 4)   │       │  (Proxy 6)   │         │
│  └──────────────┘  └──────────────┘       └──────────────┘         │
└─────────────────────────────────────────────────────────────────────┘

PERFORMANCE IMPROVEMENTS:
-------------------------
OLD APPROACH (Browser restart per proxy):
  - Browser launch: ~1000-2000ms (spawns new Chromium process)
  - Memory overhead: ~150-300MB per browser
  - CPU spike on each restart
  - Max RPS on 12-core: ~6-12 requests/sec (bottlenecked by process spawn)

NEW APPROACH (Context rotation):
  - Context creation: ~50-100ms (reuses existing browser process)
  - Memory overhead: ~20-50MB per context
  - No CPU spike, smooth distribution
  - Max RPS on 12-core: ~50-100+ requests/sec (10x improvement)

WHY THIS WORKS:
  - Browser process = Chromium executable (heavy, shared resources)
  - BrowserContext = Isolated session within browser (lightweight, has own proxy)
  - Each context has isolated: cookies, localStorage, proxy, user-agent
  - Contexts can be created/destroyed rapidly without process overhead
"""

import asyncio
import logging
import time
from typing import Optional, Dict, Any, List, Callable, Awaitable
from dataclasses import dataclass, field
from contextlib import asynccontextmanager
import random

from patchright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright

from .config import get_config

logger = logging.getLogger(__name__)


# User agents for rotation (fingerprint diversity)
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
]

# Viewport variations (fingerprint diversity)
VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 1680, "height": 1050},
]


@dataclass
class BrowserProcess:
    """
    Represents a PERSISTENT browser process in the pool.
    Each process can host multiple BrowserContexts concurrently.
    
    Memory footprint: ~150-300MB per browser process
    Context footprint: ~20-50MB per context (lightweight)
    """
    browser: Browser
    id: int
    created_at: float = field(default_factory=time.time)
    total_contexts_served: int = 0
    active_contexts: int = 0
    
    # Lock for this specific browser (fine-grained locking)
    _context_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass 
class AcquiredContext:
    """
    Represents an acquired context session.
    Automatically cleaned up when released.
    """
    page: Page
    context: BrowserContext
    browser_process: BrowserProcess
    proxy: Optional[Dict]
    created_at: float = field(default_factory=time.time)


class BrowserPool:
    """
    High-Performance Browser Pool with Context-Based Proxy Rotation.
    
    DESIGN PRINCIPLES:
    ------------------
    1. Browser processes are PERSISTENT (expensive to create, kept alive)
    2. Contexts are EPHEMERAL (cheap to create, destroyed after use)
    3. Each context gets its own proxy configuration
    4. Fine-grained locking per browser (not global) for max concurrency
    
    OPTIMAL SETTINGS FOR 12-CORE / 24GB RAM:
    ----------------------------------------
    - browser_count: 6-12 (1 browser per 1-2 cores)
    - max_contexts_per_browser: 10 (safe for 24GB RAM)
    - Total concurrent contexts: 60-120
    
    MEMORY CALCULATION:
    - Each browser process: ~150-300MB
    - Each context: ~20-50MB
    - 12 browsers × 300MB = 3.6GB
    - 120 contexts × 50MB = 6GB
    - Total max: ~10GB (safe buffer for 24GB RAM)
    """
    
    def __init__(
        self,
        browser_count: Optional[int] = None,
        max_contexts_per_browser: int = 10,  # Reduced from 15 for RAM safety
    ):
        config = get_config()
        
        # Number of persistent browser processes
        # Recommended: CPU cores / 2 to CPU cores (e.g., 6-12 for 12-core)
        self.browser_count = browser_count or min(config.browser.pool_size, 12)
        
        # Max concurrent contexts per browser
        # Higher = more concurrency but more memory per browser
        self.max_contexts_per_browser = max_contexts_per_browser
        
        self.headless = config.browser.headless
        self.timeout = config.browser.timeout * 1000  # Convert to ms
        self.user_agent_rotation = config.browser.user_agent_rotation
        
        # Pool of persistent browser processes
        self._browsers: List[BrowserProcess] = []
        
        # Playwright instance (singleton)
        self._playwright: Optional[Playwright] = None
        
        # Global lock for pool-level operations (initialization, shutdown)
        self._pool_lock = asyncio.Lock()
        
        # Semaphore to limit total concurrent contexts across all browsers
        # Prevents memory exhaustion: browser_count * max_contexts_per_browser
        self._global_semaphore: Optional[asyncio.Semaphore] = None
        
        self._initialized = False
        self._shutting_down = False
        
        # Statistics
        self._total_requests = 0
        self._total_contexts_created = 0
        self._active_contexts = 0
        
    async def initialize(self) -> None:
        """
        Initialize the browser pool with persistent browser processes.
        
        This is the ONLY place where browser processes are created.
        Called once at startup, processes are reused for all requests.
        """
        if self._initialized:
            return
            
        async with self._pool_lock:
            if self._initialized:
                return
            
            logger.info(
                f"Initializing browser pool: {self.browser_count} browsers, "
                f"{self.max_contexts_per_browser} max contexts/browser, "
                f"headless={self.headless}"
            )
            
            # Start Playwright (single instance)
            self._playwright = await async_playwright().start()
            
            # Initialize global semaphore for context limiting
            max_total_contexts = self.browser_count * self.max_contexts_per_browser
            self._global_semaphore = asyncio.Semaphore(max_total_contexts)
            
            # Launch persistent browser processes concurrently
            # This is the expensive operation - done ONCE at startup
            launch_tasks = [
                self._launch_browser(i) for i in range(self.browser_count)
            ]
            browsers = await asyncio.gather(*launch_tasks, return_exceptions=True)
            
            for result in browsers:
                if isinstance(result, Exception):
                    logger.error(f"Failed to launch browser: {result}")
                elif isinstance(result, BrowserProcess):
                    self._browsers.append(result)
            
            self._initialized = True
            logger.info(
                f"Browser pool ready: {len(self._browsers)} browsers, "
                f"max {max_total_contexts} concurrent contexts"
            )
    
    async def _launch_browser(self, browser_id: int) -> Optional[BrowserProcess]:
        """
        Launch a single persistent browser process.
        
        Browser args are optimized for:
        - Memory efficiency (disable unnecessary features)
        - Stealth (avoid detection)
        - Stability (disable GPU in headless)
        """
        if self._playwright is None:
            raise RuntimeError("Playwright not initialized")
        
        try:
            # Optimized launch args for high-concurrency server
            browser = await self._playwright.chromium.launch(
                headless=self.headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",  # Prevents /dev/shm memory issues
                    "--no-sandbox",  # Required for Docker/server environments
                    "--disable-gpu",  # Reduce memory in headless
                    "--disable-extensions",
                    "--disable-background-networking",
                    "--disable-default-apps",
                    "--disable-sync",
                    "--disable-translate",
                    "--metrics-recording-only",
                    "--mute-audio",
                    "--no-first-run",
                    "--safebrowsing-disable-auto-update",
                ]
            )
            
            logger.debug(f"Browser process #{browser_id} launched successfully")
            
            return BrowserProcess(
                browser=browser,
                id=browser_id,
            )
            
        except Exception as e:
            logger.error(f"Failed to launch browser #{browser_id}: {e}")
            return None
    
    def _select_browser(self) -> BrowserProcess:
        """
        Select the best browser for a new context.
        
        Strategy: Round-robin with load balancing
        - Prefer browsers with fewer active contexts
        - Distributes load evenly across all browser processes
        
        This ensures optimal CPU utilization across all 12 cores.
        """
        if not self._browsers:
            raise RuntimeError("No browsers available in pool")
        
        # Sort by active contexts (least loaded first)
        return min(self._browsers, key=lambda b: b.active_contexts)
    
    async def _create_context(
        self,
        browser_process: BrowserProcess,
        proxy: Optional[Dict] = None,
    ) -> AcquiredContext:
        """
        Create a lightweight BrowserContext on an existing browser process.
        
        This is the KEY OPTIMIZATION:
        - Context creation: ~50-100ms (vs ~1000-2000ms for browser launch)
        - Each context has isolated: proxy, cookies, localStorage, cache
        - Context can be destroyed without affecting other contexts
        
        PROXY ROTATION:
        - Proxy is set at CONTEXT level, not browser level
        - Different contexts on same browser can have different proxies
        - No need to restart browser when changing proxy!
        """
        # Rotate fingerprint elements for each context
        user_agent = random.choice(USER_AGENTS) if self.user_agent_rotation else USER_AGENTS[0]
        viewport = random.choice(VIEWPORTS)
        
        # Context options with optional proxy
        context_options: Dict[str, Any] = {
            "user_agent": user_agent,
            "viewport": viewport,
            "locale": "en-US",
            "timezone_id": random.choice([
                "America/New_York",
                "America/Chicago",
                "America/Los_Angeles",
                "America/Denver",
            ]),
            # Permissions that help avoid detection
            "permissions": ["geolocation"],
            # Color scheme randomization
            "color_scheme": random.choice(["light", "dark", "no-preference"]),
        }
        
        # PROXY SET AT CONTEXT LEVEL - This is the magic!
        # Each context can have a different proxy without restarting browser
        if proxy:
            context_options["proxy"] = proxy
            logger.debug(f"Creating context with proxy: {proxy.get('server', 'unknown')}")
        
        # Fine-grained lock: only lock this specific browser, not the entire pool
        # This allows concurrent context creation on different browsers
        async with browser_process._context_lock:
            context = await browser_process.browser.new_context(**context_options)
            browser_process.active_contexts += 1
            browser_process.total_contexts_served += 1
        
        # Set timeout
        context.set_default_timeout(self.timeout)
        
        # Create page within context
        page = await context.new_page()
        
        # Update global stats
        self._total_contexts_created += 1
        self._active_contexts += 1
        
        return AcquiredContext(
            page=page,
            context=context,
            browser_process=browser_process,
            proxy=proxy,
        )
    
    async def _destroy_context(self, acquired: AcquiredContext) -> None:
        """
        Destroy a context and release resources.
        
        MEMORY MANAGEMENT:
        - Closes the context (releases ~20-50MB)
        - Browser process stays alive (no restart overhead)
        - Ready for new context immediately
        """
        try:
            # Close context (this closes all pages within it)
            await acquired.context.close()
        except Exception as e:
            logger.warning(f"Error closing context: {e}")
        finally:
            # Update browser process stats
            async with acquired.browser_process._context_lock:
                acquired.browser_process.active_contexts = max(
                    0, acquired.browser_process.active_contexts - 1
                )
            
            # Update global stats  
            self._active_contexts = max(0, self._active_contexts - 1)
    
    @asynccontextmanager
    async def acquire(self, proxy: Optional[Dict] = None):
        """
        Acquire a page with optional proxy configuration.
        
        Usage:
            async with pool.acquire(proxy={"server": "http://proxy:8080"}) as page:
                await page.goto("https://example.com")
                # Page and context automatically cleaned up after block
        
        CONCURRENCY MODEL:
        - Semaphore limits total concurrent contexts (prevents OOM)
        - Fine-grained per-browser locks (maximizes parallelism)
        - Context automatically destroyed on exit (prevents memory leaks)
        
        On 12-core machine with 12 browsers and 15 contexts/browser:
        - Max concurrent: 180 contexts
        - Each with independent proxy
        - ~50-100 RPS theoretical max
        """
        await self.initialize()
        
        if self._shutting_down:
            raise RuntimeError("Browser pool is shutting down")
        
        if self._global_semaphore is None:
            raise RuntimeError("Pool not properly initialized")
        
        # Acquire semaphore slot (blocks if at max capacity)
        # This prevents memory exhaustion from too many concurrent contexts
        async with self._global_semaphore:
            self._total_requests += 1
            
            # Select least-loaded browser
            browser_process = self._select_browser()
            
            # Create ephemeral context with proxy
            acquired = await self._create_context(browser_process, proxy)
            
            try:
                yield acquired.page
            finally:
                # ALWAYS destroy context - prevents memory leaks
                await self._destroy_context(acquired)
    
    async def acquire_with_cleanup(
        self,
        proxy: Optional[Dict] = None,
    ) -> tuple[Page, Callable[[], Awaitable[None]]]:
        """
        Acquire a page and return cleanup function for manual lifecycle control.
        
        Use this when you need to control when the context is destroyed,
        e.g., for long-running operations or multi-step workflows.
        
        Returns:
            (page, cleanup_func) - Call cleanup_func() when done
        
        Usage:
            page, cleanup = await pool.acquire_with_cleanup(proxy=proxy)
            try:
                await page.goto(url)
                # ... do work ...
            finally:
                await cleanup()  # MUST call to prevent memory leak
        """
        await self.initialize()
        
        if self._shutting_down:
            raise RuntimeError("Browser pool is shutting down")
        
        if self._global_semaphore is None:
            raise RuntimeError("Pool not properly initialized")
        
        # Acquire semaphore
        await self._global_semaphore.acquire()
        
        self._total_requests += 1
        browser_process = self._select_browser()
        acquired = await self._create_context(browser_process, proxy)
        
        async def cleanup():
            try:
                await self._destroy_context(acquired)
            finally:
                # Always release semaphore
                self._global_semaphore.release()  # type: ignore
        
        return acquired.page, cleanup
    
    async def close(self) -> None:
        """
        Gracefully shutdown the browser pool.
        
        Steps:
        1. Mark as shutting down (reject new requests)
        2. Wait for active contexts to complete
        3. Close all browser processes
        4. Stop Playwright
        """
        async with self._pool_lock:
            if not self._initialized:
                return
            
            self._shutting_down = True
            logger.info("Shutting down browser pool...")
            
            # Wait briefly for active contexts to finish
            for _ in range(10):  # Max 5 seconds
                if self._active_contexts == 0:
                    break
                logger.debug(f"Waiting for {self._active_contexts} active contexts...")
                await asyncio.sleep(0.5)
            
            # Force close all browsers
            for browser_proc in self._browsers:
                try:
                    await browser_proc.browser.close()
                    logger.debug(f"Browser #{browser_proc.id} closed")
                except Exception as e:
                    logger.error(f"Error closing browser #{browser_proc.id}: {e}")
            
            self._browsers.clear()
            
            # Stop Playwright
            if self._playwright:
                await self._playwright.stop()
                self._playwright = None
            
            self._initialized = False
            self._shutting_down = False
            
            logger.info(
                f"Browser pool closed. Stats: {self._total_requests} total requests, "
                f"{self._total_contexts_created} contexts created"
            )
    
    @property
    def active_contexts(self) -> int:
        """Get count of currently active contexts"""
        return self._active_contexts
    
    @property
    def browser_count_actual(self) -> int:
        """Get actual number of running browsers"""
        return len(self._browsers)
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get comprehensive pool statistics.
        
        Useful for monitoring and capacity planning.
        """
        browser_stats = []
        for bp in self._browsers:
            browser_stats.append({
                "id": bp.id,
                "active_contexts": bp.active_contexts,
                "total_served": bp.total_contexts_served,
                "uptime_seconds": time.time() - bp.created_at,
            })
        
        max_capacity = self.browser_count * self.max_contexts_per_browser
        
        return {
            "initialized": self._initialized,
            "shutting_down": self._shutting_down,
            "browser_count": len(self._browsers),
            "max_contexts_per_browser": self.max_contexts_per_browser,
            "max_total_capacity": max_capacity,
            "active_contexts": self._active_contexts,
            "available_slots": max_capacity - self._active_contexts,
            "utilization_percent": (self._active_contexts / max_capacity * 100) if max_capacity > 0 else 0,
            "total_requests": self._total_requests,
            "total_contexts_created": self._total_contexts_created,
            "headless": self.headless,
            "browsers": browser_stats,
        }


# =============================================================================
# GLOBAL SINGLETON
# =============================================================================

_pool: Optional[BrowserPool] = None
_pool_creation_lock = asyncio.Lock()


async def get_browser_pool() -> BrowserPool:
    """
    Get the global browser pool singleton.
    
    Thread-safe initialization with double-checked locking pattern.
    """
    global _pool
    
    if _pool is None:
        async with _pool_creation_lock:
            if _pool is None:
                _pool = BrowserPool()
                await _pool.initialize()
    
    return _pool


async def close_browser_pool() -> None:
    """Close the global browser pool"""
    global _pool
    
    if _pool:
        await _pool.close()
        _pool = None
