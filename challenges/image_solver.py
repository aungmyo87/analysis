"""
Image Challenge Solver - Global YOLO Singleton
===============================================

Handles reCAPTCHA image challenges using YOLOv8 object detection.

CRITICAL OPTIMIZATION: GLOBAL SINGLETON PATTERN
------------------------------------------------
The YOLO model is loaded ONCE at server startup and shared across all requests.

WHY THIS MATTERS:
- Model loading from disk: ~2-5 seconds
- Model already in memory: ~0ms
- On a 12-core VPS with 100 RPS, this saves 200-500 seconds of CPU time per second!

MEMORY FOOTPRINT:
- YOLOv8m model: ~50MB GPU / ~100MB CPU
- Loaded once, stays in memory for server lifetime

USAGE:
------
# At startup (in main.py lifespan):
from challenges.image_solver import load_yolo_model
model = load_yolo_model()

# In request handlers:
from challenges.image_solver import get_yolo_model
model = get_yolo_model()  # Returns cached instance, no disk I/O
"""

import os
import logging
import tempfile
import base64
import asyncio
import aiohttp
import uuid
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, Optional, List, Tuple
from pathlib import Path
from PIL import Image
import io

logger = logging.getLogger(__name__)


# =============================================================================
# ACTIVE LEARNING DATA COLLECTION
# =============================================================================

# Thread pool for non-blocking image saving
_image_save_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="img_saver")

# Base paths for data collection
DATA_COLLECTION_BASE = Path(__file__).parent.parent / "data" / "training_collection"
FAILED_CASES_DIR = DATA_COLLECTION_BASE / "failed_cases"

# Confidence thresholds for uncertain predictions (Active Learning)
AL_CONFIDENCE_LOW = 0.3
AL_CONFIDENCE_HIGH = 0.6

# Unique class names for folder creation (from CHALLENGE_MAPPING values)
UNIQUE_CLASSES = {
    "bicycle", "bus", "car", "crosswalk", "fire_hydrant",
    "motorcycle", "traffic_light", "stairs", "chimney",
    "bridge", "boat", "tractor"
}


def _ensure_collection_directories():
    """
    Create all necessary directories for data collection.
    Called once at startup.
    """
    try:
        # Create base directory
        DATA_COLLECTION_BASE.mkdir(parents=True, exist_ok=True)
        
        # Create class subdirectories
        for class_name in UNIQUE_CLASSES:
            class_dir = DATA_COLLECTION_BASE / class_name
            class_dir.mkdir(exist_ok=True)
        
        # Create failed_cases directory
        FAILED_CASES_DIR.mkdir(exist_ok=True)
        
        logger.info(f"Active Learning directories initialized at: {DATA_COLLECTION_BASE}")
    except Exception as e:
        logger.warning(f"Could not create data collection directories: {e}")


def _save_image_sync(image_bytes: bytes, save_path: Path):
    """
    Synchronous image save (runs in thread pool).
    """
    try:
        with open(save_path, "wb") as f:
            f.write(image_bytes)
    except Exception as e:
        logger.debug(f"Failed to save image to {save_path}: {e}")


def save_uncertain_tile(image_bytes: bytes, class_name: str, confidence: float):
    """
    Save a tile with uncertain prediction for Active Learning.
    Non-blocking - submits to thread pool.
    
    Args:
        image_bytes: Raw image bytes
        class_name: Target class name (e.g., "bicycle", "car")
        confidence: Model confidence score
    """
    try:
        class_dir = DATA_COLLECTION_BASE / class_name
        if not class_dir.exists():
            class_dir.mkdir(parents=True, exist_ok=True)
        
        # Generate unique filename with confidence score
        filename = f"{uuid.uuid4().hex[:12]}_conf{confidence:.2f}.jpg"
        save_path = class_dir / filename
        
        # Submit to thread pool (non-blocking)
        _image_save_executor.submit(_save_image_sync, image_bytes, save_path)
        logger.debug(f"Queued uncertain tile for saving: {class_name} (conf={confidence:.2f})")
    except Exception as e:
        logger.debug(f"Error queueing uncertain tile: {e}")


