# CAS + DIS Final Local Run Guide

This package keeps CAS and DIS separate, but integrated:

- Frontend calls CAS only.
- CAS backend validates CAS JWT and calls DIS with the internal service token.
- DIS stores source files, extracted source content, source indexes, and generated Style/CDD/Blueprint copies in S3.
- CAS PostgreSQL keeps application state only: users, projects, clusters/categories, courses, active pointers, workflow, and export state.

## Final data ownership

```text
CAS PostgreSQL
  users / projects / clusters / courses
  active generated document IDs / workflow state / export state

DIS S3
  uploaded source files
  extracted clean source content
  source_index/source_list.json
  generated/style/*.json
  generated/cdd/*.json
  generated/blueprint/*.json
  generated_index/generated_list.json

OpenSearch optional
  source/generated chunks and embeddings for semantic retrieval
```

## 1. Start DIS backend

```powershell
cd "<repo>\dis_backend"
.\.venv\Scripts\Activate.ps1

$env:DIS_SERVICE_TOKEN="dev-dis-token"
$env:DIS_S3_BUCKET="content-ai-studio"
$env:DIS_S3_BASE_PREFIX="DIS"
$env:AWS_REGION="us-east-1"

uvicorn main:app --host 0.0.0.0 --port 8010 --reload
```

## 2. Start CAS backend

```powershell
cd "<repo>"
.\.venv\Scripts\Activate.ps1

$env:DIS_ENABLED="true"
$env:DIS_API_BASE_URL="http://127.0.0.1:8010/v1"
$env:DIS_SERVICE_TOKEN="dev-dis-token"
$env:DIS_API_TIMEOUT_SECONDS="300"

uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --reload-dir app --reload-dir promptops_app --reload-dir config
```

## 3. Start frontend

```powershell
cd "<repo>\frontend"
npm install
npm run dev
```

## Final workflow

1. Create/open CAS project. Project decides `client_id`.
2. Upload source docs in Source Library. Source list auto-loads from DIS/S3 every time the tab opens.
3. Style page selects DIS Source Library style documents only.
4. Understand/Refine creates generated Style and copies the body to DIS/S3.
5. CDD generation retrieves DIS CDD context and copies generated CDD to DIS/S3.
6. Blueprint generation retrieves DIS blueprint context and copies generated Blueprint to DIS/S3.
7. Generate retrieves active generated Style/CDD/Blueprint + matching source content from DIS.
8. Editor/Workflow/Export remain CAS-owned.

## Useful DIS-generated endpoints through CAS

```text
GET /api/v1/source-library/generated-documents?generated_type=style&search=<text>
GET /api/v1/source-library/generated-documents/{generated_doc_id}
POST /api/v1/source-library/generated-documents/{generated_doc_id}/activate
DELETE /api/v1/source-library/generated-documents/{generated_doc_id}
```

## Source Library behavior

- No visibility/restricted-content dropdown.
- Source documents are internal course-development sources.
- List is recent-first and S3-backed.
- Search and filters call CAS proxy, which calls DIS.

## Style behavior

- No Document Registry tab.
- Generated Styles list is recent-first.
- Search bar can find style by name, ID, description, or understanding preview.
- The UI reloads styles when the workflow opens; no manual refresh needed.

## Large Source File Viewing

This build stores the full extracted source content in DIS/S3, but CAS does not load a large file into one modal.

Source Library `View` now uses these paginated DIS-backed APIs through CAS:

- `GET /api/v1/source-library/documents/{job_id}/overview`
- `GET /api/v1/source-library/documents/{job_id}/content/pages?page=1&page_size=3`
- `GET /api/v1/source-library/documents/{job_id}/content/units`
- `GET /api/v1/source-library/documents/{job_id}/content/units/{unit_id}`
- `GET /api/v1/source-library/documents/{job_id}/search?q=keyword`

For 50MB+ files, users see overview, sections, search, and paginated page groups. Generation/retrieval still uses the full indexed/extracted content from DIS.
