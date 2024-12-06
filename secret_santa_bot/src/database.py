import sqlite3
from typing import Dict, List, Optional
import random

class DatabaseManager:
    def __init__(self, db_name: str = "secret_santa.db"):
        self.conn = sqlite3.connect(db_name)
        self.cursor = self.conn.cursor()
        self.create_tables()

    def create_tables(self):
        # Create initial tables
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS participants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT UNIQUE,
                name TEXT,
                wishlist TEXT
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS pairings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                giver_id TEXT,
                receiver_id TEXT,
                FOREIGN KEY(giver_id) REFERENCES participants(user_id),
                FOREIGN KEY(receiver_id) REFERENCES participants(user_id)
            )
        """)
        
        # Check if address and is_creator columns exist, if not add them
        self.cursor.execute("PRAGMA table_info(participants)")
        columns = [column[1] for column in self.cursor.fetchall()]
        if 'address' not in columns:
            self.cursor.execute("ALTER TABLE participants ADD COLUMN address TEXT")
        if 'is_creator' not in columns:
            self.cursor.execute("ALTER TABLE participants ADD COLUMN is_creator BOOLEAN DEFAULT 0")
        
        self.conn.commit()

    def add_participant(self, user_id: str, name: str, is_creator: bool = False):
        """Add a participant to the Secret Santa event"""
        self.cursor.execute("""
            INSERT OR REPLACE INTO participants 
            (user_id, name, is_creator)
            VALUES (?, ?, ?)
        """, (user_id, name, 1 if is_creator else 0))
        self.conn.commit()

    def set_wishlist(self, user_id: str, wishlist: str):
        self.cursor.execute("""
            UPDATE participants SET wishlist = ?
            WHERE user_id = ?
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
        self.conn.close()

    def get_gifter_for_user(self, user_id: str) -> Optional[str]:
        """Get the ID of the person giving a gift to this user"""
        self.cursor.execute("""
            SELECT giver FROM pairings WHERE receiver = ?
        """, (user_id,))
        result = self.cursor.fetchone()
        return result[0] if result else None

    def get_giftee_for_user(self, user_id: str) -> Optional[str]:
        """Get the ID of the person this user is giving a gift to"""
        self.cursor.execute("""
            SELECT receiver FROM pairings WHERE giver = ?
        """, (user_id,))
        result = self.cursor.fetchone()
        return result[0] if result else None

    def get_partner_info(self, user_id: str) -> Optional[Dict[str, str]]:
        """Get information about the user's gift recipient"""
        self.cursor.execute("""
            SELECT p.name, p.wishlist
            FROM pairings 
            JOIN participants p ON p.user_id = pairings.receiver_id
            WHERE giver_id = ?
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
            self.cursor.execute("SELECT address FROM participants WHERE user_id = ?", (receiver['user_id'],))
            address_result = self.cursor.fetchone()
            receiver_address = address_result[0] if address_result else "No address set"
            
            # Store pairing in database
            self.cursor.execute("""
                INSERT INTO pairings (giver_id, receiver_id)
                VALUES (?, ?)
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
            UPDATE participants SET address = ?
            WHERE user_id = ?
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
            WHERE user_id = ?
        """, (user_id,))
        result = self.cursor.fetchone()
        return bool(result and result[0])
