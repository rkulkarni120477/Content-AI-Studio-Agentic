#!/usr/bin/env python3
"""
Seed SQLite database with basic test data.
Creates essential tables and adds sample users and organizations.
"""

import sqlite3
from datetime import datetime
import hashlib

def hash_password(password: str) -> str:
    """Simple password hashing (matching app's security pattern)."""
    return hashlib.sha256(password.encode()).hexdigest()

def seed_test_data():
    """Create tables and populate with test data."""
    conn = sqlite3.connect('content_ai.db')
    cursor = conn.cursor()

    print("Creating test data tables...")

    try:
        # Create users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                email TEXT,
                display_name TEXT,
                role TEXT DEFAULT 'user',
                is_active BOOLEAN DEFAULT 1,
                is_platform_admin BOOLEAN DEFAULT 0,
                permissions TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                project_id INTEGER,
                microsoft_oid TEXT
            )
        ''')

        # Create projects table (organizations/tenants)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Create courses table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS courses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Create blocks table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS blocks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id INTEGER,
                block_type TEXT,
                block_label TEXT,
                content TEXT,
                workflow_state TEXT DEFAULT 'draft',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Create audit_logs table
        cursor.execute('''
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
            )
        ''')

        # Create alembic_version table if not exists
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS alembic_version (
                version_num TEXT NOT NULL PRIMARY KEY
            )
        ''')

        print("✓ Tables created\n")

        # Insert test data
        print("Inserting test data...")

        # Create test project
        cursor.execute('INSERT OR IGNORE INTO projects (name, description) VALUES (?, ?)',
                      ('Test Organization', 'Default test organization'))

        # Create AIM project
        cursor.execute('INSERT OR IGNORE INTO projects (name, description) VALUES (?, ?)',
                      ('AIM', 'Academic Integrity Management'))

        # Create admin user
        admin_password = hash_password('Admin@123')
        cursor.execute('''INSERT OR IGNORE INTO users
                         (username, password_hash, email, display_name, role, is_active, is_platform_admin)
                         VALUES (?, ?, ?, ?, ?, ?, ?)''',
                      ('admin', admin_password, 'admin@localhost', 'Admin User', 'admin', 1, 1))

        # Create test user
        test_password = hash_password('Test@123')
        cursor.execute('''INSERT OR IGNORE INTO users
                         (username, password_hash, email, display_name, role, is_active)
                         VALUES (?, ?, ?, ?, ?, ?)''',
                      ('testuser', test_password, 'test@localhost', 'Test User', 'user', 1))

        # Create demo user
        demo_password = hash_password('Demo@123')
        cursor.execute('''INSERT OR IGNORE INTO users
                         (username, password_hash, email, display_name, role, is_active)
                         VALUES (?, ?, ?, ?, ?, ?)''',
                      ('demo', demo_password, 'demo@localhost', 'Demo User', 'author', 1))

        conn.commit()

        # Show what was created
        cursor.execute("SELECT COUNT(*) FROM users")
        user_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM projects")
        project_count = cursor.fetchone()[0]

        print(f"✓ Created {user_count} users")
        print(f"✓ Created {project_count} projects")

        print("\n" + "="*70)
        print("✅ TEST DATA CREATED SUCCESSFULLY")
        print("="*70)
        print("\n📋 LOGIN CREDENTIALS:\n")
        print("Admin Account:")
        print("  Username: admin")
        print("  Password: Admin@123")
        print("  Role: Platform Administrator\n")
        print("Test Account:")
        print("  Username: testuser")
        print("  Password: Test@123")
        print("  Role: User\n")
        print("Demo Account:")
        print("  Username: demo")
        print("  Password: Demo@123")
        print("  Role: Author\n")
        print("Organization Code: (leave blank)\n")
        print("="*70)
        print("\n🚀 Access the application at: http://127.0.0.1:3001")
        print("="*70)

        conn.close()
        return True

    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
        conn.close()
        return False

if __name__ == "__main__":
    import sys
    success = seed_test_data()
    sys.exit(0 if success else 1)
