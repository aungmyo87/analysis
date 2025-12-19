"""
Challenge Solvers Module
========================

Contains audio and image challenge solvers.

YOLO MODEL SINGLETON:
--------------------
The YOLO model is loaded ONCE at server startup and shared across all requests.

Usage:
    # At startup (main.py):
    from challenges.image_solver import load_yolo_model
    load_yolo_model()
    
    # In request handlers:
    from challenges.image_solver import get_yolo_model
    model = get_yolo_model()  # Returns cached instance
"""

from .audio_solver import AudioSolver, AudioRateLimitError
from .image_solver import (
    ImageSolver,
    load_yolo_model,
    get_yolo_model,
    get_yolo_model_async,
)

__all__ = [
    'AudioSolver',
    'AudioRateLimitError',
    'ImageSolver',
    'load_yolo_model',
    'get_yolo_model',
    'get_yolo_model_async',
]
