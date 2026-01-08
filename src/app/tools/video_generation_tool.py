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
    style: str = "photorealistic",
    method: Literal["both", "image-sequence", "sora"] = "both"
) -> dict:
    """
    Generate a video from a text prompt using image sequences and/or Sora.
    
    Args:
        prompt: Text description of the video to generate
        duration: Video duration in seconds (default: 3.0, range: 1-6)
        fps: Frames per second (default: 8, recommended: 8-12 for image sequences)
        model: Image generation model to use for image-sequence method:
            - "dall-e-3": Photorealistic, high-quality images (recommended for realistic videos)
            - "FLUX.2-pro": Artistic, stylized images (recommended for creative/artistic videos)
        style: Visual style hint - "photorealistic", "artistic", "animated", "cinematic"
        method: Video generation method:
            - "both": Generate with both image-sequence and Sora for comparison (default)
            - "image-sequence": Use DALL-E-3/FLUX.2-pro frame stitching only
            - "sora": Use Sora native video generation only
    
    Returns:
        dict containing:
            - success: Whether the video(s) were generated successfully
            - image_sequence_video: Result from image-sequence method (if method="both" or "image-sequence")
            - sora_video: Result from Sora method (if method="both" or "sora")
            - comparison_note: Explanation of differences between methods
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
        - Image-sequence: Creates N frames (N = duration × fps) and stitches them
        - Sora: Native video generation with smooth, realistic motion
        - "both" method generates videos with both approaches for comparison
    """
    image_service = get_image_service()
    
    # Validate parameters
    duration = max(1.0, min(6.0, duration))  # Limit to 1-6 seconds
    fps = max(4, min(12, fps))  # Limit to 4-12 fps for image sequences
    
    result = {"success": False, "message": ""}
    
    # Generate with image-sequence method
    if method in ["both", "image-sequence"]:
        image_seq_result = image_service.generate_video(
            prompt=prompt,
            duration=duration,
            fps=fps,
            model=model,
            style=style
        )
        result["image_sequence_video"] = image_seq_result
        result["success"] = image_seq_result.get("success", False)
    
    # Generate with Sora method
    if method in ["both", "sora"]:
        sora_result = image_service.generate_video_with_sora(
            prompt=prompt,
            duration=duration
        )
        result["sora_video"] = sora_result
        result["success"] = result.get("success", False) or sora_result.get("success", False)
    
    # Add comparison note when both methods are used
    if method == "both":
        result["comparison_note"] = {
            "image_sequence": "Created by stitching DALL-E-3/FLUX.2-pro image frames - may show slight jumps between frames",
            "sora": "Native video generation with smooth, realistic motion - higher quality and more natural movement",
            "recommendation": "Compare both to see the quality difference. Sora typically produces superior results for video content."
        }
        result["message"] = "Generated videos with both methods for comparison"
    elif method == "image-sequence":
        result["message"] = result.get("image_sequence_video", {}).get("message", "")
    else:
        result["message"] = result.get("sora_video", {}).get("message", "")
    
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
            "sora": {
                "available": True,
                "type": "native_video",
                "status": "deployed",
                "note": "Native video generation with smooth, realistic motion. Superior quality compared to image sequences."
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
    "description": "Generate a video from a text description using Sora and/or image sequences. Can create both for comparison.",
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
            },
            "method": {
                "type": "string",
                "enum": ["both", "image-sequence", "sora"],
                "description": "Generation method: 'both' creates videos with both approaches for comparison, 'image-sequence' uses frame stitching, 'sora' uses native video generation",
                "default": "both"
            }
        },
        "required": ["prompt"]
    }
}
