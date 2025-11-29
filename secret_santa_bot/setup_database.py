#!/usr/bin/env python3
"""
Database setup script for Secret Santa Bot
Run this once to initialize the database tables
"""

import os
import psycopg2
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def setup_database():
    """Create the required database tables"""

    # Try DATABASE_URL first, then individual variables
    db_url = os.getenv('DATABASE_URL')

    if not db_url:
        # Try individual environment variables
        db_host = os.getenv('DB_HOST')
        db_port = os.getenv('DB_PORT')
        db_name = os.getenv('DB_NAME')
        db_user = os.getenv('DB_USER')
        db_password = os.getenv('DB_PASSWORD')

        if all([db_host, db_port, db_name, db_user, db_password]):
            db_url = f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
            print("🔧 Using individual database environment variables...")
        else:
            print("❌ Neither DATABASE_URL nor individual DB_* environment variables are set")
            return False

    try:
        print(f"🔧 Connecting to database...")
        print(f"   Connection string: {db_url.replace(db_url.split('@')[0].split(':')[1], '***') if '@' in db_url else '***'}")
        conn = psycopg2.connect(db_url)
        cursor = conn.cursor()

        # Check current user and database
        cursor.execute("SELECT current_user, current_database();")
        user, database = cursor.fetchone()
        print(f"   Connected as: {user} to database: {database}")

        print("📋 Creating participants table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS participants (
                id SERIAL PRIMARY KEY,
                user_id TEXT UNIQUE,
                name TEXT,
                wishlist TEXT,
                address TEXT,
                is_creator BOOLEAN DEFAULT FALSE
            )
        """)

        print("📋 Creating pairings table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pairings (
                id SERIAL PRIMARY KEY,
                giver_id TEXT,
                receiver_id TEXT,
                FOREIGN KEY(giver_id) REFERENCES participants(user_id),
                FOREIGN KEY(receiver_id) REFERENCES participants(user_id)
            )
        """)

        conn.commit()
        cursor.close()
        conn.close()

        print("✅ Database tables created successfully!")
        return True

    except psycopg2.errors.InsufficientPrivilege as e:
        print(f"❌ Permission denied: {e}")
        print("💡 The database user doesn't have CREATE permissions.")
        print("💡 Please ask your database administrator to run:")
        print()
        print("CREATE TABLE IF NOT EXISTS participants (")
        print("    id SERIAL PRIMARY KEY,")
        print("    user_id TEXT UNIQUE,")
        print("    name TEXT,")
        print("    wishlist TEXT,")
        print("    address TEXT,")
        print("    is_creator BOOLEAN DEFAULT FALSE")
        print(");")
        print()
        print("CREATE TABLE IF NOT EXISTS pairings (")
        print("    id SERIAL PRIMARY KEY,")
        print("    giver_id TEXT,")
        print("    receiver_id TEXT,")
        print("    FOREIGN KEY(giver_id) REFERENCES participants(user_id),")
        print("    FOREIGN KEY(receiver_id) REFERENCES participants(user_id)")
        print(");")
        return False

    except Exception as e:
        print(f"❌ Error setting up database: {e}")
        return False

if __name__ == "__main__":
    print("🎄 Secret Santa Bot - Database Setup")
    print("=" * 40)

    success = setup_database()

    if success:
        print("\n🎉 Setup complete! Your bot is ready to use.")
    else:
        print("\n⚠️  Setup failed. Please resolve the issues above.")

    print("\nTo run the bot: python src/bot.py")
