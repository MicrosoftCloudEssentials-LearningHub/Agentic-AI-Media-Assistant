"""
Direct Model API Service - FLUX and Sora
Uses direct Azure inference API calls (not through agents)
"""
import os
import logging
import json
import requests
from typing import Dict, Any, Optional
from azure.identity import DefaultAzureCredential
from azure.core.credentials import AccessToken

logger = logging.getLogger(__name__)


class DirectModelService:
    """
    Direct API access to FLUX and Sora models.
    These models are deployed in Azure AI Foundry but accessed via inference API,
    not through agents (which require chat_completion capability).
    """
    
    def __init__(self):
        """Initialize direct model API access."""
        self.credential = DefaultAzureCredential()
        
        # Azure Cognitive Services endpoints (dynamically set by terraform)
        # Format: https://<foundry-name>.cognitiveservices.azure.com
        self.sweden_inference = os.getenv("AZURE_AI_INFERENCE_ENDPOINT_SWEDEN")
        self.westus3_inference = os.getenv("AZURE_AI_INFERENCE_ENDPOINT_WESTUS3")
        
        if not self.sweden_inference or not self.westus3_inference:
            logger.warning(
                "Inference endpoints not set in environment. "
                "Set AZURE_AI_INFERENCE_ENDPOINT_SWEDEN and AZURE_AI_INFERENCE_ENDPOINT_WESTUS3. "
                "These are automatically configured by terraform."
            )
        
        # Deployment names
        self.flux1_deployment = "FLUX.1-Kontext-pro"  # Sweden Central
        self.flux2_deployment = "FLUX.2-pro"          # West US 3
        self.sora_deployment = "sora"                 # Sweden Central
        
        # API version for Azure OpenAI
        self.api_version = "2024-10-21"
    
    def _get_access_token(self) -> str:
        """Get Azure AD access token for API authentication."""
        token = self.credential.get_token("https://cognitiveservices.azure.com/.default")
        return token.token
    
    def generate_image_flux1(self, prompt: str, size: str = "1024x1024", **kwargs) -> Dict[str, Any]:
        """
        Generate images using FLUX.1-Kontext-pro (for documents/contextual images).
        
        Args:
            prompt: Image description
            size: Image size (default: "1024x1024")
            **kwargs: Additional parameters (quality, style, etc.)
        
        Returns:
            dict with image data (base64 or URL) and metadata
        """
        logger.info(f"Generating image with FLUX.1-Kontext-pro: {prompt[:100]}")
        
        try:
            # Azure OpenAI deployments endpoint for FLUX.1
            # Format: https://<foundry>.cognitiveservices.azure.com/openai/deployments/<deployment>/images/generations
            url = f"{self.sweden_inference}/openai/deployments/{self.flux1_deployment}/images/generations"
            
            headers = {
                "Authorization": f"Bearer {self._get_access_token()}",
                "Content-Type": "application/json",
                "api-key": self._get_access_token()  # Some endpoints require this
            }
            
            # Add api-version as query parameter
            url_with_version = f"{url}?api-version={self.api_version}"
            
            payload = {
                "prompt": prompt,
                "size": size,
                "n": kwargs.get("n", 1),
                "quality": kwargs.get("quality", "standard"),
                "response_format": kwargs.get("response_format", "url")  # "url" or "b64_json"
            }
            
            response = requests.post(url_with_version, headers=headers, json=payload, timeout=120)
            
            if response.status_code == 200:
                result = response.json()
                logger.info(f"SUCCESS - FLUX.1-Kontext-pro image generated")
                return {
                    "model": self.flux1_deployment,
                    "data": result.get("data", []),
                    "status": "success"
                }
            else:
                logger.error(f"FLUX.1 API error: {response.status_code} - {response.text}")
                return {
                    "model": self.flux1_deployment,
                    "error": response.text,
                    "status_code": response.status_code,
                    "status": "error"
                }
                
        except Exception as e:
            logger.error(f"Exception in FLUX.1 generation: {str(e)}")
            return {
                "model": self.flux1_deployment,
                "error": str(e),
                "status": "exception"
            }
    
    def generate_image_flux2(self, prompt: str, size: str = "1024x1024", **kwargs) -> Dict[str, Any]:
        """
        Generate images using FLUX.2-pro (for high-quality visual content).
        
        Args:
            prompt: Image description
            size: Image size (default: "1024x1024")
            **kwargs: Additional parameters (quality, style, etc.)
        
        Returns:
            dict with image data (base64 or URL) and metadata
        """
        logger.info(f"Generating image with FLUX.2-pro: {prompt[:100]}")
        
        try:
            # Azure OpenAI deployments endpoint for FLUX.2 (West US 3)
            # Format: https://<foundry>.cognitiveservices.azure.com/openai/deployments/<deployment>/images/generations
            url = f"{self.westus3_inference}/openai/deployments/{self.flux2_deployment}/images/generations"
            
            headers = {
                "Authorization": f"Bearer {self._get_access_token()}",
                "Content-Type": "application/json",
                "api-key": self._get_access_token()
            }
            
            # Add api-version as query parameter
            url_with_version = f"{url}?api-version={self.api_version}"
            
            payload = {
                "prompt": prompt,
                "size": size,
                "n": kwargs.get("n", 1),
                "quality": kwargs.get("quality", "hd"),  # FLUX.2-pro supports higher quality
                "response_format": kwargs.get("response_format", "url")
            }
            
            response = requests.post(url_with_version, headers=headers, json=payload, timeout=120)
            
            if response.status_code == 200:
                result = response.json()
                logger.info(f"✓ FLUX.2-pro image generated successfully")
                return {
                    "model": self.flux2_deployment,
                    "data": result.get("data", []),
                    "status": "success"
                }
            else:
                logger.error(f"FLUX.2 API error: {response.status_code} - {response.text}")
                return {
                    "model": self.flux2_deployment,
                    "error": response.text,
                    "status_code": response.status_code,
                    "status": "error"
                }
                
        except Exception as e:
            logger.error(f"Exception in FLUX.2 generation: {str(e)}")
            return {
                "model": self.flux2_deployment,
                "error": str(e),
                "status": "exception"
            }
    
    def generate_video_sora(self, prompt: str, duration: int = 10, resolution: str = "1920x1080", **kwargs) -> Dict[str, Any]:
        """
        Generate videos using Sora.
        
        Args:
            prompt: Video description
            duration: Video length in seconds (default: 10)
            resolution: Video resolution (default: "1920x1080")
            **kwargs: Additional parameters (fps, style, etc.)
        
        Returns:
            dict with video data and metadata
        """
        logger.info(f"Generating video with Sora: {prompt[:100]}, duration={duration}s")
        
        try:
            # Azure OpenAI deployments endpoint for Sora
            # Format: https://<foundry>.cognitiveservices.azure.com/openai/deployments/<deployment>/videos/generations
            url = f"{self.sweden_inference}/openai/deployments/{self.sora_deployment}/videos/generations"
            
            headers = {
                "Authorization": f"Bearer {self._get_access_token()}",
                "Content-Type": "application/json",
                "api-key": self._get_access_token()
            }
            
            # Add api-version as query parameter
            url_with_version = f"{url}?api-version={self.api_version}"
            
            payload = {
                "prompt": prompt,
                "duration": duration,
                "resolution": resolution,
                "fps": kwargs.get("fps", 30),
                "response_format": kwargs.get("response_format", "url")  # "url" or "b64_json"
            }
            
            # Video generation may take longer
            response = requests.post(url_with_version, headers=headers, json=payload, timeout=300)
            
            if response.status_code == 200:
                result = response.json()
                logger.info(f"✓ Sora video generated successfully")
                return {
                    "model": self.sora_deployment,
                    "data": result.get("data", []),
                    "duration": duration,
                    "status": "success"
                }
            else:
                logger.error(f"Sora API error: {response.status_code} - {response.text}")
                return {
                    "model": self.sora_deployment,
                    "error": response.text,
                    "status_code": response.status_code,
                    "status": "error"
                }
                
        except Exception as e:
            logger.error(f"Exception in Sora generation: {str(e)}")
            return {
                "model": self.sora_deployment,
                "error": str(e),
                "status": "exception"
            }
    
    def get_model_info(self) -> Dict[str, Any]:
        """Get information about available models and their endpoints."""
        return {
            "models": {
                "FLUX.1-Kontext-pro": {
                    "deployment": self.flux1_deployment,
                    "endpoint": self.sweden_inference,
                    "region": "Sweden Central",
                    "purpose": "Document and contextual image generation"
                },
                "FLUX.2-pro": {
                    "deployment": self.flux2_deployment,
                    "endpoint": self.westus3_inference,
                    "region": "West US 3",
                    "purpose": "High-quality visual content generation"
                },
                "Sora": {
                    "deployment": self.sora_deployment,
                    "endpoint": self.sweden_inference,
                    "region": "Sweden Central",
                    "purpose": "Video generation"
                }
            },
            "api_version": self.api_version
        }

    
    def generate_background(self, prompt: str, remove_background: bool = False, **kwargs):
        """
        Generate or replace backgrounds using FLUX.2-pro.
        
        Args:
            prompt: Background description
            remove_background: Whether to generate transparent background
            **kwargs: Additional parameters
        
        Returns:
            Generated background image
        """
        if remove_background:
            prompt = f"{prompt}, transparent background, isolated subject"
        else:
            prompt = f"Background scene: {prompt}"
        
        return self.generate_visual_content(prompt, **kwargs)
    
    def generate_thumbnail(self, prompt: str, aspect_ratio="16:9", **kwargs):
        """
        Generate video thumbnails using FLUX.2-pro.
        
        Args:
            prompt: Thumbnail description
            aspect_ratio: Thumbnail aspect ratio
            **kwargs: Additional parameters
        
        Returns:
            Generated thumbnail image
        """
        # Map aspect ratios to sizes
        size_map = {
            "16:9": "1792x1024",
            "9:16": "1024x1792",
            "1:1": "1024x1024"
        }
        
        kwargs['size'] = size_map.get(aspect_ratio, "1792x1024")
        kwargs['quality'] = 'hd'  # Thumbnails need high quality
        
        enhanced_prompt = f"Eye-catching YouTube thumbnail: {prompt}, professional, vibrant colors, high contrast"
        
        return self.generate_visual_content(enhanced_prompt, **kwargs)
