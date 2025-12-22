import json
import os
from typing import List, Dict, Any, Generator
from azure.ai.inference import ChatCompletionsClient
from azure.core.credentials import AzureKeyCredential

try:
    from app.agents.agents_config import AGENT_INSTRUCTIONS
except Exception:
    # Fallback if agents_config has issues
    AGENT_INSTRUCTIONS = {
        "orchestrator": "You are the Zava Media Orchestrator. Route requests to cropping_agent, background_agent, thumbnail_generator, or video_agent.",
        "cropping_agent": "You are the Cropping Specialist. Identify objects and provide cropping coordinates.",
        "background_agent": "You are the Background Specialist. Remove or replace backgrounds.",
        "thumbnail_generator": "You are the Thumbnail Generator. Create eye-catching video thumbnails.",
        "video_agent": "You are the Video Specialist. Create videos from text or images."
    }

try:
    from services.image_service import get_image_service
    IMAGE_SERVICE_AVAILABLE = True
except Exception:
    IMAGE_SERVICE_AVAILABLE = False

class LocalAgentProcessor:
    """Local agent implementation using GPT with domain-specific prompts.

    Each agent uses the same GPT model but with different system prompts,
    creating distinct personas for different media domains.
    """
    def __init__(self, agent_id: str, domain: str):
        self.agent_id = agent_id
        self.domain = domain
        
        # Initialize GPT client (shared across all agents)
        endpoint = os.getenv("gpt_endpoint", "")
        api_key = os.getenv("gpt_api_key", "")
        deployment = os.getenv("gpt_deployment", "gpt-4o")
        
        # Convert endpoint to Foundry format if needed
        if endpoint:
            foundry_endpoint = endpoint.replace(".cognitiveservices.", ".services.ai.")
            if ".services.azure.com" in foundry_endpoint and ".services.ai.azure.com" not in foundry_endpoint:
                foundry_endpoint = foundry_endpoint.replace(".services.azure.com", ".services.ai.azure.com")
            if not foundry_endpoint.endswith("/models"):
                foundry_endpoint = f"{foundry_endpoint.rstrip('/')}/models"
        
        self.use_gpt = bool(endpoint and api_key)
        if self.use_gpt:
            try:
                self.client = ChatCompletionsClient(
                    endpoint=foundry_endpoint,
                    credential=AzureKeyCredential(api_key)
                )
                self.model = deployment
            except Exception:
                self.use_gpt = False

    def _call_gpt(self, user_message: str, conversation_history: List[Dict[str, str]] | None = None, additional_context: Dict[str, Any] | None = None) -> str:
        """Call GPT with domain-specific system prompt."""
        if not self.use_gpt:
            return f"I am your {self.domain.replace('_', ' ')} assistant. {user_message[:50]}... (GPT unavailable)"
        
        try:
            # Build system prompt
            system_prompt = AGENT_INSTRUCTIONS.get(self.domain, "You are a helpful assistant.")
            
            # Build messages
            messages = [{"role": "system", "content": system_prompt}]
            
            # Add conversation history (last few messages)
            if conversation_history:
                messages.extend(conversation_history[-5:])
            
            # Add current message
            messages.append({"role": "user", "content": user_message})
            
            # Call GPT
            response = self.client.complete(
                messages=messages,
                model=self.model,
                temperature=0.7,
                max_tokens=500
            )
            
            return response.choices[0].message.content
        except Exception as e:
            return f"I am having trouble connecting right now. Error: {str(e)[:100]}"
    
    def _handle_media_request(self, user_message: str, conversation_history: List[Dict[str, str]] | None = None, additional_context: Dict[str, Any] | None = None) -> Dict[str, Any]:
        # Check if this is an image generation request
        lower_msg = user_message.lower()
        image_keywords = ["generate", "create", "visualize", "show me", "design", "picture of", "thumbnail", "background"]
        
        should_generate_image = any(keyword in lower_msg for keyword in image_keywords)
        
        # Get text answer from GPT
        answer = self._call_gpt(user_message, conversation_history, additional_context)
        
        result = {"answer": answer}
        
        # Generate image if requested and service is available
        if should_generate_image and IMAGE_SERVICE_AVAILABLE:
            try:
                image_service = get_image_service()
                if image_service.is_configured():
                    # Determine model based on keywords
                    model = None
                    if "flux" in lower_msg or "high quality" in lower_msg:
                        model = image_service.flux_model
                    elif "gpt-image" in lower_msg:
                        model = image_service.gpt_image_model
                    elif "video" in lower_msg or "movie" in lower_msg:
                        # Handle video request
                        video_prompt = f"{user_message}. {answer[:200]}"
                        video_result = image_service.generate_video(video_prompt)
                        if video_result["success"]:
                            result["answer"] = f"{answer}\n\n[VIDEO] Video generated: {video_result['video_url']}"
                        else:
                            result["answer"] = f"{answer}\n\n(Video generation pending: {video_result['message']})"
                        return result

                    # Create image prompt from user message and GPT response
                    image_prompt = f"{user_message}. {answer[:200]}"
                    image_result = image_service.generate_image(image_prompt, model=model)
                    
                    if image_result["success"]:
                        result["image_url"] = image_result["blob_url"] or image_result["image_url"]
                        result["image_prompt"] = image_result.get("prompt", "")
                        # Append image info to answer
                        result["answer"] = f"{answer}\n\n[IMAGE] I have generated a visualization for you using {model or 'default model'}!"
            except Exception as e:
                # Don"t fail the whole request if image generation fails
                result["answer"] = f"{answer}\n\n(Note: Image generation unavailable at the moment: {str(e)})"
        
        return result

    def run_conversation_with_text_stream(
        self,
        user_message: str,
        conversation_history: List[Dict[str, str]] | None = None,
        additional_context: Dict[str, Any] | None = None
    ) -> Generator[str, None, None]:
        """Yield response chunks (single chunk for local processor)."""
        additional_context = additional_context or {}
        try:
            # For now, all agents use the same handler which supports image generation
            # In a real implementation, you might have specific handlers for cropping, video, etc.
            payload = self._handle_media_request(user_message, conversation_history, additional_context)
            yield json.dumps(payload)
        except Exception as e:
            yield json.dumps({"answer": f"I apologize, but I am having trouble right now. Please try again. ({str(e)[:50]})"})

