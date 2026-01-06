import json
import os
import logging
import traceback
from typing import List, Dict, Any, Generator
from datetime import datetime
from openai import AzureOpenAI
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

# Setup comprehensive logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
)
logger = logging.getLogger(__name__)

try:
    from app.agents.agents_config import AGENT_INSTRUCTIONS
    logger.info("Loaded AGENT_INSTRUCTIONS from agents_config")
except Exception as e:
    logger.warning(f"Failed to load agents_config, using fallback: {e}")
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
    logger.info("Image service available")
except Exception as e:
    IMAGE_SERVICE_AVAILABLE = False
    logger.warning(f"Image service not available: {e}")

class LocalAgentProcessor:
    """Azure AI Foundry-based agent using GPT with domain-specific prompts.
    
    This uses Azure AI Foundry models (Sora, DALL-E, FLUX, GPT)
    via Managed Identity - fully Azure-hosted, no local processing.
    """
    def __init__(self, agent_id: str, domain: str):
        logger.info(f"Initializing LocalAgentProcessor - agent_id={agent_id}, domain={domain}")
        self.agent_id = agent_id
        self.domain = domain
        
        # Initialize Azure AI Foundry client with Managed Identity
        # Try Azure AI Foundry project endpoint first, fallback to direct endpoint
        foundry_endpoint = os.getenv("AZURE_AI_FOUNDRY_ENDPOINT", "")
        project_endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT", "")
        fallback_endpoint = os.getenv("gpt_endpoint", "")
        
        # Use project endpoint if available, otherwise fallback
        endpoint = project_endpoint if project_endpoint else (foundry_endpoint if foundry_endpoint else fallback_endpoint)
        
        api_key = os.getenv("AZURE_AI_FOUNDRY_API_KEY", os.getenv("gpt_api_key", "MANAGED_IDENTITY"))
        deployment = os.getenv("AZURE_AI_AGENT_MODEL_DEPLOYMENT_NAME", os.getenv("gpt_deployment", "gpt-4o"))
        api_version = os.getenv("gpt_api_version", "2024-08-01-preview")
        
        logger.info(f"[INIT] Azure AI Foundry Configuration:")
        logger.info(f"  - Foundry Endpoint: {foundry_endpoint}")
        logger.info(f"  - Project Endpoint: {project_endpoint}")
        logger.info(f"  - Fallback Endpoint: {fallback_endpoint}")
        logger.info(f"  - Using Endpoint: {endpoint}")
        logger.info(f"  - Deployment: {deployment}")
        logger.info(f"  - API Version: {api_version}")
        logger.info(f"  - Using Managed Identity: {api_key == 'MANAGED_IDENTITY'}")
        
        # Debug: Check all Azure AI related environment variables
        logger.debug(f"[ENV DEBUG] AZURE_AI_FOUNDRY_ENDPOINT: {os.getenv('AZURE_AI_FOUNDRY_ENDPOINT')}")
        logger.debug(f"[ENV DEBUG] AZURE_AI_PROJECT_ENDPOINT: {os.getenv('AZURE_AI_PROJECT_ENDPOINT')}")
        logger.debug(f"[ENV DEBUG] AZURE_AI_AGENT_MODEL_DEPLOYMENT_NAME: {os.getenv('AZURE_AI_AGENT_MODEL_DEPLOYMENT_NAME')}")
        logger.debug(f"[ENV DEBUG] gpt_endpoint: {os.getenv('gpt_endpoint')}")
        logger.debug(f"[ENV DEBUG] gpt_deployment: {os.getenv('gpt_deployment')}")
        logger.debug(f"[ENV DEBUG] gpt_api_version: {os.getenv('gpt_api_version')}")
        
        # Use the Azure AI Foundry client
        self.use_gpt = bool(endpoint)
        if self.use_gpt:
            try:
                # Use Managed Identity if api_key is "MANAGED_IDENTITY"
                if api_key == "MANAGED_IDENTITY":
                    logger.info("[INIT] Setting up Azure AI Foundry Managed Identity authentication...")
                    logger.debug("[INIT] Creating DefaultAzureCredential...")
                    credential = DefaultAzureCredential()
                    logger.debug("[INIT] Credential created successfully")
                    
                    logger.debug("[INIT] Creating bearer token provider for Cognitive Services...")
                    token_provider = get_bearer_token_provider(
                        credential, 
                        "https://cognitiveservices.azure.com/.default"
                    )
                    logger.debug("[INIT] Token provider created successfully")
                    
                    logger.info(f"[INIT] Creating AzureOpenAI client for Azure AI Foundry project...")
                    logger.info(f"[INIT] Endpoint: {endpoint}")
                    
                    # For Azure AI Foundry project endpoints, we need to handle them differently
                    if "/projects/" in endpoint:
                        # This is a project endpoint - extract the base endpoint
                        import re
                        match = re.match(r'(https://[^/]+)', endpoint)
                        if match:
                            base_endpoint = match.group(1)
                            logger.info(f"[INIT] Extracted base endpoint from project URL: {base_endpoint}")
                            self.client = AzureOpenAI(
                                azure_endpoint=base_endpoint,
                                azure_ad_token_provider=token_provider,
                                api_version=api_version
                            )
                        else:
                            logger.warning("[INIT] Could not parse project endpoint, using as-is")
                            self.client = AzureOpenAI(
                                azure_endpoint=endpoint,
                                azure_ad_token_provider=token_provider,
                                api_version=api_version
                            )
                    else:
                        # Direct endpoint
                        self.client = AzureOpenAI(
                            azure_endpoint=endpoint,
                            azure_ad_token_provider=token_provider,
                            api_version=api_version
                        )
                    
                    logger.info("[INIT] AzureOpenAI client created with Azure AI Foundry Managed Identity")
                else:
                    logger.info("[INIT] Creating AzureOpenAI client with API key...")
                    self.client = AzureOpenAI(
                        azure_endpoint=endpoint,
                        api_key=api_key,
                        api_version=api_version
                    )
                    logger.info("[INIT] AzureOpenAI client created with API key")
                    
                self.model = deployment
                logger.info(f"✓ Azure AI Foundry client initialized successfully")
                logger.info(f"  - Endpoint: {endpoint}")
                logger.info(f"  - Model: {deployment}")
                logger.info(f"  - API Version: {api_version}")
            except Exception as e:
                logger.error(f"✗ Failed to initialize Azure AI Foundry client: {e}", exc_info=True)
                logger.error(f"Traceback: {traceback.format_exc()}")
                self.use_gpt = False
        else:
            logger.warning("No Azure AI Foundry endpoint configured - use_gpt=False")

    def _call_gpt(self, user_message: str, conversation_history: List[Dict[str, str]] | None = None, additional_context: Dict[str, Any] | None = None) -> str:
        """Call GPT with domain-specific system prompt."""
        call_id = datetime.utcnow().isoformat()
        logger.info(f"[{call_id}] _call_gpt invoked")
        logger.debug(f"[{call_id}] user_message: {user_message[:200]}...")
        logger.debug(f"[{call_id}] use_gpt: {self.use_gpt}")
        
        if not self.use_gpt:
            error_msg = f"Azure AI Foundry client not initialized. Check Azure AI endpoints."
            logger.error(f"[{call_id}] {error_msg}")
            return f"I am your {self.domain.replace('_', ' ')} assistant. {user_message[:50]}... (GPT unavailable)"
        
        try:
            # Build system prompt
            system_prompt = AGENT_INSTRUCTIONS.get(self.domain, "You are a helpful assistant.")
            logger.debug(f"[{call_id}] System prompt: {system_prompt[:100]}...")
            
            # Build messages
            messages = [{"role": "system", "content": system_prompt}]
            
            # Add conversation history (last few messages)
            if conversation_history:
                messages.extend(conversation_history[-5:])
                logger.debug(f"[{call_id}] Added {len(conversation_history[-5:])} history messages")
            
            # Add current message
            messages.append({"role": "user", "content": user_message})
            logger.debug(f"[{call_id}] Total messages: {len(messages)}")
            
            logger.info(f"[{call_id}] Calling Azure AI Foundry...")
            logger.debug(f"[{call_id}] Model: {self.model}")
            logger.debug(f"[{call_id}] Messages count: {len(messages)}")
            
            # Call Azure AI Foundry using chat.completions.create
            logger.debug(f"[{call_id}] Invoking client.chat.completions.create...")
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.7,
                max_tokens=500
            )
            
            logger.debug(f"[{call_id}] Response received from Azure AI Foundry")
            logger.debug(f"[{call_id}] Response ID: {response.id}")
            logger.debug(f"[{call_id}] Model used: {response.model}")
            logger.debug(f"[{call_id}] Choices: {len(response.choices)}")
            
            result = response.choices[0].message.content
            logger.info(f"[{call_id}] ✓ Azure AI Foundry response received: {len(result)} characters")
            logger.debug(f"[{call_id}] Response preview: {result[:200]}...")
            return result
            
        except Exception as e:
            error_msg = f"Azure AI Foundry call failed: {str(e)}"
            logger.error(f"[{call_id}] {error_msg}", exc_info=True)
            logger.error(f"[{call_id}] Traceback: {traceback.format_exc()}")
            logger.error(f"[{call_id}] Exception type: {type(e).__name__}")
            logger.error(f"[{call_id}] Exception args: {e.args}")
            return f"I am having trouble connecting to Azure AI Foundry right now. Error: {str(e)[:100]}"
    
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
        stream_id = datetime.utcnow().isoformat()
        logger.info(f"[{stream_id}] run_conversation_with_text_stream called")
        logger.debug(f"[{stream_id}] user_message: {user_message[:200]}...")
        logger.debug(f"[{stream_id}] has conversation_history: {conversation_history is not None}")
        logger.debug(f"[{stream_id}] has additional_context: {additional_context is not None}")
        
        try:
            # Handle media request
            logger.debug(f"[{stream_id}] Calling _handle_media_request...")
            result = self._handle_media_request(user_message, conversation_history, additional_context)
            logger.info(f"[{stream_id}] _handle_media_request completed")
            logger.debug(f"[{stream_id}] Result keys: {list(result.keys())}")
            
            # Yield the answer as a single chunk
            answer = result.get("answer", "No response generated")
            logger.info(f"[{stream_id}] Yielding answer: {len(answer)} characters")
            logger.debug(f"[{stream_id}] Answer preview: {answer[:200]}...")
            yield answer
            logger.debug(f"[{stream_id}] Stream complete")
            
        except Exception as e:
            error_msg = f"Error in run_conversation_with_text_stream: {str(e)}"
            logger.error(f"[{stream_id}] {error_msg}", exc_info=True)
            logger.error(f"[{stream_id}] Traceback: {traceback.format_exc()}")
            yield f"Error processing request: {str(e)}"


