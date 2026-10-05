# SQLite Local Development Setup Guide

## Overview
This project has been configured to use SQLite for local development, replacing the production PostgreSQL setup. This guide covers the migration and local development workflow.

## What's Changed

### 1. Configuration Files Updated
- **`.env`** - Created with SQLite configuration (new file)
- **`.env.example`** - Updated DATABASE_URL to use SQLite
- **`app/core/database.py`** - Enhanced with SQLite PRAGMA settings (foreign keys)

### 2. Database Setup
Two SQLite databases are now created:
- **`content_ai.db`** - Main application database
- **`phoenix.db`** - Phoenix LLM tracing database

### 3. Setup Scripts Created
- **`scripts/setup_sqlite_for_dev.py`** - Main setup script (already run)
- **`scripts/convert_pg_to_sqlite.py`** - PostgreSQL dump converter (requires pg_dump tools)

## Current Status ✓

The following have been completed:
- ✓ SQLite databases created (`content_ai.db`, `phoenix.db`)
- ✓ Configuration files updated (`.env` ready to use)
- ✓ Foreign key support enabled in database.py
- ✓ SQLAlchemy configured for SQLite

## Quick Start for Local Development

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Run Database Migrations (Optional)
If you want to update the schema to match the latest code:
```bash
python -m alembic upgrade head
```

### 3. Start the Backend
```bash
python -m uvicorn app.main:app --reload --port 8000
```

The app will use the SQLite database defined in `.env`:
```
DATABASE_URL=sqlite:///./content_ai.db
```

### 4. Start the Frontend (in another terminal)
```bash
cd frontend
npm install
npm run dev
```

The frontend will run on `http://localhost:5173`

## Database Files

SQLite stores everything in single files:
```
C:\...\Content-AI-Studio-Agentic\
├── content_ai.db          ← Main app database
├── phoenix.db             ← Tracing database
└── .env                   ← Configuration
```

These files are git-ignored (in `.gitignore`).

## Key Differences from PostgreSQL

### Connection URL Format
```env
# PostgreSQL (production)
DATABASE_URL=postgresql+psycopg2://user:password@localhost:5432/db

# SQLite (local development)
DATABASE_URL=sqlite:///./content_ai.db
```

### No Connection Pooling
SQLite handles concurrency differently than PostgreSQL. The app automatically detects SQLite and skips connection pool configuration.

### Foreign Keys
Foreign key constraints are enabled via SQLite PRAGMA in `app/core/database.py`:
```python
PRAGMA foreign_keys=ON
```

### No Redis/Celery Required
For local development, you can skip Redis/Celery setup:
```env
# Optional for local dev - can be disabled
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/0
```

## Migrating Data from PostgreSQL

### Option 1: Using pg_dump (Recommended for Production Data)
If you have PostgreSQL client tools installed:
```bash
python scripts/convert_pg_to_sqlite.py
```

This requires:
- `pg_dump` or `pg_restore` (PostgreSQL client tools)
- The binary PostgreSQL dump file at `db/cas-prod-db 1`

### Option 2: Manual Export
```bash
# Export from PostgreSQL
pg_dump -U user -d database_name > dump.sql

# Convert to SQLite (in convert script)
# Then load into SQLite:
sqlite3 content_ai.db < dump.sql
```

### Option 3: Start Fresh
The current `content_ai.db` contains the basic schema. Alembic migrations will populate additional tables:
```bash
alembic upgrade head
```

## Troubleshooting

### Issue: Alembic Migration Fails
**Symptom:** Pydantic settings parsing error when running migrations

**Solution:** Make sure `.env` is properly configured:
```bash
# Check that DATABASE_URL points to SQLite
grep DATABASE_URL .env
# Should output: DATABASE_URL=sqlite:///./content_ai.db
```

### Issue: Database is Locked
**Symptom:** `sqlite3.OperationalError: database is locked`

**Solution:** This happens with concurrent writes:
1. Ensure only one process is accessing the database
2. Stop all running instances of the app
3. Delete `content_ai.db` and re-run setup if corrupted:
   ```bash
   rm content_ai.db
   python scripts/setup_sqlite_for_dev.py
   ```

### Issue: Foreign Key Constraint Error
**Symptom:** `FOREIGN KEY constraint failed`

**Solution:** This is expected if:
1. Inserting child records before parent records
2. Deleting parent records with child references
3. Check table relationships in the schema

Verify foreign keys are enabled:
```bash
sqlite3 content_ai.db "PRAGMA foreign_keys;"
# Should output: 1
```

## Environment Variables (.env Reference)

### Required for Local Dev
```env
APP_ENV=development
DATABASE_URL=sqlite:///./content_ai.db
JWT_SECRET_KEY=dev-secret-key-...
```

### Optional (Can be left empty)
```env
OPENAI_API_KEY=
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
DIS_S3_BUCKET=
```

### For Tracing (Optional)
```env
PHOENIX_SQL_DATABASE_URL=sqlite:///./phoenix.db
PHOENIX_COLLECTOR_ENDPOINT=http://localhost:6006/v1/traces
```

## Reverting to PostgreSQL

To switch back to PostgreSQL for production or testing:

1. **Update `.env`:**
   ```env
   DATABASE_URL=postgresql+psycopg2://user:password@host:5432/db
   ```

2. **Restart the app:**
   ```bash
   python -m uvicorn app.main:app --reload
   ```

The same ORM models and code work with both databases — only the connection URL changes.

## Performance Notes

### SQLite Limitations for Local Dev
- ✓ Good for: Single user, development, testing
- ✗ Not ideal for: High concurrency, production use
- ✗ Cannot use with: Multiple workers/processes

### For Production
Always use PostgreSQL for:
- High concurrency requirements
- Multiple application instances
- Large datasets
- Complex transactions

## Testing with SQLite

### Run Unit Tests
```bash
pytest tests/unit/ -v
```

### Run Integration Tests
```bash
pytest tests/integration/ -v
```

Tests may already use SQLite by default (see `tests/conftest.py`).

## Next Steps

1. ✓ Verify the databases exist:
   ```bash
   ls -la *.db
   ```

2. Run migrations (if needed):
   ```bash
   python -m alembic upgrade head
   ```

3. Start developing:
   ```bash
   python -m uvicorn app.main:app --reload
   ```

4. Check app health:
   ```bash
   curl http://localhost:8000/api/v1/health
   ```

## Support

For issues specific to SQLite migration:
1. Check this guide first
2. Review error logs in the console
3. Check `.env` configuration
4. Run setup script again if needed:
   ```bash
   python scripts/setup_sqlite_for_dev.py
   ```

## Summary of Changes

| Component | Before | After |
|-----------|--------|-------|
| **Database** | PostgreSQL | SQLite |
| **Connection URL** | `postgresql+psycopg2://...` | `sqlite:///./content_ai.db` |
| **Connection Pooling** | Enabled | Disabled (SQLite) |
| **Foreign Keys** | Default | Explicitly enabled |
| **Redis/Celery** | Required | Optional |
| **Development Setup** | Docker + PostgreSQL | Single SQLite file |
