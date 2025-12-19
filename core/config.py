"""
Centralized Configuration Module
Loads settings from config.yaml and environment variables
"""

import os
import yaml
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, Union
from pathlib import Path


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8080
    workers: int = 4
    debug: bool = False


@dataclass
class BrowserConfig:
    pool_size: int = 20
    headless: bool = True
    timeout: int = 60
    user_agent_rotation: bool = True


@dataclass
class AudioConfig:
    engine: str = "whisper"  # whisper | google | azure
    whisper_model: str = "base"
    max_attempts: int = 5


@dataclass
class ImageConfig:
    engine: str = "yolo"  # yolo | clip | openai
    model_path: str = "models/recaptcha_yolov8m_best.pt"
    confidence_threshold: float = 0.5
    max_rounds: int = 10


@dataclass
class SolverConfig:
    primary_method: str = "audio"  # audio | image
    fallback_enabled: bool = True
    max_retries: int = 3
    audio: AudioConfig = field(default_factory=AudioConfig)
    image: ImageConfig = field(default_factory=ImageConfig)


@dataclass
class PricingConfig:
    normal_v2: float = 0.001
    invisible_v2: float = 0.0012
    enterprise_v2: float = 0.0015


@dataclass
class RateLimitConfig:
    requests_per_minute: int = 60
    concurrent_tasks: int = 50


@dataclass
class LoggingConfig:
    level: str = "INFO"
    format: str = "json"
    file: str = "logs/solver.log"


@dataclass
class Config:
    """Main configuration class"""
    server: ServerConfig = field(default_factory=ServerConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    solver: SolverConfig = field(default_factory=SolverConfig)
    pricing: PricingConfig = field(default_factory=PricingConfig)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    
    # Paths
    base_dir: Path = field(default_factory=lambda: Path(__file__).parent.parent)
    models_dir: Path = field(default_factory=lambda: Path(__file__).parent.parent / "models")
    logs_dir: Path = field(default_factory=lambda: Path(__file__).parent.parent / "logs")
    data_dir: Path = field(default_factory=lambda: Path(__file__).parent.parent / "data")


def load_config(config_path: Optional[Union[str, Path]] = None) -> Config:
    """
    Load configuration from YAML file and environment variables.
    Environment variables override YAML settings.
    """
    config = Config()
    
    # Determine config file path
    if config_path is None:
        env_path = os.environ.get("SOLVER_CONFIG")
        config_path = env_path if env_path else Path(__file__).parent.parent / "config.yaml"
    
    # Convert to string for file operations
    config_path_str = str(config_path)
    
    # Load from YAML if exists
    if os.path.exists(config_path_str):
        with open(config_path_str, 'r') as f:
            yaml_config = yaml.safe_load(f)
            
        if yaml_config:
            # Server
            if 'server' in yaml_config:
                config.server = ServerConfig(**yaml_config['server'])
            
            # Browser
            if 'browser' in yaml_config:
                config.browser = BrowserConfig(**yaml_config['browser'])
            
            # Solver
            if 'solver' in yaml_config:
                solver_data = yaml_config['solver'].copy()
                audio_data = solver_data.pop('audio', {})
                image_data = solver_data.pop('image', {})
                config.solver = SolverConfig(
                    **solver_data,
                    audio=AudioConfig(**audio_data),
                    image=ImageConfig(**image_data)
                )
            
            # Pricing
            if 'pricing' in yaml_config:
                config.pricing = PricingConfig(**yaml_config['pricing'])
            
            # Rate Limit
            if 'rate_limit' in yaml_config:
                config.rate_limit = RateLimitConfig(**yaml_config['rate_limit'])
            
            # Logging
            if 'logging' in yaml_config:
                config.logging = LoggingConfig(**yaml_config['logging'])
    
    # Override with environment variables
    if os.environ.get('SOLVER_HOST'):
        config.server.host = os.environ['SOLVER_HOST']
    if os.environ.get('SOLVER_PORT'):
        config.server.port = int(os.environ['SOLVER_PORT'])
    if os.environ.get('SOLVER_DEBUG'):
        config.server.debug = os.environ['SOLVER_DEBUG'].lower() == 'true'
    if os.environ.get('BROWSER_HEADLESS'):
        config.browser.headless = os.environ['BROWSER_HEADLESS'].lower() == 'true'
    if os.environ.get('YOLO_MODEL_PATH'):
        config.solver.image.model_path = os.environ['YOLO_MODEL_PATH']
    
    # Ensure directories exist
    config.models_dir.mkdir(parents=True, exist_ok=True)
    config.logs_dir.mkdir(parents=True, exist_ok=True)
    config.data_dir.mkdir(parents=True, exist_ok=True)
    
    return config


# Global config instance
_config: Optional[Config] = None


def get_config() -> Config:
    """Get the global configuration instance"""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reload_config(config_path: Optional[str] = None) -> Config:
    """Reload configuration from file"""
    global _config
    _config = load_config(config_path)
    return _config
