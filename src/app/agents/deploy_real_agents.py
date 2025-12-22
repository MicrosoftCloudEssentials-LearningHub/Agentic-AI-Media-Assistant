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

def deploy_agents():
    """Deploy or update agents idempotently, emitting structured JSON for Terraform."""

    project_endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT") or os.getenv("AZURE_AI_FOUNDRY_ENDPOINT")
    if not project_endpoint:
        print("ERROR: AZURE_AI_PROJECT_ENDPOINT / AZURE_AI_FOUNDRY_ENDPOINT not configured")
        sys.exit(0)  # Do not hard fail; allow Terraform run to proceed

    print("=" * 70)
    print("Idempotent Multi-Agent Provisioning - Azure AI Foundry")
    print("=" * 70)
    print(f"Project Endpoint: {project_endpoint}")
    print()

    # Try to construct connection string if available
    project_connection_string = os.getenv("AZURE_AI_PROJECT_CONNECTION_STRING")
    if not project_connection_string:
        sub_id = os.getenv("AZURE_SUBSCRIPTION_ID")
        rg = os.getenv("AZURE_RESOURCE_GROUP")
        project_name = os.getenv("AZURE_AI_PROJECT_NAME")
        location = os.getenv("AZURE_LOCATION")
        
        if sub_id and rg and project_name and location:
            project_connection_string = f"{location}.api.azureml.ms;subscription_id={sub_id};resource_group={rg};project_name={project_name}"
            print(f"Constructed connection string: {project_connection_string}")

    # Agent config definitions
    agents_config = [
        {
            "name": "Zava Media Orchestrator",
            "env_var": "orchestrator",
            "instructions": (
                "You are the Zava Media Orchestrator. Your job is to analyze user requests related to image and video processing and route them to the appropriate specialist agent. "
                "- If the user wants to crop an image or object, delegate to the 'cropping_agent'. "
                "- If the user wants to change the background, delegate to the 'background_agent'. "
                "- If the user wants to create a new thumbnail or image, delegate to the 'thumbnail_generator'. "
                "- If the user wants to create a video, delegate to the 'video_agent'. "
                "- For general questions, answer them yourself."
            ),
            "model": "gpt-4o-mini"
        },
        {
            "name": "Cropping Specialist",
            "env_var": "cropping_agent",
            "instructions": (
                "You are the Cropping Specialist. Your task is to identify objects in images and provide cropping coordinates or cropped images. "
                "You use advanced vision models to detect subjects."
            ),
            "model": "gpt-4o-mini"
        },
        {
            "name": "Background Specialist",
            "env_var": "background_agent",
            "instructions": (
                "You are the Background Specialist. Your task is to remove or replace backgrounds in images. "
                "You can create new backgrounds based on text descriptions."
            ),
            "model": "gpt-4o-mini"
        },
        {
            "name": "Thumbnail Generator",
            "env_var": "thumbnail_generator",
            "instructions": (
                "You are the Thumbnail Generator. Your task is to create eye-catching video thumbnails. "
                "You combine images, text, and effects to maximize click-through rates."
            ),
            "model": "gpt-4o-mini"
        },
        {
            "name": "Video Agent",
            "env_var": "video_agent",
            "instructions": (
                "You are the Video Agent. Your task is to create and process video content. "
                "You can generate videos from prompts, edit existing videos, and provide video recommendations."
            ),
            "model": "gpt-4o-mini"
        }
    ]

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
        
        # Use DefaultAzureCredential for authentication
        credential = DefaultAzureCredential()
        
        # Get required environment variables
        sub_id = os.getenv("AZURE_SUBSCRIPTION_ID")
        rg = os.getenv("AZURE_RESOURCE_GROUP")
        project_name = os.getenv("AZURE_AI_PROJECT_NAME")
        
        if not all([sub_id, rg, project_name]):
            raise ValueError("Missing required environment variables: AZURE_SUBSCRIPTION_ID, AZURE_RESOURCE_GROUP, AZURE_AI_PROJECT_NAME")
        
        # Fix endpoint domain if needed (Cognitive Services -> AI Services)
        if project_endpoint and "cognitiveservices.azure.com" in project_endpoint:
            print(f"Converting endpoint domain: cognitiveservices.azure.com -> services.ai.azure.com")
            project_endpoint = project_endpoint.replace("cognitiveservices.azure.com", "services.ai.azure.com")
            os.environ["AZURE_AI_PROJECT_ENDPOINT"] = project_endpoint
        
        # Construct proper project endpoint: https://<hub>.services.ai.azure.com/api/projects/<project>
        # Remove any existing path segments first
        base_endpoint = project_endpoint.split("/api/")[0]  # Get just the base URL
        base_endpoint = base_endpoint.rstrip('/')
        
        # Now add the proper API path with project name
        full_project_endpoint = f"{base_endpoint}/api/projects/{project_name}"
        
        print(f"Project Endpoint (base): {base_endpoint}")
        print(f"Project Endpoint (full): {full_project_endpoint}")
        print(f"Subscription: {sub_id}")
        print(f"Resource Group: {rg}")
        print(f"Project Name: {project_name}")
        
        # Initialize AIProjectClient with endpoint and credential
        # The SDK requires the full project endpoint
        project_client = AIProjectClient(
            endpoint=full_project_endpoint,
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
                            model=cfg["model"], 
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
                model=cfg["model"], 
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
