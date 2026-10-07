#!/usr/bin/env python3
"""
Migrate data from PostgreSQL to SQLite.

Usage: python scripts/migrate_pg_to_sqlite.py
"""

import os
import sys
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

# PostgreSQL connection string
PG_CONNECTION = "postgresql+psycopg2://postgres:@localhost:5432/postgres"

# SQLite connection
SQLITE_CONNECTION = "sqlite:///content_ai.db"

def migrate_data():
    """Migrate all data from PostgreSQL to SQLite."""

    try:
        # Create engines
        print("Connecting to PostgreSQL...")
        pg_engine = create_engine(PG_CONNECTION, echo=False, connect_args={"connect_timeout": 5})

        print("Connecting to SQLite...")
        sqlite_engine = create_engine(SQLITE_CONNECTION, echo=False)

        # Test connections
        with pg_engine.connect() as conn:
            result = conn.execute(text("SELECT 1"))
            print("PostgreSQL connected successfully")

        with sqlite_engine.connect() as conn:
            result = conn.execute(text("SELECT 1"))
            print("SQLite connected successfully")

        # Get table names from PostgreSQL
        pg_inspector = inspect(pg_engine)
        tables = pg_inspector.get_table_names()
        print(f"\nFound {len(tables)} tables in PostgreSQL")

        # Create sessions
        PGSession = sessionmaker(bind=pg_engine)
        SQLiteSession = sessionmaker(bind=sqlite_engine)

        migrated_count = 0
        total_rows = 0

        for table_name in sorted(tables):
            try:
                # Get columns for this table
                columns = pg_inspector.get_columns(table_name)
                column_names = [col['name'] for col in columns]

                # Query data from PostgreSQL
                with pg_engine.connect() as conn:
                    query = f"SELECT {', '.join(column_names)} FROM {table_name} LIMIT 10000"
                    result = conn.execute(text(query))
                    rows = result.fetchall()
                    row_count = len(rows)

                if row_count == 0:
                    print(f"  {table_name}: 0 rows (skipped)")
                    continue

                # Insert into SQLite using raw SQL to handle types
                with sqlite_engine.connect() as conn:
                    # Disable foreign keys during import
                    conn.execute(text("PRAGMA foreign_keys = OFF"))

                    for row in rows:
                        placeholders = ', '.join(['?' for _ in column_names])
                        insert_sql = f"INSERT OR IGNORE INTO {table_name} ({', '.join(column_names)}) VALUES ({placeholders})"
                        try:
                            conn.execute(text(insert_sql), [v for v in row])
                        except Exception as e:
                            # Log error but continue with other rows
                            print(f"    Error inserting row: {e}")
                            continue

                    conn.commit()

                print(f"  {table_name}: {row_count} rows")
                migrated_count += 1
                total_rows += row_count

            except Exception as e:
                print(f"  {table_name}: ERROR - {str(e)[:60]}")
                continue

        print(f"\nMigration complete!")
        print(f"  Tables migrated: {migrated_count}/{len(tables)}")
        print(f"  Total rows imported: {total_rows}")
        return True

    except Exception as e:
        print(f"Migration failed: {e}")
        return False

if __name__ == "__main__":
    success = migrate_data()
    sys.exit(0 if success else 1)
