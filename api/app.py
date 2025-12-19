"""
Flask Application Factory
Creates and configures the Flask app with all routes
"""

import logging
from typing import Optional
from flask import Flask

try:
    from flask_cors import CORS
except ImportError:
    CORS = None  # type: ignore

from ..core.config import get_config

logger = logging.getLogger(__name__)


def create_app(config_path: Optional[str] = None) -> Flask:
    """
    Create and configure the Flask application.
    
    Args:
        config_path: Optional path to config file
    
    Returns:
        Configured Flask app
    """
    app = Flask(__name__)
    
    # Load configuration
    config = get_config()
    app.config['DEBUG'] = config.server.debug
    
    # Enable CORS if available
    if CORS is not None:
        CORS(app, resources={r"/*": {"origins": "*"}})
    
    # Register blueprints
    from .routes.tasks import tasks_bp
    from .routes.balance import balance_bp
    from .routes.health import health_bp
    
    app.register_blueprint(tasks_bp, url_prefix='/api/v1')
    app.register_blueprint(balance_bp, url_prefix='/api/v1')
    app.register_blueprint(health_bp)
    
    # Error handlers
    @app.errorhandler(400)
    def bad_request(error):
        return {"errorId": 15, "errorMessage": "Bad request"}, 400
    
    @app.errorhandler(404)
    def not_found(error):
        return {"errorId": 16, "errorMessage": "Not found"}, 404
    
    @app.errorhandler(500)
    def internal_error(error):
        logger.error(f"Internal error: {error}")
        return {"errorId": 99, "errorMessage": "Internal server error"}, 500
    
    logger.info(f"Flask app created, debug={config.server.debug}")
    return app


def run_app():
    """Run the Flask application"""
    config = get_config()
    app = create_app()
    
    logger.info(f"Starting server on {config.server.host}:{config.server.port}")
    app.run(
        host=config.server.host,
        port=config.server.port,
        debug=config.server.debug,
        threaded=True
    )
