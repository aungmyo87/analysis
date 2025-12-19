"""
Base Solver Class
Abstract base class for all reCAPTCHA solvers
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


@dataclass
class SolverResult:
    """Result from a solver attempt"""
    success: bool
    token: Optional[str] = None
    error: Optional[str] = None
    method: Optional[str] = None  # audio | image
    attempts: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "token": self.token,
            "error": self.error,
            "method": self.method,
            "attempts": self.attempts,
        }


class BaseSolver(ABC):
    """
    Abstract base class for reCAPTCHA solvers.
    Provides common functionality and interface for all solver types.
    """
    
    def __init__(self):
        from ..core.config import get_config
        self.config = get_config()
        self.logger = logging.getLogger(self.__class__.__name__)
    
    @abstractmethod
    async def solve(
        self,
        url: str,
        sitekey: str,
        proxy: Optional[Dict] = None,
        **kwargs
    ) -> SolverResult:
        """
        Solve a reCAPTCHA challenge.
        
        Args:
            url: Target website URL
            sitekey: reCAPTCHA site key
            proxy: Optional proxy configuration
            **kwargs: Additional solver-specific parameters
        
        Returns:
            SolverResult with success status and token
        """
        pass
    
    async def _click_checkbox(self, page) -> bool:
        """
        Click the reCAPTCHA checkbox.
        
        Returns:
            True if checkbox was clicked successfully
        """
        try:
            # Wait for reCAPTCHA iframe
            iframe_selectors = [
                "iframe[src*='recaptcha'][src*='anchor']",
                "iframe[src*='google.com/recaptcha/api2/anchor']",
                "iframe[src*='google.com/recaptcha/enterprise/anchor']",
                "iframe[title*='reCAPTCHA']",
            ]
            
            iframe = None
            for selector in iframe_selectors:
                try:
                    iframe = await page.wait_for_selector(selector, timeout=10000)
                    if iframe:
                        break
                except Exception:
                    continue
            
            if not iframe:
                self.logger.error("Could not find reCAPTCHA iframe")
                return False
            
            # Get iframe content
            frame = await iframe.content_frame()
            if not frame:
                self.logger.error("Could not access iframe content")
                return False
            
            # Click the checkbox
            checkbox = await frame.wait_for_selector("#recaptcha-anchor", timeout=5000)
            if checkbox:
                await checkbox.click()
                await page.wait_for_timeout(1000)
                return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"Error clicking checkbox: {e}")
            return False
    
    async def _check_auto_pass(self, page) -> Optional[str]:
        """
        Check if reCAPTCHA was auto-passed (no challenge).
        
        Returns:
            Token if auto-passed, None otherwise
        """
        try:
            # Check if checkbox is checked (green checkmark)
            iframe_selectors = [
                "iframe[src*='recaptcha'][src*='anchor']",
                "iframe[src*='google.com/recaptcha/api2/anchor']",
                "iframe[src*='google.com/recaptcha/enterprise/anchor']",
            ]
            
            for selector in iframe_selectors:
                try:
                    iframe = await page.query_selector(selector)
                    if iframe:
                        frame = await iframe.content_frame()
                        if frame:
                            # Check for checked state
                            is_checked = await frame.evaluate('''
                                () => {
                                    const anchor = document.querySelector('#recaptcha-anchor');
                                    return anchor && anchor.classList.contains('recaptcha-checkbox-checked');
                                }
                            ''')
                            
                            if is_checked:
                                # Get the token
                                token = await self._extract_token(page)
                                if token:
                                    return token
                except Exception:
                    continue
            
            return None
            
        except Exception as e:
            self.logger.debug(f"Auto-pass check: {e}")
            return None
    
    async def _extract_token(self, page) -> Optional[str]:
        """
        Extract the reCAPTCHA token from the page.
        
        Returns:
            Token string or None
        """
        try:
            # Method 1: Get from textarea
            token = await page.evaluate('''
                () => {
                    // Try g-recaptcha-response textarea
                    const textarea = document.querySelector('textarea[name="g-recaptcha-response"]');
                    if (textarea && textarea.value) {
                        return textarea.value;
                    }
                    
                    // Try hidden input
                    const input = document.querySelector('input[name="g-recaptcha-response"]');
                    if (input && input.value) {
                        return input.value;
                    }
                    
                    // Try all textareas with recaptcha in id
                    const textareas = document.querySelectorAll('textarea[id*="g-recaptcha-response"]');
                    for (const ta of textareas) {
                        if (ta.value) return ta.value;
                    }
                    
                    return null;
                }
            ''')
            
            if token:
                return token
            
            # Method 2: Try grecaptcha.getResponse()
            token = await page.evaluate('''
                () => {
                    try {
                        if (typeof grecaptcha !== 'undefined') {
                            const response = grecaptcha.getResponse();
                            if (response) return response;
                        }
                    } catch (e) {}
                    
                    try {
                        if (typeof grecaptcha !== 'undefined' && grecaptcha.enterprise) {
                            const response = grecaptcha.enterprise.getResponse();
                            if (response) return response;
                        }
                    } catch (e) {}
                    
                    return null;
                }
            ''')
            
            return token
            
        except Exception as e:
            self.logger.error(f"Error extracting token: {e}")
            return None
    
    async def _wait_for_challenge(self, page, timeout: int = 5000) -> bool:
        """
        Wait for challenge popup to appear.
        
        Returns:
            True if challenge appeared, False otherwise
        """
        try:
            challenge_selectors = [
                "iframe[src*='recaptcha'][src*='bframe']",
                "iframe[src*='google.com/recaptcha/api2/bframe']",
                "iframe[src*='google.com/recaptcha/enterprise/bframe']",
                "iframe[title='recaptcha challenge expires in two minutes']",
            ]
            
            for selector in challenge_selectors:
                try:
                    challenge = await page.wait_for_selector(selector, timeout=timeout)
                    if challenge:
                        return True
                except Exception:
                    continue
            
            return False
            
        except Exception:
            return False
    
    async def _get_challenge_frame(self, page):
        """Get the challenge iframe content frame"""
        challenge_selectors = [
            "iframe[src*='recaptcha'][src*='bframe']",
            "iframe[src*='google.com/recaptcha/api2/bframe']",
            "iframe[src*='google.com/recaptcha/enterprise/bframe']",
        ]
        
        for selector in challenge_selectors:
            try:
                iframe = await page.query_selector(selector)
                if iframe:
                    frame = await iframe.content_frame()
                    if frame:
                        return frame
            except Exception:
                continue
        
        return None
