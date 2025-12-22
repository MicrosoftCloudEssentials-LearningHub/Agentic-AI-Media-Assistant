"""Local agent initializer stub.

This replaces remote Microsoft Foundry agent creation with deterministic
pseudo agent IDs for environments where the Agents API is unavailable.
It preserves the AGENT_ID: output pattern used by Terraform provisioner
scripts so existing parsing logic continues to work.
"""
import os

def initialize_local_agent(env_var_name: str, name: str) -> str:
    pseudo_id = f"asst_local_{env_var_name}".replace('-', '_')
    # Persist to environment for current process (optional)
    os.environ[env_var_name] = pseudo_id
    print("=" * 60)
    print(f"Local pseudo agent ready: {name}")
    print(f"Environment Variable: {env_var_name}")
    print(f"AGENT_ID:{pseudo_id}")
    print("=" * 60)
    return pseudo_id

# Backwards compatibility name
def initialize_agent(**kwargs):  # type: ignore
    return initialize_local_agent(kwargs.get("env_var_name", "unknown"), kwargs.get("name", "Unnamed Agent"))