def save_failed_case_tiles(tiles: List[Tuple[int, bytes]], challenge_type: str):
    """
    Save all tiles from a failed solve attempt.
    Non-blocking - submits to thread pool.
    
    Args:
        tiles: List of (index, image_bytes) tuples
        challenge_type: The challenge type that failed
    """
    try:
        # Create a unique subfolder for this failed case
        case_id = uuid.uuid4().hex[:8]
        safe_challenge = challenge_type.replace(" ", "_").replace("/", "_")[:30]
        case_dir = FAILED_CASES_DIR / f"{safe_challenge}_{case_id}"
        case_dir.mkdir(parents=True, exist_ok=True)
        
        # Submit each tile to thread pool
        for idx, image_bytes in tiles:
            filename = f"tile_{idx:02d}.jpg"
            save_path = case_dir / filename
            _image_save_executor.submit(_save_image_sync, image_bytes, save_path)
        
        logger.info(f"Queued {len(tiles)} tiles from failed case to: {case_dir.name}")
    except Exception as e:
        logger.debug(f"Error saving failed case tiles: {e}")


# =============================================================================
# GLOBAL SINGLETON - YOLO Model
# =============================================================================

# The model instance - loaded ONCE, used by ALL requests
_yolo_model = None
_yolo_model_lock = asyncio.Lock()


def load_yolo_model(model_path: Optional[str] = None):
    """
    Load the YOLO model into memory (SINGLETON).
    
    This should be called ONCE during server startup.
    The model stays in memory for the lifetime of the server.
    
    Args:
        model_path: Optional path to custom model. If None, uses config.
    
    Returns:
        YOLO model instance
    
    Usage:
        # In main.py lifespan startup:
        model = load_yolo_model()
    """
    global _yolo_model
    
    if _yolo_model is not None:
        logger.debug("YOLO model already loaded (singleton)")
        return _yolo_model
    
    # Initialize Active Learning directories
    _ensure_collection_directories()
    
    try:
        from ultralytics import YOLO  # type: ignore
        from ..core.config import get_config
        
        config = get_config()
        
        # Determine model path
        if model_path is None:
            model_path = config.solver.image.model_path
        
        # Resolve relative paths
        path = Path(model_path)
        if not path.is_absolute():
            path = config.base_dir / model_path
        
        # Load model
        if path.exists():
            logger.info(f"Loading custom YOLO model from {path}")
            _yolo_model = YOLO(str(path))
        else:
            logger.warning(f"Custom model not found at {path}, using yolov8m")
            _yolo_model = YOLO("yolov8m.pt")
        
        # Warm up the model with a dummy prediction (loads weights into GPU/CPU cache)
        logger.info("Warming up YOLO model...")
        dummy_image = Image.new('RGB', (640, 640), color='white')
        _yolo_model.predict(dummy_image, verbose=False)
        
        logger.info(f"YOLO model loaded successfully: {type(_yolo_model).__name__}")
        return _yolo_model
        
    except Exception as e:
        logger.error(f"Failed to load YOLO model: {e}")
        raise


def get_yolo_model():
    """
    Get the loaded YOLO model (SINGLETON).
    
    Returns None if model hasn't been loaded yet.
    This is a zero-cost operation - just returns the cached instance.
    
    Returns:
        YOLO model instance or None
    
    Usage:
        model = get_yolo_model()
        if model:
            results = model.predict(image)
    """
    return _yolo_model


async def get_yolo_model_async():
    """
    Get the YOLO model, loading it if necessary (thread-safe).
    
    This is the async-safe version that can be called from request handlers.
    Uses a lock to prevent multiple simultaneous loads.
    
    Returns:
        YOLO model instance
    """
    global _yolo_model
    
    if _yolo_model is not None:
        return _yolo_model
    
    async with _yolo_model_lock:
        if _yolo_model is not None:
            return _yolo_model
        
        # Run the synchronous load in a thread pool
        loop = asyncio.get_event_loop()
        _yolo_model = await loop.run_in_executor(None, load_yolo_model)
        return _yolo_model


