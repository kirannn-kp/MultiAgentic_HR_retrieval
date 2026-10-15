"""Preview regional web-source definitions locally. Azure is changed only with --apply."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mbpoc.config import get_settings  # noqa: E402
from mbpoc.regional import SOURCE_NAMES, load_catalog  # noqa: E402
from mbpoc.regional_retrieval import require_preview  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Explicitly create/attach regional web sources on the configured Azure KB")
    parser.add_argument("--update-sources", action="store_true", help="Also replace existing regional source allowlists; requires --apply")
    parser.add_argument("--output", type=Path, help="Save the local source-definition plan as JSON")
    args = parser.parse_args()
    if args.update_sources and not args.apply:
        parser.error("--update-sources requires --apply")
    catalog = load_catalog()
    definitions = catalog.web_definitions()
    for definition in definitions:
        count = len(definition["webParameters"]["domains"]["allowedDomains"])
        print(f"{definition['name']}: {count} approved URLs")
    if args.output:
        args.output.write_text(json.dumps(definitions, indent=2, ensure_ascii=False), encoding="utf-8")
    if not args.apply:
        print("Local preview only. No Azure calls or changes. Use --apply when ready.")
        return
    from mbpoc.infra import search_admin

    settings = get_settings()
    require_preview(settings)
    if not settings.search_endpoint:
        raise ValueError("Configure the Search endpoint first")
    kb = search_admin.get_resource(settings, "knowledgebases", settings.kb_name)
    if kb is None:
        raise ValueError("The selected KB must already exist; this command does not create another KB")
    pending = []
    for definition in definitions:
        existing = search_admin.get_resource(settings, "knowledgesources", definition["name"])
        if existing is not None and not args.update_sources:
            region = next(region for region, name in SOURCE_NAMES.items() if name == definition["name"])
            try:
                catalog.validate_web_definition(region, existing)
            except ValueError as exc:
                raise ValueError(f"{definition['name']} differs from the approved catalogue. Use --apply --update-sources to update deliberately.") from exc
            print(f"REUSED {definition['name']} (unchanged)")
        else:
            pending.append(definition)
    for definition in pending:
        search_admin._put(settings, "knowledgesources", definition["name"], definition)
    attached = {source["name"] for source in kb["knowledgeSources"]}
    additions = [{"name": definition["name"]} for definition in definitions if definition["name"] not in attached]
    if additions:
        kb["knowledgeSources"].extend(additions)
        search_admin._put(settings, "knowledgebases", settings.kb_name, kb)
    print("Regional sources attached to the same KB; other sources and agent definitions were not replaced.")
    print("Enable REGIONAL_RETRIEVAL_ENABLED=true, refresh hosted deployment if used, and restart the demo.")


if __name__ == "__main__":
    main()
