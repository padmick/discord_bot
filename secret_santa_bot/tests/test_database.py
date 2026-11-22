from datetime import date

import psycopg2
import pytest

from database import DatabaseManager


@pytest.fixture
def db(postgres_url):
    manager = DatabaseManager(postgres_url)
    manager.reset()
    yield manager
    manager.reset()
    manager.close()


def test_past_partners_keep_the_latest_name_and_signup_date_round_trips(db):
    first = db.create_event("guild-history", "Year One", "org")
    db.add_participant(first["id"], "11", "Ada")
    db.add_participant(first["id"], "22", "Bea Old")
    db.replace_pairings(first["id"], [("11", "22", "received"), ("22", "11", "received")])
    db.add_exclusion_pair(first["id"], "11", "22", locked=True)
    db.update_event(first["id"], phase="done")

    second = db.create_event("guild-history", "Year Two", "org")
    db.add_participant(second["id"], "11", "Ada")
    db.add_participant(second["id"], "22", "Bea New")
    db.add_participant(second["id"], "33", "Cara")
    db.replace_pairings(
        second["id"],
        [("11", "22", "received"), ("22", "33", "received"), ("33", "11", "received")],
    )
    db.update_event(second["id"], phase="done", signup_deadline=None)

    partners = {row["user_id"]: row["name"] for row in db.list_past_partners("guild-history", "11")}
    assert partners["22"] == "Bea New"
    assert partners["33"] == "Cara"
    assert "11" not in partners
    latest = db.latest_finished_event("guild-history")
    assert latest["id"] == second["id"]
    assert db.list_locked_pairs(latest["id"]) == []
    assert db.list_locked_pairs(first["id"]) == [("11", "22")]

    current = db.create_event("guild-history", "Year Three", "org")
    db.update_event(current["id"], signup_deadline=date(2026, 12, 1), announce_channel_id="42")
    stored = db.get_event(current["id"])
    assert stored["signup_deadline"].isoformat() == "2026-12-01"
    assert stored["announce_channel_id"] == "42"


def test_organizer_lock_cap_and_rules_round_trip(db):
    event = db.create_event("guild-rules", "Party", "org")
    db.add_exclusion_pair(event["id"], "11", "22", locked=True)
    assert db.remove_exclusion_pair(event["id"], "11", "22") == "locked"
    assert ("11", "22") in db.list_exclusions(event["id"])
    assert db.list_locked_pairs(event["id"]) == [("11", "22")]
    db.add_exclusion_pair(event["id"], "11", "22", locked=False)
    assert db.list_locked_pairs(event["id"]) == [("11", "22")]
    assert db.remove_exclusion_pair(event["id"], "11", "22", include_locked=True) == "removed"
    assert db.list_exclusions(event["id"]) == set()
    db.update_event(event["id"], spending_cap="$25", rules="Handmade only")
    stored = db.get_event(event["id"])
    assert stored["spending_cap"] == "$25"
    assert stored["rules"] == "Handmade only"


def test_budget_tracking_notes_and_reports_round_trip(db):
    event = db.create_event("guild-budget", "Party", "org")
    db.add_participant(event["id"], "ada", "Ada")
    db.add_participant(event["id"], "bea", "Bea")
    db.update_profile(event["id"], "ada", budget="$20")
    assert db.get_participant(event["id"], "ada")["budget"] == "$20"
    db.replace_pairings(event["id"], [("ada", "bea", "shipped")])
    db.set_gift_status(event["id"], "ada", "shipped", tracking_url="https://track.example/1")
    assert db.list_pairings(event["id"])[0]["tracking_url"] == "https://track.example/1"
    db.replace_pairings(event["id"], [("ada", "bea", "shipped")])
    assert db.list_pairings(event["id"])[0]["tracking_url"] == "https://track.example/1"
    db.replace_pairings(event["id"], [("ada", "cara", "not_bought")])
    assert db.list_pairings(event["id"])[0]["tracking_url"] == ""

    db.save_note(event["id"], "ada", "bea", "hello", "https://cdn.example/a.gif")
    db.save_note(event["id"], "ada", "bea", "updated", "")
    db.save_note(event["id"], "bea", "ada", "thanks", "")
    received = db.list_notes(event["id"], "bea")
    assert {row["body"] for row in received} == {"updated", "thanks"}
    db.delete_participant(event["id"], "ada")
    assert db.list_notes(event["id"], "bea") == []

    db.add_feedback("guild-budget", "99", "problem", "the button failed")
    db.add_feedback("other-guild", "99", "feature", "stickers")
    assert [row["body"] for row in db.list_feedback("guild-budget")] == ["the button failed"]
    assert db.get_latest_event("guild-budget")["id"] == event["id"]
    db.delete_event(event["id"])
    assert db.get_latest_event("guild-budget") is None
    assert db.list_notes(event["id"], "bea") == []


