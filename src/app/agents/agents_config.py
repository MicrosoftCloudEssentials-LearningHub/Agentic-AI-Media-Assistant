"""Domain-specific agent instruction consolidation.

Defines the instructions for the Zava Media AI Assistant agents.
"""
import os

PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "prompts")

def _read(name: str) -> str:
    path = os.path.join(PROMPTS_DIR, name)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    return ""

# Define instructions directly here for simplicity, or read from files if preferred
ORCHESTRATOR_PROMPT = _read("OrchestratorPrompt.txt") or """You are the Zava Media Orchestrator. Your job is to analyze user requests related to image and video processing and route them to the appropriate specialist agent.
- If the user wants to crop an image or object, delegate to the "cropping_agent".
- If the user wants to change the background, delegate to the "background_agent".
- If the user wants to create a new thumbnail or image, delegate to the "thumbnail_generator".
- If the user wants to create a video, delegate to the "video_agent".
- For general questions, answer them yourself.
"""

CROPPING_PROMPT = _read("CroppingAgentPrompt.txt") or """You are the Cropping Specialist. Your task is to identify objects in images and provide cropping coordinates or cropped images.
You use advanced vision models to detect subjects.
"""

BACKGROUND_PROMPT = _read("BackgroundAgentPrompt.txt") or """You are the Background Specialist. Your task is to remove or replace backgrounds in images.
You can create new backgrounds based on text descriptions.
"""

THUMBNAIL_PROMPT = _read("ThumbnailGeneratorPrompt.txt") or """You are the Thumbnail Generator. Your task is to create eye-catching video thumbnails.
You combine images, text, and effects to maximize click-through rates.
"""

VIDEO_PROMPT = _read("VideoAgentPrompt.txt") or """You are the Video Specialist. Your task is to create videos from text descriptions or images.
You use advanced video generation models like Sora to bring ideas to life.
"""

AGENT_INSTRUCTIONS = {
    "orchestrator": ORCHESTRATOR_PROMPT,
    "cropping_agent": CROPPING_PROMPT,
    "background_agent": BACKGROUND_PROMPT,
    "thumbnail_generator": THUMBNAIL_PROMPT,
    "video_agent": VIDEO_PROMPT
}

