import psycopg2
import os
from typing import Dict, List, Optional
import random

class DatabaseManager:
    def __init__(self):
        db_url = os.getenv('DATABASE_URL')
        if not db_url:
            raise ValueError("DATABASE_URL environment variable is not set")

        print(f"🔧 Connecting to database...")
        self.conn = psycopg2.connect(db_url)
        self.cursor = self.conn.cursor()
        self.create_tables()

    def create_tables(self):
        """Create database tables on startup"""
        print("📋 Creating database tables...")

        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS participants (
                id SERIAL PRIMARY KEY,
                user_id TEXT UNIQUE,
                name TEXT,
                wishlist TEXT,
                address TEXT,
                is_creator BOOLEAN DEFAULT FALSE
            )
        """)

        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS pairings (
                id SERIAL PRIMARY KEY,
                giver_id TEXT,
                receiver_id TEXT,
                FOREIGN KEY(giver_id) REFERENCES participants(user_id),
                FOREIGN KEY(receiver_id) REFERENCES participants(user_id)
            )
        """)

        self.conn.commit()
        print("✅ Database tables ready!")

    def add_participant(self, user_id: str, name: str, is_creator: bool = False):
        """Add a participant to the Secret Santa event"""
        self.cursor.execute("""
            INSERT INTO participants (user_id, name, is_creator)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id)
            DO UPDATE SET name = EXCLUDED.name, is_creator = EXCLUDED.is_creator
        """, (user_id, name, is_creator))
        self.conn.commit()

    def set_wishlist(self, user_id: str, wishlist: str):
        self.cursor.execute("""
            UPDATE participants SET wishlist = %s
            WHERE user_id = %s
        """, (wishlist, user_id))
        self.conn.commit()

    def get_pairings(self) -> List[Dict[str, str]]:
        self.cursor.execute("""
            SELECT p1.name AS giver_name, p2.name AS receiver_name
            FROM pairings
            JOIN participants p1 ON pairings.giver_id = p1.user_id
            JOIN participants p2 ON pairings.receiver_id = p2.user_id
        """)
        return [{"giver": row[0], "receiver": row[1]} for row in self.cursor.fetchall()]

    def get_all_participants(self) -> List[Dict[str, str]]:
        self.cursor.execute("""
            SELECT user_id, name, wishlist
            FROM participants
        """)
        rows = self.cursor.fetchall()
        return [{"user_id": row[0], "name": row[1], "wishlist": row[2]} for row in rows]

    def close_connection(self):
        self.cursor.close()
        self.conn.close()

    def get_gifter_for_user(self, user_id: str) -> Optional[str]:
        """Get the ID of the person giving a gift to this user"""
        self.cursor.execute("""
            SELECT giver_id FROM pairings WHERE receiver_id = %s
        """, (user_id,))
        result = self.cursor.fetchone()
        return result[0] if result else None

    def get_giftee_for_user(self, user_id: str) -> Optional[str]:
        """Get the ID of the person this user is giving a gift to"""
        self.cursor.execute("""
            SELECT receiver_id FROM pairings WHERE giver_id = %s
        """, (user_id,))
        result = self.cursor.fetchone()
        return result[0] if result else None

    def get_partner_info(self, user_id: str) -> Optional[Dict[str, str]]:
        """Get information about the user's gift recipient"""
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
        self.cursor.execute("DELETE FROM pairings")
        self.cursor.execute("DELETE FROM participants")
        self.conn.commit()

    def is_event_active(self) -> bool:
        """Check if there's an active Secret Santa event"""
        self.cursor.execute("SELECT COUNT(*) FROM participants")
        count = self.cursor.fetchone()[0]
        return count > 0

    def set_address(self, user_id: str, address: str):
        self.cursor.execute("""
            UPDATE participants SET address = %s
            WHERE user_id = %s
        """, (address, user_id))
        self.conn.commit()

    def check_missing_info(self) -> List[Dict[str, str]]:
        """Returns list of users with missing wishlist or address"""
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
        self.cursor.execute("""
            SELECT is_creator 
            FROM participants 
            WHERE user_id = %s
        """, (user_id,))
        result = self.cursor.fetchone()
        return bool(result and result[0])
