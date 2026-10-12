"""Step 2 - upload data/erd/*.md to the ERD blob container (created if missing). Needs 'Storage Blob Data Contributor'."""
from pathlib import Path

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

from mbpoc.config import ROOT, get_settings

if __name__ == "__main__":
    s = get_settings()
    if not s.storage_resource_id:
        raise SystemExit("Set STORAGE_RESOURCE_ID in .env (ARM id of the storage account).")
    account = s.storage_resource_id.rstrip("/").split("/")[-1]
    service = BlobServiceClient(f"https://{account}.blob.core.windows.net", credential=DefaultAzureCredential())
    container = service.get_container_client(s.erd_container)
    if not container.exists():
        container.create_container()
    for path in sorted(Path(ROOT / "data" / "erd").glob("*.md")):
        container.upload_blob(path.name, path.read_bytes(), overwrite=True)
        print(f"uploaded {path.name}")
    print(f"\nDone: {account}/{s.erd_container}. The Azure AI Search managed identity needs 'Storage Blob Data Reader' on this account.")
