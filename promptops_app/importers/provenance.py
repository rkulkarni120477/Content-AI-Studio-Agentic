"""Provenance map CRUD for the IMSCC importer.

Records the Canvas-item ↔ CAS-entity mapping (``import_provenance`` table, added
in Session 0) so a later export can reuse the original Canvas identifiers for a
high-fidelity round-trip (Session 7). Append/read only — like the rest of the
course graph, ids are set manually (no DB-level FK, no cascade). See
reverse_cas.md §S3.
"""

from __future__ import annotations

from promptops_app.database import ImportProvenance

# canvas_type is constrained to these by the column comment (page | quiz | module).
CANVAS_PAGE = "page"
CANVAS_QUIZ = "quiz"
CANVAS_MODULE = "module"

# cas_entity_type
CAS_BLOCK = "block"
CAS_MODULE = "module"


def record(
    db,
    *,
    import_id: int,
    canvas_identifier: str,
    canvas_type: str,
    cas_entity_type: str,
    cas_entity_id: int,
) -> ImportProvenance:
    """Add one provenance row (not committed — the caller batches commits).

    ``canvas_identifier`` is the stable Canvas ``identifier`` carried on the
    ``ICourse`` items as ``provenance_id``.
    """
    row = ImportProvenance(
        import_id=import_id,
        canvas_identifier=canvas_identifier or "",
        canvas_type=canvas_type,
        cas_entity_type=cas_entity_type,
        cas_entity_id=cas_entity_id,
    )
    db.add(row)
    return row


def list_for_import(db, import_id: int) -> list[ImportProvenance]:
    """Return every provenance row captured for an import, oldest first."""
    return (
        db.query(ImportProvenance)
        .filter(ImportProvenance.import_id == import_id)
        .order_by(ImportProvenance.id.asc())
        .all()
    )


def block_identifier_map(db, import_id: int) -> dict[int, str]:
    """Return ``{block_id: canvas_identifier}`` for an import's block provenance.

    Used by provenance-aware export (S7) to reuse the original Canvas item
    identifiers so a re-import maps back to the same items. Empty dict when the
    import has no block provenance (export then falls back to fresh ids).
    """
    rows = (
        db.query(ImportProvenance)
        .filter(
            ImportProvenance.import_id == import_id,
            ImportProvenance.cas_entity_type == CAS_BLOCK,
        )
        .all()
    )
    return {r.cas_entity_id: r.canvas_identifier for r in rows if r.canvas_identifier}
