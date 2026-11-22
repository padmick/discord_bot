"""Postgres storage for per-server Secret Santa events."""

from __future__ import annotations

import os
from typing import Any, Callable, Iterable

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

EVENT_FIELDS = (
    "id, guild_id, name, phase, organizer_id, deadline, "
    "last_profile_reminder, last_shipping_reminder, "
    "profile_reminders, shipping_reminders, reminder_interval_days, shipping_lead_days"
)


def _qualified(columns: str, table: str) -> str:
    """Prefix each column so a join does not see two tables' id or name."""
    return ", ".join(f"{table}.{name.strip()}" for name in columns.split(","))


PARTICIPANT_FIELDS = (
    "event_id, user_id, name, wishlist, address, do_not_gift, notes, digital_only, budget"
)
PROFILE_FIELDS = {
    "name",
    "wishlist",
    "address",
    "do_not_gift",
    "notes",
    "digital_only",
    "budget",
}
EVENT_UPDATE_FIELDS = {
    "phase",
    "deadline",
    "name",
    "last_profile_reminder",
    "last_shipping_reminder",
    "profile_reminders",
    "shipping_reminders",
    "reminder_interval_days",
    "shipping_lead_days",
}


class DatabaseManager:
    """Connection-pooled access to one Postgres database.

    Each call checks out its own connection. A dropped connection is closed
    and the call is tried once more.
    """

    def __init__(self, dsn: str | None = None):
        self.dsn = dsn or os.getenv("DATABASE_URL")
        if not self.dsn:
            raise ValueError("DATABASE_URL environment variable is not set")
        self.pool = ThreadedConnectionPool(1, 5, self.dsn)
        legacy_count = self._run(self._migrate)
        if legacy_count is not None:
            print(f"Legacy participants kept in participants_legacy: {legacy_count}")

    def close(self) -> None:
        self.pool.closeall()

    def ping(self) -> bool:
        try:
            self._run(lambda cur: cur.execute("SELECT 1"))
            return True
        except Exception:
            return False

    def reset(self) -> None:
        """Delete event data. Schema migrations stay in place."""
        self._run(lambda cur: cur.execute("TRUNCATE TABLE events RESTART IDENTITY CASCADE"))

    def legacy_participant_count(self) -> int:
        def query(cur):
            if not self._table_exists(cur, "participants_legacy"):
                return 0
            cur.execute("SELECT COUNT(*) AS count FROM participants_legacy")
            return int(cur.fetchone()["count"])

        return self._run(query)

    def create_event(self, guild_id: str, name: str, organizer_id: str) -> dict[str, Any]:
        def query(cur):
            cur.execute(
                f"""
                INSERT INTO events (guild_id, name, organizer_id)
                VALUES (%s, %s, %s)
                RETURNING {EVENT_FIELDS}
                """,
                (str(guild_id), name, str(organizer_id)),
            )
            return dict(cur.fetchone())

        return self._run(query)

    def get_event(self, event_id: int) -> dict[str, Any] | None:
        def query(cur):
            cur.execute(f"SELECT {EVENT_FIELDS} FROM events WHERE id = %s", (event_id,))
            row = cur.fetchone()
            return dict(row) if row else None

        return self._run(query)

    def get_open_event(self, guild_id: str) -> dict[str, Any] | None:
        def query(cur):
            cur.execute(
                f"""
                SELECT {EVENT_FIELDS}
                FROM events
                WHERE guild_id = %s AND phase <> 'done'
                """,
                (str(guild_id),),
            )
            row = cur.fetchone()
            return dict(row) if row else None

        return self._run(query)

    def get_latest_event(self, guild_id: str) -> dict[str, Any] | None:
        def query(cur):
            cur.execute(
                f"""
                SELECT {EVENT_FIELDS}
                FROM events
                WHERE guild_id = %s
                ORDER BY id DESC
                LIMIT 1
                """,
                (str(guild_id),),
            )
            row = cur.fetchone()
            return dict(row) if row else None

        return self._run(query)

    def list_actionable_events(self) -> list[dict[str, Any]]:
        def query(cur):
            cur.execute(
                f"""
                SELECT {EVENT_FIELDS}
                FROM events
                WHERE phase <> 'done'
                ORDER BY id
                """
            )
            return [dict(row) for row in cur.fetchall()]

        return self._run(query)

    def update_event(self, event_id: int, **fields: Any) -> None:
        assignments, values = _assignments(fields, EVENT_UPDATE_FIELDS)

        def query(cur):
            cur.execute(
                f"UPDATE events SET {assignments} WHERE id = %s",
                (*values, event_id),
            )

        self._run(query)

    def add_feedback(self, guild_id: str, user_id: str, kind: str, body: str) -> None:
        def query(cur):
            cur.execute(
                """
                INSERT INTO feedback (guild_id, user_id, kind, body)
                VALUES (%s, %s, %s, %s)
                """,
                (str(guild_id or ""), str(user_id), kind, body),
            )

        self._run(query)

    def list_feedback(self, guild_id: str, limit: int = 10) -> list[dict[str, Any]]:
        def query(cur):
            cur.execute(
                """
                SELECT user_id, kind, body
                FROM feedback
                WHERE guild_id = %s
                ORDER BY id DESC
                LIMIT %s
                """,
                (str(guild_id), int(limit)),
            )
            return [dict(row) for row in cur.fetchall()]

        return self._run(query)

    def claim_changelog(self, user_id: str, version: int) -> bool:
        def query(cur):
            cur.execute(
                """
                INSERT INTO changelog_seen (user_id, version)
                VALUES (%s, %s)
                ON CONFLICT (user_id) DO UPDATE
                SET version = EXCLUDED.version, seen_at = NOW()
                WHERE changelog_seen.version < EXCLUDED.version
                RETURNING user_id
                """,
                (str(user_id), int(version)),
            )
            return cur.fetchone() is not None

        return self._run(query)

    def claim_tip(self, user_id: str, tip_id: str) -> bool:
        def query(cur):
            cur.execute(
                """
                INSERT INTO tips_seen (user_id, tip_id)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
                RETURNING user_id
                """,
                (str(user_id), tip_id),
            )
            return cur.fetchone() is not None

        return self._run(query)

    def delete_event(self, event_id: int) -> None:
        self._run(lambda cur: cur.execute("DELETE FROM events WHERE id = %s", (event_id,)))

    def add_participant(self, event_id: int, user_id: str, name: str) -> dict[str, Any]:
        def query(cur):
            cur.execute(
                f"""
                INSERT INTO participants (event_id, user_id, name)
                VALUES (%s, %s, %s)
                ON CONFLICT (event_id, user_id)
                DO UPDATE SET name = EXCLUDED.name
                RETURNING {PARTICIPANT_FIELDS}
                """,
                (event_id, str(user_id), name),
            )
            return dict(cur.fetchone())

        return self._run(query)

    def get_participant(self, event_id: int, user_id: str) -> dict[str, Any] | None:
        def query(cur):
            cur.execute(
                f"""
                SELECT {PARTICIPANT_FIELDS}
                FROM participants
                WHERE event_id = %s AND user_id = %s
                """,
                (event_id, str(user_id)),
            )
            row = cur.fetchone()
            return dict(row) if row else None

        return self._run(query)

    def list_participants(self, event_id: int) -> list[dict[str, Any]]:
        def query(cur):
            cur.execute(
                f"""
                SELECT {PARTICIPANT_FIELDS}
                FROM participants
                WHERE event_id = %s
                ORDER BY id
                """,
                (event_id,),
            )
            return [dict(row) for row in cur.fetchall()]

        return self._run(query)

    def list_open_participations(self, user_id: str) -> list[dict[str, Any]]:
        def query(cur):
            cur.execute(
                f"""
                SELECT {_qualified(EVENT_FIELDS, "events")}
                FROM events
                JOIN participants ON participants.event_id = events.id
                WHERE participants.user_id = %s AND events.phase <> 'done'
                ORDER BY events.id
                """,
                (str(user_id),),
            )
            return [dict(row) for row in cur.fetchall()]

        return self._run(query)

    def update_profile(self, event_id: int, user_id: str, **fields: Any) -> int:
        assignments, values = _assignments(fields, PROFILE_FIELDS)

        def query(cur):
            cur.execute(
                f"""
                UPDATE participants
                SET {assignments}
                WHERE event_id = %s AND user_id = %s
                """,
                (*values, event_id, str(user_id)),
            )
            return cur.rowcount

        return self._run(query)

    def delete_participant(self, event_id: int, user_id: str) -> bool:
        def query(cur):
            cur.execute(
                """
                DELETE FROM exclusions
                WHERE event_id = %s AND (user_id = %s OR excluded_user_id = %s)
                """,
                (event_id, str(user_id), str(user_id)),
            )
            cur.execute(
                """
                DELETE FROM pairings
                WHERE event_id = %s AND (giver_id = %s OR receiver_id = %s)
                """,
                (event_id, str(user_id), str(user_id)),
            )
            cur.execute(
                """
                DELETE FROM anonymous_notes
                WHERE event_id = %s AND (sender_id = %s OR recipient_id = %s)
                """,
                (event_id, str(user_id), str(user_id)),
            )
            cur.execute(
                "DELETE FROM participants WHERE event_id = %s AND user_id = %s",
                (event_id, str(user_id)),
            )
            return cur.rowcount > 0

        return self._run(query)

    def add_exclusion_pair(self, event_id: int, user_id: str, other_id: str) -> None:
        def query(cur):
            for giver, receiver in ((str(user_id), str(other_id)), (str(other_id), str(user_id))):
                cur.execute(
                    """
                    INSERT INTO exclusions (event_id, user_id, excluded_user_id)
                    VALUES (%s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (event_id, giver, receiver),
                )

        self._run(query)

    def remove_exclusion_pair(self, event_id: int, user_id: str, other_id: str) -> int:
        def query(cur):
            cur.execute(
                """
                DELETE FROM exclusions
                WHERE event_id = %s
                  AND (
                    (user_id = %s AND excluded_user_id = %s)
                    OR (user_id = %s AND excluded_user_id = %s)
                  )
                """,
                (event_id, str(user_id), str(other_id), str(other_id), str(user_id)),
            )
            return cur.rowcount

        return self._run(query)

    def list_exclusions(self, event_id: int) -> set[tuple[str, str]]:
        def query(cur):
            cur.execute(
                """
                SELECT user_id, excluded_user_id
                FROM exclusions
                WHERE event_id = %s
                """,
                (event_id,),
            )
            return {(row["user_id"], row["excluded_user_id"]) for row in cur.fetchall()}

        return self._run(query)

    def replace_pairings(self, event_id: int, pairs: Iterable[tuple[str, str, str]]) -> None:
        rows = list(pairs)

        def query(cur):
            cur.execute(
                """
                SELECT giver_id, receiver_id, tracking_url
                FROM pairings
                WHERE event_id = %s
                """,
                (event_id,),
            )
            kept = {
                (row["giver_id"], row["receiver_id"]): row["tracking_url"] or ""
                for row in cur.fetchall()
            }
            cur.execute("DELETE FROM pairings WHERE event_id = %s", (event_id,))
            for giver_id, receiver_id, gift_status in rows:
                pair = (str(giver_id), str(receiver_id))
                cur.execute(
                    """
                    INSERT INTO pairings (event_id, giver_id, receiver_id, gift_status, tracking_url)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (event_id, pair[0], pair[1], gift_status, kept.get(pair, "")),
                )

        self._run(query)

    def list_pairings(self, event_id: int) -> list[dict[str, str]]:
        def query(cur):
            cur.execute(
                """
                SELECT giver_id, receiver_id, gift_status, tracking_url
                FROM pairings
                WHERE event_id = %s
                ORDER BY id
                """,
                (event_id,),
            )
            return [dict(row) for row in cur.fetchall()]

        return self._run(query)

    def set_gift_status(
        self,
        event_id: int,
        giver_id: str,
        status: str,
        tracking_url: str | None = None,
    ) -> int:
        def query(cur):
            if tracking_url is None:
                cur.execute(
                    """
                    UPDATE pairings
                    SET gift_status = %s
                    WHERE event_id = %s AND giver_id = %s
                    """,
                    (status, event_id, str(giver_id)),
                )
            else:
                cur.execute(
                    """
                    UPDATE pairings
                    SET gift_status = %s, tracking_url = %s
                    WHERE event_id = %s AND giver_id = %s
                    """,
                    (status, tracking_url, event_id, str(giver_id)),
                )
            return cur.rowcount

        return self._run(query)

    def save_note(
        self,
        event_id: int,
        sender_id: str,
        recipient_id: str,
        body: str,
        image_url: str = "",
    ) -> None:
        def query(cur):
            cur.execute(
                """
                INSERT INTO anonymous_notes (event_id, sender_id, recipient_id, body, image_url)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (event_id, sender_id, recipient_id) DO UPDATE
                SET body = EXCLUDED.body,
                    image_url = EXCLUDED.image_url,
                    created_at = NOW()
                """,
                (event_id, str(sender_id), str(recipient_id), body, image_url or ""),
            )

        self._run(query)

    def list_notes(self, event_id: int, user_id: str) -> list[dict[str, Any]]:
        def query(cur):
            cur.execute(
                """
                SELECT sender_id, recipient_id, body, image_url
                FROM anonymous_notes
                WHERE event_id = %s AND (sender_id = %s OR recipient_id = %s)
                ORDER BY created_at DESC
                """,
                (event_id, str(user_id), str(user_id)),
            )
            return [dict(row) for row in cur.fetchall()]

        return self._run(query)

    def _run(self, fn: Callable):
        last_error: Exception | None = None
        for _attempt in range(2):
            conn = self.pool.getconn()
            close_conn = False
            try:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    result = fn(cur)
                conn.commit()
                return result
            except psycopg2.OperationalError as exc:
                last_error = exc
                close_conn = True
                _rollback(conn)
            except Exception:
                _rollback(conn)
                raise
            finally:
                self.pool.putconn(conn, close=close_conn)
        assert last_error is not None
        raise last_error

    def _migrate(self, cur) -> int | None:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute("SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations")
        version = int(cur.fetchone()["version"])
        legacy_count = None
        if version < 1:
            legacy_count = self._migrate_v1(cur)
        if self._schema_version(cur) < 2:
            self._migrate_v2(cur)
        if self._schema_version(cur) < 3:
            self._migrate_v3(cur)
        if self._schema_version(cur) < 4:
            self._migrate_v4(cur)
        if self._schema_version(cur) < 5:
            self._migrate_v5(cur)
        return legacy_count

    def _migrate_v1(self, cur) -> int | None:
        legacy_count = None
        participant_columns = self._column_names(cur, "participants")
        if participant_columns and "event_id" not in participant_columns:
            cur.execute("ALTER TABLE participants RENAME TO participants_legacy")
            cur.execute("SELECT COUNT(*) AS count FROM participants_legacy")
            legacy_count = int(cur.fetchone()["count"])
            pairing_columns = self._column_names(cur, "pairings")
            if pairing_columns and "event_id" not in pairing_columns:
                cur.execute("ALTER TABLE pairings RENAME TO pairings_legacy")

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id SERIAL PRIMARY KEY,
                guild_id TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT 'Secret Santa',
                phase TEXT NOT NULL DEFAULT 'signup',
                organizer_id TEXT NOT NULL,
                deadline DATE,
                last_profile_reminder DATE,
                last_shipping_reminder DATE,
                profile_reminders BOOLEAN NOT NULL DEFAULT TRUE,
                shipping_reminders BOOLEAN NOT NULL DEFAULT TRUE,
                reminder_interval_days INTEGER NOT NULL DEFAULT 7,
                shipping_lead_days INTEGER NOT NULL DEFAULT 3,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT events_phase_check CHECK (phase IN ('signup', 'locked', 'drawn', 'done'))
            )
            """
        )
        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS one_open_event_per_guild
            ON events (guild_id)
            WHERE phase <> 'done'
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS participants (
                id SERIAL PRIMARY KEY,
                event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                wishlist TEXT,
                address TEXT,
                do_not_gift TEXT,
                notes TEXT,
                digital_only BOOLEAN NOT NULL DEFAULT FALSE,
                budget TEXT,
                UNIQUE (event_id, user_id)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS exclusions (
                event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                user_id TEXT NOT NULL,
                excluded_user_id TEXT NOT NULL,
                PRIMARY KEY (event_id, user_id, excluded_user_id)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS pairings (
                id SERIAL PRIMARY KEY,
                event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                giver_id TEXT NOT NULL,
                receiver_id TEXT NOT NULL,
                gift_status TEXT NOT NULL DEFAULT 'not_bought',
                tracking_url TEXT NOT NULL DEFAULT '',
                UNIQUE (event_id, giver_id),
                UNIQUE (event_id, receiver_id),
                CONSTRAINT pairings_status_check CHECK (
                    gift_status IN ('not_bought', 'bought', 'shipped', 'received')
                )
            )
            """
        )
        cur.execute("INSERT INTO schema_migrations (version) VALUES (1)")
        return legacy_count

    def _migrate_v2(self, cur) -> None:
        cur.execute("ALTER TABLE participants DROP COLUMN IF EXISTS shirt_size")
        cur.execute(
            """
            ALTER TABLE events
            ADD COLUMN IF NOT EXISTS profile_reminders BOOLEAN NOT NULL DEFAULT TRUE
            """
        )
        cur.execute(
            """
            ALTER TABLE events
            ADD COLUMN IF NOT EXISTS shipping_reminders BOOLEAN NOT NULL DEFAULT TRUE
            """
        )
        cur.execute(
            """
            ALTER TABLE events
            ADD COLUMN IF NOT EXISTS reminder_interval_days INTEGER NOT NULL DEFAULT 7
            """
        )
        cur.execute(
            """
            ALTER TABLE events
            ADD COLUMN IF NOT EXISTS shipping_lead_days INTEGER NOT NULL DEFAULT 3
            """
        )
        cur.execute("INSERT INTO schema_migrations (version) VALUES (2)")

    def _migrate_v3(self, cur) -> None:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS feedback (
                id SERIAL PRIMARY KEY,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                guild_id TEXT NOT NULL DEFAULT '',
                user_id TEXT NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('problem', 'feature')),
                body TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS changelog_seen (
                user_id TEXT PRIMARY KEY,
                version INTEGER NOT NULL,
                seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute("INSERT INTO schema_migrations (version) VALUES (3)")

    def _migrate_v4(self, cur) -> None:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS tips_seen (
                user_id TEXT NOT NULL,
                tip_id TEXT NOT NULL,
                seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (user_id, tip_id)
            )
            """
        )
        cur.execute("INSERT INTO schema_migrations (version) VALUES (4)")

    def _migrate_v5(self, cur) -> None:
        if self._table_exists(cur, "participants"):
            cur.execute("ALTER TABLE participants ADD COLUMN IF NOT EXISTS budget TEXT")
        if self._table_exists(cur, "pairings"):
            cur.execute(
                """
                ALTER TABLE pairings
                ADD COLUMN IF NOT EXISTS tracking_url TEXT NOT NULL DEFAULT ''
                """
            )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS anonymous_notes (
                event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                sender_id TEXT NOT NULL,
                recipient_id TEXT NOT NULL,
                body TEXT NOT NULL DEFAULT '',
                image_url TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (event_id, sender_id, recipient_id)
            )
            """
        )
        cur.execute("INSERT INTO schema_migrations (version) VALUES (5)")

    def _schema_version(self, cur) -> int:
        cur.execute("SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations")
        return int(cur.fetchone()["version"])

    @staticmethod
    def _column_names(cur, table: str) -> set[str]:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            """,
            (table,),
        )
        return {row["column_name"] for row in cur.fetchall()}

    @staticmethod
    def _table_exists(cur, table: str) -> bool:
        cur.execute("SELECT to_regclass(%s) AS relation", (f"public.{table}",))
        return cur.fetchone()["relation"] is not None


def _assignments(fields: dict[str, Any], allowed: set[str]) -> tuple[str, list[Any]]:
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"Unknown fields: {', '.join(sorted(unknown))}")
    if not fields:
        raise ValueError("No fields to update")
    columns = list(fields)
    return ", ".join(f"{column} = %s" for column in columns), [fields[column] for column in columns]


def _rollback(conn) -> None:
    try:
        conn.rollback()
    except Exception:
        pass
