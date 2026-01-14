"""
Deploy Azure AI Foundry Agents using NEW Agents API (2.0.0b1+).

This creates Azure AI agents in Microsoft Foundry using the NEW Agents API:
- Requires: azure-ai-projects>=2.0.0b1
- Uses: project_client.agents.create(agent_name=..., model=...)
- Access: project_client.agents.get(agent_name=...)
- Invoke: openai_client.responses.create(extra_body={"agent": {"name": ..., "type": "agent_reference"}})

Reference: https://learn.microsoft.com/en-us/azure/ai-foundry/agents/how-to/migrate
"""
import os
import sys
import json
import hashlib
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

load_dotenv()

# Ensure we're using the NEW API version
try:
    import pkg_resources
    version = pkg_resources.get_distribution("azure-ai-projects").version
    print(f"Azure AI Projects SDK version: {version}")
    if version < "2.0.0":
        print("[WARNING] Requires azure-ai-projects>=2.0.0b1 for NEW Agents API")
except:
    pass

# Debug environment variables
print(f"DEBUG: AZURE_SUBSCRIPTION_ID={os.getenv('AZURE_SUBSCRIPTION_ID')}")
print(f"DEBUG: AZURE_RESOURCE_GROUP={os.getenv('AZURE_RESOURCE_GROUP')}")
print(f"DEBUG: AZURE_AI_PROJECT_NAME={os.getenv('AZURE_AI_PROJECT_NAME')}")
print(f"DEBUG: AZURE_LOCATION={os.getenv('AZURE_LOCATION')}")

