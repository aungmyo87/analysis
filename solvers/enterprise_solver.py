"""
Enterprise reCAPTCHA v2 Solver
Handles reCAPTCHA Enterprise with action parameters
"""

import logging
from typing import Optional, Dict

from .base_solver import BaseSolver, SolverResult
from ..core.browser_pool import get_browser_pool
from ..challenges import AudioSolver, ImageSolver

logger = logging.getLogger(__name__)


class EnterpriseSolver(BaseSolver):
    """
    Solver for reCAPTCHA Enterprise v2.
    
    Enterprise reCAPTCHA uses different API endpoints and supports
    action parameters for risk analysis.
    
    Flow:
    1. Navigate to target URL
    2. Detect enterprise reCAPTCHA iframe
    3. Execute with enterprise API and action parameter
    4. Handle challenge if presented
    5. Extract token via enterprise callback
    """
    
    async def solve(
        self,
        url: str,
        sitekey: str,
        proxy: Optional[Dict] = None,
        action: Optional[str] = None,
        enterprise_payload: Optional[Dict] = None,
        **kwargs
    ) -> SolverResult:
        """
        Solve an enterprise reCAPTCHA v2.
        
        Args:
            url: Target website URL
            sitekey: reCAPTCHA site key
            proxy: Optional proxy configuration
            action: Action parameter for enterprise
            enterprise_payload: Additional enterprise parameters
        
        Returns:
            SolverResult with token or error
        """
        browser_pool = await get_browser_pool()
        attempts = 0
        max_retries = self.config.solver.max_retries
        
        enterprise_payload = enterprise_payload or {}
        action = action or enterprise_payload.get('action', 'submit')
        
        for attempt in range(max_retries):
            attempts += 1
            page = None
            cleanup = None
            
            try:
                # Acquire browser with fresh context
                page, cleanup = await browser_pool.acquire_with_cleanup(proxy)
                
                self.logger.info(f"Attempt {attempt + 1}: Navigating to {url}")
                
                # Set up token interception for enterprise
                token_result = {"token": None}
                
                await page.expose_function(
                    "captureEnterpriseToken",
                    lambda token: token_result.update({"token": token})
                )
                
                # Inject enterprise callback interceptor
                await page.add_init_script('''
                    // Override grecaptcha.enterprise.execute
                    const checkEnterprise = setInterval(() => {
                        if (window.grecaptcha && window.grecaptcha.enterprise) {
                            const originalExecute = window.grecaptcha.enterprise.execute;
                            window.grecaptcha.enterprise.execute = function(...args) {
                                return originalExecute.apply(this, args).then(token => {
                                    if (token && window.captureEnterpriseToken) {
                                        window.captureEnterpriseToken(token);
                                    }
                                    return token;
                                });
                            };
                            clearInterval(checkEnterprise);
                        }
                    }, 100);
                ''')
                
                # Navigate to target URL
                # OPTIMIZATION: Use domcontentloaded for faster navigation
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                
                # Wait for reCAPTCHA to be ready
                try:
                    await page.wait_for_selector("iframe[src*='recaptcha']", timeout=10000)
                except Exception:
                    await page.wait_for_timeout(2000)
                
                # Check if this is enterprise reCAPTCHA
                is_enterprise = await self._detect_enterprise(page)
                self.logger.info(f"Enterprise reCAPTCHA detected: {is_enterprise}")
                
                # Try to trigger enterprise reCAPTCHA
                triggered = await self._trigger_enterprise(page, sitekey, action, enterprise_payload)
                
                if not triggered:
                    # Try clicking checkbox if visible
                    checkbox_clicked = await self._click_checkbox(page)
                    if checkbox_clicked:
                        await page.wait_for_timeout(2000)
                
                # Check if we got token from callback
                if token_result["token"]:
                    self.logger.info("Got token from enterprise callback")
                    return SolverResult(
                        success=True,
                        token=token_result["token"],
                        method="enterprise_callback",
                        attempts=attempts
                    )
                
                # Check for auto-pass
                token = await self._check_auto_pass(page)
                if token:
                    return SolverResult(
                        success=True,
                        token=token,
                        method="auto",
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
                    token = await self._extract_enterprise_token(page)
                    if token:
                        return SolverResult(
                            success=True,
                            token=token,
                            method="auto",
                            attempts=attempts
                        )
                    
                    # Wait and check callback again
                    await page.wait_for_timeout(3000)
                    if token_result["token"]:
                        return SolverResult(
                            success=True,
                            token=token_result["token"],
                            method="enterprise_callback",
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
    
    async def _detect_enterprise(self, page) -> bool:
        """Detect if page uses enterprise reCAPTCHA"""
        try:
            result = await page.evaluate('''
                () => {
                    // Check for enterprise script
                    const scripts = document.querySelectorAll('script[src*="recaptcha"]');
                    for (const script of scripts) {
                        if (script.src.includes('enterprise')) return true;
                    }
                    
                    // Check for enterprise iframe
                    const iframes = document.querySelectorAll('iframe[src*="recaptcha"]');
                    for (const iframe of iframes) {
                        if (iframe.src.includes('enterprise')) return true;
                    }
                    
                    // Check for grecaptcha.enterprise
                    if (window.grecaptcha && window.grecaptcha.enterprise) return true;
                    
                    return false;
                }
            ''')
            return result
        except Exception:
            return False
    
    async def _trigger_enterprise(self, page, sitekey: str, action: str, payload: Dict) -> bool:
        """
        Trigger enterprise reCAPTCHA execution.
        
        Args:
            page: Browser page
            sitekey: reCAPTCHA site key
            action: Action parameter
            payload: Additional enterprise parameters
        
        Returns:
            True if triggered successfully
        """
        try:
            s_token = payload.get('s', '')
            
            trigger_script = f'''
                async () => {{
                    try {{
                        if (typeof grecaptcha !== 'undefined' && grecaptcha.enterprise) {{
                            const options = {{ action: '{action}' }};
                            {'options.s = "' + s_token + '";' if s_token else ''}
                            await grecaptcha.enterprise.execute('{sitekey}', options);
                            return true;
                        }}
                    }} catch (e) {{
                        console.log('Enterprise execute error:', e);
                    }}
                    
                    // Fallback: find enterprise widget
                    try {{
                        const widgets = document.querySelectorAll('.g-recaptcha');
                        for (const widget of widgets) {{
                            const wSitekey = widget.dataset.sitekey;
                            if (wSitekey && grecaptcha.enterprise) {{
                                await grecaptcha.enterprise.execute(wSitekey, {{ action: '{action}' }});
                                return true;
                            }}
                        }}
                    }} catch (e) {{
                        console.log('Widget fallback error:', e);
                    }}
                    
                    return false;
                }}
            '''
            
            result = await page.evaluate(trigger_script)
            return result
            
        except Exception as e:
            self.logger.error(f"Error triggering enterprise reCAPTCHA: {e}")
            return False
    
    async def _extract_enterprise_token(self, page) -> Optional[str]:
        """Extract token using enterprise-specific methods"""
        try:
            # Try standard extraction first
            token = await self._extract_token(page)
            if token:
                return token
            
            # Try enterprise-specific extraction
            token = await page.evaluate('''
                () => {
                    try {
                        if (typeof grecaptcha !== 'undefined' && grecaptcha.enterprise) {
                            return grecaptcha.enterprise.getResponse();
                        }
                    } catch (e) {}
                    return null;
                }
            ''')
            
            return token
            
        except Exception as e:
            self.logger.error(f"Error extracting enterprise token: {e}")
            return None
    
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
                token = await self._extract_enterprise_token(page)
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
                token = await self._extract_enterprise_token(page)
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
