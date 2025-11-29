#!/usr/bin/env python3
"""
Test database connection to verify defaultdb access
"""

import os
import psycopg2
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def test_connection():
    """Test connection to defaultdb"""

    # Get individual environment variables
    db_host = os.getenv('DB_HOST')
    db_port = os.getenv('DB_PORT')
    db_user = os.getenv('DB_USER')
    db_password = os.getenv('DB_PASSWORD')

    if not all([db_host, db_port, db_user, db_password]):
        print("❌ Missing database environment variables")
        return False

    # Try defaultdb
    db_url = f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/defaultdb"

    try:
        print(f"🔧 Testing connection to defaultdb...")
        conn = psycopg2.connect(db_url)
        cursor = conn.cursor()

        # Test basic query
        cursor.execute("SELECT version();")
        version = cursor.fetchone()[0]
        print(f"✅ Connected successfully to PostgreSQL: {version[:50]}...")

        # Test permissions with a simple table creation
        cursor.execute("CREATE TABLE IF NOT EXISTS test_table (id INT);")
        conn.commit()
        print("✅ Table creation permissions: OK")

        # Clean up
        cursor.execute("DROP TABLE IF EXISTS test_table;")
        conn.commit()

        cursor.close()
        conn.close()

        print("✅ All database tests passed!")
        return True

    except psycopg2.errors.InsufficientPrivilege as e:
        print(f"❌ Permission denied: {e}")
        print("💡 This confirms the permission issue with defaultdb")
        return False

    except Exception as e:
        print(f"❌ Connection failed: {e}")
        return False

if __name__ == "__main__":
    print("🧪 Testing database connection...")
    success = test_connection()
    exit(0 if success else 1)