def test_unknown_fields_and_feedback_kinds_are_rejected(db):
    event = db.create_event("guild", "Party", "org")
    with pytest.raises(ValueError, match="shirt_size"):
        db.update_event(event["id"], shirt_size="M")
    db.add_feedback("", "ada", "feature", "more stickers")
    with pytest.raises(psycopg2.Error):
        db.add_feedback("guild", "ada", "complaint", "nope")


def test_guilds_stay_isolated_across_cancel_and_finish(db):
    first = db.create_event("guild-a", "Alpha", "org-a")
    second = db.create_event("guild-b", "Beta", "org-b")
    db.add_participant(first["id"], "user-1", "Ada")
    db.add_participant(second["id"], "user-2", "Bea")

    db.delete_event(first["id"])
    assert db.get_open_event("guild-a") is None
    assert db.get_event(first["id"]) is None
    assert db.get_open_event("guild-b")["name"] == "Beta"
    assert [person["name"] for person in db.list_participants(second["id"])] == ["Bea"]

    finished = db.create_event("guild-a", "Alpha again", "org-a")
    db.add_participant(finished["id"], "user-3", "Cara")
    db.update_event(finished["id"], phase="drawn")
    db.update_event(finished["id"], phase="done")
    assert db.get_open_event("guild-a") is None
    assert db.get_event(finished["id"])["phase"] == "done"
    assert db.get_participant(finished["id"], "user-3")["name"] == "Cara"
    assert db.get_open_event("guild-b")["id"] == second["id"]

    reopened = db.create_event("guild-a", "Third", "org-a")
    assert reopened["id"] != finished["id"]
    assert db.get_open_event("guild-a")["name"] == "Third"
    assert db.get_open_event("guild-b")["id"] == second["id"]


def test_open_participations_use_the_event_columns(db):
    """events and participants both have id and name, so the join must name the event columns."""
    first = db.create_event("guild-a", "Alpha", "org-a")
    second = db.create_event("guild-b", "Beta", "org-b")
    finished = db.create_event("guild-c", "Old", "org-c")
    db.add_participant(first["id"], "ada", "Ada")
    db.add_participant(second["id"], "ada", "Ada")
    db.add_participant(second["id"], "bea", "Bea")
    db.add_participant(finished["id"], "ada", "Ada")
    db.update_event(finished["id"], phase="done")

    rows = db.list_open_participations("ada")
    assert [(row["id"], row["name"], row["guild_id"]) for row in rows] == [
        (first["id"], "Alpha", "guild-a"),
        (second["id"], "Beta", "guild-b"),
    ]
    assert db.list_open_participations("bea") == [second]
    assert db.list_open_participations("nobody") == []


def test_second_open_event_in_one_guild_fails(db):
    db.create_event("guild-a", "One", "org")
    with pytest.raises(psycopg2.IntegrityError):
        db.create_event("guild-a", "Two", "org")
    assert db.get_open_event("guild-a")["name"] == "One"


def test_cascade_delete_removes_participants_pairings_and_exclusions(db):
    event = db.create_event("guild-a", "Party", "org")
    db.add_participant(event["id"], "a", "Ada")
    db.add_participant(event["id"], "b", "Bea")
    db.add_exclusion_pair(event["id"], "a", "b")
    db.replace_pairings(
        event["id"],
        [("a", "b", "not_bought"), ("b", "a", "bought")],
    )
    assert db.list_exclusions(event["id"]) == {("a", "b"), ("b", "a")}

    db.delete_event(event["id"])
    assert db.list_participants(event["id"]) == []
    assert db.list_pairings(event["id"]) == []
    assert db.list_exclusions(event["id"]) == set()


def test_removing_a_person_keeps_pairings_that_no_longer_include_them(db):
    event = db.create_event("guild-a", "Party", "org")
    for user_id, name in (("a", "Ada"), ("b", "Bea"), ("c", "Cara")):
        db.add_participant(event["id"], user_id, name)
    db.replace_pairings(
        event["id"],
        [("a", "c", "not_bought"), ("c", "a", "bought")],
    )
    assert db.delete_participant(event["id"], "b") is True
    assert {
        (row["giver_id"], row["receiver_id"], row["gift_status"])
        for row in db.list_pairings(event["id"])
    } == {("a", "c", "not_bought"), ("c", "a", "bought")}
    assert db.get_participant(event["id"], "b") is None


def test_unknown_profile_update_changes_zero_rows_and_rejoin_keeps_it(db):
    event = db.create_event("guild-a", "Party", "org")
    assert db.update_profile(event["id"], "nobody", wishlist="books") == 0
    db.add_participant(event["id"], "somebody", "Sam")
    assert db.update_profile(event["id"], "somebody", wishlist="books", address="1 Road") == 1
    db.add_participant(event["id"], "somebody", "Samantha")
    person = db.get_participant(event["id"], "somebody")
    assert person["name"] == "Samantha"
    assert person["wishlist"] == "books"
    assert person["address"] == "1 Road"
    assert person["digital_only"] is False


