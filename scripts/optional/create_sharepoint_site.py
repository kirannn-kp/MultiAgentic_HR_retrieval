"""Step 1 - create (or reuse) the SharePoint test site and upload data/sharepoint/*.md via Microsoft Graph.

Signs in with a device code (user with rights to create M365 groups / upload to the site).
Prints the site URL: put it into SHAREPOINT_SITE_URL in .env.
"""
from mbpoc.config import get_settings
from mbpoc.infra.graph import setup_sharepoint

if __name__ == "__main__":
    url = setup_sharepoint(get_settings())
    print(f"\nNext: set SHAREPOINT_SITE_URL={url} in .env (plus SHAREPOINT_APP_ID / SHAREPOINT_APP_SECRET / M365_TENANT_ID for the indexer).")