# =============================================================================
# CHALLENGE TYPE MAPPING
# =============================================================================

CHALLENGE_MAPPING = {
    # Singular forms
    "bicycle": "bicycle",
    "bus": "bus",
    "car": "car",
    "crosswalk": "crosswalk",
    "fire hydrant": "fire_hydrant",
    "hydrant": "fire_hydrant",
    "motorcycle": "motorcycle",
    "traffic light": "traffic_light",
    "stair": "stairs",
    "stairs": "stairs",
    "chimney": "chimney",
    "bridge": "bridge",
    "boat": "boat",
    "tractor": "tractor",
    
    # Plural forms
    "bicycles": "bicycle",
    "buses": "bus",
    "cars": "car",
    "crosswalks": "crosswalk",
    "fire hydrants": "fire_hydrant",
    "hydrants": "fire_hydrant",
    "motorcycles": "motorcycle",
    "traffic lights": "traffic_light",
    "chimneys": "chimney",
    "bridges": "bridge",
    "boats": "boat",
    "tractors": "tractor",
    
    # With article
    "a bicycle": "bicycle",
    "a bus": "bus",
    "a car": "car",
    "a crosswalk": "crosswalk",
    "a fire hydrant": "fire_hydrant",
    "a motorcycle": "motorcycle",
    "a traffic light": "traffic_light",
    "a boat": "boat",
    "a tractor": "tractor",
}


# =============================================================================
# IMAGE SOLVER CLASS
# =============================================================================

