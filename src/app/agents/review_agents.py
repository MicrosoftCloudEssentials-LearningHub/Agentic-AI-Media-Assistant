"""
Review deployed Azure AI Foundry Agents using NEW Agents API (2.0.0b1+).

Lists all deployed agents in the Azure AI Project and outputs summary to JSON.
"""
import os
import json
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

load_dotenv()

def review_deployed_agents():
    """List and review all deployed agents in Azure AI Foundry."""
    
    # Get project connection details
    subscription_id = os.getenv("AZURE_SUBSCRIPTION_ID")
    resource_group = os.getenv("AZURE_RESOURCE_GROUP")
    project_name = os.getenv("AZURE_AI_PROJECT_NAME")
    
    if not all([subscription_id, resource_group, project_name]):
        print("ERROR: Missing required environment variables:")
        print(f"  AZURE_SUBSCRIPTION_ID: {subscription_id}")
        print(f"  AZURE_RESOURCE_GROUP: {resource_group}")
        print(f"  AZURE_AI_PROJECT_NAME: {project_name}")
        return
    
    try:
        # Initialize project client
        project_client = AIProjectClient(
            credential=DefaultAzureCredential(),
            subscription_id=subscription_id,
            resource_group_name=resource_group,
            project_name=project_name
        )
        
        print(f"Connected to project: {project_name}")
        print(f"Resource Group: {resource_group}")
        print(f"Subscription: {subscription_id}\n")
        
        # List all agents
        agents = []
        try:
            agent_list = project_client.agents.list()
            
            for agent in agent_list:
                agent_info = {
                    "name": getattr(agent, 'name', 'Unknown'),
                    "id": getattr(agent, 'id', 'Unknown'),
                    "model": getattr(agent, 'model', 'Unknown'),
                    "created_at": str(getattr(agent, 'created_at', 'Unknown')),
                    "description": getattr(agent, 'description', '')
                }
                agents.append(agent_info)
                
                print(f"Agent: {agent_info['name']}")
                print(f"  ID: {agent_info['id']}")
                print(f"  Model: {agent_info['model']}")
                print(f"  Created: {agent_info['created_at']}")
                if agent_info['description']:
                    print(f"  Description: {agent_info['description']}")
                print()
        
        except Exception as e:
            print(f"Warning: Could not list agents: {e}")
            print("This may be normal if no agents have been deployed yet.\n")
        
        # Create summary
        summary = {
            "project": {
                "name": project_name,
                "resource_group": resource_group,
                "subscription_id": subscription_id
            },
            "agent_count": len(agents),
            "agents": agents
        }
        
        # Save to JSON file
        output_file = "agents_review.json"
        with open(output_file, 'w') as f:
            json.dump(summary, f, indent=2)
        
        print(f"✓ Review complete - saved to {output_file}")
        print(f"Total agents deployed: {len(agents)}")
        
    except Exception as e:
        print(f"ERROR during review: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    review_deployed_agents()
