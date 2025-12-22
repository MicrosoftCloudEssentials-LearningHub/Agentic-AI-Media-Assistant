import logging
import os
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchIndex,
    SearchField,
    SearchFieldDataType,
    SimpleField,
    SearchableField,
    VectorSearch,
    HnswAlgorithmConfiguration,
    VectorSearchProfile,
    AzureOpenAIVectorizer,
    AzureOpenAIVectorizerParameters
)
from azure.core.credentials import AzureKeyCredential
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

load_dotenv()

# Configuration
SEARCH_ENDPOINT = os.environ.get("SEARCH_SERVICE_ENDPOINT")
SEARCH_KEY = os.environ.get("SEARCH_SERVICE_KEY")
INDEX_NAME = os.environ.get("SEARCH_INDEX_NAME", "products-index")
AZURE_OPENAI_ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_API_KEY = os.environ.get("AZURE_OPENAI_API_KEY")
EMBEDDING_DEPLOYMENT = os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-small")

# Configure logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

def create_search_index():
    """Create Azure AI Search index with vector search capabilities."""
    
    if not SEARCH_ENDPOINT:
        raise ValueError("SEARCH_SERVICE_ENDPOINT must be provided in environment variables")
    
    # Create client
    try:
        logger.info("Attempting to create Search Index Client...")
        if SEARCH_KEY:
            credential = AzureKeyCredential(SEARCH_KEY)
        else:
            credential = DefaultAzureCredential()
        
        index_client = SearchIndexClient(endpoint=SEARCH_ENDPOINT, credential=credential)
        logger.info("Search Index Client created successfully")
    except Exception as e:
        logger.error(f"Failed to create Search Index Client: {e}")
        raise
    
    # Define the index fields
    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True, filterable=True),
        SimpleField(name="ProductID", type=SearchFieldDataType.String, filterable=True),
        SearchableField(name="ProductName", type=SearchFieldDataType.String, searchable=True),
        SearchableField(name="ProductCategory", type=SearchFieldDataType.String, searchable=True, filterable=True, facetable=True),
        SearchableField(name="ProductDescription", type=SearchFieldDataType.String, searchable=True),
        SimpleField(name="ProductPrice", type=SearchFieldDataType.Double, filterable=True, sortable=True),
        SimpleField(name="ProductImageURL", type=SearchFieldDataType.String),
        SearchableField(name="content_for_vector", type=SearchFieldDataType.String, searchable=True),
        SearchField(
            name="content_vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=1536,  # text-embedding-3-small dimensions
            vector_search_profile_name="vector-profile"
        )
    ]
    
    # Configure vector search
    vector_search = VectorSearch(
        algorithms=[
            HnswAlgorithmConfiguration(name="hnsw-algorithm")
        ],
        profiles=[
            VectorSearchProfile(
                name="vector-profile",
                algorithm_configuration_name="hnsw-algorithm",
                vectorizer_name="openai-vectorizer"
            )
        ],
        vectorizers=[
            AzureOpenAIVectorizer(
                vectorizer_name="openai-vectorizer",
                parameters=AzureOpenAIVectorizerParameters(
                    resource_url=AZURE_OPENAI_ENDPOINT,
                    deployment_name=EMBEDDING_DEPLOYMENT,
                    model_name="text-embedding-3-small",  # Required in API version 2025-09-01
                    api_key=AZURE_OPENAI_API_KEY
                )
            )
        ]
    )
    
    # Create the search index
    index = SearchIndex(
        name=INDEX_NAME,
        fields=fields,
        vector_search=vector_search
    )
    
    try:
        logger.info(f"Creating search index: {INDEX_NAME}...")
        result = index_client.create_or_update_index(index)
        logger.info(f"Search index '{result.name}' created successfully")
        return result
    except Exception as e:
        logger.error(f"Failed to create search index: {e}")
        raise

def main():
    create_search_index()
    logger.info("Search index creation completed successfully")

if __name__ == "__main__":
    main()
