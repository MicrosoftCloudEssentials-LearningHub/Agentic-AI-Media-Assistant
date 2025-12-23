import logging
import os
from typing import Optional, List
from azure.cosmos import CosmosClient
from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential
from azure.identity import DefaultAzureCredential
from openai import AzureOpenAI
from dotenv import load_dotenv

load_dotenv()

# Configuration
COSMOS_ENDPOINT = os.environ.get("COSMOS_DB_ENDPOINT")
COSMOS_KEY = os.environ.get("COSMOS_DB_KEY")
DATABASE_NAME = os.environ.get("COSMOS_DB_NAME")
CONTAINER_NAME = os.environ.get("COSMOS_DB_CONTAINER_NAME")
SEARCH_ENDPOINT = os.environ.get("SEARCH_SERVICE_ENDPOINT")
SEARCH_KEY = os.environ.get("SEARCH_SERVICE_KEY")
INDEX_NAME = os.environ.get("SEARCH_INDEX_NAME", "products-index")
AZURE_OPENAI_ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_API_KEY = os.environ.get("AZURE_OPENAI_API_KEY")
EMBEDDING_DEPLOYMENT = os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-small")
AZURE_OPENAI_API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-01")
TENANT_ID = os.environ.get("COSMOS_TENANT_ID", "zava-demo")

# Configure logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

def get_cosmos_client(endpoint: str, key: str | None = None):
    """Get Cosmos DB client with AAD or key-based auth."""
    if not endpoint:
        raise ValueError("COSMOS_DB_ENDPOINT must be provided")
    
    # Try AAD first
    try:
        logger.info("Attempting to authenticate to Cosmos DB using DefaultAzureCredential (AAD)...")
        credential = DefaultAzureCredential()
        client = CosmosClient(endpoint, credential=credential)
        # Validate
        _ = list(client.list_databases())
        logger.info("Authenticated to Cosmos DB with DefaultAzureCredential.")
        return client
    except Exception as ex:
        logger.warning(f"AAD authentication failed: {ex}")
    
    # Fallback to key
    if key:
        try:
            logger.info("Falling back to key-based authentication for Cosmos DB...")
            client = CosmosClient(endpoint, key)
            # Validate
            _ = list(client.list_databases())
            logger.info("Authenticated to Cosmos DB with key.")
            return client
        except Exception as ex:
            logger.error(f"Key authentication failed: {ex}")
    
    raise RuntimeError("Failed to authenticate to Cosmos DB")

def _build_embedding_client() -> Optional[AzureOpenAI]:
    if AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY:
        return AzureOpenAI(
            api_key=AZURE_OPENAI_API_KEY,
            azure_endpoint=AZURE_OPENAI_ENDPOINT,
            api_version=AZURE_OPENAI_API_VERSION
        )
    logger.warning("Azure OpenAI credentials missing; skipping vector enrichment")
    return None


def _generate_embeddings(client: Optional[AzureOpenAI], payloads: List[dict[str, str]]):
    if not client:
        return

    batch_size = 16
    for i in range(0, len(payloads), batch_size):
        batch = payloads[i:i + batch_size]
        texts = [doc.get("content_for_vector", "") or "" for doc in batch]
        try:
            response = client.embeddings.create(model=EMBEDDING_DEPLOYMENT, input=texts)
            for doc, embedding in zip(batch, response.data):
                doc["content_vector"] = embedding.embedding
        except Exception as exc:
            logger.warning("Embedding batch failed: %s", exc)
            break


def upload_documents_to_search():
    """Read documents from Cosmos DB and upload directly to Azure AI Search."""
    
    # Connect to Cosmos DB
    cosmos_client = get_cosmos_client(COSMOS_ENDPOINT, COSMOS_KEY)
    database = cosmos_client.get_database_client(DATABASE_NAME)
    container = database.get_container_client(CONTAINER_NAME)
    
    # Get all documents from Cosmos DB
    logger.info(f"Reading documents from Cosmos DB container: {CONTAINER_NAME}...")
    query = "SELECT * FROM c"
    items = list(container.query_items(query=query, enable_cross_partition_query=True))
    logger.info(f"Retrieved {len(items)} documents from Cosmos DB")
    
    if len(items) == 0:
        logger.warning("No documents found in Cosmos DB container")
        return
    
    # Connect to Search
    if SEARCH_KEY:
        search_credential = AzureKeyCredential(SEARCH_KEY)
    else:
        search_credential = DefaultAzureCredential()
    
    search_client = SearchClient(endpoint=SEARCH_ENDPOINT, index_name=INDEX_NAME, credential=search_credential)
    
    # Prepare documents for upload
    documents = []
    for item in items:
        # Map Cosmos DB fields to Search index fields
        doc = {
            "id": str(item.get("id", item.get("ProductID"))),  # Use Cosmos id or ProductID
            "ProductID": str(item.get("ProductID")),
            "ProductName": item.get("ProductName"),
            "ProductCategory": item.get("ProductCategory"),
            "ProductDescription": item.get("ProductDescription"),
            "ProductPrice": float(item.get("Price", item.get("ProductPrice", 0.0))),
            "ProductImageURL": item.get("ImageUrl", item.get("ProductImageURL", "")),
            "content_for_vector": item.get("content_for_vector", ""),
            "TenantId": item.get("TenantId", TENANT_ID)
        }
        documents.append(doc)

    embedding_client = _build_embedding_client()
    _generate_embeddings(embedding_client, documents)
    
    # Upload documents in batches
    logger.info(f"Uploading {len(documents)} documents to Azure AI Search index: {INDEX_NAME}...")
    try:
        result = search_client.upload_documents(documents=documents)
        success_count = sum(1 for r in result if r.succeeded)
        failed_count = len(result) - success_count
        
        logger.info(f"Upload completed: {success_count} succeeded, {failed_count} failed")
        
        if failed_count > 0:
            for r in result:
                if not r.succeeded:
                    logger.error(f"Failed to upload document {r.key}: {r.error_message}")
        
        return success_count
    except Exception as e:
        logger.error(f"Failed to upload documents to search: {e}")
        raise

def main():
    logger.info("Starting data upload from Cosmos DB to Azure AI Search...")
    count = upload_documents_to_search()
    logger.info(f"Data upload completed successfully. {count} documents uploaded.")

if __name__ == "__main__":
    main()
