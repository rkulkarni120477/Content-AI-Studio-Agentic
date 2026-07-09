"""
Documents router — reference document library.

Streamlit equivalent: The document registry section in ``pages/style.py``
and the "Additional Source Materials from Library" multiselect in ``pages/generate.py``.

Documents are uploaded once and reused across multiple generations as
supplementary context injected into prompts.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db
from app.core.exceptions import NotFoundError
from app.schemas.common import PaginatedResponse
from app.schemas.document import DocumentContentResponse, DocumentListItem, DocumentRead, ParsedFileResponse

_log = logging.getLogger(__name__)
router = APIRouter()


def _get_document_or_404(db: Session, document_id: int):
    """Fetch a document by ID or raise HTTP 404."""
    from promptops_app.repositories import document_repository
    doc = document_repository.get_document_by_id(db, document_id)
    if doc is None:
        raise NotFoundError("Document", document_id)
    return doc


@router.get(
    "",
    response_model=PaginatedResponse[DocumentListItem],
    summary="List all active documents in the library",
    description="Used to populate the 'Additional Source Materials' multiselect on the Generate page.",
)
def list_documents(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> PaginatedResponse[DocumentListItem]:
    """Return documents in the global library (not scoped to a course or project)."""
    from promptops_app.repositories import document_repository

    documents = document_repository.list_active_documents(db, limit=500)
    total = len(documents)
    start = (page - 1) * page_size
    return PaginatedResponse.create(
        items=[DocumentListItem.model_validate(d) for d in documents[start: start + page_size]],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/parse",
    response_model=ParsedFileResponse,
    summary="Parse an uploaded file without saving to the library",
    description=(
        "Extracts text from PDF, DOCX, TXT, or XLSX for supplementary generation context. "
        "Matches Streamlit's in-form file upload on the Generate page."
    ),
)
async def parse_uploaded_file(
    file: UploadFile = File(...),
    source_type: str = Form(default="reference", description="guidelines | checklist | chapter | reference"),
    current_user=Depends(get_current_user),
) -> ParsedFileResponse:
    """Parse file bytes to text — used by the React Generate page supplementary uploads."""
    import io
    from promptops_app.parsers.file_parser import _parse_uploaded_file

    raw_bytes = await file.read()

    class _NamedBytesIO(io.BytesIO):
        def __init__(self, data, name):
            super().__init__(data)
            self.name = name

    name, content, err = _parse_uploaded_file(_NamedBytesIO(raw_bytes, file.filename))
    if err:
        from app.core.exceptions import ValidationError
        raise ValidationError(f"Could not parse file '{file.filename}': {err}")

    return ParsedFileResponse(name=name, content=content or "", source_type=source_type)


@router.post(
    "/upload",
    response_model=DocumentRead,
    status_code=201,
    summary="Upload a reference document to the library",
    description="Accepts PDF, DOCX, TXT, or XLSX. The file is parsed and stored as text.",
)
async def upload_document(
    file: UploadFile = File(...),
    source_type: str = Form(default="reference", description="reference | guidelines | chapter"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> DocumentRead:
    """
    Parse an uploaded file and add it to the document library.

    Replicates the file uploader in the Streamlit Style tab document registry.
    Uses the existing file_parser._parse_uploaded_file() for extraction.
    """
    import io
    from promptops_app.database import Document
    from promptops_app.parsers.file_parser import _parse_uploaded_file

    raw_bytes = await file.read()

    class _NamedBytesIO(io.BytesIO):
        def __init__(self, data, name):
            super().__init__(data)
            self.name = name

    name, content, err = _parse_uploaded_file(_NamedBytesIO(raw_bytes, file.filename))

    if err:
        from app.core.exceptions import ValidationError
        raise ValidationError(f"Could not parse file '{file.filename}': {err}")

    doc = Document(
        filename=name,
        content=content,
        file_type=(file.filename or "").rsplit(".", 1)[-1].lower(),
        doc_tag=source_type,
        status="active",
        uploaded_by=current_user.username,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    _log.info("document_uploaded  user=%s  doc_id=%d  name=%s",
              current_user.username, doc.id, doc.filename)
    return DocumentRead.model_validate(doc)


@router.get(
    "/{document_id}",
    response_model=DocumentRead,
    summary="Get document metadata",
)
def get_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> DocumentRead:
    """Return document metadata without content."""
    return DocumentRead.model_validate(_get_document_or_404(db, document_id))


@router.get(
    "/{document_id}/content",
    response_model=DocumentContentResponse,
    summary="Get the parsed text content of a document",
    description="Returns the full extracted text. Used when building generation context.",
)
def get_document_content(
    document_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> DocumentContentResponse:
    """Return the full parsed text of a document."""
    doc = _get_document_or_404(db, document_id)
    return DocumentContentResponse(id=doc.id, name=doc.filename, content=doc.content or "")


@router.delete(
    "/{document_id}",
    status_code=204,
    summary="Remove a document from the library",
)
def delete_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> None:
    """Soft-archive a document by marking it inactive."""
    doc = _get_document_or_404(db, document_id)
    doc.status = "archived"
    db.commit()
    _log.info("document_archived  user=%s  doc_id=%d", current_user.username, document_id)