def test_legacy_tables_are_renamed_and_left_in_place(postgres_url):
    connection = psycopg2.connect(postgres_url)
    connection.autocommit = True
    with connection.cursor() as cursor:
        cursor.execute("DROP SCHEMA public CASCADE")
        cursor.execute("CREATE SCHEMA public")
        cursor.execute(
            """
            CREATE TABLE participants (
                id SERIAL PRIMARY KEY,
                user_id TEXT UNIQUE,
                name TEXT,
                wishlist TEXT,
                address TEXT,
                is_creator BOOLEAN DEFAULT FALSE
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE pairings (
                id SERIAL PRIMARY KEY,
                giver_id TEXT,
                receiver_id TEXT
            )
            """
        )
        cursor.execute(
            "INSERT INTO participants (user_id, name, wishlist) VALUES ('legacy-user', 'Ada', 'books')"
        )
    connection.close()

    manager = DatabaseManager(postgres_url)
    try:
        assert manager.legacy_participant_count() == 1
        event = manager.create_event("guild-new", "Fresh", "organizer")
        manager.add_participant(event["id"], "new-user", "Bea")
        assert manager.get_participant(event["id"], "legacy-user") is None
        assert manager.get_participant(event["id"], "new-user")["name"] == "Bea"
        assert manager.legacy_participant_count() == 1
        assert manager.ping() is True
    finally:
        manager.close()


def test_version_two_drops_shirt_size_and_adds_reminder_settings(postgres_url):
    connection = psycopg2.connect(postgres_url)
    connection.autocommit = True
    with connection.cursor() as cursor:
        cursor.execute("DROP SCHEMA public CASCADE")
        cursor.execute("CREATE SCHEMA public")
        cursor.execute(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cursor.execute("INSERT INTO schema_migrations (version) VALUES (1)")
        cursor.execute(
            """
            CREATE TABLE events (
                id SERIAL PRIMARY KEY,
                guild_id TEXT NOT NULL,
                name TEXT NOT NULL,
                phase TEXT NOT NULL DEFAULT 'signup',
                organizer_id TEXT NOT NULL,
                deadline DATE,
                last_profile_reminder DATE,
                last_shipping_reminder DATE
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE participants (
                id SERIAL PRIMARY KEY,
                event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                wishlist TEXT,
                address TEXT,
                do_not_gift TEXT,
                shirt_size TEXT,
                notes TEXT,
                digital_only BOOLEAN NOT NULL DEFAULT FALSE,
                UNIQUE (event_id, user_id)
            )
            """
        )
        cursor.execute("INSERT INTO events (guild_id, name, organizer_id) VALUES ('guild', 'Party', 'org') RETURNING id")
        event_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO participants (event_id, user_id, name, wishlist, shirt_size)
            VALUES (%s, 'ada', 'Ada', 'books', 'M')
            """,
            (event_id,),
        )
        cursor.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'participants'
            """
        )
        assert "shirt_size" in {row[0] for row in cursor.fetchall()}
    connection.close()

    manager = DatabaseManager(postgres_url)
    try:
        person = manager.get_participant(event_id, "ada")
        assert person["wishlist"] == "books"
        assert person["name"] == "Ada"
        assert "shirt_size" not in person
        assert person["budget"] is None
        assert person["delivery_window"] is None
        event = manager.get_open_event("guild")
        assert event["profile_reminders"] is True
        assert event["shipping_reminders"] is True
        assert event["reminder_interval_days"] == 7
        assert event["shipping_lead_days"] == 3
        assert event["spending_cap"] is None
        assert event["rules"] is None
        assert event["signup_deadline"] is None
        assert event["announce_channel_id"] is None
        assert manager.claim_changelog("ada", 1) is True
        assert manager.claim_changelog("ada", 1) is False
        assert manager.claim_changelog("ada", 2) is True
        manager.add_feedback("guild", "ada", "problem", "menu failed")
        assert manager.claim_tip("ada", "profile") is True
        assert manager.claim_tip("ada", "profile") is False
        assert manager.claim_tip("ada", "notes") is True
    finally:
        manager.close()

    check = psycopg2.connect(postgres_url)
    try:
        with check.cursor() as cursor:
            cursor.execute("SELECT guild_id, user_id, kind, body FROM feedback")
            assert cursor.fetchall() == [("guild", "ada", "problem", "menu failed")]
            cursor.execute("SELECT version FROM changelog_seen WHERE user_id = 'ada'")
            assert cursor.fetchone()[0] == 2
            cursor.execute("SELECT tip_id FROM tips_seen WHERE user_id = 'ada' ORDER BY tip_id")
            assert [row[0] for row in cursor.fetchall()] == ["notes", "profile"]
            cursor.execute("SELECT MAX(version) FROM schema_migrations")
            assert cursor.fetchone()[0] == 9
    finally:
        check.close()

    again = DatabaseManager(postgres_url)
    again.close()
