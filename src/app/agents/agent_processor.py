"""
Agent processor for handling interactions with Microsoft Foundry agents.
Includes MCP (Model Context Protocol) integration for tool calling.
"""
import os
import json
import time
from typing import List, Dict, Any
try:
    from azure.ai.agents import AgentsClient  # type: ignore
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
    """Handles communication with Azure OpenAI models directly (no Agents API)"""
    
    def __init__(self, agent_id: str = None, project_endpoint: str = None):
        """
        Initialize agent processor using Azure OpenAI directly.
        
        Args:
            agent_id: Ignored - kept for compatibility
            project_endpoint: Ignored - uses GPT endpoint from environment
        """
        from azure.ai.inference import ChatCompletionsClient
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider
        from azure.core.credentials import AzureKeyCredential
        
        # Get GPT endpoint for orchestration
        self.endpoint = os.environ.get("gpt_endpoint") or os.environ.get("AZURE_OPENAI_ENDPOINT")
        api_key = os.environ.get("gpt_api_key") or os.environ.get("AZURE_OPENAI_API_KEY")
        self.model = os.environ.get("gpt_deployment", "gpt-4o")
        
        if not self.endpoint:
            raise ValueError("gpt_endpoint or AZURE_OPENAI_ENDPOINT must be set")
        
        # Use Managed Identity if api_key is "MANAGED_IDENTITY"
        if api_key == "MANAGED_IDENTITY":
            print("[INFO] Using Managed Identity for Azure OpenAI authentication")
            credential = DefaultAzureCredential()
            token_provider = get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")
            
            # Create client with token provider
            from azure.ai.inference.aio import ChatCompletionsClient as AsyncChatClient
            from azure.core.credentials import AccessToken
            
            class TokenCredential:
                def __init__(self, token_provider):
                    self.token_provider = token_provider
                def get_token(self, *scopes, **kwargs):
                    token = self.token_provider()
                    return AccessToken(token, 0)
            
            self.client = ChatCompletionsClient(endpoint=self.endpoint, credential=TokenCredential(token_provider))
        else:
            print("[INFO] Using API Key for Azure OpenAI authentication")
            self.client = ChatCompletionsClient(endpoint=self.endpoint, credential=AzureKeyCredential(api_key))
        
        print(f"[INFO] Initialized Azure OpenAI client: {self.endpoint} / {self.model}")
        
    def run_conversation_with_text_stream(
        self,
        user_message: str,
        conversation_history: List[Dict[str, str]] = None,
        additional_context: Dict[str, Any] = None
    ):
        """
        Run a conversation using Azure OpenAI directly.
        
        Args:
            user_message: The user's message
            conversation_history: Optional conversation history
            additional_context: Additional context to provide to the agent
        
        Yields:
            Chunks of the agent's response
        """
        try:
            # Build messages for Azure OpenAI
            messages = [
                {"role": "system", "content": "You are Zava Media Orchestrator. Analyze user requests for image and video processing. For cropping, background changes, thumbnails, or videos, explain what you would do. Currently in direct Azure OpenAI mode."}
            ]
            
            if conversation_history:
                messages.extend(conversation_history[-5:])
            
            messages.append({"role": "user", "content": user_message})
            
            # Call Azure OpenAI
            response = self.client.complete(
                messages=messages,
                model=self.model,
                temperature=0.7,
                max_tokens=800
            )
            
            # Yield the full response
            yield response.choices[0].message.content
            
        except Exception as e:
            yield f"Error communicating with agent: {str(e)}"
