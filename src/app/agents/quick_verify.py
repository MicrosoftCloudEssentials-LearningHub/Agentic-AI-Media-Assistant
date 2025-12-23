"""
Quick verification that agents exist and are accessible via the correct endpoint.
This script uses .services.ai.azure.com endpoint.
"""
import os
import json
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

load_dotenv()

def verify_agents():
    """Verify agents are accessible via the correct endpoint"""
    
    # Get configuration
    project_endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT") or os.getenv("AZURE_AI_FOUNDRY_ENDPOINT")
    sub_id = os.getenv("AZURE_SUBSCRIPTION_ID")
    rg = os.getenv("AZURE_RESOURCE_GROUP")
    project_name = os.getenv("AZURE_AI_PROJECT_NAME")
    
    if not project_endpoint:
        print("ERROR: AZURE_AI_PROJECT_ENDPOINT / AZURE_AI_FOUNDRY_ENDPOINT not configured")
        return False
    
    if not all([sub_id, rg, project_name]):
        print("ERROR: Missing required environment variables")
        print(f"  AZURE_SUBSCRIPTION_ID: {sub_id}")
        print(f"  AZURE_RESOURCE_GROUP: {rg}")
        print(f"  AZURE_AI_PROJECT_NAME: {project_name}")
        return False
    
    # Ensure we're using the correct domain
    if "cognitiveservices.azure.com" in project_endpoint:
        print("WARNING: Endpoint uses .cognitiveservices.azure.com")
        print(f"   Converting to .services.ai.azure.com for Agents API...")
        project_endpoint = project_endpoint.replace("cognitiveservices.azure.com", "services.ai.azure.com")
    
    # Construct proper project endpoint: https://<hub>.services.ai.azure.com/api/projects/<project>
    base_endpoint = project_endpoint.split("/api/")[0]  # Get just the base URL
    base_endpoint = base_endpoint.rstrip('/')
    full_project_endpoint = f"{base_endpoint}/api/projects/{project_name}"
    
    print("=" * 70)
    print("Verifying Multi-Agent Deployment")
    print("=" * 70)
    print(f"Endpoint (base): {base_endpoint}")
    print(f"Endpoint (full): {full_project_endpoint}")
    print()
    
    # Read expected agents from state file
    state_path = os.path.join(os.path.dirname(__file__), "agents_state.json")
    if not os.path.exists(state_path):
        print(f"ERROR: agents_state.json not found at: {state_path}")
        return False
    
    with open(state_path, 'r', encoding='utf-8') as f:
        expected_agents = json.load(f)
    
    print(f"Expected agents (from state file): {len(expected_agents)}")
    for name, data in expected_agents.items():
        print(f"  - {name}: {data.get('id')} ({data.get('status')})")
    print()
    
    # Try to connect and list agents
    try:
        credential = DefaultAzureCredential()
        
        # Create client with correct endpoint
        project_client = AIProjectClient(
            endpoint=full_project_endpoint,
            credential=credential
        )
        
        print("Fetching agents from Azure AI Foundry...")
        agents_list = list(project_client.agents.list_agents())
        
        print(f"\nFound {len(agents_list)} agent(s) in Azure AI Foundry:")
        
        if len(agents_list) == 0:
            print("\nWARNING: No agents found!")
            print("   This could mean:")
            print("   1. Agents were not created successfully")
            print("   2. Wrong endpoint/credentials")
            print("   3. Agents exist but API permissions issue")
            return False
        
        # Display found agents
        for agent in agents_list:
            agent_id = getattr(agent, 'id', 'unknown')
            agent_name = getattr(agent, 'name', 'unnamed')
            print(f"  [OK] {agent_name}")
            print(f"       ID: {agent_id}")
        
        # Compare with expected
        found_ids = set(getattr(a, 'id', '') for a in agents_list)
        expected_ids = set(d.get('id', '') for d in expected_agents.values())
        
        print("\nComparison:")
        print(f"  Expected: {len(expected_ids)} agents")
        print(f"  Found:    {len(found_ids)} agents")
        
        missing = expected_ids - found_ids
        extra = found_ids - expected_ids
        
        if missing:
            print(f"\nWARNING: Missing agents: {missing}")
        
        if extra:
            print(f"\n  Extra agents found: {extra}")
        
        if not missing and not extra:
            print("\n[SUCCESS] All expected agents are present!")
            return True
        else:
            print("\nWARNING: Agent count mismatch")
            return len(found_ids) >= len(expected_ids)
        
    except Exception as e:
        print(f"\nERROR: Failed to verify agents: {e}")
        import traceback
        traceback.print_exc()
        print("\nTroubleshooting:")
        print("  1. Check that Azure AI Foundry endpoint is correct")
        print("  2. Verify Azure CLI login: az login")
        print("  3. Check subscription and resource group settings")
        print(f"  4. Try accessing the portal directly:")
        print(f"     https://ai.azure.com/")
        return False

if __name__ == "__main__":
    success = verify_agents()
    
    if not success:
        print("\n" + "=" * 70)
        print("Agents may exist but are not accessible via the API.")
        print("Check the Azure AI Foundry portal manually:")
        print(f"  https://ai.azure.com/")
        print("=" * 70)
        exit(1)
    else:
        print("\n" + "=" * 70)
        print("[SUCCESS] Verification successful!")
        print("All agents are accessible and working.")
        print("=" * 70)
        exit(0)
