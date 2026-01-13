"""
Image generation service using Azure OpenAI DALL-E and blob storage.
"""
import os
import logging
import uuid
from datetime import datetime, timedelta
from io import BytesIO
import requests
from azure.storage.blob import BlobServiceClient, generate_blob_sas, BlobSasPermissions
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AzureOpenAI

logger = logging.getLogger(__name__)


class ImageService:
    """Service for generating and storing images."""
    
    def __init__(self):
        """Initialize image service with Azure OpenAI and Blob Storage."""
        # Azure OpenAI Configuration - Prefer Secondary Endpoint for DALL-E 3
        self.endpoint = os.getenv("AZURE_OPENAI_SECONDARY_ENDPOINT") or os.getenv("AZURE_OPENAI_ENDPOINT") or os.getenv("gpt_endpoint")
        self.api_key = os.getenv("AZURE_OPENAI_SECONDARY_API_KEY") or os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("gpt_api_key")
        self.api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01")
        
        # Deployment names (match Terraform deployments)
        self.image_model = os.getenv("AZURE_OPENAI_IMAGE_DEPLOYMENT", "dall-e-3")
        self.flux_model = "flux"
        self.gpt_image_model = "gpt-image"
        self.sora_model = "sora"
        
        # Model-specific endpoints/keys (per-region routing)
        self.model_endpoints = {
            "dall-e-3": os.getenv("AZURE_OPENAI_ENDPOINT_DALLE3") or self.endpoint,
            self.flux_model: os.getenv("AZURE_OPENAI_ENDPOINT_FLUX") or os.getenv("AZURE_OPENAI_ENDPOINT_FLUX_KON") or self.endpoint,
            self.gpt_image_model: os.getenv("AZURE_OPENAI_ENDPOINT_GPT_IMAGE") or self.endpoint,
            self.sora_model: os.getenv("AZURE_OPENAI_ENDPOINT_SORA") or self.endpoint,
        }
        self.model_keys = {
            "dall-e-3": os.getenv("AZURE_OPENAI_API_KEY_DALLE3") or self.api_key,
            self.flux_model: os.getenv("AZURE_OPENAI_API_KEY_FLUX") or os.getenv("AZURE_OPENAI_API_KEY_FLUX_KON"),
            self.gpt_image_model: os.getenv("AZURE_OPENAI_API_KEY_GPT_IMAGE") or self.api_key,
            self.sora_model: os.getenv("AZURE_OPENAI_API_KEY_SORA") or self.api_key,
        }
        self._clients = {}
        
        # Blob storage configuration
        self.storage_account = os.getenv("STORAGE_ACCOUNT_NAME")
        self.storage_connection_string = os.getenv("STORAGE_CONNECTION_STRING")
        self.container_name = "generated-images"
        
        # Initialize default OpenAI client (fallback)
        if self.endpoint and self.api_key:
            self.client = AzureOpenAI(
                azure_endpoint=self.endpoint,
                api_key=self.api_key,
                api_version=self.api_version
            )
        else:
            self.client = None
            logger.warning("Azure OpenAI credentials not configured")
        
        # Initialize Blob Storage client
        if self.storage_connection_string:
            self.blob_service_client = BlobServiceClient.from_connection_string(
                self.storage_connection_string
            )
            self._ensure_container_exists()
        else:
            self.blob_service_client = None
            logger.warning("Blob storage not configured")
    
    def _ensure_container_exists(self):
        """Create the container if it doesn't exist."""
        try:
            container_client = self.blob_service_client.get_container_client(self.container_name)
            if not container_client.exists():
                container_client.create_container()
                logger.info(f"Created blob container: {self.container_name}")
        except Exception as e:
            logger.error(f"Error ensuring container exists: {e}")
    
    def generate_image(self, prompt: str, size: str = "1024x1024", model: str = None) -> dict:
        """
        Generate an image using DALL-E, Flux, or GPT-Image and upload to blob storage.
        
        Args:
            prompt: Text description of the image to generate
            size: Image size (1024x1024, 1024x1792, or 1792x1024)
            model: Specific model to use (default: configured default, usually dall-e-3)
        
        Returns:
            dict with 'success', 'image_url', 'blob_url', 'prompt', and 'message'
        """
        # Pick the best client for the target model
        target_model = model or self.image_model
        client = self._get_client_for_model(target_model)

        if not client:
            return {
                "success": False,
                "message": "Image generation not configured. Please set up Azure OpenAI.",
                "image_url": None,
                "blob_url": None
            }
        
        try:
            logger.info(f"Generating image with model {target_model} and prompt: {prompt[:100]}...")
            
            # Generate image
            response = client.images.generate(
                model=target_model,
                prompt=prompt,
                size=size,
                quality="standard",
                n=1
            )
            
            # Get the generated image URL
            image_url = response.data[0].url
            logger.info(f"Image generated successfully: {image_url}")
            
            # Upload to blob storage if configured
            blob_url = None
            if self.blob_service_client:
                blob_url = self._upload_image_to_blob(image_url, prompt)
            
            return {
                "success": True,
                "message": "Image generated successfully",
                "image_url": image_url,  # Temporary Azure OpenAI URL
                "blob_url": blob_url,    # Permanent blob storage URL
                "prompt": prompt
            }
            
        except Exception as e:
            logger.error(f"Error generating image: {e}", exc_info=True)
            return {
                "success": False,
                "message": f"Error generating image: {str(e)}",
                "image_url": None,
                "blob_url": None
            }

    def generate_video(self, prompt: str, duration: float = 3.0, fps: int = 8, 
                      model: str = "dall-e-3", style: str = "photorealistic") -> dict:
        """
        Generate a video using image sequence stitching (DALL-E-3 or FLUX.2-pro).
        
        Args:
            prompt: Text description of the video
            duration: Video duration in seconds (default: 3.0)
            fps: Frames per second (default: 8, recommended: 8-12)
            model: Image model to use - "dall-e-3" (photorealistic) or "FLUX.2-pro" (artistic)
            style: Visual style hint - "photorealistic", "artistic", "animated", "cinematic"
            
        Returns:
            dict with 'success', 'video_url', 'metadata', 'implementation_note', and 'message'
        """
        try:
            # Import video service (lazy import to avoid circular dependencies)
            from services.video_service import get_video_service
            
            video_service = get_video_service(self)
            
            result = video_service.generate_video_from_prompt(
                prompt=prompt,
                duration=duration,
                fps=fps,
                model=model,
                style=style
            )
            
            return result
            
        except Exception as e:
            logger.error(f"Error generating video: {e}", exc_info=True)
            return {
                "success": False, 
                "message": f"Error generating video: {str(e)}",
                "video_url": None
            }

    def generate_video_with_sora(self, prompt: str, duration: float = 3.0) -> dict:
        """
        Generate a video using Sora native video generation.
        
        Args:
            prompt: Text description of the video
            duration: Video duration in seconds (default: 3.0, max: 6.0)
            
        Returns:
            dict with 'success', 'video_url', 'metadata', and 'message'
        """
        try:
            client = self._get_client_for_model(self.sora_model)
            
            if not client:
                logger.error("Sora client not configured")
                logger.error(f"Sora endpoint: {self.model_endpoints.get(self.sora_model)}")
                logger.error(f"Sora key configured: {bool(self.model_keys.get(self.sora_model))}")
                return {
                    "success": False,
                    "message": "Sora client not configured. Check AZURE_OPENAI_ENDPOINT_SORA and ensure Sora is deployed in your Azure AI Foundry.",
                    "video_url": None
                }
            
            # Generate video with Sora
            logger.info(f"Generating video with Sora: {prompt[:100]}...")
            logger.info(f"Using endpoint: {self.model_endpoints.get(self.sora_model)}")
            
            response = client.videos.generate(
                model="sora",  # Use deployment name directly
                prompt=prompt,
                duration=min(duration, 6.0)  # Limit to 6 seconds
            )
            
            if not response or not hasattr(response, 'data'):
                return {
                    "success": False,
                    "message": "Sora returned empty response",
                    "video_url": None
                }
            
            # Get video URL from response
            video_url = response.data[0].url if response.data else None
            
            if not video_url:
                return {
                    "success": False,
                    "message": "Sora did not return a video URL",
                    "video_url": None
                }
            
            # Download and upload to blob storage for consistent access
            blob_url = self._upload_video_from_url(video_url, prompt)
            
            return {
                "success": True,
                "video_url": blob_url or video_url,
                "original_url": video_url,
                "metadata": {
                    "model": "sora",
                    "duration": duration,
                    "method": "native_video_generation",
                    "prompt": prompt
                },
                "message": "Video generated successfully with Sora"
            }
            
        except Exception as e:
            logger.error(f"Error generating video with Sora: {e}", exc_info=True)
            return {
                "success": False,
                "message": f"Error generating video with Sora: {str(e)}",
                "video_url": None
            }

    def _upload_video_from_url(self, video_url: str, prompt: str) -> str:
        """Download video from URL and upload to blob storage."""
        try:
            if not self.blob_service_client:
                return None
            
            # Download video
            response = requests.get(video_url, timeout=30)
            response.raise_for_status()
            
            # Generate blob name
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            safe_prompt = "".join(c for c in prompt[:30] if c.isalnum() or c in (' ', '-', '_')).strip().replace(' ', '_')
            blob_name = f"sora_{safe_prompt}_{timestamp}.mp4"
            
            # Upload to blob storage
            blob_client = self.blob_service_client.get_blob_client(
                container=self.container_name,
                blob=blob_name
            )
            
            blob_client.upload_blob(response.content, overwrite=True)
            
            # Generate SAS URL
            sas_token = generate_blob_sas(
                account_name=self.storage_account,
                container_name=self.container_name,
                blob_name=blob_name,
                permission=BlobSasPermissions(read=True),
                expiry=datetime.utcnow() + timedelta(days=7)
            )
            
            return f"{blob_client.url}?{sas_token}"
            
        except Exception as e:
            logger.error(f"Error uploading video to blob storage: {e}")
            return None

    def _get_client_for_model(self, model: str):
        endpoint = self.model_endpoints.get(model) or self.endpoint
        key = self.model_keys.get(model) or self.api_key
        
        if not endpoint:
            return None
            
        # If key is missing or explicitly set to MANAGED_IDENTITY, use Managed Identity
        use_mi = not key or key == "MANAGED_IDENTITY"
        
        cache_key = f"{endpoint}|{key if key else 'MI'}"
        if cache_key not in self._clients:
            try:
                if use_mi:
                    token_provider = get_bearer_token_provider(
                        DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
                    )
                    self._clients[cache_key] = AzureOpenAI(
                        azure_endpoint=endpoint,
                        azure_ad_token_provider=token_provider,
                        api_version=self.api_version
                    )
                else:
                    self._clients[cache_key] = AzureOpenAI(
                        azure_endpoint=endpoint,
                        api_key=key,
                        api_version=self.api_version
                    )
            except Exception as e:
                logger.error(f"Failed to init client for {model}: {e}")
                return None

        return self._clients[cache_key]
    
    def _upload_image_to_blob(self, image_url: str, prompt: str) -> str:
        """
        Download image from URL and upload to blob storage.
        
        Args:
            image_url: URL of the generated image
            prompt: Original prompt for metadata
        
        Returns:
            Public URL of the blob
        """
        try:
            # Download image
            response = requests.get(image_url, timeout=30)
            response.raise_for_status()
            
            # Generate unique blob name
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            blob_name = f"image_{timestamp}_{uuid.uuid4().hex[:8]}.png"
            
            # Upload to blob storage
            blob_client = self.blob_service_client.get_blob_client(
                container=self.container_name,
                blob=blob_name
            )
            
            blob_client.upload_blob(
                response.content,
                overwrite=True,
                metadata={
                    "prompt": prompt[:500],  # Limit metadata size
                    "generated_at": timestamp
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
            logger.info(f"Image uploaded to blob storage: {blob_name}")
            
            return blob_url
            
        except Exception as e:
            logger.error(f"Error uploading image to blob: {e}", exc_info=True)
            return None
    
    def is_configured(self) -> bool:
        """Check if image generation is properly configured."""
        return self.client is not None and self.blob_service_client is not None


# Global instance
_image_service = None

def get_image_service() -> ImageService:
    """Get or create the global ImageService instance."""
    global _image_service
    if _image_service is None:
        _image_service = ImageService()
    return _image_service
