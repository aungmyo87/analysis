"""
Audio Challenge Solver
=========================

Handles reCAPTCHA audio challenges using speech recognition.

OPTIMIZATIONS:
--------------
1. Audio Cleaning: Normalize + remove silence via pydub before Whisper
2. Whisper Tuning: Medium model + initial_prompt for digit recognition
3. Stealth Download: User-Agent header matching browser fingerprint
4. Error Handling: AudioRateLimitError for immediate YOLO fallback
"""

import os
import logging
import tempfile
import asyncio
import aiohttp
from typing import Dict, Any, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


class AudioRateLimitError(Exception):
    """
    Raised when reCAPTCHA rate-limits audio challenges.
    
    This signals NormalSolver to skip remaining audio attempts
    and switch immediately to YOLO image solver.
    """
    pass


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
                
                # Check for rate limit - raise exception for immediate fallback
                if await self._check_rate_limit(challenge_frame):
                    logger.warning("Rate limited - raising AudioRateLimitError for YOLO fallback")
                    raise AudioRateLimitError("reCAPTCHA audio rate limited")
                
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
    
    async def _download_audio(self, url: str, user_agent: Optional[str] = None) -> Optional[str]:
        """
        Download audio file with stealth headers.
        
        Args:
            url: Audio file URL
            user_agent: Browser User-Agent to match fingerprint
        
        Returns:
            Path to downloaded temp file or None
        """
        try:
            # Stealth headers to match browser fingerprint
            headers = {
                "Accept": "audio/webm,audio/ogg,audio/wav,audio/*;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "identity",
                "Referer": "https://www.google.com/",
                "Sec-Fetch-Dest": "audio",
                "Sec-Fetch-Mode": "no-cors",
                "Sec-Fetch-Site": "cross-site",
            }
            
            # Use provided user_agent or default Chrome UA
            headers["User-Agent"] = user_agent or (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            
            async with aiohttp.ClientSession(headers=headers) as session:
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
    
    def _clean_audio(self, audio_path: str) -> str:
        """
        Clean audio for better Whisper transcription.
        
        Operations:
        1. Normalize volume to -20 dBFS
        2. Remove leading/trailing silence
        3. Apply slight noise reduction via low-pass filter
        
        Args:
            audio_path: Path to raw audio file
        
        Returns:
            Path to cleaned audio file
        """
        try:
            from pydub import AudioSegment
            from pydub.silence import strip_silence
            
            # Load audio
            audio = AudioSegment.from_mp3(audio_path)
            
            # Normalize volume to -20 dBFS (good level for speech)
            target_dBFS = -20.0
            change_in_dBFS = target_dBFS - audio.dBFS
            audio = audio.apply_gain(change_in_dBFS)
            
            # Remove leading/trailing silence
            # silence_thresh: audio below this is considered silence
            # min_silence_len: minimum length of silence to strip (ms)
            audio = strip_silence(
                audio,
                silence_thresh=-40,  # dBFS threshold
                padding=100  # Keep 100ms padding
            )
            
            # Low-pass filter to reduce high-frequency noise
            # reCAPTCHA audio is speech, mostly below 4kHz
            audio = audio.low_pass_filter(4000)
            
            # Export cleaned audio
            cleaned_path = audio_path.replace(".mp3", "_cleaned.mp3")
            audio.export(cleaned_path, format="mp3")
            
            logger.debug(f"Audio cleaned: {audio_path} -> {cleaned_path}")
            return cleaned_path
            
        except Exception as e:
            logger.warning(f"Audio cleaning failed, using original: {e}")
            return audio_path
    
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
        """
        Transcribe using local Whisper model with reCAPTCHA-tuned settings.
        
        Optimizations:
        - Medium model for better accuracy (fits 24GB RAM)
        - initial_prompt tuned for digit/letter sequences
        - Clean audio before transcription
        """
        try:
            import whisper  # type: ignore
            
            # Load model (cached) - use medium for better accuracy
            if self._whisper_model is None:
                model_name = self.config.solver.audio.whisper_model
                logger.info(f"Loading Whisper model: {model_name}")
                self._whisper_model = whisper.load_model(model_name)
            
            # Clean audio before transcription
            cleaned_path = self._clean_audio(audio_path)
            
            try:
                # Initial prompt tuned for reCAPTCHA audio challenges
                # Primes Whisper to expect digits and letters
                initial_prompt = (
                    "The audio contains spoken digits and letters. "
                    "Examples: 7 3 9 2 5, a b c d e, 4 8 1 6 0, "
                    "m n p q r, 2 4 6 8 0."
                )
                
                # Transcribe with optimized settings
                result = self._whisper_model.transcribe(
                    cleaned_path,
                    language="en",
                    fp16=False,  # CPU mode
                    initial_prompt=initial_prompt,
                    temperature=0.0,  # Deterministic output
                    compression_ratio_threshold=2.4,
                    logprob_threshold=-1.0,
                    no_speech_threshold=0.6,
                )
                
                text = result.get("text", "").strip()
                
                # Post-process: remove extra spaces, lowercase
                text = " ".join(text.split()).lower()
                
                return text if text else None
                
            finally:
                # Cleanup cleaned audio if different from original
                if cleaned_path != audio_path and os.path.exists(cleaned_path):
                    os.remove(cleaned_path)
            
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
