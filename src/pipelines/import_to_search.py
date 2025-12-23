import logging
import os
from azure.cosmos import CosmosClient
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexerClient
from azure.search.documents.indexes.models import (
    SearchIndexerDataSourceConnection,
    SearchIndexerDataContainer,
    SearchIndexer,
    FieldMapping,
    IndexingSchedule
)
from azure.core.credentials import AzureKeyCredential
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv
import time

load_dotenv()

# Configuration
COSMOS_ENDPOINT = os.environ.get("COSMOS_DB_ENDPOINT")
COSMOS_KEY = os.environ.get("COSMOS_DB_KEY")
DATABASE_NAME = os.environ.get("COSMOS_DB_NAME")
CONTAINER_NAME = os.environ.get("COSMOS_DB_CONTAINER_NAME")
SEARCH_ENDPOINT = os.environ.get("SEARCH_SERVICE_ENDPOINT")
SEARCH_KEY = os.environ.get("SEARCH_SERVICE_KEY")
INDEX_NAME = os.environ.get("SEARCH_INDEX_NAME", "products-index")
DATASOURCE_NAME = f"{INDEX_NAME}-datasource"
INDEXER_NAME = f"{INDEX_NAME}-indexer"

# Configure logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

def create_cosmos_datasource():
    """Create a data source connection to Cosmos DB."""
    
    if not SEARCH_KEY:
        credential = DefaultAzureCredential()
    else:
        credential = AzureKeyCredential(SEARCH_KEY)
    
    indexer_client = SearchIndexerClient(endpoint=SEARCH_ENDPOINT, credential=credential)
    
    # Create the data source connection
    container = SearchIndexerDataContainer(name=CONTAINER_NAME)
    
    data_source_connection = SearchIndexerDataSourceConnection(
        name=DATASOURCE_NAME,
        type="cosmosdb",
        connection_string=f"AccountEndpoint={COSMOS_ENDPOINT};AccountKey={COSMOS_KEY};Database={DATABASE_NAME}",
        container=container
    )
    
    try:
        logger.info(f"Creating data source: {DATASOURCE_NAME}...")
        result = indexer_client.create_or_update_data_source_connection(data_source_connection)
        logger.info(f"Data source '{result.name}' created successfully")
        return result
    except Exception as e:
        logger.error(f"Failed to create data source: {e}")
        raise

def create_indexer():
    """Create an indexer to import data from Cosmos DB to Azure AI Search."""
    
    if not SEARCH_KEY:
        credential = DefaultAzureCredential()
    else:
        credential = AzureKeyCredential(SEARCH_KEY)
    
    indexer_client = SearchIndexerClient(endpoint=SEARCH_ENDPOINT, credential=credential)
    
    # Create the indexer
    indexer = SearchIndexer(
        name=INDEXER_NAME,
        data_source_name=DATASOURCE_NAME,
        target_index_name=INDEX_NAME,
        field_mappings=[
            FieldMapping(source_field_name="id", target_field_name="id"),
            FieldMapping(source_field_name="ProductID", target_field_name="ProductID"),
            FieldMapping(source_field_name="ProductName", target_field_name="ProductName"),
            FieldMapping(source_field_name="ProductCategory", target_field_name="ProductCategory"),
            FieldMapping(source_field_name="ProductDescription", target_field_name="ProductDescription"),
            FieldMapping(source_field_name="ProductPrice", target_field_name="ProductPrice"),
            FieldMapping(source_field_name="ProductImageURL", target_field_name="ProductImageURL"),
            FieldMapping(source_field_name="TenantId", target_field_name="TenantId"),
            FieldMapping(source_field_name="content_for_vector", target_field_name="content_for_vector"),
        ]
    )
    
    try:
        logger.info(f"Creating indexer: {INDEXER_NAME}...")
        result = indexer_client.create_or_update_indexer(indexer)
        logger.info(f"Indexer '{result.name}' created successfully")
        return result
    except Exception as e:
        logger.error(f"Failed to create indexer: {e}")
        raise

def run_indexer():
    """Run the indexer to start data import."""
    
    if not SEARCH_KEY:
        credential = DefaultAzureCredential()
    else:
        credential = AzureKeyCredential(SEARCH_KEY)
    
    indexer_client = SearchIndexerClient(endpoint=SEARCH_ENDPOINT, credential=credential)
    
    try:
        logger.info(f"Running indexer: {INDEXER_NAME}...")
        indexer_client.run_indexer(INDEXER_NAME)
        logger.info("Indexer started successfully")
        
        # Wait for indexer to complete
        logger.info("Waiting for indexer to complete...")
        for i in range(30):  # Wait up to 5 minutes
            time.sleep(10)
            status = indexer_client.get_indexer_status(INDEXER_NAME)
            last_result = status.last_result
            
            if last_result:
                logger.info(f"Indexer status: {last_result.status}")
                if last_result.status == "success":
                    logger.info(f"Indexer completed successfully. Indexed {last_result.items_processed} items.")
                    return
                elif last_result.status == "transientFailure" or last_result.status == "persistentFailure":
                    logger.error(f"Indexer failed: {last_result.error_message}")
                    raise Exception(f"Indexer failed: {last_result.error_message}")
        
        logger.warning("Indexer is still running after timeout")
    except Exception as e:
        logger.error(f"Failed to run indexer: {e}")
        raise

def main():
    # Step 1: Create Cosmos DB data source
    create_cosmos_datasource()
    
    # Step 2: Create indexer
    create_indexer()
    
    # Step 3: Run indexer
    run_indexer()
    
    logger.info("Data import to Azure AI Search completed successfully")

if __name__ == "__main__":
    main()
