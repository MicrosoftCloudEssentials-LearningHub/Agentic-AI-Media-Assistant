#!/usr/bin/env python3
"""
Read agents state script for Terraform external data source.

This script reads the agent IDs from a state file created by deploy_real_agents.py
and returns them in JSON format for Terraform to use.
"""

import json
import os
import sys
from pathlib import Path

def read_agents_state():
    """Read agent state from file and return as JSON."""
    
    # Look for agents state file in multiple locations
    possible_locations = [
        ".terraform/terraform_agents_state.json",  # New Terraform-compatible format (preferred)
        ".terraform/agents_state.json",            # Original deploy script format
        "../src/agents_state.json",
        "../src/.agents_state.json", 
        "agents_state.json",
        ".agents_state.json"
    ]
    
    agents_state = None
    state_file_path = None
    
    # Try to find the agents state file
    for location in possible_locations:
        if os.path.exists(location):
            state_file_path = location
            break
    
    if state_file_path:
        try:
            with open(state_file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            # Handle both new format (direct agent IDs) and old format (nested structure)
            if isinstance(data, dict):
                # Check if this is the new Terraform-compatible format with direct agent IDs
                if any(key.startswith("agent_") and key.endswith("_id") for key in data.keys()):
                    agents_state = data
                else:
                    # Convert old nested format to new flat format
                    agents_state = {}
                    for agent_name, agent_data in data.items():
                        if isinstance(agent_data, dict) and 'id' in agent_data:
                            agents_state[f"agent_{agent_name}_id"] = agent_data['id']
                        elif isinstance(agent_data, str):
                            agents_state[f"agent_{agent_name}_id"] = agent_data
                            
        except (json.JSONDecodeError, IOError) as e:
            print(f"Error reading agents state from {state_file_path}: {e}", file=sys.stderr)
            agents_state = None
    
    # If we couldn't read the state file, use default local agent IDs
    if not agents_state:
        agents_state = {
            "agent_cora_id": "asst_local_cora",
            "agent_interior_designer_id": "asst_local_interior_design", 
            "agent_inventory_agent_id": "asst_local_inventory",
            "agent_customer_loyalty_id": "asst_local_customer_loyalty",
            "agent_cart_manager_id": "asst_local_cart_manager"
        }
        
        # Write default state to file for future reference
        try:
            os.makedirs("../src", exist_ok=True)
            with open("../src/agents_state.json", 'w', encoding='utf-8') as f:
                json.dump(agents_state, f, indent=2)
        except IOError:
            pass  # Ignore if we can't write the file
    
    # Ensure all required keys exist with defaults
    required_keys = [
        "agent_orchestrator_id",
        "agent_cropping_agent_id", 
        "agent_background_agent_id",
        "agent_thumbnail_generator_id"
    ]
    
    # Only keep the required keys for media agents
    filtered_state = {}
    for key in required_keys:
        if key in agents_state:
            filtered_state[key] = agents_state[key]
        else:
            # Generate a default local agent ID
            agent_name = key.replace("agent_", "").replace("_id", "")
            filtered_state[key] = f"asst_local_{agent_name}"
    
    return filtered_state

def main():
    """Main function for Terraform external data source."""
    try:
        # Read the agents state
        result = read_agents_state()
        
        # Output as JSON for Terraform
        print(json.dumps(result))
        return 0
        
    except Exception as e:
        # In case of any error, output default values
        print(f"Error in read_agents_state.py: {e}", file=sys.stderr)
        
        default_result = {
            "agent_orchestrator_id": "asst_local_orchestrator",
            "agent_cropping_agent_id": "asst_local_cropping_agent",
            "agent_background_agent_id": "asst_local_background_agent", 
            "agent_thumbnail_generator_id": "asst_local_thumbnail_generator"
        }
        
        print(json.dumps(default_result))
        return 0

if __name__ == "__main__":
    sys.exit(main())