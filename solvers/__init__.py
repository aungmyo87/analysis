"""
Solvers Module
Contains solver implementations for different reCAPTCHA types
"""

from typing import Optional, Dict
from .base_solver import BaseSolver, SolverResult
from .normal_solver import NormalSolver
from .invisible_solver import InvisibleSolver
from .enterprise_solver import EnterpriseSolver

# Main solve function
async def solve_captcha(
    url: str,
    sitekey: str,
    captcha_type: str = "normal",
    proxy: Optional[Dict] = None,
    is_invisible: bool = False,
    action: Optional[str] = None,
    enterprise_payload: Optional[Dict] = None,
) -> dict:
    """
    Main entry point for solving captchas.
    Routes to appropriate solver based on type.
    
    Args:
        url: Target website URL
        sitekey: reCAPTCHA site key
        captcha_type: normal | invisible | enterprise
        proxy: Proxy configuration
        is_invisible: Whether reCAPTCHA is invisible
        action: Action parameter (for invisible/enterprise)
        enterprise_payload: Enterprise-specific payload
    
    Returns:
        dict with 'success', 'token', 'error', 'method' keys
    """
    # Select appropriate solver
    if captcha_type == "enterprise":
        solver = EnterpriseSolver()
    elif captcha_type == "invisible" or is_invisible:
        solver = InvisibleSolver()
    else:
        solver = NormalSolver()
    
    # Solve
    result = await solver.solve(
        url=url,
        sitekey=sitekey,
        proxy=proxy,
        action=action,
        enterprise_payload=enterprise_payload,
    )
    
    return result.to_dict()


__all__ = [
    'BaseSolver',
    'SolverResult',
    'NormalSolver',
    'InvisibleSolver',
    'EnterpriseSolver',
    'solve_captcha',
]
