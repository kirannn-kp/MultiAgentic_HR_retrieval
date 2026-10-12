"""Refresh the documents behind the "SharePoint" knowledge source (re-run any time after changing documents).

  python scripts/sync_documents.py                   # sample documents in data/sharepoint (or the site in SHAREPOINT_SITE_URL, if set)
  python scripts/sync_documents.py --local           # force the sample documents
  python scripts/sync_documents.py --folder <dir>    # your own documents: put .docx / .pdf / .md / .txt files in a folder
  python scripts/sync_documents.py --graph           # read a SharePoint site through Microsoft Graph (needs a tenant that allows it)

The index mirrors the source: documents removed from the folder are removed from the index.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mbpoc.config import get_settings  # noqa: E402
from mbpoc.infra import sp_sync  # noqa: E402

if __name__ == "__main__":
    args = sys.argv[1:]
    if "--folder" in args:
        sp_sync.sync(get_settings(), "folder", args[args.index("--folder") + 1])
    else:
        sp_sync.sync(get_settings(), "local" if "--local" in args else "graph" if "--graph" in args else "auto")
