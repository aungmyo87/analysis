"""
Normal reCAPTCHA v2 Solver
Handles standard checkbox reCAPTCHA with visible checkbox
"""

import logging
from typing import Optional, Dict

from .base_solver import BaseSolver, SolverResult
from ..core.browser_pool import get_browser_pool
from ..challenges import AudioSolver, ImageSolver

logger = logging.getLogger(__name__)


class NormalSolver(BaseSolver):
    """
    Solver for normal reCAPTCHA v2 with visible checkbox.
    
    Flow:
    1. Navigate to target URL
    2. Click reCAPTCHA checkbox
    3. Check for auto-pass
    4. If challenge: Try audio solver, fallback to image solver
    5. Extract and return token
    """
    
    async def solve(
        self,
        url: str,
        sitekey: str,
        proxy: Optional[Dict] = None,
        **kwargs
    ) -> SolverResult:
        """
        Solve a normal reCAPTCHA v2.
        
        Args:
            url: Target website URL
            sitekey: reCAPTCHA site key
            proxy: Optional proxy configuration
        
        Returns:
            SolverResult with token or error
        """
        browser_pool = await get_browser_pool()
        attempts = 0
        max_retries = self.config.solver.max_retries
        
        for attempt in range(max_retries):
            attempts += 1
            page = None
            cleanup = None
            
            try:
                # Acquire browser with fresh context
                page, cleanup = await browser_pool.acquire_with_context(proxy)
                
                self.logger.info(f"Attempt {attempt + 1}: Navigating to {url}")
                
                # Navigate to target URL
                await page.goto(url, wait_until="networkidle", timeout=30000)
                await page.wait_for_timeout(2000)
                
                # Click the checkbox
                checkbox_clicked = await self._click_checkbox(page)
                if not checkbox_clicked:
                    self.logger.warning("Failed to click checkbox")
                    continue
                
                await page.wait_for_timeout(2000)
                
                # Check for auto-pass
                token = await self._check_auto_pass(page)
                if token:
                    self.logger.info("Auto-passed! No challenge needed.")
                    return SolverResult(
                        success=True,
                        token=token,
                        method="auto",
                        attempts=attempts
                    )
                
                # Wait for challenge to appear
                challenge_appeared = await self._wait_for_challenge(page, timeout=5000)
                
                if not challenge_appeared:
                    # Maybe auto-passed but we didn't detect it
                    token = await self._extract_token(page)
                    if token:
                        return SolverResult(
                            success=True,
                            token=token,
                            method="auto",
                            attempts=attempts
                        )
                    
                    self.logger.warning("No challenge appeared and no token found")
                    continue
                
                # Solve the challenge
                result = await self._solve_challenge(page)
                
                if result.success:
                    return SolverResult(
                        success=True,
                        token=result.token,
                        method=result.method,
                        attempts=attempts
                    )
                
            except Exception as e:
                self.logger.error(f"Attempt {attempt + 1} failed: {e}")
                
            finally:
                if cleanup:
                    await cleanup()
        
        return SolverResult(
            success=False,
            error=f"Failed after {attempts} attempts",
            attempts=attempts
        )
    
    async def _solve_challenge(self, page) -> SolverResult:
        """
        Solve the reCAPTCHA challenge (audio or image).
        
        Args:
            page: Browser page with challenge
        
        Returns:
            SolverResult
        """
        primary_method = self.config.solver.primary_method
        fallback_enabled = self.config.solver.fallback_enabled
        
        # Try primary method
        if primary_method == "audio":
            result = await self._try_audio_solver(page)
            if result.success:
                return result
            
            # Fallback to image
            if fallback_enabled:
                self.logger.info("Audio failed, trying image solver")
                result = await self._try_image_solver(page)
                if result.success:
                    return result
        else:
            result = await self._try_image_solver(page)
            if result.success:
                return result
            
            # Fallback to audio
            if fallback_enabled:
                self.logger.info("Image failed, trying audio solver")
                result = await self._try_audio_solver(page)
                if result.success:
                    return result
        
        return SolverResult(success=False, error="All solving methods failed")
    
    async def _try_audio_solver(self, page) -> SolverResult:
        """Try solving with audio method"""
        try:
            audio_solver = AudioSolver()
            result = await audio_solver.solve(page)
            
            if result.get('success'):
                # Extract token after solving
                token = await self._extract_token(page)
                if token:
                    return SolverResult(
                        success=True,
                        token=token,
                        method="audio"
                    )
            
            return SolverResult(
                success=False,
                error=result.get('error', 'Audio solver failed'),
                method="audio"
            )
            
        except Exception as e:
            self.logger.error(f"Audio solver error: {e}")
            return SolverResult(success=False, error=str(e), method="audio")
    
    async def _try_image_solver(self, page) -> SolverResult:
        """Try solving with image method"""
        try:
            image_solver = ImageSolver()
            result = await image_solver.solve(page)
            
            if result.get('success'):
                # Extract token after solving
                token = await self._extract_token(page)
                if token:
                    return SolverResult(
                        success=True,
                        token=token,
                        method="image"
                    )
            
            return SolverResult(
                success=False,
                error=result.get('error', 'Image solver failed'),
                method="image"
            )
            
        except Exception as e:
            self.logger.error(f"Image solver error: {e}")
            return SolverResult(success=False, error=str(e), method="image")
