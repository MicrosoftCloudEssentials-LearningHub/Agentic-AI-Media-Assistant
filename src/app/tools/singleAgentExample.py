import os
import time
from azure.ai.inference import ChatCompletionsClient
from azure.core.credentials import AzureKeyCredential
from dotenv import load_dotenv

# Load environment variables (Azure endpoint, deployment, keys, etc.)
load_dotenv()

# Retrieve credentials (fallback across legacy/new variable names)
endpoint = (
    os.getenv("gpt_endpoint")
    or os.getenv("AZURE_OPENAI_ENDPOINT")
    or os.getenv("AZURE_AI_FOUNDRY_ENDPOINT")
    or os.getenv("AZURE_AI_PROJECT_ENDPOINT")
)
api_key = (
    os.getenv("gpt_api_key")
    or os.getenv("AZURE_OPENAI_API_KEY")
    or os.getenv("AZURE_AI_FOUNDRY_API_KEY")
)
deployment = (
    os.getenv("gpt_deployment")
    or os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT")
    or "gpt-4o-mini"
)

# Global client instance
client = None

def get_client():
    """Lazily initialize and return the MSFT Foundry client"""
    global client
    if client is None:
        # Graceful fallback if endpoint or key missing
        if not endpoint or not api_key:
            # Provide a stub-like response by using dummy client pattern
            # Instead of raising, log and use lightweight shim that returns explanatory text
            class _Shim:
                def complete(self, *_, **__):
                    class _Resp:
                        choices = [type("_C", (), {"message": type("_M", (), {"content": "Configuration error: missing endpoint or api key. Please ensure terraform apply populated .env with gpt_endpoint and gpt_api_key."})()})]
                    return _Resp()
            return _Shim()

        foundry_endpoint = endpoint.replace('.cognitiveservices.', '.services.ai.')
        if '.services.azure.com' in foundry_endpoint and '.services.ai.azure.com' not in foundry_endpoint:
            foundry_endpoint = foundry_endpoint.replace('.services.azure.com', '.services.ai.azure.com')
        if not foundry_endpoint.endswith('/models'):
            foundry_endpoint = f"{foundry_endpoint.rstrip('/')}/models"
        client = ChatCompletionsClient(endpoint=foundry_endpoint, credential=AzureKeyCredential(api_key))
    return client

def generate_response(text_input):
    start_time = time.time()
    """
    Input:
        text_input (str): The user's chat input.

    Output:
        response (str): A Markdown-formatted response from the agent.
    """
    
    # Get initialized client
    client = get_client()

    # Prepare the messages for MSFT Foundry
    messages = [
        {
            "role": "system",
            "content": """You are an AI assistant for Zava, a leading home improvement and DIY products company.

Your capabilities include:
- Providing expert advice on DIY projects, home improvement, repairs, and renovations
- Recommending products from Zava's extensive catalog (tools, materials, paint, hardware, etc.)
- Offering step-by-step guidance for various home projects
- Answering general questions about home maintenance, safety, and best practices
- Discussing design ideas, project planning, and cost estimation
- Providing information about Zava stores and services

Product Guidelines:
- For paint colors, we feature: blue, green, and white (but can discuss other options available)
- Recommend appropriate tools and materials for each project
- Suggest safety equipment when relevant

Store Information:
- Zava has locations nationwide
- For specific store availability, direct customers to our Miami flagship store
- Mention online ordering options when appropriate

Tone & Style:
- Be friendly, helpful, and encouraging
- Provide detailed, practical advice
- Ask clarifying questions when needed
- Be enthusiastic about DIY projects while emphasizing safety
- Feel free to engage in broader conversations about home improvement topics

You can discuss a wide range of topics related to home improvement, construction, design, and general DIY advice. Don't limit yourself to just product recommendations - provide comprehensive assistance!
            """
        },
        {
            "role": "user",
            "content": text_input
        }
    ]

    # Call MSFT Foundry chat API
    response = client.complete(
        model=deployment,
        messages=messages,
        max_tokens=10000,
        temperature=1.0,
        top_p=1.0,
        frequency_penalty=0,
        presence_penalty=0
    )
    
    end_sum = time.time()
    print(f"generate_response Execution Time: {end_sum - start_time} seconds")
    
    # Return response content
    return response.choices[0].message.content
