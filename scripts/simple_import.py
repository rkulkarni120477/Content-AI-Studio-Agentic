#!/usr/bin/env python3
"""
Simple PostgreSQL SQL dump to SQLite import.
Focuses on CREATE TABLE and INSERT statements only.
"""

import sqlite3
import re
import sys
from pathlib import Path

SQL_FILE = "db/pg_dump.sql"
SQLITE_DB = "content_ai.db"

def main():
    print("=" * 60)
    print("Simple PostgreSQL SQL to SQLite Import")
    print("=" * 60)

    if not Path(SQL_FILE).exists():
        print(f"ERROR: {SQL_FILE} not found")
        return False

    print(f"\nReading {SQL_FILE}...")
    with open(SQL_FILE, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    print(f"Read {len(content) / 1024 / 1024:.1f}MB")

    # Connect to SQLite
    db = sqlite3.connect(SQLITE_DB)
    cursor = db.cursor()
    cursor.execute("PRAGMA foreign_keys = OFF")

    # Extract all SQL statements
    print("\nProcessing SQL statements...")

    # Remove comments
    content = re.sub(r'--.*?$', '', content, flags=re.MULTILINE)
    content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)

    # Split by semicolon and process each statement
    statements = content.split(';')

    executed = 0
    skipped = 0
    errors = 0

    for i, stmt in enumerate(statements):
        stmt = stmt.strip()
        if not stmt or len(stmt) < 5:
            continue

        # Skip non-data statements
        stmt_upper = stmt.upper()
        if any(x in stmt_upper for x in ['REVOKE', 'GRANT', 'COMMENT ON', 'COPY',
                                          'BEGIN', 'COMMIT', 'ROLLBACK', 'PRAGMA',
                                          'SET', 'LOCK', 'UNLOCK', 'EXPLAIN',
                                          'CREATE SCHEMA', 'CREATE TYPE', 'ALTER']):
            skipped += 1
            continue

        # Only execute CREATE TABLE and INSERT statements
        if not any(x in stmt_upper for x in ['CREATE TABLE', 'INSERT INTO', 'CREATE INDEX']):
            skipped += 1
            continue

        # For CREATE TABLE: remove PostgreSQL-specific syntax
        if 'CREATE TABLE' in stmt_upper:
            # Remove IF NOT EXISTS since it's already converted
            stmt = stmt.replace('IF NOT EXISTS', '')

            # Convert data types
            stmt = re.sub(r'\bboolean\b', 'INTEGER', stmt, flags=re.IGNORECASE)
            stmt = re.sub(r'\btext\[\]', 'TEXT', stmt, flags=re.IGNORECASE)
            stmt = re.sub(r'\buuid\b', 'TEXT', stmt, flags=re.IGNORECASE)
            stmt = re.sub(r'\bjson\b', 'TEXT', stmt, flags=re.IGNORECASE)
            stmt = re.sub(r'\bjsonb\b', 'TEXT', stmt, flags=re.IGNORECASE)
            stmt = re.sub(r'\btimestamp.*?\b', 'DATETIME', stmt, flags=re.IGNORECASE)

            # Remove DEFAULT nextval (sequences)
            stmt = re.sub(r'DEFAULT\s+nextval\([^)]*\)', '', stmt, flags=re.IGNORECASE)
            stmt = re.sub(r'REFERENCES\s+\w+\s*\([^)]*\)', '', stmt, flags=re.IGNORECASE)

        try:
            cursor.execute(stmt)
            executed += 1
            if executed % 100 == 0:
                print(f"  Executed {executed} statements...")
        except sqlite3.OperationalError as e:
            if 'already exists' not in str(e):
                errors += 1
                if errors <= 10:
                    print(f"    Error: {str(e)[:80]}")
        except Exception as e:
            errors += 1
            if errors <= 10:
                print(f"    Error: {str(e)[:80]}")

    db.commit()
    cursor.execute("PRAGMA foreign_keys = ON")
    db.commit()
    db.close()

    print(f"\n" + "=" * 60)
    print(f"Import complete:")
    print(f"  Executed: {executed}")
    print(f"  Skipped: {skipped}")
    print(f"  Errors: {errors}")
    print("=" * 60)

    # Verify
    try:
        db = sqlite3.connect(SQLITE_DB)
        cursor = db.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        print(f"\nTables created: {len(tables)}")
        db.close()
    except Exception as e:
        print(f"Could not verify: {e}")

    return errors < 50

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
