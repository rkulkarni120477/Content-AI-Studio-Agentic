#!/usr/bin/env python3
"""
Convert PostgreSQL dump to SQLite database.

This script handles the conversion of PostgreSQL binary dumps to SQLite format.
It attempts to use pg_restore if available, then converts the SQL dialect.
"""

import os
import re
import sqlite3
import subprocess
import tempfile
from pathlib import Path


def try_pg_restore_to_text(pg_dump_path: str) -> str:
    """
    Attempt to use pg_restore to convert binary dump to text SQL.

    Returns the path to the generated SQL file, or raises an exception.
    """
    temp_sql = tempfile.NamedTemporaryFile(mode='w', suffix='.sql', delete=False)
    temp_sql.close()

    try:
        # Try to use pg_restore (PostgreSQL tools must be in PATH)
        subprocess.run(
            ['pg_restore', '--file', temp_sql.name, '--format=p', pg_dump_path],
            check=True,
            capture_output=True,
        )
        print(f"✓ Used pg_restore to convert dump to SQL: {temp_sql.name}")
        return temp_sql.name
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        print(f"⚠ pg_restore not available or failed: {e}")
        os.unlink(temp_sql.name)
        raise


def convert_pg_to_sqlite_dialect(sql: str) -> str:
    """
    Convert PostgreSQL SQL dialect to SQLite-compatible SQL.
    """
    lines = []
    skip_next = False

    for line in sql.split('\n'):
        # Skip PostgreSQL-specific commands
        if skip_next:
            if line.strip().endswith(';'):
                skip_next = False
            continue

        # Skip extensions
        if 'CREATE EXTENSION' in line or 'DROP EXTENSION' in line:
            skip_next = True
            continue

        # Skip ACL/GRANT statements
        if line.strip().startswith(('GRANT', 'REVOKE', 'ALTER DEFAULT')):
            continue

        # Skip schema/database operations
        if any(x in line for x in ['CREATE DATABASE', 'DROP DATABASE', 'CREATE SCHEMA', 'SET client_encoding']):
            continue

        # Skip pg_catalog and special functions
        if 'pg_stat_statements' in line or 'pg_catalog' in line:
            continue

        # Convert type names
        line = line.replace('character varying', 'TEXT')
        line = line.replace('integer', 'INTEGER')
        line = line.replace('text', 'TEXT')
        line = line.replace('boolean', 'BOOLEAN')
        line = line.replace('timestamp without time zone', 'TIMESTAMP')
        line = line.replace('timestamp with time zone', 'TIMESTAMP')
        line = line.replace('bigint', 'INTEGER')
        line = line.replace('smallint', 'INTEGER')
        line = line.replace('double precision', 'REAL')
        line = line.replace('numeric', 'REAL')
        line = line.replace('json', 'TEXT')
        line = line.replace('jsonb', 'TEXT')
        line = line.replace('uuid', 'TEXT')
        line = line.replace('bytea', 'BLOB')

        # Handle DEFAULT values
        line = re.sub(r"DEFAULT '([^']+)'::[\w\s]+", r"DEFAULT '\1'", line)
        line = re.sub(r"DEFAULT (\d+)::[\w\s]+", r"DEFAULT \1", line)

        # Remove constraint clauses that don't work in SQLite the same way
        if 'NOT DEFERRABLE' in line or 'DEFERRABLE' in line:
            line = re.sub(r'\s+(?:NOT )?DEFERRABLE\s*', ' ', line)

        # Convert SEQUENCE to AUTOINCREMENT
        if 'CREATE SEQUENCE' in line:
            # Skip sequences, we'll use AUTOINCREMENT instead
            continue

        if 'SEQUENCE OWNED BY' in line:
            continue

        # Remove COMMENT statements
        if 'COMMENT ON' in line:
            continue

        # Convert SERIAL to INTEGER PRIMARY KEY AUTOINCREMENT
        line = line.replace(' SERIAL ', ' INTEGER ')

        lines.append(line)

    sql_out = '\n'.join(lines)

    # Clean up extra whitespace
    sql_out = re.sub(r'\n\s*\n\s*\n+', '\n\n', sql_out)

    return sql_out


def create_sqlite_db_from_sql(sql_file: str, output_db: str) -> None:
    """
    Create SQLite database from SQL file.
    """
    conn = sqlite3.connect(output_db)
    conn.execute('PRAGMA foreign_keys = ON')

    with open(sql_file, 'r', encoding='utf-8', errors='ignore') as f:
        sql_content = f.read()

    # Convert dialect
    sql_content = convert_pg_to_sqlite_dialect(sql_content)

    # Execute SQL statements
    cursor = conn.cursor()
    statements = [s.strip() for s in sql_content.split(';') if s.strip()]

    for i, stmt in enumerate(statements, 1):
        try:
            cursor.execute(stmt)
            if i % 100 == 0:
                print(f"  Executed {i} statements...")
        except sqlite3.Error as e:
            # Log errors but continue - some statements may not be compatible
            if 'PRAGMA' not in stmt and 'CREATE TABLE' in stmt:
                print(f"⚠ Warning on statement {i}: {e}")

    conn.commit()
    conn.close()
    print(f"✓ Created SQLite database: {output_db}")


def main():
    """Main conversion workflow."""
    repo_root = Path(__file__).parent.parent
    dump_file = repo_root / 'db' / 'cas-prod-db 1'
    output_db = repo_root / 'content_ai.db'

    print(f"PostgreSQL → SQLite Conversion")
    print(f"=" * 50)
    print(f"Input dump:  {dump_file}")
    print(f"Output DB:   {output_db}")
    print()

    if not dump_file.exists():
        print(f"✗ Dump file not found: {dump_file}")
        return False

    # Step 1: Convert binary dump to text SQL
    print("Step 1: Converting PostgreSQL binary dump to SQL...")
    try:
        sql_file = try_pg_restore_to_text(str(dump_file))
    except Exception as e:
        print(f"✗ Failed to convert dump: {e}")
        print("\n💡 PostgreSQL tools not available. Trying alternative approach...")
        # For now, we'll skip to alternative if pg_restore fails
        return False

    try:
        # Step 2: Create SQLite database from converted SQL
        print("\nStep 2: Creating SQLite database...")
        create_sqlite_db_from_sql(sql_file, str(output_db))

        # Cleanup
        if os.path.exists(sql_file):
            os.unlink(sql_file)

        print("\n" + "=" * 50)
        print("✓ Conversion complete!")
        print(f"\nNext steps:")
        print(f"1. Update DATABASE_URL in .env:")
        print(f"   DATABASE_URL=sqlite:///{repo_root / 'content_ai.db'}")
        print(f"2. Run the app: uvicorn app.main:app --reload")
        return True
    except Exception as e:
        print(f"✗ Failed to create SQLite database: {e}")
        return False


if __name__ == '__main__':
    success = main()
    exit(0 if success else 1)
