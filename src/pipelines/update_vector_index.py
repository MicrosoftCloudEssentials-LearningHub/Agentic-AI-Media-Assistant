import os, sys, json, hashlib
from dotenv import load_dotenv
load_dotenv()

CATALOG_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'updated_product_catalog(in).csv')

def hash_file(path):
    h = hashlib.sha256()
    with open(path,'rb') as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()

def main():
    if not os.path.exists(CATALOG_PATH):
        print("Catalog file missing; skipping vector update.")
        return
    file_hash = hash_file(CATALOG_PATH)
    print(f"Vector index update triggered. Catalog SHA256={file_hash}")
    # Placeholder for embedding + index update logic
    # Would: read rows, call embedding deployment, build JSON batch, push to search index
    print("(Stub) Generate embeddings using deployment 'text-embedding-3-small' and upsert to Azure AI Search index 'products-index-vectors'.")
    result = {"status":"stub", "hash": file_hash}
    print(json.dumps(result))

if __name__ == '__main__':
    main()