def _hash_instructions(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def _resolve_model_name(model: str) -> str:
    """
    Resolve model name to EXACT Azure deployment name.
    CRITICAL: Must match exact deployment names in Azure AI Foundry.
    Tested deployments in Sweden Central:
    - model-router (NOT model_router)
    - gpt-4o (NOT gpt_4o)
    - FLUX.1-Kontext-pro (NOT FLUX.1_Kontext_pro)
    - sora
    """
    model_map = {
        # Underscore to hyphen mappings
        "model_router": "model-router",
        "gpt_4o": "gpt-4o",
        "FLUX.1_Kontext_pro": "FLUX.1-Kontext-pro",
        "FLUX.2_pro": "FLUX.2-pro",
        # Already correct
        "model-router": "model-router",
        "gpt-4o": "gpt-4o",
        "FLUX.1-Kontext-pro": "FLUX.1-Kontext-pro",
        "sora": "sora",
    }
    resolved = model_map.get(model, model)
    # Fallback: replace underscores with hyphens
    if resolved == model and '_' in model:
        resolved = model.replace('_', '-')
    return resolved

def _sanitize_agent_name(name: str) -> str:
    """
    Sanitize agent name for NEW API requirements:
    - Must start and end with alphanumeric
    - Can contain hyphens in middle
    - Max 63 characters
    """
    import re
    # Replace spaces with hyphens
    name = name.replace(" ", "-")
    # Remove non-alphanumeric except hyphens
    name = re.sub(r'[^a-zA-Z0-9-]', '', name)
    # Remove consecutive hyphens
    name = re.sub(r'-+', '-', name)
    # Trim to 63 chars
    name = name[:63]
    # Ensure starts and ends with alphanumeric
    name = name.strip('-')
    return name.lower()

def _get_agent_tools(agent_type: str) -> list:
    """
    Get tools configuration for different agent types.
    NEW Agents API (2.0.0b1+) requires container property for code_interpreter.
    Reference: https://learn.microsoft.com/en-us/azure/ai-foundry/agents/how-to/migrate
    """
    tools = []
    
    if agent_type == "cropping_agent":
        # Vision analyst - no tools needed, uses GPT-4o vision to analyze and provide coordinates
        # Application code handles actual image manipulation via PIL/OpenCV
        pass
    
    elif agent_type == "visual_content_agent":
        # Image generation agent - code interpreter for image manipulation
        tools.append({
            "type": "code_interpreter",
            "container": {"type": "auto"}
        })
    
    elif agent_type == "video_agent":
        # Video generation agent - code interpreter
        tools.append({
            "type": "code_interpreter",
            "container": {"type": "auto"}
        })
    
    elif agent_type == "document_agent":
        # Document processing - code interpreter for document analysis
        # Note: file_search requires vector_store_ids setup
        tools.append({
            "type": "code_interpreter",
            "container": {"type": "auto"}
        })
    
    elif agent_type == "orchestrator":
        # Orchestrator can use code interpreter
        # NEW API format: container property is required
        tools.append({
            "type": "code_interpreter",
            "container": {"type": "auto"}
        })
    
    return tools

def deploy_agents():
    """Deploy or update agents idempotently, emitting structured JSON for Terraform."""

    project_endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT") or os.getenv("AZURE_AI_FOUNDRY_ENDPOINT")
    deploy_region = os.getenv("DEPLOY_REGION", "swedencentral").lower()
    
    # Load dynamic configuration from environment variables (set by Terraform)
    agent_region_map = json.loads(os.getenv("AGENT_REGION_MAP", "{}"))
    agent_model_map = json.loads(os.getenv("AGENT_MODEL_MAP", "{}"))
    
    if not project_endpoint:
        # Construct endpoint from Terraform output values
        project_name = os.getenv("AZURE_AI_PROJECT_NAME", "proj-swedencentral-CHANGE_ME")
        foundry_name = os.getenv("AZURE_AI_FOUNDRY_NAME", "aif-swedencentral-CHANGE_ME")
        # Use NEW Agents API format: .services.ai.azure.com/api/projects/...
        project_endpoint = f"https://{foundry_name}.services.ai.azure.com/api/projects/{project_name}"

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

    # HYBRID ARCHITECTURE:
    # - NEW Agents: Only chat models (orchestrator, cropping)
    # - Direct API: FLUX/Sora accessed via application code (not agents)
    # 
    # Rationale: NEW Agents API only supports chat_completion models
    # Image/video generation models (FLUX, Sora) must be called directly
    region_agents = agent_region_map if agent_region_map else {
        "swedencentral": [
            "orchestrator",      # model-router: intelligent routing
            "cropping_agent"     # gpt-4o: vision analysis
        ],
        # Other regions: No agents needed (direct API calls)
    }
    
    # Get agents for this region
    agents_to_deploy = region_agents.get(deploy_region, [])
    
    if not agents_to_deploy:
        print(f"No agents configured for region: {deploy_region}")
        return
    
    print(f"Deploying agents for {deploy_region}: {', '.join(agents_to_deploy)}")
    print()

    # Agent config definitions
    # ONLY chat-compatible agents are deployed as NEW Agents
    # Image/video generation handled by direct API calls in application
    agents_config = [
        {
            "name": "Zava Media Orchestrator",
            "env_var": "orchestrator",
            "instructions": (
                "You are the Zava Media Orchestrator powered by Azure OpenAI Model Router. "
                "Your job is to analyze user requests and provide routing decisions.\n\n"
                "**ROUTING LOGIC:**\n"
                "- Image analysis/object detection → Route to 'cropping_agent' (Vision Analyst)\n"
                "- Image generation (backgrounds, thumbnails) → Return: {\"action\": \"call_flux\", \"prompt\": \"<optimized>\"}\n"
                "- Video generation → Return: {\"action\": \"call_sora\", \"prompt\": \"<optimized>\"}\n"
                "- Document image generation → Return: {\"action\": \"call_flux_kontext\", \"prompt\": \"<optimized>\"}\n"
                "- General questions → Answer directly\n\n"
                "Model Router selects optimal AI model from 18 options (GPT-4o, Claude, DeepSeek, Llama, Grok).\n\n"
                "NOTE: You route and optimize. Application code executes image/video generation."
            ),
            "model": agent_model_map.get("orchestrator", "model-router"),
            "use_router": True
        },
        {
            "name": "Vision Analyst",
            "env_var": "cropping_agent",
            "instructions": (
                "You are the Vision Analyst powered by GPT-4o vision. "
                "Analyze images to identify objects, detect bounding boxes, and provide precise coordinates. "
                "Return object locations as JSON: {\"object\": \"person\", \"bbox\": [x, y, width, height], \"confidence\": 0.95}. "
                "You provide analysis and coordinates only - the application handles actual image manipulation. "
                "Focus on: object detection, coordinate calculation, composition analysis, and visual understanding."
            ),
            "model": agent_model_map.get("cropping_agent", "gpt-4o")
        },
        # REMOVED: visual_content_agent, video_agent, document_agent
        # These require FLUX/Sora models which are not chat-compatible
        # Use direct API calls in application code instead
        {
            "name": "REMOVED - Use Direct API",
            "env_var": "visual_content_agent",
            "skip": True,  # Not deployed as agent
            "note": "FLUX.2-pro accessed via direct API in application"
        },
        {
            "name": "REMOVED - Use Direct API",
            "env_var": "video_agent",
            "skip": True,  # Not deployed as agent
            "note": "Sora accessed via direct API in application"
        },
        {
            "name": "REMOVED - Use Direct API",
            "env_var": "document_agent",
            "skip": True,  # Not deployed as agent
            "note": "FLUX.1-Kontext-pro accessed via direct API in application"
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
        
        # Get environment variables for diagnostics
        sub_id = os.getenv("AZURE_SUBSCRIPTION_ID")
        rg = os.getenv("AZURE_RESOURCE_GROUP")
        project_name = os.getenv("AZURE_AI_PROJECT_NAME")
        
        print(f"Project Endpoint: {project_endpoint}")
        print(f"Subscription: {sub_id}")
        print(f"Resource Group: {rg}")
        print(f"Project Name: {project_name}")
        
        # Use official Microsoft pattern from documentation
        # https://learn.microsoft.com/en-us/azure/ai-foundry/how-to/develop/sdk-overview
        credential = DefaultAzureCredential()
        
        # Initialize AIProjectClient using the official Microsoft pattern
        # The endpoint should be: https://<foundry>.services.ai.azure.com/api/projects/<project>
        project_client = AIProjectClient(
            endpoint=project_endpoint,
            credential=credential
        )
        
        print("Successfully initialized AIProjectClient")
        print("Fetching existing NEW agents...")
        
        existing_agents = {}
        
        try:
            # NEW API (2.0.0b1+): Use project_client.agents.list()
            agent_list = list(project_client.agents.list())
            existing_agents = {a.name: a for a in agent_list}
            print(f"Found {len(existing_agents)} existing NEW agent(s)")
            for agent in agent_list:
                print(f"  - {agent.name}")
        except Exception as list_err:
            print(f"Could not list existing agents (may be first run): {list_err}")
            import traceback
            traceback.print_exc()
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
        
        # Skip agents marked as removed (direct API only)
        if cfg.get("skip", False):
            print(f"[{env_var}] SKIPPED - {cfg.get('note', 'Not deployed as agent')}")
            deployed_agents[env_var] = "skipped"
            statuses[env_var] = "skipped"
            continue
        
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

        # Check if agent exists - NEW API gets by name
        # Use sanitized name for comparison since that's what gets created
        sanitized_name = _sanitize_agent_name(name)
        if sanitized_name in existing_agents:
            existing = existing_agents[sanitized_name]
            print(f"[{env_var}] Agent '{sanitized_name}' already exists (NEW API), reusing")
            deployed_agents[env_var] = sanitized_name  # NEW API uses agent name as reference
            statuses[env_var] = "reused"
            continue

        # Create new agent using NEW Agents API (2.0.0b1+)
        print(f"[{env_var}] Creating NEW agent: {name}")
        try:
            # Get tools for this agent type
            agent_tools = _get_agent_tools(env_var)
            
            # Already sanitized above for duplicate check
            print(f"[{env_var}] Model: {_resolve_model_name(cfg['model'])}")
            print(f"[{env_var}] Sanitized Name: {sanitized_name}")
            print(f"[{env_var}] Tools: {agent_tools}")
            
            # NEW API (2.0.0b3): Use project_client.agents.create() with PromptAgentDefinition
            # Reference: https://learn.microsoft.com/en-us/azure/ai-foundry/agents/how-to/migrate
            from azure.ai.projects.models import PromptAgentDefinition
            
            agent_def = PromptAgentDefinition(
                model=_resolve_model_name(cfg["model"]),
                instructions=instr,
                tools=agent_tools
            )
            agent = project_client.agents.create(name=sanitized_name, definition=agent_def)
            
            deployed_agents[env_var] = name  # NEW API uses agent name as reference
            statuses[env_var] = "created"
            print(f"[{env_var}] SUCCESS - Created NEW agent: {name}")
            print(f"[{env_var}]   Model: {_resolve_model_name(cfg['model'])}")
            print(f"[{env_var}]   Tools: {', '.join([str(t.get('type', t)) for t in agent_tools])}")
            print(f"[{env_var}]   Format: NEW Agents API (2.0.0b1+)")
        except Exception as ce:
            print(f"[{env_var}] FAILED to create {name}: {ce}")
            import traceback
            traceback.print_exc()
            
            # Use fallback local ID
            fallback_id = f"local_{env_var}"
            deployed_agents[env_var] = fallback_id
            statuses[env_var] = "fallback-creation-failed"
            print(f"[{env_var}] Using fallback: {fallback_id}")

    # Persist state (hash + id)
    new_state = {}
    terraform_state = {}  # Terraform-compatible format
    for cfg in agents_config:
        ev = cfg["env_var"]
        agent_id = deployed_agents.get(ev)
        
        # Skip agents marked as removed (no instructions field)
        if cfg.get("skip", False):
            new_state[ev] = {
                "id": "skipped",
                "hash": "N/A",
                "status": "skipped"
            }
            terraform_state[f"agent_{ev}_id"] = "skipped"
            continue
        
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
