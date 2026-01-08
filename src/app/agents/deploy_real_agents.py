"""
Deploy real agents to MSFT Foundry using the AI Projects SDK.
This creates 6 specialized agents in the MSFT Foundry project with enhanced A2A protocol support.
"""
import os
import sys
import json
import hashlib
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from azure.core.credentials import AzureKeyCredential
from dotenv import load_dotenv

load_dotenv()

# Debug environment variables
print(f"DEBUG: AZURE_SUBSCRIPTION_ID={os.getenv('AZURE_SUBSCRIPTION_ID')}")
print(f"DEBUG: AZURE_RESOURCE_GROUP={os.getenv('AZURE_RESOURCE_GROUP')}")
print(f"DEBUG: AZURE_AI_PROJECT_NAME={os.getenv('AZURE_AI_PROJECT_NAME')}")
print(f"DEBUG: AZURE_LOCATION={os.getenv('AZURE_LOCATION')}")

def _hash_instructions(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def _resolve_model_name(model: str) -> str:
    """
    Resolve model name for Azure AI Projects.
    The 'model-router' uses the actual Azure OpenAI Model Router (2025-11-18) which
    intelligently routes requests across 18 models including GPT-4o, GPT-4o-mini,
    Claude, DeepSeek, Llama, and Grok based on prompt characteristics.
    
    Azure AI Agents API converts hyphens to underscores in model names.
    """
    # Azure AI Agents API converts hyphen to underscore
    return model.replace("-", "_")

def deploy_agents():
    """Deploy or update agents idempotently, emitting structured JSON for Terraform."""

    project_endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT") or os.getenv("AZURE_AI_FOUNDRY_ENDPOINT")
    deploy_region = os.getenv("DEPLOY_REGION", "swedencentral").lower()
    
    # Load dynamic configuration from environment variables (set by Terraform)
    agent_region_map = json.loads(os.getenv("AGENT_REGION_MAP", "{}"))
    agent_model_map = json.loads(os.getenv("AGENT_MODEL_MAP", "{}"))
    
    if not project_endpoint:
        # Use the actual deployed endpoint from terraform
        project_endpoint = "https://aif-eastus2-5d360b17.cognitiveservices.azure.com/"

    print("=" * 70)
    print(f"Idempotent Multi-Agent Provisioning - Azure AI Foundry ({deploy_region})")
    print("=" * 70)
    print(f"Project Endpoint: {project_endpoint}")
    print(f"Deploy Region: {deploy_region}")
    print(f"Agent-Region Map: {agent_region_map}")
    print(f"Agent-Model Map: {agent_model_map}")
    print()

    # Try to construct connection string if available
    project_connection_string = os.getenv("AZURE_AI_PROJECT_CONNECTION_STRING")
    if not project_connection_string:
        sub_id = os.getenv("AZURE_SUBSCRIPTION_ID", "407f4106-0fd3-42e0-9348-3686dd1e7347")
        rg = os.getenv("AZURE_RESOURCE_GROUP", "RG-AI-Mediax4v")  # Fixed: v not z
        project_name = os.getenv("AZURE_AI_PROJECT_NAME", "proj-eastus2-5d360b17")  # Fixed: actual project name
        location = os.getenv("AZURE_LOCATION", "eastus2")
        
        if sub_id and rg and project_name and location:
            project_connection_string = f"{location}.api.azureml.ms;subscription_id={sub_id};resource_group={rg};project_name={project_name}"
            print(f"Constructed connection string: {project_connection_string}")

    # Use dynamic region_agents from Terraform variable or fallback to defaults
    region_agents = agent_region_map if agent_region_map else {
        "swedencentral": [
            "orchestrator", "cropping_agent", "video_agent", "document_agent"
        ],
        "eastus": ["visual_content_agent"]  # Co-located with FLUX.2-pro for low-latency image generation
    }
    
    # Get agents for this region
    agents_to_deploy = region_agents.get(deploy_region, [])
    
    if not agents_to_deploy:
        print(f"No agents configured for region: {deploy_region}")
        return
    
    print(f"Deploying agents for {deploy_region}: {', '.join(agents_to_deploy)}")
    print()

    # Agent config definitions (all agents defined, filtered by region)
    # Models are dynamically loaded from agent_model_map
    agents_config = [
        {
            "name": "Zava Media Orchestrator",
            "env_var": "orchestrator",
            "instructions": (
                "You are the Zava Media Orchestrator powered by Azure OpenAI Model Router. "
                "Your job is to analyze user requests related to image, video, and document processing and route them to the appropriate specialist agent.\n\n"
                "**ROUTING LOGIC:**\n"
                "- If the user wants to crop an image or object, delegate to the 'cropping_agent'. "
                "- If the user wants to change backgrounds, create thumbnails, or generate images, delegate to the 'visual_content_agent'. "
                "- If the user wants to create a video, delegate to the 'video_agent'. "
                "- If the user wants to process, extract, or analyze documents/PDFs, delegate to the 'document_agent'. "
                "- For general questions, answer them yourself.\n\n"
                "The Model Router automatically selects the optimal AI model (from 18 available models including GPT-4o, Claude, DeepSeek, Llama, Grok) based on your prompt characteristics."
            ),
            "model": agent_model_map.get("orchestrator", "model-router"),
            "use_router": True
        },
        {
            "name": "Cropping Specialist",
            "env_var": "cropping_agent",
            "instructions": (
                "You are the Cropping Specialist. Your task is to identify objects in images and provide cropping coordinates or cropped images. "
                "You use advanced vision models to detect subjects and understand image content."
            ),
            "model": agent_model_map.get("cropping_agent", "gpt-4o")
        },
        {
            "name": "Visual Content Specialist",
            "env_var": "visual_content_agent",
            "instructions": (
                "You are the Visual Content Specialist. Your task is to handle all visual content creation including:\n"
                "1. **Background Management** - Remove or replace backgrounds in images, create new backgrounds based on descriptions\n"
                "2. **Thumbnail Generation** - Create eye-catching video thumbnails that maximize engagement\n"
                "3. **Image Creation** - Generate high-quality images from text descriptions\n\n"
                "You use FLUX.2-pro for advanced image generation. "
                "Combine creative vision with technical execution to deliver professional visual content."
            ),
            "model": agent_model_map.get("visual_content_agent", "FLUX.2-pro")  # Deployed in East US with FLUX.2-pro
        },
        {
            "name": "Video Agent",
            "env_var": "video_agent",
            "instructions": (
                "You are the Video Agent. Your task is to create and process video content. "
                "You can analyze videos, provide editing recommendations, and suggest video enhancements. "
                "You can generate videos using Sora for native video generation or image-sequence methods."
            ),
            "model": agent_model_map.get("video_agent", "sora")
        },
        {
            "name": "Document Processor",
            "env_var": "document_agent",
            "instructions": (
                "You are the Document Processor. Your task is to analyze, extract, and generate content from documents including PDFs, images with text, and structured documents. "
                "You can extract text, understand layout, generate document summaries, and create visual representations of document content. "
                "You excel at contextual understanding of documents and can help with document-to-image conversion, text extraction, and document enhancement."
            ),
            "model": agent_model_map.get("document_agent", "FLUX.1-Kontext-pro")
        }
    ]
    # --- Dynamic Model Assignment ---
    # Models are loaded from the agent_model_map environment variable (set by Terraform).
    # This allows easy reconfiguration via terraform.tfvars without code changes.
    # Defaults are provided as fallbacks if the map is not available.
    # ---
    # Load prior state (instruction hashes) if present
    # Write to terraform temp directory instead of src/app/agents
    terraform_dir = os.path.join(os.path.dirname(__file__), "..", "..", "..", "terraform-infrastructure")
    state_path = os.path.join(terraform_dir, ".terraform", "agents_state.json")
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    prior_state = {}
    if os.path.exists(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as sf:
                prior_state = json.load(sf)
        except Exception:
            prior_state = {}

    deployed_agents = {}
    statuses = {}

    try:
        print("Initializing Azure AI Project Client...")
        
        # Use DefaultAzureCredential with correct scope for Azure AI
        # The Azure AI Foundry API requires tokens scoped to https://ai.azure.com
        from azure.core.credentials import AccessToken
        from datetime import datetime
        
        base_credential = DefaultAzureCredential()
        
        # Create a wrapper credential that requests tokens with the correct audience
        class AzureAIScopedCredential:
            """Wrapper credential that requests tokens with the correct Azure AI audience."""
            def __init__(self, credential):
                self._credential = credential
            
            def get_token(self, *scopes, **kwargs):
                # Request token with Azure AI audience
                return self._credential.get_token("https://ai.azure.com/.default", **kwargs)
        
        credential = AzureAIScopedCredential(base_credential)
        
        # Get required environment variables
        sub_id = os.getenv("AZURE_SUBSCRIPTION_ID")
        rg = os.getenv("AZURE_RESOURCE_GROUP")
        project_name = os.getenv("AZURE_AI_PROJECT_NAME")
        
        if not all([sub_id, rg, project_name]):
            raise ValueError("Missing required environment variables: AZURE_SUBSCRIPTION_ID, AZURE_RESOURCE_GROUP, AZURE_AI_PROJECT_NAME")
        
        # Keep the original cognitiveservices.azure.com endpoint domain
        # The Azure AI Projects API uses the cognitiveservices domain
        
        # Use the base endpoint directly
        print(f"Project Endpoint: {project_endpoint}")
        print(f"Subscription: {sub_id}")
        print(f"Resource Group: {rg}")
        print(f"Project Name: {project_name}")
        
        # Initialize AIProjectClient using subscription, resource group, and project name
        # This is the recommended approach for Azure AI Projects
        project_client = AIProjectClient(
            endpoint=project_endpoint,  # Use the base endpoint
            subscription_id=sub_id,
            resource_group_name=rg,
            project_name=project_name,
            credential=credential
        )
        
        print("Successfully initialized AIProjectClient")
        print("Fetching existing agents...")
        
        existing_agents = {}
        try:
            agent_list = list(project_client.agents.list_agents())
            existing_agents = {a.name: a for a in agent_list}
            print(f"Found {len(existing_agents)} existing agent(s)")
        except Exception as list_err:
            print(f"Could not list existing agents (may be first run): {list_err}")
            existing_agents = {}
            
    except Exception as e:
        print(f"ERROR initializing AIProjectClient: {e}")
        import traceback
        traceback.print_exc()
        print("\nFalling back to local pseudo-agents...")
        existing_agents = {}
        # Don't exit - continue with fallback IDs
        project_client = None

    for cfg in agents_config:
        name = cfg["name"]
        env_var = cfg["env_var"]
        
        # Skip agents not configured for this region
        if env_var not in agents_to_deploy:
            print(f"[{env_var}] Skipping - not configured for {deploy_region}")
            continue
        
        instr = cfg["instructions"]
        instr_hash = _hash_instructions(instr)
        prior_hash = prior_state.get(env_var, {}).get("hash")

        # Skip if no project client available
        if project_client is None:
            print(f"[{env_var}] No project client - using fallback ID")
            fallback_id = f"asst_local_{env_var}"
            deployed_agents[env_var] = fallback_id
            statuses[env_var] = "fallback-no-client"
            continue

        # Idempotent logic - check if agent already exists
        if name in existing_agents:
            agent_obj = existing_agents[name]
            agent_id = getattr(agent_obj, "id", None) or getattr(agent_obj, "agentId", f"unknown-{env_var}")
            
            # Attempt update if instructions changed
            if prior_hash and prior_hash != instr_hash:
                print(f"[{env_var}] Updating agent (instructions changed): {name}")
                try:
                    # Try native update if available
                    try:
                        project_client.agents.update_agent(agent_id=agent_id, instructions=instr)
                        statuses[env_var] = "updated"
                        print(f"[{env_var}] Successfully updated: {agent_id}")
                    except Exception:
                        # Fallback recreate strategy
                        print(f"[{env_var}] Update not supported, recreating...")
                        try:
                            project_client.agents.delete_agent(agent_id)
                        except Exception:
                            pass
                        new_agent = project_client.agents.create_agent(
                            model=_resolve_model_name(cfg["model"]), 
                            name=name, 
                            instructions=instr
                        )
                        agent_id = new_agent.id
                        statuses[env_var] = "recreated"
                        print(f"[{env_var}] Successfully recreated: {agent_id}")
                except Exception as ue:
                    print(f"[{env_var}] Failed to update {name}: {ue}")
                    statuses[env_var] = "existing-no-update"
                deployed_agents[env_var] = agent_id
            else:
                print(f"[{env_var}] Reusing existing agent: {name} ({agent_id})")
                deployed_agents[env_var] = agent_id
                statuses[env_var] = "existing"
            continue

        # Create new agent
        print(f"[{env_var}] Creating new agent: {name}")
        try:
            agent = project_client.agents.create_agent(
                model=_resolve_model_name(cfg["model"]), 
                name=name, 
                instructions=instr
            )
            agent_id = agent.id
            deployed_agents[env_var] = agent_id
            statuses[env_var] = "created"
            print(f"[{env_var}] SUCCESS - Created agent: {agent_id}")
        except Exception as ce:
            print(f"[{env_var}] FAILED to create {name}: {ce}")
            import traceback
            traceback.print_exc()
            
            # Use fallback local ID
            fallback_id = f"asst_local_{env_var}"
            deployed_agents[env_var] = fallback_id
            statuses[env_var] = "fallback-creation-failed"
            print(f"[{env_var}] Using fallback local simulation: {fallback_id}")

    # Persist state (hash + id)
    new_state = {}
    terraform_state = {}  # Terraform-compatible format
    for cfg in agents_config:
        ev = cfg["env_var"]
        agent_id = deployed_agents.get(ev)
        new_state[ev] = {
            "id": agent_id,
            "hash": _hash_instructions(cfg["instructions"]),
            "status": statuses.get(ev)
        }
        # Also save in Terraform-compatible format
        terraform_state[f"agent_{ev}_id"] = agent_id
        
    try:
        with open(state_path, "w", encoding="utf-8") as sf:
            json.dump(new_state, sf, indent=2)
        print(f"[STATE] State file updated: {state_path}")
        
        # Also save Terraform-compatible state file
        terraform_state_path = os.path.join(terraform_dir, ".terraform", "terraform_agents_state.json")
        with open(terraform_state_path, "w", encoding="utf-8") as tf:
            json.dump(terraform_state, tf, indent=2)
        print(f"[STATE] Terraform state file updated: {terraform_state_path}")
        
    except Exception as se:
        print(f"WARNING: Failed to write state file: {se}")

    # Update .env with real agent IDs (early propagation)
    env_path = os.path.join(os.path.dirname(__file__), '..', '..', '.env')
    if os.path.exists(env_path):
        try:
            with open(env_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Replace each agent ID
            for var, aid in deployed_agents.items():
                # Use regex to replace the value after the = sign
                import re
                pattern = rf'^{re.escape(var)}=.*$'
                replacement = f'{var}={aid}'
                content = re.sub(pattern, replacement, content, flags=re.MULTILINE)
            
            # Also fix the project endpoint domain in .env
            if "cognitiveservices.azure.com" in content:
                print("Fixing endpoint domains in .env...")
                content = content.replace(
                    "AZURE_AI_PROJECT_ENDPOINT=https://aif-",
                    "# AZURE_AI_PROJECT_ENDPOINT=https://aif-"  # Comment out old
                )
                # Add corrected endpoint after the Azure AI Foundry section
                if "# Azure AI Foundry Configuration" in content:
                    content = content.replace(
                        "# Azure AI Foundry Configuration\n",
                        "# Azure AI Foundry Configuration\n# Note: Agents API uses .services.ai.azure.com domain\n"
                    )
            
            with open(env_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            print(f"[{env_var}] Updated .env with agent IDs: {env_path}")
            print("Agent IDs written:")
            for var, aid in deployed_agents.items():
                print(f"  {var}: {aid}")
        except Exception as ee:
            print(f"WARNING: Failed to update .env: {ee}")
            import traceback
            traceback.print_exc()
    else:
        print("INFO: .env file not found for agent ID propagation")

    print("\n" + "=" * 70)
    print("DEPLOYMENT SUMMARY")
    print("=" * 70)
    for k, v in deployed_agents.items():
        status = statuses.get(k, "unknown")
        print(f"  {k}: {v} [{status}]")

    # Emit structured JSON sentinel block for Terraform parsing
    payload = {"agents": deployed_agents, "statuses": statuses}
    print("===AGENTS_JSON_START===")
    print(json.dumps(payload, indent=2))
    print("===AGENTS_JSON_END===")

    return deployed_agents

if __name__ == "__main__":
    deploy_agents()
