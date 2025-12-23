"""
Agent module initialization
"""

from .agent_adapters import (
    ZavaAgentAdapter,
    OrchestratorAgentAdapter,
    CroppingAgentAdapter,
    BackgroundAgentAdapter,
    ThumbnailGeneratorAdapter,
    VideoAgentAdapter
)
from .coordinator import A2ACoordinatorAgent, EnhancedProductManagementAgent

__all__ = [
    "ZavaAgentAdapter",
    "OrchestratorAgentAdapter",
    "CroppingAgentAdapter",
    "BackgroundAgentAdapter",
    "ThumbnailGeneratorAdapter",
    "VideoAgentAdapter",
    "A2ACoordinatorAgent",
    "EnhancedProductManagementAgent"
]