import logging
import pandas as pd
import os
from azure.cosmos import CosmosClient, PartitionKey
from azure.identity import DefaultAzureCredential
from azure.core.exceptions import AzureError
from dotenv import load_dotenv

load_dotenv()

# CONFIGURATIONS - Replace with your actual values
COSMOS_ENDPOINT = os.environ.get("COSMOS_DB_ENDPOINT")
COSMOS_KEY = os.environ.get("COSMOS_DB_KEY")
DATABASE_NAME = os.environ.get("COSMOS_DB_NAME")
CONTAINER_NAME = os.environ.get("COSMOS_DB_CONTAINER_NAME")
SKIP_IF_EXISTS = os.environ.get("COSMOS_SKIP_IF_EXISTS", "true").lower() == "true"
FORCE_INGEST = os.environ.get("COSMOS_FORCE_INGEST", "false").lower() == "true"
TENANT_ID = os.environ.get("COSMOS_TENANT_ID", "zava-demo")

# Find CSV file - check both relative and absolute paths
_csv_candidates = [
    "data/updated_product_catalog(in).csv",  # From repo root
    "src/data/updated_product_catalog(in).csv",  # From terraform-infrastructure dir
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "updated_product_catalog(in).csv")),  # Absolute
]
CSV_FILE = None
for candidate in _csv_candidates:
    if os.path.exists(candidate):
        CSV_FILE = candidate
        break

if not CSV_FILE:
    raise FileNotFoundError(f"Product catalog CSV not found. Tried: {_csv_candidates}")

# Configure logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

def get_cosmos_client(endpoint: str | None, key: str | None = None):
    """Try to authenticate to Cosmos DB using DefaultAzureCredential first.
    
    If that fails, fall back to using the provided key.
    Returns a connected CosmosClient instance.
    """
    if not endpoint:
        raise ValueError("COSMOS_DB_ENDPOINT must be provided in environment variables")
    
    # Try AAD first
    try:
        logger.info("Attempting to authenticate to Cosmos DB using DefaultAzureCredential (AAD)...")
        credential = DefaultAzureCredential()
        client = CosmosClient(endpoint, credential=credential)
        
        # Perform a light operation to validate the credential
        _ = list(client.list_databases())
        logger.info("Authenticated to Cosmos DB with DefaultAzureCredential.")
        return client
    except AzureError as ex:
        logger.warning("AAD authentication failed: %s", ex)
    
    # Fallback to key
    if key:
        try:
            logger.info("Falling back to endpoint + key authentication for Cosmos DB...")
            client = CosmosClient(endpoint, key)
            # Validate key by a light operation
            _ = list(client.list_databases())
            logger.info("Authenticated to Cosmos DB with endpoint+key.")
            return client
        except Exception as ex:
            logger.error("Endpoint+key authentication failed: %s", ex)
            raise
    
    # If we reach here, both auth methods failed or no key provided
    raise RuntimeError("Failed to authenticate to Cosmos DB using DefaultAzureCredential and no valid COSMOS_DB_KEY was provided")

def main():
    # 1. Read data from CSV
    logger.info(f"Reading data from {CSV_FILE}...")
    df = pd.read_csv(CSV_FILE, encoding='utf-8', quoting=1)  # quoting=1 is csv.QUOTE_ALL
    
    # Create content for vector search
    df['content_for_vector'] = (
        df['ProductName'].fillna('').astype(str) + ' | ' +
        df['ProductCategory'].fillna('').astype(str) + ' | ' +
        df['ProductDescription'].fillna('').astype(str)
    )
    
    logger.info(f"Loaded {len(df)} products from CSV")
    
    # 2. Connect to Cosmos DB
    client = get_cosmos_client(COSMOS_ENDPOINT, COSMOS_KEY)
    
    if not DATABASE_NAME:
        raise ValueError("COSMOS_DB_NAME must be provided in environment variables")
    
    if not CONTAINER_NAME:
        raise ValueError("COSMOS_DB_CONTAINER_NAME must be provided in environment variables")
    
    database = client.create_database_if_not_exists(id=DATABASE_NAME)
    logger.info(f"Connected to database: {DATABASE_NAME}")
    
    container = database.create_container_if_not_exists(
        id=CONTAINER_NAME,
        partition_key=PartitionKey(path="/TenantId")
    )
    logger.info(f"Connected to container: {CONTAINER_NAME}")

    # Check existing item count (lightweight)
    existing_count = 0
    try:
        count_query = list(container.query_items(
            query="SELECT VALUE COUNT(1) FROM c",
            enable_cross_partition_query=True
        ))
        if count_query:
            raw_val = count_query[0]
            if isinstance(raw_val, dict):
                for k in ("$1", "count", "COUNT"):
                    if k in raw_val:
                        raw_val = raw_val[k]
                        break
            if isinstance(raw_val, (int, float, str)):
                existing_count = int(raw_val)
    except Exception as ex:
        logger.warning(f"Count query failed (will ignore): {ex}")

    if existing_count > 0 and SKIP_IF_EXISTS and not FORCE_INGEST:
        logger.info(
            f"Container already has {existing_count} items. Skipping ingestion (SKIP_IF_EXISTS=true, FORCE_INGEST=false)."
        )
        return
    
    # 3. Upload items
    logger.info("Starting data upload to Cosmos DB...")
    for idx, row in enumerate(df.itertuples(index=False), start=1):
        # Convert row to dict
        item = row._asdict()
        item['id'] = str(item['ProductID'])
        item['ProductID'] = str(item['ProductID'])
        item['TenantId'] = TENANT_ID
        item['ProductCategory'] = item.get('ProductCategory', 'uncategorized') or 'uncategorized'
        
        # Insert or update item
        container.upsert_item(body=item)
        if idx % 10 == 0:
            logger.info(f"Uploaded {idx}/{len(df)} products")
    
    logger.info(f"Successfully uploaded all {len(df)} products to Cosmos DB.")

if __name__ == "__main__":
    main()
