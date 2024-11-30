import sqlite3
from typing import Dict, List, Optional

class DatabaseManager:
    def __init__(self, db_name: str = "secret_santa.db"):
        self.conn = sqlite3.connect(db_name)
        self.cursor = self.conn.cursor()
        self.create_tables()

    def create_tables(self):
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
        self.conn.commit()

    def add_participant(self, user_id: str, name: str):
        self.cursor.execute("""
            INSERT OR REPLACE INTO participants (user_id, name)
            VALUES (?, ?)
        """, (user_id, name))
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
