"""
Image Challenge Solver
Handles reCAPTCHA image challenges using YOLO object detection
"""

import os
import logging
import tempfile
import base64
import aiohttp
from typing import Dict, Any, Optional, List, Tuple
from pathlib import Path
from PIL import Image
import io

logger = logging.getLogger(__name__)


# Challenge type to YOLO class mapping
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


class ImageSolver:
    """
    Solves reCAPTCHA image challenges using YOLOv8 object detection.
    
    Supports:
    - 3x3 grid challenges
    - 4x4 grid challenges
    - Dynamic/multi-round challenges
    """
    
    def __init__(self):
        from ..core.config import get_config
        self.config = get_config()
        self.model_path = self.config.solver.image.model_path
        self.confidence_threshold = self.config.solver.image.confidence_threshold
        self.max_rounds = self.config.solver.image.max_rounds
        self._model = None
    
    def _load_model(self):
        """Load the YOLO model"""
        if self._model is None:
            try:
                from ultralytics import YOLO  # type: ignore
                
                # Check if custom model exists
                model_path = Path(self.model_path)
                if not model_path.is_absolute():
                    model_path = self.config.base_dir / model_path
                
                if model_path.exists():
                    logger.info(f"Loading custom YOLO model from {model_path}")
                    self._model = YOLO(str(model_path))
                else:
                    logger.warning(f"Custom model not found at {model_path}, using yolov8m")
                    self._model = YOLO("yolov8m.pt")
                    
            except Exception as e:
                logger.error(f"Error loading YOLO model: {e}")
                raise
        
        return self._model
    
    async def solve(self, page) -> Dict[str, Any]:
        """
        Solve the image challenge.
        
        Args:
            page: Browser page with reCAPTCHA challenge
        
        Returns:
            dict with 'success' and 'error' keys
        """
        try:
            # Load model
            model = self._load_model()
            
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
                
                # Classify tiles
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
            
            return {"success": False, "error": f"Failed after {self.max_rounds} rounds"}
            
        except Exception as e:
            logger.error(f"Image solve error: {e}")
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
            # Get instruction text
            instruction = await frame.query_selector(".rc-imageselect-desc-wrapper")
            if instruction:
                text = await instruction.text_content()
                text = text.lower().strip()
                
                # Extract the target object
                # "Select all images with bicycles"
                # "Click verify once there are none left"
                
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
        
        # Direct lookup
        if challenge_lower in CHALLENGE_MAPPING:
            return CHALLENGE_MAPPING[challenge_lower]
        
        # Partial match
        for key, value in CHALLENGE_MAPPING.items():
            if key in challenge_lower:
                return value
        
        return None
    
    async def _get_tile_images(self, frame) -> List[Tuple[int, bytes]]:
        """
        Get all tile images from the challenge.
        
        Returns:
            List of (index, image_bytes) tuples
        """
        tiles = []
        
        try:
            # Get tile elements
            tile_elements = await frame.query_selector_all(".rc-imageselect-tile")
            
            for i, tile in enumerate(tile_elements):
                try:
                    # Get the image element within the tile
                    img = await tile.query_selector("img")
                    if img:
                        src = await img.get_attribute("src")
                        if src:
                            if src.startswith("data:"):
                                # Base64 encoded
                                data = src.split(",")[1]
                                image_bytes = base64.b64decode(data)
                            else:
                                # URL - download
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
        
        Args:
            tiles: List of (index, image_bytes) tuples
            target_class: Target class name to detect
            model: YOLO model
        
        Returns:
            List of tile indices that contain the target
        """
        matching_indices = []
        
        for idx, image_bytes in tiles:
            try:
                # Convert bytes to PIL Image
                image = Image.open(io.BytesIO(image_bytes))
                
                # Run YOLO prediction
                results = model.predict(
                    image,
                    conf=self.confidence_threshold,
                    verbose=False
                )
                
                # Check if target class detected
                if results and len(results) > 0:
                    for detection in results[0].boxes:
                        class_id = int(detection.cls)
                        class_name = model.names[class_id]
                        confidence = float(detection.conf)
                        
                        # Check if matches target
                        if class_name.lower() == target_class.lower():
                            logger.debug(f"Tile {idx}: Found {class_name} with conf {confidence:.2f}")
                            matching_indices.append(idx)
                            break
                        
                        # Also check for similar names
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
                    await frame.wait_for_timeout(200)  # Small delay between clicks
                    
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
            # Look for tiles that are fading in or have loading state
            loading_tiles = await frame.query_selector_all(".rc-imageselect-dynamic-selected")
            return len(loading_tiles) > 0
        except Exception:
            return False
