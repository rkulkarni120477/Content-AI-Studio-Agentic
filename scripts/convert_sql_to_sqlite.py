#!/usr/bin/env python3
"""
Convert PostgreSQL SQL dump to SQLite database.

Reads the pg_restore output SQL and converts dialect on-the-fly,
loading into SQLite in chunks to handle large files efficiently.
"""

import re
import sqlite3
import sys
from pathlib import Path


def convert_pg_to_sqlite_dialect(line: str) -> str:
    """Convert a single line of PostgreSQL SQL to SQLite dialect."""
    stripped = line.strip()

    # Skip comments and backslash commands
    if stripped.startswith(('--', '\\')):
        return ""

    # Skip PostgreSQL-specific SET commands
    if any(stripped.startswith(x) for x in [
        'SET client_encoding', 'SET standard_conforming_strings',
        'SET search_path', 'SET statement_timeout', 'SET lock_timeout',
        'SET idle_in_transaction', 'SET transaction_timeout',
        'SET check_function_bodies', 'SET xmloption',
        'SET client_min_messages', 'SET row_security',
        'SET default_tablespace', 'SET default_table_access_method',
    ]):
        return ""

    # Skip PostgreSQL-specific CREATE/DROP commands
    if any(stripped.startswith(x) for x in [
        'CREATE EXTENSION', 'DROP EXTENSION',
        'GRANT ', 'REVOKE ',
        'COMMENT ON', 'ALTER DATABASE', 'ALTER SCHEMA',
        'CREATE AGGREGATE', 'CREATE OPERATOR', 'CREATE FUNCTION', 'CREATE DOMAIN',
        'SELECT pg_catalog',
    ]):
        return ""

    # Skip OWNER TO statements
    if 'OWNER TO' in stripped:
        return ""

    # Skip pg_stat_statements
    if 'pg_stat_statements' in stripped:
        return ""

    # Convert type names
    conversions = [
        ('character varying', 'TEXT'),
        ('integer', 'INTEGER'),
        ('text', 'TEXT'),
        ('boolean', 'BOOLEAN'),
        ('timestamp without time zone', 'TIMESTAMP'),
        ('timestamp with time zone', 'TIMESTAMP'),
        ('bigint', 'INTEGER'),
        ('smallint', 'INTEGER'),
        ('double precision', 'REAL'),
        ('numeric', 'REAL'),
        ('json', 'TEXT'),
        ('jsonb', 'TEXT'),
        ('uuid', 'TEXT'),
        ('bytea', 'BLOB'),
    ]

    for pg_type, sqlite_type in conversions:
        # Use word boundaries to avoid substring matches
        line = re.sub(rf'\b{re.escape(pg_type)}\b', sqlite_type, line, flags=re.IGNORECASE)

    # Handle DEFAULT values with type casts
    line = re.sub(r"DEFAULT '([^']+)'::[\w\s]+", r"DEFAULT '\1'", line)
    line = re.sub(r"DEFAULT (\d+)::[\w\s]+", r"DEFAULT \1", line)

    # Remove constraint clauses
    if 'DEFERRABLE' in line:
        line = re.sub(r'\s+(?:NOT )?DEFERRABLE\s*', ' ', line)

    # Skip SEQUENCE definitions (SQLite uses AUTOINCREMENT)
    if 'CREATE SEQUENCE' in line or 'SEQUENCE OWNED BY' in line:
        return ""

    # Convert SERIAL to INTEGER
    line = line.replace(' SERIAL ', ' INTEGER ')
    line = line.replace(' SERIAL,', ' INTEGER,')

    # Skip SET statements
    if line.strip().startswith('SET '):
        return ""

    return line


def process_sql_file(sql_file: str, output_db: str) -> None:
    """Process PostgreSQL SQL file and load into SQLite."""
    conn = sqlite3.connect(output_db)
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('PRAGMA journal_mode = WAL')  # Use WAL for faster writes
    cursor = conn.cursor()

    statement_buffer = []
    statement_count = 0
    line_count = 0

    print(f"Processing SQL file: {sql_file}")
    print(f"Output database: {output_db}")
    print()

    try:
        with open(sql_file, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line_count += 1

                # Convert dialect
                line = convert_pg_to_sqlite_dialect(line)

                if line:
                    statement_buffer.append(line)

                # Execute when we hit a statement terminator
                if line.rstrip().endswith(';'):
                    statement = ''.join(statement_buffer).strip()
                    statement_buffer = []

                    if statement and not statement.startswith('--'):
                        try:
                            cursor.execute(statement)
                            statement_count += 1

                            if statement_count % 1000 == 0:
                                print(f"  Executed {statement_count} statements ({line_count} lines)...")
                                conn.commit()  # Commit every 1000 statements
                        except sqlite3.Error as e:
                            # Log errors but continue
                            if any(x in statement for x in ['CREATE TABLE', 'INSERT INTO']):
                                print(f"⚠ Error (statement {statement_count}): {e}")
                                print(f"  Statement: {statement[:100]}...")

        # Commit any remaining statements
        conn.commit()
        conn.close()

        print(f"\n✓ Successfully loaded {statement_count} statements from {line_count} lines")
        print(f"✓ Created SQLite database: {output_db}")

    except Exception as e:
        conn.close()
        raise RuntimeError(f"Failed to process SQL file: {e}")


def main():
    """Main conversion workflow."""
    repo_root = Path(__file__).parent.parent
    sql_file = repo_root / 'output.sql'
    output_db = repo_root / 'content_ai.db'

    print("=" * 70)
    print("SQL to SQLite Conversion")
    print("=" * 70)
    print()

    if not sql_file.exists():
        print(f"✗ SQL file not found: {sql_file}")
        print(f"\nFirst, convert the binary dump using pg_restore:")
        print(f"  pg_restore --file output.sql db/cas-prod-db.dump")
        return False

    try:
        # Remove old database if it exists
        if output_db.exists():
            print(f"Removing old database: {output_db}")
            output_db.unlink()

        process_sql_file(str(sql_file), str(output_db))

        print("\n" + "=" * 70)
        print("✓ Conversion Complete!")
        print("=" * 70)
        print(f"\nDatabase ready at: {output_db}")
        print("\nNext steps:")
        print("1. Start the app: python -m uvicorn app.main:app --reload")
        print("2. Check .env has: DATABASE_URL=sqlite:///./content_ai.db")
        return True
    except Exception as e:
        print(f"\n✗ Conversion failed: {e}")
        return False


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
