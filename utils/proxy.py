"""
Proxy Utilities
Functions for parsing and validating proxy configurations
"""

import logging
from typing import Optional, Dict, Tuple

logger = logging.getLogger(__name__)


def parse_proxy(proxy_string: str) -> Optional[Dict]:
    """
    Parse a proxy string into a configuration dict.
    
    Supported formats:
    - host:port
    - host:port:username:password
    - http://host:port
    - http://username:password@host:port
    
    Args:
        proxy_string: Proxy string in various formats
    
    Returns:
        Dict with 'server', 'username', 'password' keys or None
    """
    if not proxy_string:
        return None
    
    proxy_string = proxy_string.strip()
    
    try:
        # Handle URL format
        if proxy_string.startswith(('http://', 'https://', 'socks5://')):
            from urllib.parse import urlparse
            parsed = urlparse(proxy_string)
            
            result = {
                "server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
            }
            
            if parsed.username:
                result["username"] = parsed.username
            if parsed.password:
                result["password"] = parsed.password
            
            return result
        
        # Handle colon-separated format
        parts = proxy_string.split(':')
        
        if len(parts) == 2:
            # host:port
            return {"server": f"http://{parts[0]}:{parts[1]}"}
        
        elif len(parts) == 4:
            # host:port:username:password
            return {
                "server": f"http://{parts[0]}:{parts[1]}",
                "username": parts[2],
                "password": parts[3]
            }
        
        elif len(parts) == 3:
            # Could be host:port:username or other format
            # Assume host:port:username with no password
            return {
                "server": f"http://{parts[0]}:{parts[1]}",
                "username": parts[2]
            }
        
        else:
            logger.warning(f"Unknown proxy format: {proxy_string}")
            return None
            
    except Exception as e:
        logger.error(f"Error parsing proxy: {e}")
        return None


def validate_proxy(proxy: Dict) -> Tuple[bool, Optional[str]]:
    """
    Validate a proxy configuration.
    
    Args:
        proxy: Proxy dict with 'server' key
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not proxy:
        return True, None  # No proxy is valid
    
    if not isinstance(proxy, dict):
        return False, "Proxy must be a dictionary"
    
    server = proxy.get('server')
    if not server:
        return False, "Proxy must have 'server' key"
    
    # Basic format validation
    if not server.startswith(('http://', 'https://', 'socks5://')):
        return False, "Proxy server must start with http://, https://, or socks5://"
    
    return True, None


def format_proxy_for_display(proxy: Dict) -> str:
    """Format proxy for display (hiding password)"""
    if not proxy:
        return "No proxy"
    
    server = proxy.get('server', 'unknown')
    username = proxy.get('username')
    
    if username:
        return f"{server} (user: {username})"
    return server
