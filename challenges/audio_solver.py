"""
Audio Challenge Solver
Handles reCAPTCHA audio challenges using speech recognition
"""

import os
import logging
import tempfile
import aiohttp
from typing import Dict, Any, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


class AudioSolver:
    """
    Solves reCAPTCHA audio challenges using speech recognition.
    
    Supports multiple transcription engines:
    - Whisper (local, recommended)
    - Google Speech Recognition
    - Azure Speech Services
    """
    
    def __init__(self):
        from ..core.config import get_config
        self.config = get_config()
        self.engine = self.config.solver.audio.engine
        self.max_attempts = self.config.solver.audio.max_attempts
        self._whisper_model = None
    
    async def solve(self, page) -> Dict[str, Any]:
        """
        Solve the audio challenge.
        
        Args:
            page: Browser page with reCAPTCHA challenge
        
        Returns:
            dict with 'success' and 'error' keys
        """
        for attempt in range(self.max_attempts):
            try:
                logger.info(f"Audio solve attempt {attempt + 1}/{self.max_attempts}")
                
                # Get challenge frame
                challenge_frame = await self._get_challenge_frame(page)
                if not challenge_frame:
                    logger.error("Could not find challenge frame")
                    continue
                
                # Click audio button
                clicked = await self._click_audio_button(challenge_frame)
                if not clicked:
                    logger.warning("Could not click audio button")
                    continue
                
                await page.wait_for_timeout(1000)
                
                # Check for rate limit
                if await self._check_rate_limit(challenge_frame):
                    logger.warning("Rate limited, cannot use audio")
                    return {"success": False, "error": "Rate limited"}
                
                # Get audio URL
                audio_url = await self._get_audio_url(challenge_frame)
                if not audio_url:
                    logger.warning("Could not get audio URL")
                    continue
                
                # Download audio
                audio_path = await self._download_audio(audio_url)
                if not audio_path:
                    logger.warning("Could not download audio")
                    continue
                
                try:
                    # Transcribe audio
                    transcription = await self._transcribe_audio(audio_path)
                    if not transcription:
                        logger.warning("Could not transcribe audio")
                        # Try new audio
                        await self._click_reload_button(challenge_frame)
                        await page.wait_for_timeout(1000)
                        continue
                    
                    logger.info(f"Transcription: {transcription}")
                    
                    # Submit answer
                    submitted = await self._submit_answer(challenge_frame, transcription)
                    if not submitted:
                        logger.warning("Could not submit answer")
                        continue
                    
                    await page.wait_for_timeout(2000)
                    
                    # Check if solved
                    if await self._check_solved(page):
                        logger.info("Audio challenge solved!")
                        return {"success": True}
                    
                    # Wrong answer, try again with new audio
                    logger.info("Wrong answer, trying new audio")
                    await self._click_reload_button(challenge_frame)
                    await page.wait_for_timeout(1000)
                    
                finally:
                    # Cleanup temp file
                    if audio_path and os.path.exists(audio_path):
                        os.remove(audio_path)
                
            except Exception as e:
                logger.error(f"Audio solve attempt {attempt + 1} error: {e}")
        
        return {"success": False, "error": f"Failed after {self.max_attempts} attempts"}
    
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
    
    async def _click_audio_button(self, frame) -> bool:
        """Click the audio challenge button"""
        try:
            # Find audio button
            audio_button = await frame.query_selector("#recaptcha-audio-button")
            if audio_button:
                await audio_button.click()
                return True
            
            # Alternative selector
            audio_button = await frame.query_selector('button[id="recaptcha-audio-button"]')
            if audio_button:
                await audio_button.click()
                return True
            
            return False
        except Exception as e:
            logger.error(f"Error clicking audio button: {e}")
            return False
    
    async def _check_rate_limit(self, frame) -> bool:
        """Check if rate limited"""
        try:
            error_element = await frame.query_selector(".rc-doscaptcha-header-text")
            if error_element:
                text = await error_element.text_content()
                if "try again later" in text.lower():
                    return True
            return False
        except Exception:
            return False
    
    async def _get_audio_url(self, frame) -> Optional[str]:
        """Get the audio challenge URL"""
        try:
            # Method 1: Get from audio element
            audio_source = await frame.query_selector("#audio-source")
            if audio_source:
                src = await audio_source.get_attribute("src")
                if src:
                    return src
            
            # Method 2: Get from download link
            download_link = await frame.query_selector(".rc-audiochallenge-tdownload-link")
            if download_link:
                href = await download_link.get_attribute("href")
                if href:
                    return href
            
            return None
        except Exception as e:
            logger.error(f"Error getting audio URL: {e}")
            return None
    
    async def _download_audio(self, url: str) -> Optional[str]:
        """Download audio file to temp location"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        # Save to temp file
                        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                            f.write(await response.read())
                            return f.name
            return None
        except Exception as e:
            logger.error(f"Error downloading audio: {e}")
            return None
    
    async def _transcribe_audio(self, audio_path: str) -> Optional[str]:
        """Transcribe audio using configured engine"""
        try:
            if self.engine == "whisper":
                return await self._transcribe_whisper(audio_path)
            elif self.engine == "google":
                return await self._transcribe_google(audio_path)
            elif self.engine == "azure":
                return await self._transcribe_azure(audio_path)
            else:
                # Default to whisper
                return await self._transcribe_whisper(audio_path)
        except Exception as e:
            logger.error(f"Transcription error: {e}")
            return None
    
    async def _transcribe_whisper(self, audio_path: str) -> Optional[str]:
        """Transcribe using local Whisper model"""
        try:
            import whisper  # type: ignore
            
            # Load model (cached)
            if self._whisper_model is None:
                model_name = self.config.solver.audio.whisper_model
                logger.info(f"Loading Whisper model: {model_name}")
                self._whisper_model = whisper.load_model(model_name)
            
            # Transcribe
            result = self._whisper_model.transcribe(
                audio_path,
                language="en",
                fp16=False
            )
            
            text = result.get("text", "").strip()
            return text if text else None
            
        except Exception as e:
            logger.error(f"Whisper transcription error: {e}")
            return None
    
    async def _transcribe_google(self, audio_path: str) -> Optional[str]:
        """Transcribe using Google Speech Recognition"""
        try:
            import speech_recognition as sr
            from pydub import AudioSegment
            
            # Convert MP3 to WAV
            audio = AudioSegment.from_mp3(audio_path)
            wav_path = audio_path.replace(".mp3", ".wav")
            audio.export(wav_path, format="wav")
            
            try:
                # Recognize
                recognizer = sr.Recognizer()
                with sr.AudioFile(wav_path) as source:
                    audio_data = recognizer.record(source)
                    text = recognizer.recognize_google(audio_data)  # type: ignore
                    return text
            finally:
                if os.path.exists(wav_path):
                    os.remove(wav_path)
                    
        except Exception as e:
            logger.error(f"Google transcription error: {e}")
            return None
    
    async def _transcribe_azure(self, audio_path: str) -> Optional[str]:
        """Transcribe using Azure Speech Services"""
        try:
            # Azure SDK would be imported here
            # For now, fallback to Whisper
            logger.warning("Azure not configured, falling back to Whisper")
            return await self._transcribe_whisper(audio_path)
        except Exception as e:
            logger.error(f"Azure transcription error: {e}")
            return None
    
    async def _submit_answer(self, frame, answer: str) -> bool:
        """Submit the audio transcription answer"""
        try:
            # Find input field
            input_field = await frame.query_selector("#audio-response")
            if not input_field:
                return False
            
            # Clear and type answer
            await input_field.click()
            await input_field.fill(answer)
            
            # Click verify button
            verify_button = await frame.query_selector("#recaptcha-verify-button")
            if verify_button:
                await verify_button.click()
                return True
            
            return False
        except Exception as e:
            logger.error(f"Error submitting answer: {e}")
            return False
    
    async def _click_reload_button(self, frame) -> bool:
        """Click reload button to get new audio"""
        try:
            reload_button = await frame.query_selector("#recaptcha-reload-button")
            if reload_button:
                await reload_button.click()
                return True
            return False
        except Exception:
            return False
    
    async def _check_solved(self, page) -> bool:
        """Check if the captcha was solved"""
        try:
            # Check for checkbox checked state
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
