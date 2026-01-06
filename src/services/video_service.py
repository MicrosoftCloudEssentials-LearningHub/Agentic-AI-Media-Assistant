"""
Video generation service using image sequences from DALL-E-3 and FLUX.2-pro.

TEMPORARY IMPLEMENTATION NOTE:
This service generates videos by creating image sequences and stitching them together.
This is a workaround because:
1. Sora is not available in the current Azure subscription
2. Sora-2 is still in private preview (as of January 2026)

TODO: Once Sora-2 becomes available in Azure AI Foundry, replace this implementation
      with direct Sora-2 API calls for native video generation.
"""
import os
import logging
import uuid
from datetime import datetime
from typing import List, Dict, Optional, Literal
from io import BytesIO
import cv2
import numpy as np
from PIL import Image
import requests
from azure.storage.blob import BlobServiceClient, generate_blob_sas, BlobSasPermissions
from datetime import timedelta

logger = logging.getLogger(__name__)


class VideoGenerationService:
    """
    Video generation via image sequence stitching.
    
    Supports both DALL-E-3 (photorealistic) and FLUX.2-pro (artistic) models.
    Will be replaced with Sora-2 when available.
    """
    
    def __init__(self, image_service):
        """
        Initialize video generation service.
        
        Args:
            image_service: ImageService instance for generating frames
        """
        self.image_service = image_service
        
        # Blob storage configuration
        self.storage_account = os.getenv("STORAGE_ACCOUNT_NAME")
        self.storage_connection_string = os.getenv("STORAGE_CONNECTION_STRING")
        self.container_name = "generated-videos"
        
        # Initialize Blob Storage client
        if self.storage_connection_string:
            self.blob_service_client = BlobServiceClient.from_connection_string(
                self.storage_connection_string
            )
            self._ensure_container_exists()
        else:
            self.blob_service_client = None
            logger.warning("Blob storage not configured for video service")
    
    def _ensure_container_exists(self):
        """Create the video container if it doesn't exist."""
        try:
            container_client = self.blob_service_client.get_container_client(self.container_name)
            if not container_client.exists():
                container_client.create_container()
                logger.info(f"Created blob container: {self.container_name}")
        except Exception as e:
            logger.error(f"Error ensuring video container exists: {e}")
    
    def generate_video_from_prompt(
        self,
        prompt: str,
        duration: float = 3.0,
        fps: int = 8,
        model: Literal["dall-e-3", "FLUX.2-pro"] = "dall-e-3",
        size: str = "1024x1024",
        style: str = "photorealistic"
    ) -> Dict:
        """
        Generate a video by creating an image sequence and stitching frames together.
        
        NOTE: This is a temporary solution using image sequences until Sora-2 is available.
        
        Args:
            prompt: Description of the video content
            duration: Video duration in seconds (default: 3.0)
            fps: Frames per second (default: 8, range: 4-24)
            model: Image generation model - "dall-e-3" or "FLUX.2-pro"
            size: Frame size (1024x1024, 1024x1792, or 1792x1024)
            style: Visual style hint for prompt enhancement
        
        Returns:
            dict with 'success', 'video_url', 'metadata', and 'message'
        """
        try:
            # Calculate number of frames
            num_frames = max(4, min(48, int(duration * fps)))
            logger.info(f"Generating video: {num_frames} frames at {fps} fps using {model}")
            
            # Generate frame prompts
            frame_prompts = self._generate_frame_prompts(prompt, num_frames, style)
            
            # Generate all frames
            frames = []
            for i, frame_prompt in enumerate(frame_prompts):
                logger.info(f"Generating frame {i+1}/{num_frames}")
                result = self.image_service.generate_image(
                    prompt=frame_prompt,
                    size=size,
                    model=model
                )
                
                if not result.get("success"):
                    return {
                        "success": False,
                        "message": f"Failed to generate frame {i+1}: {result.get('message')}",
                        "video_url": None
                    }
                
                # Download frame image
                frame_image = self._download_image(result["image_url"])
                if frame_image is None:
                    return {
                        "success": False,
                        "message": f"Failed to download frame {i+1}",
                        "video_url": None
                    }
                
                frames.append(frame_image)
            
            # Stitch frames into video
            video_path = self._stitch_frames_to_video(frames, fps)
            
            if video_path is None:
                return {
                    "success": False,
                    "message": "Failed to stitch frames into video",
                    "video_url": None
                }
            
            # Upload video to blob storage
            video_url = self._upload_video_to_blob(video_path, prompt, model)
            
            # Clean up temporary file
            if os.path.exists(video_path):
                os.remove(video_path)
            
            metadata = {
                "prompt": prompt,
                "duration": duration,
                "fps": fps,
                "num_frames": num_frames,
                "model": model,
                "size": size,
                "style": style,
                "implementation": "image_sequence",
                "note": "Generated using image sequences (DALL-E-3/FLUX.2-pro). Will be upgraded to Sora-2 when available."
            }
            
            return {
                "success": True,
                "message": f"Video generated successfully using {num_frames} frames from {model}",
                "video_url": video_url,
                "metadata": metadata,
                "implementation_note": "This video was created using image sequence stitching as Sora-2 is not yet available. Quality will improve significantly when Sora-2 becomes available in Azure."
            }
            
        except Exception as e:
            logger.error(f"Error generating video: {e}", exc_info=True)
            return {
                "success": False,
                "message": f"Error generating video: {str(e)}",
                "video_url": None
            }
    
    def _generate_frame_prompts(self, base_prompt: str, num_frames: int, style: str) -> List[str]:
        """
        Generate progressive prompts for each frame to ensure visual consistency.
        
        Args:
            base_prompt: Base video description
            num_frames: Number of frames to generate
            style: Visual style hint
        
        Returns:
            List of prompts for each frame
        """
        prompts = []
        
        # Add style and consistency instructions
        style_prefix = {
            "photorealistic": "Photorealistic, cinematic, high detail, 4K quality.",
            "artistic": "Artistic, stylized, creative interpretation.",
            "animated": "Animated style, smooth motion, vibrant colors.",
            "cinematic": "Cinematic lighting, professional camera work, film quality."
        }.get(style, "High quality, detailed.")
        
        for i in range(num_frames):
            # Calculate progression (0.0 to 1.0)
            progress = i / max(1, num_frames - 1)
            
            # Add temporal progression hints
            if progress < 0.33:
                temporal_hint = "Beginning of the scene."
            elif progress < 0.67:
                temporal_hint = "Middle of the scene, action progressing."
            else:
                temporal_hint = "End of the scene, conclusion."
            
            # Combine into frame-specific prompt
            frame_prompt = f"{style_prefix} {base_prompt} {temporal_hint} Frame {i+1} of {num_frames}. Maintain consistent subjects, lighting, and composition."
            prompts.append(frame_prompt)
        
        return prompts
    
    def _download_image(self, image_url: str) -> Optional[np.ndarray]:
        """
        Download image from URL and convert to OpenCV format.
        
        Args:
            image_url: URL of the image
        
        Returns:
            numpy array in BGR format for OpenCV, or None if failed
        """
        try:
            response = requests.get(image_url, timeout=30)
            response.raise_for_status()
            
            # Convert to PIL Image
            pil_image = Image.open(BytesIO(response.content))
            
            # Convert to RGB if needed
            if pil_image.mode != 'RGB':
                pil_image = pil_image.convert('RGB')
            
            # Convert PIL to OpenCV (BGR)
            opencv_image = cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)
            
            return opencv_image
            
        except Exception as e:
            logger.error(f"Error downloading image from {image_url}: {e}")
            return None
    
    def _stitch_frames_to_video(self, frames: List[np.ndarray], fps: int) -> Optional[str]:
        """
        Stitch frames together into a video file.
        
        Args:
            frames: List of frames as numpy arrays (BGR format)
            fps: Frames per second
        
        Returns:
            Path to the generated video file, or None if failed
        """
        try:
            # Create temporary file path
            temp_dir = os.path.join(os.getcwd(), "temp_videos")
            os.makedirs(temp_dir, exist_ok=True)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            video_path = os.path.join(temp_dir, f"video_{timestamp}_{uuid.uuid4().hex[:8]}.mp4")
            
            # Get frame dimensions from first frame
            height, width = frames[0].shape[:2]
            
            # Initialize video writer
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            video_writer = cv2.VideoWriter(video_path, fourcc, fps, (width, height))
            
            # Write all frames
            for frame in frames:
                # Resize if needed to match dimensions
                if frame.shape[:2] != (height, width):
                    frame = cv2.resize(frame, (width, height))
                
                video_writer.write(frame)
            
            video_writer.release()
            
            logger.info(f"Video created: {video_path}")
            return video_path
            
        except Exception as e:
            logger.error(f"Error stitching frames to video: {e}", exc_info=True)
            return None
    
    def _upload_video_to_blob(self, video_path: str, prompt: str, model: str) -> Optional[str]:
        """
        Upload video to blob storage and return public URL.
        
        Args:
            video_path: Path to the video file
            prompt: Original prompt for metadata
            model: Model used for generation
        
        Returns:
            Public URL of the blob, or None if failed
        """
        if not self.blob_service_client:
            logger.warning("Blob storage not configured, cannot upload video")
            return None
        
        try:
            # Generate unique blob name
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            blob_name = f"video_{timestamp}_{uuid.uuid4().hex[:8]}.mp4"
            
            # Upload to blob storage
            blob_client = self.blob_service_client.get_blob_client(
                container=self.container_name,
                blob=blob_name
            )
            
            with open(video_path, "rb") as video_file:
                blob_client.upload_blob(
                    video_file,
                    overwrite=True,
                    metadata={
                        "prompt": prompt[:500],
                        "model": model,
                        "generated_at": timestamp,
                        "implementation": "image_sequence",
                        "note": "Temporary solution until Sora-2 available"
                    }
                )
            
            # Generate SAS URL for public access (valid for 7 days)
            sas_token = generate_blob_sas(
                account_name=self.storage_account,
                container_name=self.container_name,
                blob_name=blob_name,
                permission=BlobSasPermissions(read=True),
                expiry=datetime.utcnow() + timedelta(days=7)
            )
            
            blob_url = f"{blob_client.url}?{sas_token}"
            logger.info(f"Video uploaded to blob storage: {blob_name}")
            
            return blob_url
            
        except Exception as e:
            logger.error(f"Error uploading video to blob: {e}", exc_info=True)
            return None


# Global instance
_video_service = None

def get_video_service(image_service):
    """Get or create the global VideoGenerationService instance."""
    global _video_service
    if _video_service is None:
        _video_service = VideoGenerationService(image_service)
    return _video_service
