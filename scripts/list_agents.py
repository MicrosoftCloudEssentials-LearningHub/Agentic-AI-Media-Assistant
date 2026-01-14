import os
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient

endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT")
project = os.getenv("AZURE_AI_PROJECT_NAME")
print(f"Endpoint: {endpoint}")
print(f"Project: {project}")

if not endpoint:
    raise SystemExit("AZURE_AI_PROJECT_ENDPOINT not set")

client = AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential())

try:
    agents = list(client.agents.list())
    print(f"Agents found: {len(agents)}")
    for agent in agents:
        print(f"- {agent.name} | id={getattr(agent, 'id', 'n/a')} | model={getattr(agent, 'model', 'n/a')}")
except Exception as exc:
    print("Failed to list agents:", exc)
    raise
