# Content AI Studio — Local Execution Guide

Step-by-step instructions to run this project locally for development.

## 1. Prerequisites

- Python 3.12
- Node.js (for the frontend)
- PostgreSQL running locally (or accessible via `DATABASE_URL`)
- Redis running locally (needed for Celery background jobs)

## 2. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` and fill in real values, at minimum:
- `JWT_SECRET_KEY` — generate with `python -c "import secrets; print(secrets.token_hex(32))"`
- `DATABASE_URL` — your Postgres connection string
- `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`
- API keys you intend to use (`OPENAI_API_KEY`, AWS Bedrock creds, `COPYLEAKS_*`) as needed

## 3. Set up Python environment (backend)

```bash
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

## 4. Run database migrations

```bash
alembic upgrade head
```

## 5. Start Redis

Make sure Redis is running on the host/port set in `REDIS_URL` (e.g. via Docker: `docker run -p 6379:6379 redis:7-alpine`, or a local Redis install).

## 6. Start the backend API

```bash
python -m uvicorn app.main:app --reload --port 8000
```

- API base: http://127.0.0.1:8000
- Swagger docs: http://127.0.0.1:8000/docs

## 7. Start the Celery worker (for background generation jobs)

In a separate terminal, with the venv activated:

```bash
celery -A promptops_app.celery_app worker --loglevel=info --concurrency=2
```

## 8. Run the frontend

In a separate terminal:

```bash
cd frontend
npm install
npm run dev
```

- Frontend dev server: http://localhost:5173 (Vite default)
- Confirm `ALLOWED_ORIGINS` in `.env` includes this URL.

## 9. Verify

- Open http://localhost:5173 in the browser — frontend should load and call the backend at port 8000.
- Open http://127.0.0.1:8000/docs to confirm the API and OpenAPI schema load correctly.

## Alternative: Run everything via Docker Compose

```bash
docker-compose up -d --build
```

This starts Redis, the Celery worker, and the API server as containers (see `docker-compose.yml` for exact ports/env vars required). The frontend still needs to be run separately via `npm run dev`.


## Simple approach: ############################

1) Clone repo 
2) Make sure docker desktop is running on your pc
3) Install node js on system
4) take .env from me it will be required
5) docker-compose up -d --build -> to install dependencies
6) cd Content-AI-STUDIO -> python -m uvicorn app.main:app --reload --port 8000  (in vscode cmd terminal)
7) cd frontend ->
npm install
npm run dev (in new terminal, it will execute the frontend)

## Restart ################################3
 When you'd actually need to rebuild/restart manually:
  - Changed requirements.txt, pyproject.toml, or the Dockerfile → rebuild: docker compose up -d --build api
  - Changed docker-compose.yml or .env → docker compose up -d (recreates affected containers)
  - Container seems stuck/not picking up reload → docker restart contentai_api

## Start fresh project ##############################
 1. Backend + supporting services (Docker) — from the project root:
  docker compose up -d --build
  This builds/starts everything: api (FastAPI, port 8000, hot-reload), promptops (Streamlit, port 8501), celery_worker, api_server
  (Copyleaks webhook, port 8502), and redis. Postgres isn't in this compose file — you're using the remote RDS instance from
  .env's DATABASE_URL.

  Check it's healthy:
  docker compose ps
  curl http://localhost:8000/api/v1/health

  2. Frontend — in a separate terminal:
  cd frontend
  npm install
  npm run dev
  Vite will print the local URL (typically http://localhost:5173). Open that in your browser.

  That's it — open the Vite URL, log in, and you're running fresh. For day-to-day work after this initial start, you don't need to
  redo any of this; just edit files and hard-refresh the browser per what I gave you earlier.

  To stop everything later: docker compose down (add -v only if you intentionally want to wipe the redis volume too).