from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential
import os
from dotenv import load_dotenv
import json

load_dotenv()

credential = DefaultAzureCredential()
client = CosmosClient(os.environ['COSMOS_DB_ENDPOINT'], credential)
db = client.get_database_client(os.environ['COSMOS_DB_NAME'])
container = db.get_container_client(os.environ['COSMOS_DB_CONTAINER_NAME'])

# Count total items
count = list(container.query_items('SELECT VALUE COUNT(1) FROM c', enable_cross_partition_query=True))[0]
print(f'✓ Total items in Cosmos DB container: {count}')

# Get sample products
items = list(container.query_items('SELECT TOP 3 c.ProductID, c.ProductName, c.ProductCategory, c.Price FROM c ORDER BY c.ProductID', enable_cross_partition_query=True))
print(f'\n✓ Sample products:')
for item in items:
    print(f"  - {item['ProductID']}: {item['ProductName']} ({item['ProductCategory']}) - ${item['Price']}")

print('\n✓ Data successfully loaded into Cosmos DB!')
