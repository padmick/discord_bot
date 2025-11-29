import psycopg2
import os
from typing import Dict, List, Optional
import random
from urllib.parse import urlparse

class DatabaseManager:
    def __init__(self):
        db_url = os.getenv('DATABASE_URL')
        if not db_url:
            # Try to construct URL from individual variables, using defaultdb for dev databases
            db_host = os.getenv('DB_HOST')
            db_port = os.getenv('DB_PORT')
            db_user = os.getenv('DB_USER')
            db_password = os.getenv('DB_PASSWORD')

            if all([db_host, db_port, db_user, db_password]):
                # Always use defaultdb for dev databases as per DigitalOcean requirements
                db_url = f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/defaultdb"
                print("🔧 Using defaultdb for application (required for dev database permissions)")

        if not db_url:
            raise ValueError("DATABASE_URL environment variable is not set")

        self.conn = psycopg2.connect(db_url)
        self.cursor = self.conn.cursor()
        self._tables_created = False
        self.initialize_database()

    def initialize_database(self):
        """Initialize database tables automatically on startup"""
        print("Initializing database...")

        # SQL to create tables
        create_participants_sql = """
            CREATE TABLE IF NOT EXISTS participants (
                id SERIAL PRIMARY KEY,
                user_id TEXT UNIQUE,
                name TEXT,
                wishlist TEXT,
                address TEXT,
                is_creator BOOLEAN DEFAULT FALSE
            )
        """

        create_pairings_sql = """
            CREATE TABLE IF NOT EXISTS pairings (
                id SERIAL PRIMARY KEY,
                giver_id TEXT,
                receiver_id TEXT,
                FOREIGN KEY(giver_id) REFERENCES participants(user_id),
                FOREIGN KEY(receiver_id) REFERENCES participants(user_id)
            )
        """

        try:
            # Try to create tables
            self.cursor.execute(create_participants_sql)
            self.cursor.execute(create_pairings_sql)
            self.conn.commit()
            print("✓ Database tables created successfully")

        except psycopg2.errors.InsufficientPrivilege as e:
            print(f"⚠️  Permission denied creating tables: {e}")
            # Rollback the aborted transaction
            self.conn.rollback()
            print("Checking if tables already exist...")
            if self._verify_tables_exist():
                print("✓ Using existing database tables")
            else:
                print("✓ App will create tables on-demand when first needed")
                # Don't fail - allow app to start and create tables later
                self._tables_created = False

        except Exception as e:
            print(f"❌ Unexpected database error: {e}")
            raise

    def _verify_tables_exist(self):
        """Verify that required tables exist and have correct structure"""
        try:
            # Make sure we're not in an aborted transaction
            self.conn.rollback()

            # Try to check if tables exist by querying information_schema
            try:
                self.cursor.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables
                        WHERE table_name = 'participants'
                    )
                """)
                participants_exists = self.cursor.fetchone()[0]

                self.cursor.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables
                        WHERE table_name = 'pairings'
                    )
                """)
                pairings_exists = self.cursor.fetchone()[0]

                if participants_exists and pairings_exists:
                    return True

            except psycopg2.Error:
                # If information_schema query fails (permission issues), try direct table access
                print("⚠️  Cannot query information_schema, trying direct table access...")

            # Fallback: try to query the tables directly
            try:
                self.cursor.execute("SELECT 1 FROM participants LIMIT 1")
                self.cursor.fetchone()  # Consume the result
                self.cursor.execute("SELECT 1 FROM pairings LIMIT 1")
                self.cursor.fetchone()  # Consume the result
                return True
            except psycopg2.errors.UndefinedTable:
                return False
            except psycopg2.Error:
                # Other database errors, assume tables don't exist
                return False

            return False

        except Exception as e:
            print(f"❌ Error verifying database tables: {e}")
            return False

    def _ensure_tables_exist(self):
        """Ensure tables exist before performing operations - always tries to create if needed"""
        if not self._tables_created:
            # Always rollback any aborted transactions first
            self.conn.rollback()

            try:
                # Try to create tables
                create_participants_sql = """
                    CREATE TABLE IF NOT EXISTS participants (
                        id SERIAL PRIMARY KEY,
                        user_id TEXT UNIQUE,
                        name TEXT,
                        wishlist TEXT,
                        address TEXT,
                        is_creator BOOLEAN DEFAULT FALSE
                    )
                """
                create_pairings_sql = """
                    CREATE TABLE IF NOT EXISTS pairings (
                        id SERIAL PRIMARY KEY,
                        giver_id TEXT,
                        receiver_id TEXT,
                        FOREIGN KEY(giver_id) REFERENCES participants(user_id),
                        FOREIGN KEY(receiver_id) REFERENCES participants(user_id)
                    )
                """

                self.cursor.execute(create_participants_sql)
                self.cursor.execute(create_pairings_sql)
                self.conn.commit()
                self._tables_created = True
                print("✓ Database tables created on-demand")

            except psycopg2.errors.InsufficientPrivilege:
                # Cannot create tables due to permissions
                self.conn.rollback()
                print("⚠️  Cannot create database tables (insufficient permissions)")
                # Check if tables actually exist despite permission error
                if self._verify_tables_exist():
                    self._tables_created = True
                    print("✓ Tables exist - using them despite permission restrictions")
                else:
                    print("🔧 Tables don't exist and can't be created")
                    print("🔧 Bot will show friendly error messages to users")
                    # Mark as "will try again" - don't set _tables_created = True
                    # This allows operations to proceed and show user-friendly errors
            except Exception as e:
                print(f"❌ Unexpected error creating tables: {e}")
                self.conn.rollback()
                # Continue anyway - let individual operations handle errors gracefully

    def add_participant(self, user_id: str, name: str, is_creator: bool = False):
        """Add a participant to the Secret Santa event"""
        try:
            self._ensure_tables_exist()
            self.cursor.execute("""
                INSERT INTO participants (user_id, name, is_creator)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id)
                DO UPDATE SET name = EXCLUDED.name, is_creator = EXCLUDED.is_creator
            """, (user_id, name, is_creator))
            self.conn.commit()
            return True
        except psycopg2.errors.UndefinedTable:
            raise Exception("❌ Database tables are not available. Please contact the bot administrator to set up the database.")
        except Exception as e:
            print(f"Error adding participant: {e}")
            raise

    def set_wishlist(self, user_id: str, wishlist: str):
        self.cursor.execute("""
            UPDATE participants SET wishlist = %s
            WHERE user_id = %s
        """, (wishlist, user_id))
        self.conn.commit()

    def get_pairings(self) -> List[Dict[str, str]]:
        self._ensure_tables_exist()
        self.cursor.execute("""
            SELECT p1.name AS giver_name, p2.name AS receiver_name
            FROM pairings
            JOIN participants p1 ON pairings.giver_id = p1.user_id
            JOIN participants p2 ON pairings.receiver_id = p2.user_id
        """)
        return [{"giver": row[0], "receiver": row[1]} for row in self.cursor.fetchall()]

    def get_all_participants(self) -> List[Dict[str, str]]:
        try:
            self._ensure_tables_exist()
            self.cursor.execute("""
                SELECT user_id, name, wishlist
                FROM participants
            """)
            rows = self.cursor.fetchall()
            return [{"user_id": row[0], "name": row[1], "wishlist": row[2]} for row in rows]
        except psycopg2.errors.UndefinedTable:
            return []  # No participants if tables don't exist
        except Exception as e:
            print(f"Error getting participants: {e}")
            return []

    def close_connection(self):
        self.cursor.close()
        self.conn.close()

    def get_gifter_for_user(self, user_id: str) -> Optional[str]:
        """Get the ID of the person giving a gift to this user"""
        self._ensure_tables_exist()
        self.cursor.execute("""
            SELECT giver_id FROM pairings WHERE receiver_id = %s
        """, (user_id,))
        result = self.cursor.fetchone()
        return result[0] if result else None

    def get_giftee_for_user(self, user_id: str) -> Optional[str]:
        """Get the ID of the person this user is giving a gift to"""
        self._ensure_tables_exist()
        self.cursor.execute("""
            SELECT receiver_id FROM pairings WHERE giver_id = %s
        """, (user_id,))
        result = self.cursor.fetchone()
        return result[0] if result else None

    def get_partner_info(self, user_id: str) -> Optional[Dict[str, str]]:
        """Get information about the user's gift recipient"""
        self._ensure_tables_exist()
        self.cursor.execute("""
            SELECT p.name, p.wishlist
            FROM pairings 
            JOIN participants p ON p.user_id = pairings.receiver_id
            WHERE giver_id = %s
        """, (user_id,))
        result = self.cursor.fetchone()
        return {'name': result[0], 'wishlist': result[1] or "No wishlist set"} if result else None

    def assign_partners(self, participants: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """
        Assign Secret Santa partners and store in database
        Returns list of pairings with giver, receiver, and receiver's info
        """
        self._ensure_tables_exist()
        if len(participants) < 2:
            raise ValueError("Need at least 2 participants to create pairings")

        # Clear existing pairings
        self.cursor.execute("DELETE FROM pairings")
        
        # Create a copy of participants for receivers
        receivers = participants.copy()
        pairings = []
        
        for giver in participants:
            # Find valid receivers (excluding self)
            valid_receivers = [r for r in receivers if r['user_id'] != giver['user_id']]
            
            if not valid_receivers:
                # If no valid receivers, reset and try again
                return self.assign_partners(participants)
            
            # Randomly select a receiver
            receiver = random.choice(valid_receivers)
            receivers.remove(receiver)
            
            # Get receiver's address
            self.cursor.execute("SELECT address FROM participants WHERE user_id = %s", (receiver['user_id'],))
            address_result = self.cursor.fetchone()
            receiver_address = address_result[0] if address_result else "No address set"
            
            # Store pairing in database
            self.cursor.execute("""
                INSERT INTO pairings (giver_id, receiver_id)
                VALUES (%s, %s)
            """, (giver['user_id'], receiver['user_id']))
            
            # Add to pairings list
            pairings.append({
                'giver': giver['user_id'],
                'receiver': receiver['user_id'],
                'receiver_wishlist': receiver.get('wishlist', "No wishlist set"),
                'receiver_address': receiver_address
            })
        
        self.conn.commit()
        return pairings

    def cancel_secret_santa(self):
        """Cancel the Secret Santa event by clearing all data"""
        self._ensure_tables_exist()
        self.cursor.execute("DELETE FROM pairings")
        self.cursor.execute("DELETE FROM participants")
        self.conn.commit()

    def is_event_active(self) -> bool:
        """Check if there's an active Secret Santa event"""
        try:
            self._ensure_tables_exist()
            self.cursor.execute("SELECT COUNT(*) FROM participants")
            count = self.cursor.fetchone()[0]
            return count > 0
        except psycopg2.errors.UndefinedTable:
            return False  # No active event if tables don't exist
        except Exception as e:
            print(f"Error checking event status: {e}")
            return False

    def set_address(self, user_id: str, address: str):
        self._ensure_tables_exist()
        self.cursor.execute("""
            UPDATE participants SET address = %s
            WHERE user_id = %s
        """, (address, user_id))
        self.conn.commit()

    def check_missing_info(self) -> List[Dict[str, str]]:
        """Returns list of users with missing wishlist or address"""
        self._ensure_tables_exist()
        self.cursor.execute("""
            SELECT user_id, name, wishlist, address
            FROM participants
            WHERE wishlist IS NULL OR address IS NULL
        """)
        rows = self.cursor.fetchall()
        return [{
            'user_id': row[0],
            'name': row[1],
            'missing_wishlist': row[2] is None,
            'missing_address': row[3] is None
        } for row in rows]

    def is_creator_or_admin(self, user_id: str) -> bool:
        """Check if user is the creator of the current event"""
        self._ensure_tables_exist()
        self.cursor.execute("""
            SELECT is_creator 
            FROM participants 
            WHERE user_id = %s
        """, (user_id,))
        result = self.cursor.fetchone()
        return bool(result and result[0])