class ImageSolver:
    """
    Solves reCAPTCHA image challenges using YOLOv8 object detection.
    
    IMPORTANT: This class uses the GLOBAL SINGLETON model.
    It does NOT load the model itself - the model must be pre-loaded
    at server startup via load_yolo_model().
    
    Supports:
    - 3x3 grid challenges
    - 4x4 grid challenges
    - Dynamic/multi-round challenges
    """
    
    def __init__(self):
        from ..core.config import get_config
        self.config = get_config()
        self.confidence_threshold = self.config.solver.image.confidence_threshold
        self.max_rounds = self.config.solver.image.max_rounds
    
    def _get_model(self):
        """
        Get the YOLO model (singleton).
        
        This is a ZERO-COST operation - just returns the cached global instance.
        No disk I/O, no initialization overhead.
        """
        model = get_yolo_model()
        if model is None:
            raise RuntimeError(
                "YOLO model not loaded. Call load_yolo_model() at startup."
            )
        return model
    
    async def solve(self, page) -> Dict[str, Any]:
        """
        Solve the image challenge.
        
        Args:
            page: Browser page with reCAPTCHA challenge
        
        Returns:
            dict with 'success' and 'error' keys
        
        ACTIVE LEARNING: On failure, saves all tiles from the last round
        to data/training_collection/failed_cases/ for analysis.
        """
        last_round_tiles = []  # Track tiles for failed case collection
        last_challenge_type = None
        
        try:
            # Get the singleton model (zero-cost)
            model = self._get_model()
            
            for round_num in range(self.max_rounds):
                logger.info(f"Image solve round {round_num + 1}/{self.max_rounds}")
                
                # Get challenge frame
                challenge_frame = await self._get_challenge_frame(page)
                if not challenge_frame:
                    logger.error("Could not find challenge frame")
                    return {"success": False, "error": "Challenge frame not found"}
                
                # Get challenge type
                challenge_type = await self._get_challenge_type(challenge_frame)
                if not challenge_type:
                    logger.warning("Could not determine challenge type")
                    return {"success": False, "error": "Unknown challenge type"}
                
                logger.info(f"Challenge type: {challenge_type}")
                last_challenge_type = challenge_type
                
                # Map to YOLO class
                target_class = self._map_challenge_to_class(challenge_type)
                if not target_class:
                    logger.warning(f"No mapping for challenge type: {challenge_type}")
                    return {"success": False, "error": f"Unsupported challenge: {challenge_type}"}
                
                logger.info(f"Target class: {target_class}")
                
                # Get tile images
                tiles = await self._get_tile_images(challenge_frame)
                if not tiles:
                    logger.warning("Could not get tile images")
                    return {"success": False, "error": "Could not get tiles"}
                
                logger.info(f"Got {len(tiles)} tiles")
                
                # Store for potential failed case collection
                last_round_tiles = tiles
                
                # Classify tiles using the singleton model
                matching_indices = await self._classify_tiles(tiles, target_class, model)
                logger.info(f"Matching tiles: {matching_indices}")
                
                # Click matching tiles
                if matching_indices:
                    await self._click_tiles(challenge_frame, matching_indices)
                    await page.wait_for_timeout(500)
                
                # Click verify
                await self._click_verify(challenge_frame)
                await page.wait_for_timeout(2000)
                
                # Check if solved
                if await self._check_solved(page):
                    logger.info("Image challenge solved!")
                    return {"success": True}
                
                # Check if new tiles appeared (multi-round)
                if await self._check_new_tiles(challenge_frame):
                    logger.info("New tiles appeared, continuing...")
                    continue
                
                # Check if challenge changed
                new_challenge_type = await self._get_challenge_type(challenge_frame)
                if new_challenge_type and new_challenge_type != challenge_type:
                    logger.info(f"Challenge changed to: {new_challenge_type}")
                    continue
            
            # ACTIVE LEARNING: Save failed case tiles
            if last_round_tiles and last_challenge_type:
                save_failed_case_tiles(last_round_tiles, last_challenge_type)
            
            return {"success": False, "error": f"Failed after {self.max_rounds} rounds"}
            
        except Exception as e:
            logger.error(f"Image solve error: {e}")
            
            # ACTIVE LEARNING: Save failed case tiles on exception too
            if last_round_tiles and last_challenge_type:
                save_failed_case_tiles(last_round_tiles, last_challenge_type)
            
            return {"success": False, "error": str(e)}
    
    async def _get_challenge_frame(self, page):
        """Get the challenge iframe content frame"""
        selectors = [
            "iframe[src*='recaptcha'][src*='bframe']",
            "iframe[src*='google.com/recaptcha/api2/bframe']",
            "iframe[src*='google.com/recaptcha/enterprise/bframe']",
        ]
        
        for selector in selectors:
            try:
                iframe = await page.query_selector(selector)
                if iframe:
                    frame = await iframe.content_frame()
                    if frame:
                        return frame
            except Exception:
                continue
        
        return None
    
    async def _get_challenge_type(self, frame) -> Optional[str]:
        """Extract the challenge type from the instructions"""
        try:
            instruction = await frame.query_selector(".rc-imageselect-desc-wrapper")
            if instruction:
                text = await instruction.text_content()
                text = text.lower().strip()
                
                for key in CHALLENGE_MAPPING.keys():
                    if key in text:
                        return key
                
                return text
            
            return None
        except Exception as e:
            logger.error(f"Error getting challenge type: {e}")
            return None
    
    def _map_challenge_to_class(self, challenge_type: str) -> Optional[str]:
        """Map challenge text to YOLO class name"""
        challenge_lower = challenge_type.lower()
        
        if challenge_lower in CHALLENGE_MAPPING:
            return CHALLENGE_MAPPING[challenge_lower]
        
        for key, value in CHALLENGE_MAPPING.items():
            if key in challenge_lower:
                return value
        
        return None
    
    async def _get_tile_images(self, frame) -> List[Tuple[int, bytes]]:
        """Get all tile images from the challenge."""
        tiles = []
        
        try:
            tile_elements = await frame.query_selector_all(".rc-imageselect-tile")
            
            for i, tile in enumerate(tile_elements):
                try:
                    img = await tile.query_selector("img")
                    if img:
                        src = await img.get_attribute("src")
                        if src:
                            if src.startswith("data:"):
                                data = src.split(",")[1]
                                image_bytes = base64.b64decode(data)
                            else:
                                async with aiohttp.ClientSession() as session:
                                    async with session.get(src) as response:
                                        if response.status == 200:
                                            image_bytes = await response.read()
                                        else:
                                            continue
                            
                            tiles.append((i, image_bytes))
                except Exception as e:
                    logger.debug(f"Error getting tile {i}: {e}")
            
            return tiles
            
        except Exception as e:
            logger.error(f"Error getting tile images: {e}")
            return []
    
    async def _classify_tiles(
        self,
        tiles: List[Tuple[int, bytes]],
        target_class: str,
        model
    ) -> List[int]:
        """
        Classify tiles and return indices of matching tiles.
        
        ACTIVE LEARNING: Tiles with uncertain predictions (confidence 0.3-0.6)
        are saved to data/training_collection/{class}/ for later labeling.
        """
        matching_indices = []
        
        for idx, image_bytes in tiles:
            try:
                image = Image.open(io.BytesIO(image_bytes))
                
                # Run prediction using the singleton model
                results = model.predict(
                    image,
                    conf=self.confidence_threshold,
                    verbose=False
                )
                
                if results and len(results) > 0:
                    for detection in results[0].boxes:
                        class_id = int(detection.cls)
                        class_name = model.names[class_id]
                        confidence = float(detection.conf)
                        
                        # Active Learning: Save uncertain predictions
                        if AL_CONFIDENCE_LOW <= confidence <= AL_CONFIDENCE_HIGH:
                            save_uncertain_tile(image_bytes, target_class, confidence)
                        
                        if class_name.lower() == target_class.lower():
                            logger.debug(f"Tile {idx}: Found {class_name} with conf {confidence:.2f}")
                            matching_indices.append(idx)
                            break
                        
                        if target_class.replace("_", " ") in class_name.lower():
                            logger.debug(f"Tile {idx}: Found {class_name} (similar) with conf {confidence:.2f}")
                            matching_indices.append(idx)
                            break
                
            except Exception as e:
                logger.debug(f"Error classifying tile {idx}: {e}")
        
        return matching_indices
    
    async def _click_tiles(self, frame, indices: List[int]):
        """Click the specified tile indices"""
        try:
            tile_elements = await frame.query_selector_all(".rc-imageselect-tile")
            
            for idx in indices:
                if idx < len(tile_elements):
                    await tile_elements[idx].click()
                    await frame.wait_for_timeout(200)
                    
        except Exception as e:
            logger.error(f"Error clicking tiles: {e}")
    
    async def _click_verify(self, frame):
        """Click the verify button"""
        try:
            verify_button = await frame.query_selector("#recaptcha-verify-button")
            if verify_button:
                await verify_button.click()
        except Exception as e:
            logger.error(f"Error clicking verify: {e}")
    
    async def _check_solved(self, page) -> bool:
        """Check if the captcha was solved"""
        try:
            iframe_selectors = [
                "iframe[src*='recaptcha'][src*='anchor']",
                "iframe[src*='google.com/recaptcha/api2/anchor']",
            ]
            
            for selector in iframe_selectors:
                try:
                    iframe = await page.query_selector(selector)
                    if iframe:
                        frame = await iframe.content_frame()
                        if frame:
                            is_checked = await frame.evaluate('''
                                () => {
                                    const anchor = document.querySelector('#recaptcha-anchor');
                                    return anchor && anchor.classList.contains('recaptcha-checkbox-checked');
                                }
                            ''')
                            if is_checked:
                                return True
                except Exception:
                    continue
            
            return False
        except Exception:
            return False
    
    async def _check_new_tiles(self, frame) -> bool:
        """Check if new tiles appeared (dynamic challenge)"""
        try:
            loading_tiles = await frame.query_selector_all(".rc-imageselect-dynamic-selected")
            return len(loading_tiles) > 0
        except Exception:
            return False
