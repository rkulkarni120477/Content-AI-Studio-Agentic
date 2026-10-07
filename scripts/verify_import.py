#!/usr/bin/env python3
"""Verify SQLite import results."""

import sqlite3

db = sqlite3.connect('content_ai.db')
cursor = db.cursor()

# Get all tables
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = cursor.fetchall()

print(f"Total tables: {len(tables)}\n")
print("Table Summary:")
print("-" * 60)

total_rows = 0
for table in tables:
    table_name = table[0]
    try:
        cursor.execute(f'SELECT COUNT(*) FROM "{table_name}"')
        count = cursor.fetchone()[0]
        total_rows += count
        if count > 0:
            print(f"{table_name:40s} {count:>15,} rows")
    except Exception as e:
        print(f"{table_name:40s} ERROR: {str(e)[:40]}")

print("-" * 60)
print(f"{'TOTAL':40s} {total_rows:>15,} rows")

# Check for key tables
print("\nKey tables present:")
key_tables = ['users', 'courses', 'documents', 'generations', 'blocks']
for table in key_tables:
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    result = cursor.fetchone()
    status = "✓" if result else "✗"
    print(f"  {status} {table}")

db.close()
