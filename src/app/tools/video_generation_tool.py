"""
Video generation tool for AI agents.

IMPLEMENTATION NOTE:
This tool generates videos using DALL-E-3 and FLUX.2-pro image sequences.
This is a temporary workaround until Sora-2 becomes available in Azure.
When Sora-2 is released, this implementation will be replaced with native video generation.
"""
from typing import Literal, Optional
from services.image_service import get_image_service


def generate_video(
    prompt: str,
    duration: float = 3.0,
    fps: int = 8,
    model: Literal["dall-e-3", "FLUX.2-pro"] = "dall-e-3",
    style: str = "photorealistic"
) -> dict:
    """
    Generate a video from a text prompt using image sequence generation.
    
    NOTE: This is a temporary implementation using DALL-E-3 or FLUX.2-pro to generate
    image sequences that are stitched together. When Sora-2 becomes available in Azure,
    this will be replaced with native video generation for superior quality.
    
    Args:
        prompt: Text description of the video to generate
        duration: Video duration in seconds (default: 3.0, range: 1-6)
        fps: Frames per second (default: 8, recommended: 8-12 for image sequences)
        model: Image generation model to use:
            - "dall-e-3": Photorealistic, high-quality images (recommended for realistic videos)
            - "FLUX.2-pro": Artistic, stylized images (recommended for creative/artistic videos)
        style: Visual style hint - "photorealistic", "artistic", "animated", "cinematic"
    
    Returns:
        dict containing:
            - success: Whether the video was generated successfully
            - video_url: URL to the generated video (if successful)
            - metadata: Video generation metadata (fps, frames, model, etc.)
            - implementation_note: Explanation that this uses image sequences
            - message: Status or error message
    
    Example:
        >>> result = generate_video(
        ...     prompt="A sunset over ocean waves",
        ...     duration=3.0,
        ...     fps=8,
        ...     model="dall-e-3",
        ...     style="cinematic"
        ... )
        >>> print(result["video_url"])
    
    Implementation Details:
        - Videos are created by generating N frames (N = duration × fps)
        - Each frame is generated with progressive prompts for consistency
        - Frames are stitched together using OpenCV
        - Final video is uploaded to Azure Blob Storage
        - Current limitation: Image sequences don't provide smooth motion like Sora-2 will
    """
    image_service = get_image_service()
    
    # Validate parameters
    duration = max(1.0, min(6.0, duration))  # Limit to 1-6 seconds
    fps = max(4, min(12, fps))  # Limit to 4-12 fps for image sequences
    
    # Generate the video
    result = image_service.generate_video(
        prompt=prompt,
        duration=duration,
        fps=fps,
        model=model,
        style=style
    )
    
    return result


def check_video_generation_status() -> dict:
    """
    Check the status of video generation capabilities.
    
    Returns:
        dict with information about available models and implementation status
    """
    return {
        "available": True,
        "models": {
            "dall-e-3": {
                "available": True,
                "type": "image_sequence",
                "recommended_for": "photorealistic videos",
                "note": "Generates realistic image frames"
            },
            "FLUX.2-pro": {
                "available": True,
                "type": "image_sequence",
                "recommended_for": "artistic/stylized videos",
                "note": "Generates artistic image frames"
            },
            "sora-2": {
                "available": False,
                "type": "native_video",
                "status": "private_preview",
                "note": "Not yet available in this subscription. Will replace image sequence generation when available."
            }
        },
        "implementation": "image_sequence_stitching",
        "future_upgrade": "Will be upgraded to Sora-2 native video generation when available in Azure AI Foundry",
        "current_limitations": [
            "Motion is simulated through image sequences, not native video",
            "Limited to 1-6 seconds duration for optimal quality",
            "Recommended 8-12 fps (lower than native video's 24-30 fps)",
            "Transitions may not be as smooth as native video generation"
        ],
        "strengths": [
            "Still produces compelling short video clips",
            "Leverages high-quality DALL-E-3 and FLUX.2-pro image models",
            "Temporal consistency through progressive prompting",
            "Immediately available (no waiting for Sora-2)"
        ]
    }


# Tool metadata for agent frameworks
TOOL_METADATA = {
    "name": "generate_video",
    "description": "Generate a video from a text description using DALL-E-3 or FLUX.2-pro image sequences (temporary implementation until Sora-2 is available)",
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Text description of the video to generate"
            },
            "duration": {
                "type": "number",
                "description": "Video duration in seconds (1-6 seconds)",
                "default": 3.0
            },
            "fps": {
                "type": "integer",
                "description": "Frames per second (4-12 recommended for image sequences)",
                "default": 8
            },
            "model": {
                "type": "string",
                "enum": ["dall-e-3", "FLUX.2-pro"],
                "description": "Image model for frame generation. dall-e-3 for photorealistic, FLUX.2-pro for artistic",
                "default": "dall-e-3"
            },
            "style": {
                "type": "string",
                "enum": ["photorealistic", "artistic", "animated", "cinematic"],
                "description": "Visual style hint for the video",
                "default": "photorealistic"
            }
        },
        "required": ["prompt"]
    }
}
