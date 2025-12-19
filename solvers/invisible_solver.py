"""
Invisible reCAPTCHA v2 Solver
Handles invisible reCAPTCHA that triggers programmatically
"""

import logging
from typing import Optional, Dict

from .base_solver import BaseSolver, SolverResult
from ..core.browser_pool import get_browser_pool
from ..challenges import AudioSolver, ImageSolver

logger = logging.getLogger(__name__)


class InvisibleSolver(BaseSolver):
    """
    Solver for invisible reCAPTCHA v2.
    
    Invisible reCAPTCHA has no visible checkbox - it triggers
    programmatically when user performs an action.
    
    Flow:
    1. Navigate to target URL
    2. Inject trigger script to execute reCAPTCHA
    3. Wait for challenge or callback
    4. If challenge: Solve with audio/image
    5. Intercept callback to get token
    """
    
    async def solve(
        self,
        url: str,
        sitekey: str,
        proxy: Optional[Dict] = None,
        action: str = None,
        **kwargs
    ) -> SolverResult:
        """
        Solve an invisible reCAPTCHA v2.
        
        Args:
            url: Target website URL
            sitekey: reCAPTCHA site key
            proxy: Optional proxy configuration
            action: Optional action to trigger
        
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
                
                # Set up token interception
                token_result = {"token": None}
                
                await page.expose_function(
                    "captureRecaptchaToken",
                    lambda token: token_result.update({"token": token})
                )
                
                # Inject callback interceptor
                await page.add_init_script('''
                    window.__recaptchaCallback = null;
                    
                    // Override grecaptcha.execute to intercept callback
                    const originalExecute = window.grecaptcha?.execute;
                    if (window.grecaptcha) {
                        window.grecaptcha.execute = function(...args) {
                            return originalExecute?.apply(this, args)?.then?.(token => {
                                if (token && window.captureRecaptchaToken) {
                                    window.captureRecaptchaToken(token);
                                }
                                return token;
                            });
                        };
                    }
                ''')
                
                # Navigate to target URL
                await page.goto(url, wait_until="networkidle", timeout=30000)
                await page.wait_for_timeout(2000)
                
                # Try to trigger invisible reCAPTCHA
                triggered = await self._trigger_invisible(page, sitekey, action)
                
                if not triggered:
                    self.logger.warning("Failed to trigger invisible reCAPTCHA")
                    continue
                
                await page.wait_for_timeout(2000)
                
                # Check if we got token from callback
                if token_result["token"]:
                    self.logger.info("Got token from callback interception")
                    return SolverResult(
                        success=True,
                        token=token_result["token"],
                        method="callback",
                        attempts=attempts
                    )
                
                # Check for challenge popup
                challenge_appeared = await self._wait_for_challenge(page, timeout=5000)
                
                if challenge_appeared:
                    # Solve the challenge
                    result = await self._solve_challenge(page)
                    
                    if result.success:
                        return SolverResult(
                            success=True,
                            token=result.token,
                            method=result.method,
                            attempts=attempts
                        )
                else:
                    # Maybe auto-passed
                    token = await self._extract_token(page)
                    if token:
                        return SolverResult(
                            success=True,
                            token=token,
                            method="auto",
                            attempts=attempts
                        )
                    
                    # Check callback result again
                    await page.wait_for_timeout(3000)
                    if token_result["token"]:
                        return SolverResult(
                            success=True,
                            token=token_result["token"],
                            method="callback",
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
    
    async def _trigger_invisible(self, page, sitekey: str, action: str = None) -> bool:
        """
        Trigger the invisible reCAPTCHA.
        
        Args:
            page: Browser page
            sitekey: reCAPTCHA site key
            action: Optional action parameter
        
        Returns:
            True if triggered successfully
        """
        try:
            # Method 1: Try grecaptcha.execute()
            trigger_script = f'''
                async () => {{
                    try {{
                        if (typeof grecaptcha !== 'undefined' && grecaptcha.execute) {{
                            {'await grecaptcha.execute("' + sitekey + '", {action: "' + (action or 'submit') + '"})' if action else 'await grecaptcha.execute()'}
                            return true;
                        }}
                    }} catch (e) {{
                        console.log('grecaptcha.execute error:', e);
                    }}
                    
                    // Method 2: Find and click submit button
                    try {{
                        const buttons = document.querySelectorAll('button[type="submit"], input[type="submit"], .g-recaptcha');
                        for (const btn of buttons) {{
                            btn.click();
                            return true;
                        }}
                    }} catch (e) {{
                        console.log('Button click error:', e);
                    }}
                    
                    // Method 3: Find invisible reCAPTCHA div and click
                    try {{
                        const recaptchaDiv = document.querySelector('.g-recaptcha[data-size="invisible"]');
                        if (recaptchaDiv) {{
                            const widgetId = recaptchaDiv.dataset.widgetId || 0;
                            grecaptcha.execute(widgetId);
                            return true;
                        }}
                    }} catch (e) {{
                        console.log('Widget execute error:', e);
                    }}
                    
                    return false;
                }}
            '''
            
            result = await page.evaluate(trigger_script)
            return result
            
        except Exception as e:
            self.logger.error(f"Error triggering invisible reCAPTCHA: {e}")
            return False
    
    async def _solve_challenge(self, page) -> SolverResult:
        """Solve the challenge using audio or image method"""
        primary_method = self.config.solver.primary_method
        fallback_enabled = self.config.solver.fallback_enabled
        
        if primary_method == "audio":
            result = await self._try_audio_solver(page)
            if result.success:
                return result
            
            if fallback_enabled:
                self.logger.info("Audio failed, trying image solver")
                result = await self._try_image_solver(page)
                if result.success:
                    return result
        else:
            result = await self._try_image_solver(page)
            if result.success:
                return result
            
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
                token = await self._extract_token(page)
                if token:
                    return SolverResult(success=True, token=token, method="audio")
            
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
                token = await self._extract_token(page)
                if token:
                    return SolverResult(success=True, token=token, method="image")
            
            return SolverResult(
                success=False,
                error=result.get('error', 'Image solver failed'),
                method="image"
            )
        except Exception as e:
            self.logger.error(f"Image solver error: {e}")
            return SolverResult(success=False, error=str(e), method="image")
