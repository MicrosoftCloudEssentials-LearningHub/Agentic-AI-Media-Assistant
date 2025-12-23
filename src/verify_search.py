from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential
from dotenv import load_dotenv
import os

load_dotenv()

credential = AzureKeyCredential(os.environ['SEARCH_SERVICE_KEY'])
client = SearchClient(
    endpoint=os.environ['SEARCH_SERVICE_ENDPOINT'],
    index_name=os.environ['SEARCH_INDEX_NAME'],
    credential=credential
)

# Count documents
results = client.search(search_text='*', include_total_count=True)
total_count = results.get_count()
print(f'✓ Total documents in Azure AI Search index: {total_count}')

# Show sample products
print(f'\n✓ Sample products:')
for i, doc in enumerate(results):
    print(f"  - {doc['ProductID']}: {doc['ProductName']} ({doc['ProductCategory']}) - ${doc['ProductPrice']}")
    if i >= 2:
        break

print('\n✓ Data successfully loaded into Azure AI Search!')
