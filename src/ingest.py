"""
ingest.py — turn a directory of documents into typed JSON records.

Replaces the manifest-driven path in `extract_local_fast.run_fast_extraction`.
The document extractors themselves are unchanged and still do the real work;
what changes is how documents are found and typed. See `discover.py`.

Every record is written to `<out_dir>/<doc_id>.json`, and a `_manifest.json`
alongside them records what was found and where it came from. Nothing else in
the pipeline may look at the input tree after this point — if a later stage
needs a specific document, it resolves it through the manifest.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.discover import Doc, discover, workbook_contract_ref

MANIFEST_NAME = "_manifest.json"


def _pdf_record(doc: Doc) -> dict:
    """Run the typed extractor for a PDF, falling back to the generic one."""
    from src.extract_local_fast import EXTRACTORS, extract_generic, extract_ref

    if doc.doc_type == "reference_letter":
        data = extract_ref(doc.text, doc.doc_id, raw_text=doc.text)
    else:
        extractor = EXTRACTORS.get(doc.doc_type)
        if extractor is None:
            data = extract_generic(doc.text, doc.doc_id, doc.doc_type)
        else:
            data = extractor(doc.text, doc.doc_id)
    # The answer engine falls back to reading a figure straight out of a
    # document when no typed field holds it, so keep the full text.
    data["_text"] = doc.text
    return data


def _workbook_record(doc: Doc) -> dict:
    from src.extract_workbooks import (extract_ageing_workbook, extract_asset_register,
                                       extract_boq_workbook, extract_trial_balance)

    handlers = {
        "ageing_workbook":        extract_ageing_workbook,
        "trial_balance_workbook": extract_trial_balance,
        "asset_register_workbook": extract_asset_register,
        "boq_workbook":           extract_boq_workbook,
    }
    handler = handlers.get(doc.doc_type, extract_boq_workbook)
    data = handler(doc.path)
    if doc.doc_type == "boq_workbook":
        # The contract number lives in the Notes sheet, not the file name.
        data["contract_ref"] = workbook_contract_ref(doc.text, data.get("contract_ref", doc.doc_id))
    if doc.doc_type == "unknown_workbook":
        data["_doc_type"] = "unknown_workbook"
    return data


def ingest(docs_root: Path, out_dir: Path, verbose: bool = True) -> dict:
    """Discover, extract and persist every document under `docs_root`."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    docs = discover(Path(docs_root), verbose=verbose)
    manifest: dict[str, dict] = {}
    written = 0
    failures: list[tuple[str, str]] = []

    for doc in docs:
        try:
            data = _pdf_record(doc) if doc.kind == "pdf" else _workbook_record(doc)
        except Exception as exc:
            failures.append((str(doc.path), f"{type(exc).__name__}: {exc}"))
            continue

        # discover() owns identity; extractors must not override it.
        data["_doc_id"] = doc.doc_id
        data.setdefault("_doc_type", doc.doc_type)
        data["_source_file"] = str(doc.path)
        data["_sniffed_type"] = doc.doc_type

        with open(out_dir / f"{doc.doc_id}.json", "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False, default=str)
        written += 1
        manifest[doc.doc_id] = {"doc_type": doc.doc_type,
                                "source_file": str(doc.path),
                                "typed_by": doc.detail}

    with open(out_dir / MANIFEST_NAME, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)

    if verbose:
        print(f"  Extracted {written} documents to {out_dir}")
        if failures:
            print(f"  {len(failures)} extraction failure(s):")
            for path, err in failures[:10]:
                print(f"      {path}: {err}")
    return {"written": written, "failures": failures, "manifest": manifest}


def load_manifest(out_dir: Path) -> dict:
    path = Path(out_dir) / MANIFEST_NAME
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def find_source(out_dir: Path, doc_type: str) -> Path | None:
    """Locate the original file for a singleton document type."""
    for _, entry in sorted(load_manifest(out_dir).items()):
        if entry.get("doc_type") == doc_type:
            return Path(entry["source_file"])
    return None
