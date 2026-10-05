import sqlite3

conn = sqlite3.connect('content_ai.db')
cursor = conn.cursor()

# List all tables
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
tables = cursor.fetchall()

print("Available tables:")
for table in tables:
    print(f"  - {table[0]}")

# Check for organization/project related tables
org_tables = ['organizations', 'projects', 'tenants', 'clients', 'workspaces']
for table_name in org_tables:
    try:
        cursor.execute(f"SELECT * FROM {table_name} LIMIT 1;")
        result = cursor.fetchall()
        if result:
            print(f"\n✓ Found {table_name} table with {len(result)} rows")
            cursor.execute(f"SELECT * FROM {table_name};")
            rows = cursor.fetchall()
            cursor.execute(f"PRAGMA table_info({table_name});")
            columns = cursor.fetchall()
            col_names = [col[1] for col in columns]
            print(f"  Columns: {col_names}")
            for row in rows:
                print(f"  {row}")
    except:
        pass

conn.close()
