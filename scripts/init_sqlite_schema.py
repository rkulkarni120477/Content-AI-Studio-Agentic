#!/usr/bin/env python3
"""
Initialize SQLite database schema by creating tables from SQLAlchemy models.
This bypasses Alembic migrations which have PostgreSQL-specific syntax.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

def init_sqlite_schema():
    """Create all tables from SQLAlchemy models in SQLite."""
    from app.core.database import engine, Base

    # Import models to register them with Base
    import promptops_app.core.models

    print("Creating SQLite schema from SQLAlchemy models...")
    try:
        Base.metadata.create_all(bind=engine)
        print("✓ Schema created successfully!")

        # List tables
        from sqlalchemy import inspect
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        print(f"\n✓ Created {len(tables)} tables:")
        for table in sorted(tables):
            print(f"  - {table}")

        return True
    except Exception as e:
        print(f"✗ Error creating schema: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = init_sqlite_schema()
    sys.exit(0 if success else 1)
