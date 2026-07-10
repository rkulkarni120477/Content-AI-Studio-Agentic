# Content AI Studio + DIS Integration

## What changed

- DIS remains a separate backend folder: `dis_backend/`.
- CAS frontend now has `Workspace -> Source Library` as the first workspace tab.
- CAS backend proxies Source Library calls through `/api/v1/source-library/*`.
- Browser talks only to CAS. CAS calls DIS using `DIS_SERVICE_TOKEN`.
- DIS supports client-config-driven Source Library filters for AIM and Cengage.
- DIS adds retrieval endpoints for Style and CDD in addition to Blueprint and Course Generation.
- Style/reference guide documents are kept as one content unit instead of chunked into many pieces.

## Run locally

### 1. Run DIS backend

```bash
cd dis_backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export DIS_SERVICE_TOKEN=dev-dis-service-token
uvicorn main:app --host 0.0.0.0 --port 8010 --reload
```

### 2. Run CAS backend

```bash
cd content-ai-studio
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export DIS_ENABLED=true
export DIS_API_BASE_URL=http://localhost:8010/v1
export DIS_SERVICE_TOKEN=dev-dis-service-token
export DIS_DEFAULT_TENANT_ID=aim
export DIS_DEFAULT_CLIENT_ID=aim
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 3. Run CAS frontend

```bash
cd frontend
npm install
npm run dev
```

## New CAS APIs

- `GET /api/v1/source-library/ui-config`
- `GET /api/v1/source-library/documents`
- `GET /api/v1/source-library/documents/{job_id}/structure`
- `POST /api/v1/source-library/documents/upload`
- `POST /api/v1/source-library/retrieve/{purpose}`

## New DIS APIs

- `GET /v1/context/ui-config`
- `GET /v1/context/documents/library`
- `POST /v1/context/retrieve/style`
- `POST /v1/context/retrieve/cdd`
- existing: `POST /v1/context/retrieve/blueprint`
- existing: `POST /v1/context/retrieve/course-generation`

## Client-specific UI filters

AIM shows: Course, Block, Day, Topic.
Cengage shows: Course/Product, Chapter, Module/Section, Learning Objective.

These are configured in:

- `dis_backend/config/clients/aim.yaml`
- `dis_backend/config/clients/cengage.yaml`

Do not hard-code client filters in CAS frontend.
