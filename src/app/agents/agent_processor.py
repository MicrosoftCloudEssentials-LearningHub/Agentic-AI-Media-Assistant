"""
Agent processor for handling interactions with Microsoft Foundry agents.
Includes MCP (Model Context Protocol) integration for tool calling.
"""
import os
import json
from typing import List, Dict, Any
try:
    from azure.ai.projects import AIProjectClient  # type: ignore
    from azure.identity import DefaultAzureCredential  # type: ignore
    _REMOTE_AVAILABLE = True
except Exception:
    _REMOTE_AVAILABLE = False


def create_function_tool_for_agent(agent_name: str) -> List[Dict[str, Any]]:
    """
    Create function tools for a specific agent using MCP.
    
    Args:
        agent_name: Name of the agent (e.g., 'interior_designer', 'inventory_agent')
    
    Returns:
        List of function tool definitions
    """
    # Placeholder for MCP tool integration
    # In production, this would connect to MCP servers to get available tools
    tools = []
    
    # Define tools based on agent type
    if agent_name == "cropping_agent":
        tools.append({
            "type": "function",
            "function": {
                "name": "crop_image",
                "description": "Crop an image to specific dimensions or object",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "image_url": {"type": "string", "description": "URL of the image to crop"},
                        "coordinates": {"type": "string", "description": "Cropping coordinates (x,y,w,h) or object name"}
                    },
                    "required": ["image_url"]
                }
            }
        })
    
    elif agent_name == "background_agent":
        tools.append({
            "type": "function",
            "function": {
                "name": "remove_background",
                "description": "Remove background from an image",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "image_url": {"type": "string", "description": "URL of the image"}
                    },
                    "required": ["image_url"]
                }
            }
        })
        tools.append({
            "type": "function",
            "function": {
                "name": "replace_background",
                "description": "Replace background of an image",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "image_url": {"type": "string", "description": "URL of the image"},
                        "background_prompt": {"type": "string", "description": "Description of the new background"}
                    },
                    "required": ["image_url", "background_prompt"]
                }
            }
        })
    
    elif agent_name == "thumbnail_generator":
        tools.append({
            "type": "function",
            "function": {
                "name": "generate_thumbnail",
                "description": "Generate a video thumbnail",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Title text for the thumbnail"},
                        "style": {"type": "string", "description": "Style of the thumbnail"}
                    },
                    "required": ["title"]
                }
            }
        })
    
    elif agent_name == "video_agent":
        tools.append({
            "type": "function",
            "function": {
                "name": "generate_video",
                "description": "Generate a video from text or image",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string", "description": "Description of the video"},
                        "image_url": {"type": "string", "description": "Optional starting image"}
                    },
                    "required": ["prompt"]
                }
            }
        })
    
    return tools


class AgentProcessor:
    """Handles communication with Microsoft Foundry agents"""
    
    def __init__(self, agent_id: str = None, project_endpoint: str = None):
        """
        Initialize agent processor.
        
        Args:
            agent_id: The agent ID from Microsoft Foundry (if None, loads from AGENT_ORCHESTRATOR_ID env)
            project_endpoint: Optional project endpoint (reads from env if not provided)
        """
        # Get agent ID from parameter or environment
        self.agent_id = agent_id or os.environ.get("AGENT_ORCHESTRATOR_ID")
        if not self.agent_id:
            raise ValueError("agent_id must be provided or AGENT_ORCHESTRATOR_ID must be set")
        
        # Get project endpoint from parameter or environment
        self.project_endpoint = project_endpoint or os.environ.get("AZURE_AI_PROJECT_ENDPOINT") or os.environ.get("AZURE_AI_AGENT_ENDPOINT")
        
        if not self.project_endpoint or not _REMOTE_AVAILABLE:
            raise ValueError(f"Remote agent support unavailable (endpoint: {self.project_endpoint}, SDK available: {_REMOTE_AVAILABLE})")
        
        # Initialize AI Project Client - use new SDK constructor that requires subscription/resource group/project
        subscription_id = os.environ.get("AZURE_SUBSCRIPTION_ID")
        resource_group = os.environ.get("AZURE_RESOURCE_GROUP")
        project_name = os.environ.get("AZURE_AI_PROJECT_NAME")
        
        if not all([subscription_id, resource_group, project_name]):
            raise ValueError(f"AIProjectClient requires subscription_id, resource_group, and project_name. "
                           f"Missing: {', '.join([k for k, v in {'AZURE_SUBSCRIPTION_ID': subscription_id, 'AZURE_RESOURCE_GROUP': resource_group, 'AZURE_AI_PROJECT_NAME': project_name}.items() if not v])}")
        
        self.client = AIProjectClient(
            endpoint=self.project_endpoint,
            subscription_id=subscription_id,
            resource_group_name=resource_group,
            project_name=project_name,
            credential=DefaultAzureCredential()
        )
    
    def run_conversation_with_text_stream(
        self,
        user_message: str,
        conversation_history: List[Dict[str, str]] = None,
        additional_context: Dict[str, Any] = None
    ):
        """
        Run a conversation with the agent and stream the response.
        
        Args:
            user_message: The user's message
            conversation_history: Optional conversation history
            additional_context: Additional context to provide to the agent
        
        Yields:
            Chunks of the agent's response
        """
        try:
            # Create a thread for this conversation
            thread = self.client.agents.create_thread()
            
            # Build the message content
            message_content = user_message
            if additional_context:
                message_content = f"Context: {json.dumps(additional_context)}\n\nUser: {user_message}"
            
            # Add message to thread
            self.client.agents.create_message(
                thread_id=thread.id,
                role="user",
                content=message_content
            )
            
            # Run the agent
            run = self.client.agents.create_and_process_run(
                thread_id=thread.id,
                assistant_id=self.agent_id
            )
            
            # Get messages
            messages = self.client.agents.list_messages(thread_id=thread.id)
            
            # Find the assistant's response
            for message in messages:
                if message.role == "assistant":
                    for content in message.content:
                        if hasattr(content, 'text'):
                            yield content.text.value
            
            # Clean up
            self.client.agents.delete_thread(thread.id)
            
        except Exception as e:
            yield f"Error communicating with agent: {str(e)}"
