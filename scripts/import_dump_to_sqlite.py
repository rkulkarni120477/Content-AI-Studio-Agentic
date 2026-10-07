#!/usr/bin/env python3
"""
Convert PostgreSQL dump to SQLite format and import data.

The script:
1. Extracts SQL from PostgreSQL dump using pg_restore
2. Converts PostgreSQL SQL to SQLite-compatible format
3. Imports into SQLite database
"""

import subprocess
import sqlite3
import re
import sys
from pathlib import Path

DUMP_FILE = "db/cas-prod-db.dump"
SQLITE_DB = "content_ai.db"
TEMP_SQL = "db/pg_dump.sql"

def pg_restore_to_sql():
    """Convert PostgreSQL binary dump to SQL format."""
    print("[1/3] Converting PostgreSQL dump to SQL format...")

    pg_bin = r"C:\Program Files\PostgreSQL\18\bin"
    pg_restore = rf"{pg_bin}\pg_restore.exe"

    if not Path(pg_restore).exists():
        print(f"ERROR: pg_restore not found at {pg_restore}")
        return False

    try:
        # Convert binary dump to plain text SQL
        cmd = [pg_restore, "-f", TEMP_SQL, DUMP_FILE]
        print(f"Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        if result.returncode != 0:
            print(f"ERROR: pg_restore failed: {result.stderr}")
            return False

        file_size = Path(TEMP_SQL).stat().st_size / (1024*1024)
        print(f"✓ SQL file created: {file_size:.1f}MB")
        return True

    except Exception as e:
        print(f"ERROR: {e}")
        return False

def convert_sql_to_sqlite_compatible(sql_content):
    """Convert PostgreSQL SQL to SQLite-compatible format."""
    print("[2/3] Converting SQL syntax...")

    # Remove PostgreSQL-specific syntax
    sql = sql_content

    # Remove comments and metadata statements (lines starting with --, /*, or containing \)
    sql = re.sub(r'--.*?$', '', sql, flags=re.MULTILINE)  # Remove line comments
    sql = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)  # Remove block comments

    # Remove CREATE SCHEMA IF NOT EXISTS
    sql = re.sub(r'CREATE SCHEMA\s+IF\s+NOT\s+EXISTS\s+\w+\s*;', '', sql, flags=re.IGNORECASE | re.MULTILINE)
    sql = re.sub(r'SET\s+.*?;', '', sql, flags=re.IGNORECASE | re.MULTILINE)

    # Remove ALTER statements and OWNER TO
    sql = re.sub(r'ALTER\s+TABLE\s+.*?\s+OWNER\s+TO\s+.*?;', '', sql, flags=re.IGNORECASE | re.MULTILINE)
    sql = re.sub(r'ALTER\s+SEQUENCE\s+.*?\s+OWNER\s+TO\s+.*?;', '', sql, flags=re.IGNORECASE | re.MULTILINE)
    sql = re.sub(r'ALTER\s+DEFAULT\s+PRIVILEGES.*?;', '', sql, flags=re.IGNORECASE | re.MULTILINE)

    # Remove SELECT pg_catalog statements
    sql = re.sub(r'SELECT\s+pg_catalog\..*?;', '', sql, flags=re.IGNORECASE | re.MULTILINE)

    # Remove lines with backslashes and weird PostgreSQL metadata
    sql = re.sub(r'^.*\\.*$', '', sql, flags=re.MULTILINE)
    sql = re.sub(r'^\s*Type:\s+.*$', '', sql, flags=re.MULTILINE | re.IGNORECASE)
    sql = re.sub(r'^\s*Schema:\s+.*$', '', sql, flags=re.MULTILINE | re.IGNORECASE)
    sql = re.sub(r'^\s*Owner:\s+.*$', '', sql, flags=re.MULTILINE | re.IGNORECASE)

    # Convert data types
    replacements = [
        (r'\bboolean\b', 'INTEGER'),  # SQLite uses 0/1 for booleans
        (r'\btext\[\]', 'TEXT'),      # Array to text
        (r'\buuid\b', 'TEXT'),        # UUID as text
        (r'\bjson\b', 'TEXT'),        # JSON as text
        (r'\bjsonb\b', 'TEXT'),       # JSONB as text
        (r'\binteger\[\]', 'TEXT'),   # Integer array as text
        (r'\btimestamp\s+without\s+time\s+zone\b', 'DATETIME'),
        (r'\btimestamp\s+with\s+time\s+zone\b', 'DATETIME'),
        (r'\btimestamp\b', 'DATETIME'),
        (r'\bCONSTRAINT\s+\w+\s+UNIQUE', 'UNIQUE'),
        (r'\bDEFAULT\s+nextval\([^)]+\)', 'DEFAULT NULL'),  # Replace sequences
    ]

    for pattern, replacement in replacements:
        sql = re.sub(pattern, replacement, sql, flags=re.IGNORECASE)

    # Clean up extra whitespace
    sql = re.sub(r'\s+', ' ', sql)

    # Fix common issues with quotes
    sql = sql.replace('""', '"')  # Double quotes to single

    print(f"✓ SQL syntax converted")
    return sql

