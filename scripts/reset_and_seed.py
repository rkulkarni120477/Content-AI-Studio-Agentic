#!/usr/bin/env python3
"""
Reset SQLite database and seed with test data.
"""

import sqlite3
import os
import hashlib

def hash_password(password: str) -> str:
    """Simple password hashing."""
    return hashlib.sha256(password.encode()).hexdigest()

def reset_and_seed():
    """Reset database and create test data."""
    db_file = 'content_ai.db'

    # Backup existing db
    if os.path.exists(db_file):
        os.rename(db_file, f'{db_file}.backup')
        print(f"Backed up existing database to {db_file}.backup")

    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()

    print("\n📊 Creating database schema...")

    try:
        # Create users table
        cursor.execute('''
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                email TEXT,
                display_name TEXT,
                role TEXT DEFAULT 'user',
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        print("✓ Created users table")

        # Create projects table
        cursor.execute('''
            CREATE TABLE projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        print("✓ Created projects table")

        # Create courses table
        cursor.execute('''
            CREATE TABLE courses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        print("✓ Created courses table")

        # Create blocks table
        cursor.execute('''
            CREATE TABLE blocks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id INTEGER,
                block_type TEXT,
                content TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        print("✓ Created blocks table")

        # Create audit_logs table
        cursor.execute('''
            CREATE TABLE audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                action TEXT,
                entity_type TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        print("✓ Created audit_logs table")

        print("\n👥 Adding test users...")

        # Add test users
        users = [
            ('admin', hash_password('Admin@123'), 'admin@localhost', 'Admin User', 'admin', 1),
            ('testuser', hash_password('Test@123'), 'test@localhost', 'Test User', 'user', 1),
            ('demo', hash_password('Demo@123'), 'demo@localhost', 'Demo User', 'author', 1),
        ]

        for username, password, email, display_name, role, is_active in users:
            cursor.execute('''
                INSERT INTO users (username, password, email, display_name, role, is_active)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (username, password, email, display_name, role, is_active))
            print(f"  ✓ Created user: {username}")

        print("\n🏢 Adding test organizations...")

        # Add test projects
        projects = [
            ('Test Organization', 'Default test organization'),
            ('AIM', 'Academic Integrity Management'),
        ]

        for name, description in projects:
            cursor.execute('''
                INSERT INTO projects (name, description)
                VALUES (?, ?)
            ''', (name, description))
            print(f"  ✓ Created project: {name}")

        conn.commit()

        print("\n" + "="*70)
        print("✅ DATABASE RESET AND SEEDED SUCCESSFULLY")
        print("="*70)
        print("\n📋 TEST LOGIN CREDENTIALS:\n")
        print("┌─ Admin Account ──────────────────────────────────────────┐")
        print("│ Username: admin                                          │")
        print("│ Password: Admin@123                                      │")
        print("│ Role: Administrator                                      │")
        print("└──────────────────────────────────────────────────────────┘\n")
        print("┌─ Test Account ───────────────────────────────────────────┐")
        print("│ Username: testuser                                       │")
        print("│ Password: Test@123                                       │")
        print("│ Role: User                                               │")
        print("└──────────────────────────────────────────────────────────┘\n")
        print("┌─ Demo Account ───────────────────────────────────────────┐")
        print("│ Username: demo                                           │")
        print("│ Password: Demo@123                                       │")
        print("│ Role: Author                                             │")
        print("└──────────────────────────────────────────────────────────┘\n")
        print("🌐 Organization Code: (leave blank)\n")
        print("🚀 Access at: http://127.0.0.1:3001")
        print("="*70 + "\n")

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
    success = reset_and_seed()
    sys.exit(0 if success else 1)
