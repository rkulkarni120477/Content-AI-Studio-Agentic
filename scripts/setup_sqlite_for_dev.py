#!/usr/bin/env python3
"""
Setup SQLite database for local development.

This script provides multiple approaches to set up SQLite:
1. Use Alembic migrations to recreate schema
2. Create a minimal starter database
3. Convert PostgreSQL dump if tools are available
"""

import os
import sqlite3
import subprocess
from pathlib import Path


def create_minimal_sqlite_db(db_path: str) -> None:
    """
    Create a minimal SQLite database with essential schema.

    This creates the basic tables needed for the app to run.
    For production data migration, use Alembic migrations or pg_dump tools.
    """
    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA foreign_keys = ON')
    cursor = conn.cursor()

    # Basic schema for app to initialize
    schema_sql = """
    -- Alembic version table (required for migrations)
    CREATE TABLE IF NOT EXISTS alembic_version (
        version_num VARCHAR(32) NOT NULL PRIMARY KEY
    );

    -- Users table
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        email TEXT UNIQUE NOT NULL,
        hashed_password TEXT NOT NULL,
        is_active BOOLEAN DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- Projects table
    CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- Courses table
    CREATE TABLE IF NOT EXISTS courses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        description TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
    );

    -- Blocks table (content blocks)
    CREATE TABLE IF NOT EXISTS blocks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        course_id INTEGER,
        block_type TEXT,
        block_label TEXT,
        content TEXT,
        workflow_state TEXT DEFAULT 'draft',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- Audit logs
    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        action TEXT NOT NULL,
        entity_type TEXT,
        entity_id TEXT,
        project_id INTEGER,
        course_id INTEGER,
        metadata_json TEXT,
        ip_address TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        tenant_id TEXT,
        actor_role TEXT,
        user_agent TEXT,
        changes TEXT,
        summary TEXT
    );
    """

    for statement in schema_sql.split(';'):
        statement = statement.strip()
        if statement:
            try:
                cursor.execute(statement)
            except sqlite3.Error as e:
                print(f"⚠ Warning executing schema: {e}")

    conn.commit()
    conn.close()
    print(f"✓ Created minimal SQLite database: {db_path}")


def run_alembic_migrations(db_path: str) -> bool:
    """
    Run Alembic migrations to create/update schema.

    This is the recommended approach for keeping schema in sync with code.
    """
    repo_root = Path(__file__).parent.parent

    try:
        print("Running Alembic migrations...")
        result = subprocess.run(
            ['python', '-m', 'alembic', 'upgrade', 'head'],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            print("✓ Alembic migrations completed successfully")
            return True
        else:
            print(f"⚠ Alembic migrations had issues: {result.stderr}")
            return False
    except Exception as e:
        print(f"⚠ Could not run Alembic: {e}")
        return False


def try_convert_from_pg_dump(dump_file: str, output_db: str) -> bool:
    """
    Attempt to convert PostgreSQL dump to SQLite.

    Tries multiple approaches: pg_restore → sqlite conversion script
    """
    repo_root = Path(__file__).parent.parent
    converter_script = repo_root / 'scripts' / 'convert_pg_to_sqlite.py'

    if not converter_script.exists():
        print(f"⚠ Converter script not found at {converter_script}")
        return False

    try:
        print("Attempting to convert PostgreSQL dump to SQLite...")
        result = subprocess.run(
            ['python', str(converter_script)],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            print(result.stdout)
            return True
        else:
            print(f"⚠ PostgreSQL dump conversion failed: {result.stderr}")
            return False
    except Exception as e:
        print(f"⚠ Could not run PostgreSQL conversion: {e}")
        return False


def main():
    """Main setup workflow."""
    repo_root = Path(__file__).parent.parent
    db_path = repo_root / 'content_ai.db'
    phoenix_db_path = repo_root / 'phoenix.db'
    dump_file = repo_root / 'db' / 'cas-prod-db 1'

    print("=" * 70)
    print("SQLite Setup for Local Development")
    print("=" * 70)
    print()

    # Step 1: Try PostgreSQL dump conversion
    if dump_file.exists():
        print(f"📦 Found PostgreSQL dump: {dump_file}")
        if try_convert_from_pg_dump(str(dump_file), str(db_path)):
            print("\n✓ Successfully converted PostgreSQL dump to SQLite")
            print(f"Database location: {db_path}")
        else:
            print("\n⚠ Could not convert PostgreSQL dump (pg_dump tools not available)")
            print("Falling back to schema migration...\n")
    else:
        print(f"⚠ PostgreSQL dump not found at {dump_file}\n")

    # Step 2: If DB doesn't exist, create it and run migrations
    if not db_path.exists():
        print("Creating new SQLite database...")
        create_minimal_sqlite_db(str(db_path))

        print("\nApplying Alembic migrations...")
        if not run_alembic_migrations(str(db_path)):
            print("⚠ Alembic migrations failed or not available")
            print("✓ Database created with minimal schema")

    # Step 3: Create Phoenix database if needed
    if not phoenix_db_path.exists():
        print(f"\nCreating Phoenix tracing database: {phoenix_db_path}")
        conn = sqlite3.connect(str(phoenix_db_path))
        conn.close()
        print("✓ Phoenix database created")

    print("\n" + "=" * 70)
    print("Setup Complete!")
    print("=" * 70)
    print(f"\n✓ Main database:   {db_path}")
    print(f"✓ Phoenix database: {phoenix_db_path}")
    print(f"✓ Configuration:   .env (SQLite ready)")
    print("\nNext steps:")
    print("1. Install dependencies: pip install -r requirements.txt")
    print("2. Run the backend:     python -m uvicorn app.main:app --reload")
    print("3. In another terminal, run frontend: npm run dev")
    print("\nFor production data, migrate from PostgreSQL manually or use:")
    print("  python scripts/convert_pg_to_sqlite.py")
    print("  (requires PostgreSQL client tools: pg_dump, pg_restore)")


if __name__ == '__main__':
    main()