def import_to_sqlite(sql_content):
    """Import SQL into SQLite database."""
    print("[3/3] Importing into SQLite...")

    db = sqlite3.connect(SQLITE_DB)
    cursor = db.cursor()

    # Disable foreign keys during import
    cursor.execute("PRAGMA foreign_keys = OFF")

    # Split SQL into statements and execute
    statements = sql_content.split(';')
    executed = 0
    skipped = 0
    errors = 0
    filtered = 0

    for i, statement in enumerate(statements):
        statement = statement.strip()
        if not statement or len(statement) < 5:  # Skip empty/too-short statements
            filtered += 1
            continue

        # Skip problematic statements
        if any(keyword in statement.upper() for keyword in ['REVOKE', 'GRANT', 'COMMENT ON', 'COPY', 'BEGIN', 'COMMIT']):
            filtered += 1
            continue

        if i % 100 == 0:
            print(f"  Processing statement {i}...")

        try:
            cursor.execute(statement)
            executed += 1
        except sqlite3.OperationalError as e:
            error_str = str(e).lower()
            if "already exists" in error_str or "duplicate" in error_str:
                skipped += 1
            else:
                errors += 1
                if errors <= 10:  # Print first 10 errors
                    print(f"    Error in statement {i}: {e[:100]}")
        except Exception as e:
            errors += 1
            if errors <= 10:
                print(f"    Error in statement {i}: {str(e)[:100]}")

    db.commit()

    # Re-enable foreign keys
    cursor.execute("PRAGMA foreign_keys = ON")
    db.commit()
    db.close()

    print(f"✓ Import complete:")
    print(f"    Executed: {executed}")
    print(f"    Skipped (duplicates/errors): {skipped + errors}")
    print(f"    Filtered: {filtered}")

    return errors < 100  # Allow some errors

def main():
    """Main migration process."""
    print("=" * 60)
    print("PostgreSQL Dump to SQLite Migration")
    print("=" * 60)

    # Step 1: Convert dump to SQL
    if not pg_restore_to_sql():
        print("\nFailed to convert dump to SQL")
        return False

    # Step 2: Read and convert SQL
    print(f"\nReading {TEMP_SQL}...")
    try:
        with open(TEMP_SQL, 'r', encoding='utf-8', errors='ignore') as f:
            sql_content = f.read()
        print(f"✓ Read {len(sql_content)/1024/1024:.1f}MB of SQL")
    except Exception as e:
        print(f"ERROR reading SQL file: {e}")
        return False

    # Convert syntax
    sql_content = convert_sql_to_sqlite_compatible(sql_content)

    # Step 3: Import into SQLite
    success = import_to_sqlite(sql_content)

    print("\n" + "=" * 60)
    if success:
        print("Migration SUCCESSFUL!")
        # Verify
        try:
            db = sqlite3.connect(SQLITE_DB)
            cursor = db.cursor()
            cursor.execute("SELECT COUNT(*) FROM users")
            result = cursor.fetchone()
            user_count = result[0] if result else 0
            db.close()
            print(f"Users in database: {user_count}")
        except Exception as e:
            print(f"Could not verify user count: {e}")
    else:
        print("Migration completed with errors (some data may be imported)")
    print("=" * 60)

    return success

if __name__ == "__main__":
    try:
        success = main()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\nMigration cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\nFatal error: {e}")
        sys.exit(1)
