from datetime import date, timedelta
from types import SimpleNamespace

from bot import _private_command_message, health_payload
from services import (
    EventService,
    RateLimiter,
    anonymous_message_log,
    format_help,
    match_text,
    reminder_interval_elapsed,
    resolve_dm_events,
    shipping_reminder_due,
    should_delete_channel_text,
)


class FakeDB:
    def __init__(self):
        self.events = {}
        self.participants = {}
        self.participant_order = []
        self.exclusions = {}
        self.pairings = {}
        self.feedback = []
        self.notes = {}
        self.seen_changelog = {}
        self.seen_tips = set()
        self.next_id = 1
        self.replace_calls = 0

    def create_event(self, guild_id, name, organizer_id):
        if self.get_open_event(guild_id):
            raise RuntimeError("open event already exists")
        event_id = self.next_id
        self.next_id += 1
        self.events[event_id] = {
            "id": event_id,
            "guild_id": str(guild_id),
            "name": name,
            "phase": "signup",
            "organizer_id": str(organizer_id),
            "deadline": None,
            "last_profile_reminder": None,
            "last_shipping_reminder": None,
            "profile_reminders": True,
            "shipping_reminders": True,
            "reminder_interval_days": 7,
            "shipping_lead_days": 3,
            "spending_cap": None,
            "rules": None,
            "signup_deadline": None,
            "announce_channel_id": None,
        }
        return dict(self.events[event_id])

    def get_event(self, event_id):
        event = self.events.get(event_id)
        return dict(event) if event else None

    def get_open_event(self, guild_id):
        for event in self.events.values():
            if event["guild_id"] == str(guild_id) and event["phase"] != "done":
                return dict(event)
        return None

    def get_latest_event(self, guild_id):
        found = [event for event in self.events.values() if event["guild_id"] == str(guild_id)]
        if not found:
            return None
        return dict(max(found, key=lambda event: event["id"]))

    def list_actionable_events(self):
        return [dict(event) for event in self.events.values() if event["phase"] != "done"]

    def update_event(self, event_id, **fields):
        self.events[event_id].update(fields)

    def delete_event(self, event_id):
        self.events.pop(event_id, None)
        self.participant_order = [key for key in self.participant_order if key[0] != event_id]
        for key in [key for key in self.participants if key[0] == event_id]:
            del self.participants[key]
        self.exclusions = {key: locked for key, locked in self.exclusions.items() if key[0] != event_id}
        self.pairings.pop(event_id, None)
        self.notes = {key: note for key, note in self.notes.items() if key[0] != event_id}

    def add_participant(self, event_id, user_id, name):
        key = (event_id, str(user_id))
        if key in self.participants:
            self.participants[key]["name"] = name
        else:
            self.participants[key] = {
                "event_id": event_id,
                "user_id": str(user_id),
                "name": name,
                "wishlist": None,
                "address": None,
                "do_not_gift": None,
                "notes": None,
                "budget": None,
                "digital_only": False,
            }
            self.participant_order.append(key)
        return dict(self.participants[key])

    def get_participant(self, event_id, user_id):
        person = self.participants.get((event_id, str(user_id)))
        return dict(person) if person else None

    def list_participants(self, event_id):
        return [dict(self.participants[key]) for key in self.participant_order if key[0] == event_id]

    def list_open_participations(self, user_id):
        found = []
        for (event_id, participant_id), person in self.participants.items():
            event = self.events.get(event_id)
            if participant_id == str(user_id) and event and event["phase"] != "done":
                found.append(dict(event))
        return found

    def update_profile(self, event_id, user_id, **fields):
        person = self.participants.get((event_id, str(user_id)))
        if person is None:
            return 0
        person.update(fields)
        return 1

    def delete_participant(self, event_id, user_id):
        user_id = str(user_id)
        key = (event_id, user_id)
        if key not in self.participants:
            return False
        del self.participants[key]
        self.participant_order = [item for item in self.participant_order if item != key]
        self.exclusions = {
            key: locked
            for key, locked in self.exclusions.items()
            if not (key[0] == event_id and (key[1] == user_id or key[2] == user_id))
        }
        self.pairings[event_id] = [
            row
            for row in self.pairings.get(event_id, [])
            if row["giver_id"] != user_id and row["receiver_id"] != user_id
        ]
        self.notes = {
            key: note
            for key, note in self.notes.items()
            if not (key[0] == event_id and (key[1] == user_id or key[2] == user_id))
        }
        return True

    def add_exclusion_pair(self, event_id, user_id, other_id, locked=False):
        for left, right in ((str(user_id), str(other_id)), (str(other_id), str(user_id))):
            key = (event_id, left, right)
            self.exclusions[key] = bool(self.exclusions.get(key)) or bool(locked)

    def remove_exclusion_pair(self, event_id, user_id, other_id, include_locked=False):
        keys = [
            (event_id, str(user_id), str(other_id)),
            (event_id, str(other_id), str(user_id)),
        ]
        existing = [key for key in keys if key in self.exclusions]
        if not existing:
            return "missing"
        if not include_locked and any(self.exclusions[key] for key in existing):
            return "locked"
        for key in existing:
            del self.exclusions[key]
        return "removed"

    def list_exclusions(self, event_id):
        return {(left, right) for (stored_event, left, right) in self.exclusions if stored_event == event_id}

    def list_locked_pairs(self, event_id):
        seen = set()
        pairs = []
        for (stored_event, left, right), locked in self.exclusions.items():
            if stored_event != event_id or not locked:
                continue
            key = tuple(sorted((left, right)))
            if key in seen or key[0] == key[1]:
                continue
            seen.add(key)
            pairs.append(key)
        return pairs

    def latest_finished_event(self, guild_id):
        done = [
            event
            for event in self.events.values()
            if event["guild_id"] == str(guild_id) and event["phase"] == "done"
        ]
        if not done:
            return None
        return dict(max(done, key=lambda event: event["id"]))

    def list_past_partners(self, guild_id, user_id):
        user_id = str(user_id)
        found = {}
        finished = sorted(
            (
                event
                for event in self.events.values()
                if event["guild_id"] == str(guild_id) and event["phase"] == "done"
            ),
            key=lambda event: event["id"],
            reverse=True,
        )
        for event in finished:
            for row in self.pairings.get(event["id"], []):
                other = None
                if row["giver_id"] == user_id:
                    other = row["receiver_id"]
                elif row["receiver_id"] == user_id:
                    other = row["giver_id"]
                if not other or other == user_id or other in found:
                    continue
                person = self.participants.get((event["id"], other))
                found[other] = person["name"] if person else "Someone"
                if len(found) == 25:
                    break
            if len(found) == 25:
                break
        return [{"user_id": other, "name": name} for other, name in found.items()]

    def replace_pairings(self, event_id, pairs):
        self.replace_calls += 1
        previous = {
            (row["giver_id"], row["receiver_id"]): row.get("tracking_url") or ""
            for row in self.pairings.get(event_id, [])
        }
        self.pairings[event_id] = [
            {
                "giver_id": str(giver),
                "receiver_id": str(receiver),
                "gift_status": status,
                "tracking_url": previous.get((str(giver), str(receiver)), ""),
            }
            for giver, receiver, status in pairs
        ]

    def list_pairings(self, event_id):
        return [dict(row) for row in self.pairings.get(event_id, [])]

    def set_gift_status(self, event_id, giver_id, status, tracking_url=None):
        updated = 0
        for row in self.pairings.get(event_id, []):
            if row["giver_id"] == str(giver_id):
                row["gift_status"] = status
                if tracking_url is not None:
                    row["tracking_url"] = tracking_url
                updated += 1
        return updated

    def save_note(self, event_id, sender_id, recipient_id, body, image_url=""):
        self.notes[(event_id, str(sender_id), str(recipient_id))] = {
            "sender_id": str(sender_id),
            "recipient_id": str(recipient_id),
            "body": body,
            "image_url": image_url or "",
        }

    def list_notes(self, event_id, user_id):
        user_id = str(user_id)
        return [
            dict(note)
            for (stored_event, sender, recipient), note in self.notes.items()
            if stored_event == event_id and user_id in {sender, recipient}
        ]

    def add_feedback(self, guild_id, user_id, kind, body):
        self.feedback.append(
            {"guild_id": str(guild_id or ""), "user_id": str(user_id), "kind": kind, "body": body}
        )

    def list_feedback(self, guild_id, limit=10):
        rows = [row for row in self.feedback if row["guild_id"] == str(guild_id)]
        return list(reversed(rows))[:limit]

    def claim_changelog(self, user_id, version):
        user_id = str(user_id)
        current = self.seen_changelog.get(user_id, 0)
        if int(version) <= current:
            return False
        self.seen_changelog[user_id] = int(version)
        return True

    def claim_tip(self, user_id, tip_id):
        key = (str(user_id), tip_id)
        if not tip_id or key in self.seen_tips:
            return False
        self.seen_tips.add(key)
        return True


def service():
    database = FakeDB()
    return EventService(database), database


def ready_participant(svc, guild, user_id, name, *, digital=False, address="99 Private Road"):
    assert svc.join(guild, user_id, name).ok
    assert svc.set_text_field(guild, user_id, "wishlist", f"books for {name}", False).ok
    if digital:
        assert svc.update_profile(guild, user_id, digital_only=True).ok
    else:
        assert svc.set_text_field(guild, user_id, "address", address, False).ok


def test_create_does_not_enroll_the_organizer_and_join_keeps_them():
    svc, database = service()
    created = svc.create_event("guild", "org", "Party")
    assert created.ok
    event = database.get_open_event("guild")
    assert event["organizer_id"] == "org"
    assert database.list_participants(event["id"]) == []

    first = svc.join("guild", "org", "Organizer")
    assert first.ok and "welcome" in first.data
    again = svc.join("guild", "org", "Renamed")
    assert again.ok and again.data is None
    assert "already in" in again.message
    assert database.get_open_event("guild")["organizer_id"] == "org"
    assert database.get_participant(event["id"], "org")["name"] == "Renamed"
    assert database.get_participant(event["id"], "org")["wishlist"] is None


def test_phase_guards_and_second_event():
    svc, database = service()
    assert svc.create_event("guild", "org", "Party").ok
    assert not svc.create_event("guild", "org", "Another").ok
    ready_participant(svc, "guild", "a", "Ada")
    assert svc.lock("guild", "other", False).code == "forbidden"
    assert svc.lock("guild", "org", False).ok
    assert svc.join("guild", "b", "Bea").code == "closed"
    assert svc.leave("guild", "a").code == "closed"
    assert svc.unlock("guild", "org", False).ok
    ready_participant(svc, "guild", "b", "Bea")
    assert svc.finish("guild", "org", False).code == "bad_phase"
    assert svc.start("guild", "other", False).code == "forbidden"
    assert svc.start("guild", "other", True).ok
    assert database.get_open_event("guild")["phase"] == "drawn"
    assert svc.join("guild", "c", "Cara").code == "closed"
    assert not svc.start("guild", "org", False).ok


def test_start_refuses_missing_wishlist_and_skips_address_for_digital_gifts():
    svc, database = service()
    svc.create_event("guild", "org", "Party")
    svc.join("guild", "a", "Ada")
    svc.join("guild", "b", "Bea")
    svc.set_text_field("guild", "a", "wishlist", "books", False)
    result = svc.start("guild", "org", False)
    assert result.code == "missing_info"
    assert database.replace_calls == 0
    assert any(person["user_id"] == "b" and "wishlist" in person["missing"] for person in result.data["missing"])

    svc.update_profile("guild", "a", digital_only=True)
    svc.set_text_field("guild", "b", "wishlist", "games", False)
    svc.update_profile("guild", "b", digital_only=True)
    started = svc.start("guild", "org", False)
    assert started.ok
    assert database.replace_calls == 1
    assert "preview" not in started.data
    assert "->" not in started.message
    assert {note["giver_id"] for note in started.data["notifications"]} == {"a", "b"}


def test_physical_gift_still_requires_an_address():
    svc, database = service()
    svc.create_event("guild", "org", "Party")
    ready_participant(svc, "guild", "a", "Ada")
    svc.join("guild", "b", "Bea")
    svc.set_text_field("guild", "b", "wishlist", "games", False)
    result = svc.start("guild", "org", False)
    assert result.code == "missing_info"
    assert database.replace_calls == 0
    assert any("address" in person["missing"] for person in result.data["missing"])


def test_impossible_exclusions_do_not_save_pairings():
    svc, database = service()
    svc.create_event("guild", "org", "Party")
    for user_id, name in (("a", "Ada"), ("b", "Bea"), ("c", "Cara")):
        ready_participant(svc, "guild", user_id, name)
    assert svc.exclude("guild", "a", "b").ok
    result = svc.start("guild", "org", False)
    assert result.code == "no_draw"
    assert database.replace_calls == 0
    assert database.list_pairings(database.get_open_event("guild")["id"]) == []
    assert database.get_open_event("guild")["phase"] == "signup"


def test_profile_updates_for_members_only_and_channel_text_is_flagged():
    svc, database = service()
    svc.create_event("guild", "org", "Party")
    missing = svc.set_text_field("guild", "stranger", "address", "99 Private Road", True)
    assert missing.code == "not_joined"
    assert missing.data["delete_message"] is True
    direct = svc.update_profile("guild", "stranger", wishlist="books")
    assert direct.code == "not_joined"

    ready_participant(svc, "guild", "a", "Ada", address="99 Private Road")
    private = svc.set_text_field("guild", "a", "wishlist", "records", False)
    assert private.ok
    assert not (private.data or {}).get("delete_message")
    assert not (private.data or {}).get("notify_gifter_id")
    public = svc.set_text_field("guild", "a", "address", "hidden street", True)
    assert public.data["delete_message"] is True
    assert not should_delete_channel_text("notes", True)
    assert should_delete_channel_text("wishlist", True)
    assert not should_delete_channel_text("address", False)

    status = svc.status("guild")
    assert "99 Private Road" not in status.message
    assert "hidden street" not in status.message
    assert "records" not in status.message
    assert status.card is not None
    assert status.card.title == "Party"
    assert status.data["missing_wishlist"] == []
    event = database.get_open_event("guild")
    assert database.get_participant(event["id"], "a")["address"] == "hidden street"


def test_wishlist_change_after_the_draw_names_the_gifter():
    svc, _database = service()
    svc.create_event("guild", "org", "Party")
    ready_participant(svc, "guild", "a", "Ada")
    ready_participant(svc, "guild", "b", "Bea")
    assert svc.start("guild", "org", False).ok
    changed = svc.set_text_field("guild", "b", "wishlist", "new books", False)
    assert changed.data["notify_gifter_id"] == "a"
    assert changed.data["wishlist"] == "new books"
    same = svc.set_text_field("guild", "b", "wishlist", "new books", False)
    assert not (same.data or {}).get("notify_gifter_id")


def test_gift_status_permissions_and_illegal_jumps():
    svc, database = service()
    svc.create_event("guild", "org", "Party")
    ready_participant(svc, "guild", "a", "Ada")
    ready_participant(svc, "guild", "b", "Bea")
    assert svc.start("guild", "org", False).ok
    event_id = database.get_open_event("guild")["id"]

    assert svc.set_gift_status("guild", "a", "shipped").code == "bad_transition"
    assert svc.set_gift_status("guild", "a", "received").code == "bad_transition"
    assert svc.set_gift_status("guild", "stranger", "bought").code == "forbidden"
    assert svc.set_gift_status("guild", "a", "bought").ok
    assert svc.set_gift_status("guild", "a", "bought").code == "bad_transition"
    assert svc.set_gift_status("guild", "a", "shipped").ok
    incoming = next(row for row in database.list_pairings(event_id) if row["giver_id"] == "a")
    assert incoming["gift_status"] == "shipped"
    assert svc.set_gift_status("guild", "b", "received").ok
    incoming = next(row for row in database.list_pairings(event_id) if row["giver_id"] == "a")
    assert incoming["gift_status"] == "received"
    outgoing = next(row for row in database.list_pairings(event_id) if row["giver_id"] == "b")
    assert outgoing["gift_status"] == "not_bought"


def test_leave_and_remove_keep_the_roster_when_reroll_fails():
    svc, database = service()
    svc.create_event("guild", "org", "Party")
    ready_participant(svc, "guild", "a", "Ada")
    ready_participant(svc, "guild", "b", "Bea")
    assert svc.start("guild", "org", False).ok
    event_id = database.get_open_event("guild")["id"]
    before = database.list_pairings(event_id)

    left = svc.leave("guild", "a")
    assert left.code == "no_draw"
    assert database.get_participant(event_id, "a") is not None
    assert database.list_pairings(event_id) == before

    removed = svc.remove("guild", "org", "b", False)
    assert removed.code == "no_draw"
    assert database.get_participant(event_id, "b") is not None
    assert database.list_pairings(event_id) == before


def test_drawn_leave_rerolls_and_preserves_untouched_gift_status():
    svc, database = service()
    svc.create_event("guild", "org", "Party")
    for user_id, name in (("a", "Ada"), ("b", "Bea"), ("c", "Cara"), ("d", "Dee")):
        ready_participant(svc, "guild", user_id, name)
    assert svc.start("guild", "org", False).ok
    event_id = database.get_open_event("guild")["id"]
    database.pairings[event_id] = [
        {"giver_id": "a", "receiver_id": "b", "gift_status": "shipped"},
        {"giver_id": "b", "receiver_id": "c", "gift_status": "not_bought"},
        {"giver_id": "c", "receiver_id": "d", "gift_status": "bought", "tracking_url": "https://track.example/c"},
        {"giver_id": "d", "receiver_id": "a", "gift_status": "not_bought"},
    ]
    result = svc.leave("guild", "b")
    assert result.ok
    rows = {row["giver_id"]: row for row in database.list_pairings(event_id)}
    assert set(rows) == {"a", "c", "d"}
    assert rows["a"]["receiver_id"] == "c"
    assert rows["a"]["gift_status"] == "not_bought"
    assert rows["c"]["receiver_id"] == "d"
    assert rows["c"]["gift_status"] == "bought"
    assert rows["c"]["tracking_url"] == "https://track.example/c"
    assert rows["a"]["tracking_url"] == ""
    assert rows["d"]["receiver_id"] == "a"
    assert {note["giver_id"] for note in result.data["changed"]} == {"a"}
    assert database.get_participant(event_id, "b") is None


def test_reveal_requires_the_confirmation_token():
    svc, _database = service()
    svc.create_event("guild", "org", "Party")
    ready_participant(svc, "guild", "a", "Ada")
    ready_participant(svc, "guild", "b", "Bea")
    assert svc.start("guild", "org", False).ok

    rejected = svc.reveal("guild", "org", False, "yes")
    assert rejected.code == "confirm"
    assert "pairings" not in (rejected.data or {})
    assert "->" not in rejected.message

    denied = svc.reveal("guild", "a", False, "reveal")
    assert denied.code == "forbidden"

    revealed = svc.reveal("guild", "org", False, "reveal")
    assert revealed.ok
    assert revealed.card is None
    assert "->" not in revealed.message
    assert "Ada" not in revealed.message
    assert "->" in revealed.data["dm_text"]
    assert len(revealed.data["pairings"]) == 2


def test_cancel_finishes_only_the_requested_guild():
    svc, database = service()
    svc.create_event("guild-a", "org-a", "A")
    svc.create_event("guild-b", "org-b", "B")
    ready_participant(svc, "guild-a", "a", "Ada")
    ready_participant(svc, "guild-b", "b", "Bea")
    assert svc.cancel("guild-a", "org-a", False).ok
    assert svc.open_event("guild-a") is None
    assert svc.open_event("guild-b")["name"] == "B"

    ready_participant(svc, "guild-b", "c", "Cara")
    assert svc.start("guild-b", "org-b", False).ok
    assert svc.finish("guild-b", "org-b", False).ok
    assert database.get_event(2)["phase"] == "done"
    assert svc.open_event("guild-b") is None
    assert svc.create_event("guild-b", "org-b", "Next").ok


def test_anonymous_log_lines_omit_display_names():
    sent = anonymous_message_log(True, "giftee", display_name="Alice Santa")
    failed = anonymous_message_log(False, "gifter", display_name="Bob Elf")
    assert "Alice" not in sent
    assert "Bob" not in failed
    assert sent == "ok anonymous giftee"
    assert failed == "failed anonymous gifter"


def test_reminder_selection_for_profiles_and_shipments():
    svc, database = service()
    today = date(2026, 12, 1)
    svc.create_event("guild", "org", "Party")
    ready_participant(svc, "guild", "ready", "Ready")
    svc.join("guild", "late", "Late")
    svc.join("guild", "digital", "Digital")
    svc.set_text_field("guild", "digital", "wishlist", "song", False)
    svc.update_profile("guild", "digital", digital_only=True)

    selected = svc.select_reminders(today)
    profile_ids = {item["user_id"] for item in selected if item["kind"] == "profile"}
    assert profile_ids == {"late"}
    assert all(item["kind"] != "shipping" for item in selected)

    event_id = database.get_open_event("guild")["id"]
    svc.mark_reminded(event_id, "profile", today)
    assert svc.select_reminders(today) == []
    assert svc.select_reminders(today + timedelta(days=1)) == []
    assert any(item["user_id"] == "late" for item in svc.select_reminders(today + timedelta(days=7)))

    svc.set_text_field("guild", "late", "wishlist", "hat", False)
    svc.set_text_field("guild", "late", "address", "1 Road", False)
    assert svc.set_deadline("guild", "org", False, (today + timedelta(days=2)).isoformat()).ok
    assert svc.start("guild", "org", False).ok
    shipping = [item for item in svc.select_reminders(today) if item["kind"] == "shipping"]
    assert {item["user_id"] for item in shipping} == {"ready", "late", "digital"}

    assert svc.set_gift_status("guild", "ready", "bought").ok
    assert svc.set_gift_status("guild", "ready", "shipped").ok
    shipping = [item for item in svc.select_reminders(today) if item["kind"] == "shipping"]
    assert "ready" not in {item["user_id"] for item in shipping}

    assert svc.set_deadline("guild", "org", False, (today + timedelta(days=4)).isoformat()).ok
    assert [item for item in svc.select_reminders(today) if item["kind"] == "shipping"] == []
    assert svc.set_deadline("guild", "org", False, (today - timedelta(days=1)).isoformat()).ok
    assert [item for item in svc.select_reminders(today) if item["kind"] == "shipping"] == []

    assert svc.set_deadline("guild", "org", False, (today + timedelta(days=1)).isoformat()).ok
    svc.mark_reminded(event_id, "shipping", today)
    assert [item for item in svc.select_reminders(today) if item["kind"] == "shipping"] == []
    assert [item for item in svc.select_reminders(today + timedelta(days=1)) if item["kind"] == "shipping"] == []
    assert svc.set_deadline("guild", "org", False, (today + timedelta(days=7)).isoformat()).ok
    assert {item["user_id"] for item in svc.select_reminders(today + timedelta(days=7)) if item["kind"] == "shipping"} == {
        "late",
        "digital",
    }


def test_organizer_controls_reminder_cadence_and_toggles():
    svc, database = service()
    today = date(2026, 12, 1)
    assert reminder_interval_elapsed(None, today, 7) is True
    assert reminder_interval_elapsed(today, today, 7) is False
    assert reminder_interval_elapsed(today - timedelta(days=6), today, 7) is False
    assert reminder_interval_elapsed(today - timedelta(days=7), today, 7) is True
    assert shipping_reminder_due(today + timedelta(days=2), today, 1) is False
    assert shipping_reminder_due(today + timedelta(days=1), today, 1) is True
    assert shipping_reminder_due(today - timedelta(days=1), today, 3) is False

    svc.create_event("guild", "org", "Party")
    svc.join("guild", "late", "Late")
    denied = svc.update_notification_settings("guild", "late", False, "off", "off", "14", "1")
    assert denied.code == "forbidden"
    assert svc.update_notification_settings("guild", "org", False, "maybe", "on", "7", "3").code == "bad_setting"
    assert svc.update_notification_settings("guild", "org", False, "on", "on", "0", "3").code == "bad_setting"
    assert svc.update_notification_settings("guild", "org", False, "on", "on", "7", "31").code == "bad_setting"
    assert svc.update_notification_settings("guild", "org", False, "on", "on", "weekly", "3").code == "bad_setting"

    saved = svc.update_notification_settings("guild", "org", False, "off", "yes", "14", "1")
    assert saved.ok
    assert "Profile reminders: off." in saved.message
    assert "14 day(s)" in saved.message
    event = database.get_open_event("guild")
    assert event["profile_reminders"] is False
    assert event["shipping_reminders"] is True
    assert event["reminder_interval_days"] == 14
    assert event["shipping_lead_days"] == 1
    assert svc.select_reminders(today) == []

    assert svc.update_notification_settings("guild", "org", False, "on", "on", "1", "1").ok
    assert {item["user_id"] for item in svc.select_reminders(today)} == {"late"}
    event_id = event["id"]
    svc.mark_reminded(event_id, "profile", today)
    assert any(item["user_id"] == "late" for item in svc.select_reminders(today + timedelta(days=1)))

    ready_participant(svc, "guild", "ready", "Ready")
    assert svc.set_text_field("guild", "late", "wishlist", "hat", False).ok
    assert svc.set_text_field("guild", "late", "address", "1 Road", False).ok
    assert svc.set_deadline("guild", "org", False, (today + timedelta(days=2)).isoformat()).ok
    assert svc.start("guild", "org", False).ok
    assert [item for item in svc.select_reminders(today) if item["kind"] == "shipping"] == []
    assert svc.set_deadline("guild", "org", False, today.isoformat()).ok
    shipping = {item["user_id"] for item in svc.select_reminders(today) if item["kind"] == "shipping"}
    assert shipping == {"late", "ready"}
    assert svc.update_notification_settings("guild", "org", False, "on", "off", "1", "1").ok
    assert [item for item in svc.select_reminders(today) if item["kind"] == "shipping"] == []


def test_profiles_no_longer_store_shirt_size():
    svc, _database = service()
    svc.create_event("guild", "org", "Party")
    ready_participant(svc, "guild", "a", "Ada")
    assert svc.update_profile("guild", "a", shirt_size="M").code == "bad_field"
    info = svc.my_info("guild", "a")
    assert "shirt" not in info.message.lower()
    assert svc.set_deadline("guild", "org", False, "2026-12-20").ok
    ready_participant(svc, "guild", "b", "Bea")
    assert svc.start("guild", "org", False).ok
    match = svc.match_info("guild", "a")
    assert "shirt" not in match.message.lower()
    assert "Shirt" not in match_text({"name": "Bea", "wishlist": "books", "digital_only": False})


async def test_notification_form_saves_the_organizer_choices():
    from views import NotificationSettingsModal

    svc, database = service()
    svc.create_event("guild", "org", "Party")
    svc.join("guild", "late", "Late")

    class Cog:
        def __init__(self):
            self.service = svc

        async def run(self, func, *args, **kwargs):
            return func(*args, **kwargs)

    class Response:
        async def defer(self, ephemeral=False):
            del ephemeral

    class Followup:
        def __init__(self):
            self.messages = []
            self.embeds = []

        async def send(self, message=None, ephemeral=False, embed=None):
            del ephemeral
            self.messages.append(message)
            self.embeds.append(embed)

    current = svc.notification_settings("guild", "org", False)
    modal = NotificationSettingsModal(Cog(), "guild", current.data)
    modal.profile_reminders._value = "off"
    modal.shipping_reminders._value = "on"
    modal.interval_days._value = "10"
    modal.lead_days._value = "0"
    saved = SimpleNamespace(user=SimpleNamespace(id="org"), response=Response(), followup=Followup())
    await modal.on_submit(saved)
    event = database.get_open_event("guild")
    assert event["profile_reminders"] is False
    assert event["shipping_reminders"] is True
    assert event["reminder_interval_days"] == 10
    assert event["shipping_lead_days"] == 0
    assert saved.followup.messages[0].startswith("Notification settings saved.")
    assert saved.followup.embeds[0].title == "Reminder settings"
    assert "10 day(s)" in saved.followup.embeds[0].description

    rejected = SimpleNamespace(user=SimpleNamespace(id="late"), response=Response(), followup=Followup())
    modal.profile_reminders._value = "on"
    await modal.on_submit(rejected)
    assert database.get_open_event("guild")["profile_reminders"] is False
    assert "organizer" in rejected.followup.messages[0].lower()


def test_past_matches_locks_and_unshipped_names_stay_separate():
    svc, database = service()
    svc.create_event("guild", "org", "Year One")
    ready_participant(svc, "guild", "11", "Ada")
    ready_participant(svc, "guild", "22", "Bea")
    ready_participant(svc, "guild", "33", "Cara")
    ready_participant(svc, "guild", "44", "Dee")
    assert svc.block_pair("guild", "org", False, "11", "Ada", "22", "Bea").ok
    assert svc.start("guild", "org", False).ok
    first_id = database.get_open_event("guild")["id"]
    drawn = {row["giver_id"]: row["receiver_id"] for row in database.list_pairings(first_id)}
    assert drawn["11"] != "22" and drawn["22"] != "11"
    assert svc.set_gift_status("guild", "11", "bought").ok
    assert svc.set_gift_status("guild", "11", "shipped").ok
    report = svc.unshipped("guild", "org", False)
    assert report.ok
    assert {row["user_id"] for row in report.data["waiting"]} == {"22", "33", "44"}
    assert all(set(row) == {"user_id", "name", "gift_status"} for row in report.data["waiting"])
    names = {"11": "Ada", "22": "Bea", "33": "Cara", "44": "Dee"}
    for row in report.data["waiting"]:
        line = next(line for line in report.card.description.splitlines() if line.startswith(names[row["user_id"]]))
        receiver = names[drawn[row["user_id"]]]
        if receiver != names[row["user_id"]]:
            assert receiver not in line
    assert svc.unshipped("guild", "11", False).code == "forbidden"
    assert svc.finish("guild", "org", False).ok

    created = svc.create_event("guild", "org", "Year Two")
    assert "s!carrylocks" in created.message
    assert "Ada" in created.message and "Bea" in created.message
    joined = svc.join("guild", "11", "Ada")
    expected = {receiver for giver, receiver in drawn.items() if giver == "11"}
    expected.update(giver for giver, receiver in drawn.items() if receiver == "11")
    assert {person["user_id"] for person in joined.data["past_partners"]} == expected
    assert "Past matches in this server" in joined.data["welcome"].text()
    second_id = database.get_open_event("guild")["id"]
    assert database.list_exclusions(second_id) == set()
    kept = joined.data["past_partners"][0]
    assert svc.skip_past_matches("guild", "11", [(kept["user_id"], kept["name"])]).ok
    assert ("11", kept["user_id"]) in database.list_exclusions(second_id)
    for other in joined.data["past_partners"][1:]:
        assert ("11", other["user_id"]) not in database.list_exclusions(second_id)

    carried = svc.carry_locks("guild", "org", False)
    assert carried.ok
    assert ("11", "22") in database.list_exclusions(second_id)
    assert svc.unexclude("guild", "11", "22", "Bea").code == "locked"
    assert svc.carry_locks("guild", "11", False).code == "forbidden"


def test_carry_uses_only_the_latest_finished_event():
    svc, database = service()
    svc.create_event("guild", "org", "Old")
    for user_id, name in (("11", "Ada"), ("22", "Bea"), ("33", "Cara"), ("44", "Dee")):
        ready_participant(svc, "guild", user_id, name)
    assert svc.block_pair("guild", "org", False, "11", "Ada", "22", "Bea").ok
    assert svc.start("guild", "org", False).ok
    assert svc.finish("guild", "org", False).ok
    svc.create_event("guild", "org", "Middle")
    for user_id, name in (("11", "Ada"), ("22", "Bea"), ("33", "Cara"), ("44", "Dee")):
        ready_participant(svc, "guild", user_id, name)
    assert svc.start("guild", "org", False).ok
    assert svc.finish("guild", "org", False).ok
    svc.create_event("guild", "org", "New")
    quiet = svc.carry_locks("guild", "org", False)
    assert "no organizer locks" in quiet.message.lower()
    assert database.list_locked_pairs(database.get_open_event("guild")["id"]) == []


def test_setup_saves_dates_and_the_announcement_hides_matches():
    svc, database = service()
    svc.create_event("guild", "org", "Party")
    assert svc.apply_setup("guild", "11", False, "2026-12-01").code == "forbidden"
    assert svc.setup_overview("guild", "11", False).code == "forbidden"
    empty = svc.apply_setup("guild", "org", False)
    assert "Nothing changed" in empty.message
    assert svc.apply_setup("guild", "org", False, signup="tomorrow", cap="$25").code == "bad_date"
    assert database.get_open_event("guild")["spending_cap"] is None
    saved = svc.apply_setup(
        "guild",
        "org",
        False,
        signup=date.today().isoformat(),
        deadline="2026-12-20",
        cap="$25",
        rules="Handmade only",
    )
    assert saved.ok
    event = database.get_open_event("guild")
    assert event["phase"] == "signup"
    assert event["signup_deadline"] == date.today()
    assert event["deadline"].isoformat() == "2026-12-20"
    assert event["spending_cap"] == "$25"
    assert event["rules"] == "Handmade only"
    assert "Last day to join" in saved.card.text()
    closed = svc.apply_setup("guild", "org", False, signup=(date.today() - timedelta(days=1)).isoformat())
    assert closed.ok
    assert "now closed" in closed.message
    assert database.get_open_event("guild")["phase"] == "locked"
    assert svc.prepare_announcement("guild", "org", False, "555").code == "bad_phase"
    assert svc.unlock("guild", "org", False).code == "signup_closed"
    svc.apply_setup("guild", "org", False, signup="clear")
    assert svc.unlock("guild", "org", False).ok
    ready = svc.prepare_announcement("guild", "org", False, "555")
    assert ready.ok
    assert "s!join" in ready.card.text()
    assert "Handmade only" in ready.card.text()
    assert "Bea" not in ready.card.text()
    assert svc.prepare_announcement("guild", "org", False, "general").code == "bad_channel"
    assert svc.remember_announcement("guild", "org", False, "555").ok
    assert database.get_open_event("guild")["announce_channel_id"] == "555"
    listed = svc.show_hint("")
    assert "s!hint profile" in listed.card.text()
    assert svc.show_hint("wishlist").ok
    assert "wishlist" in svc.show_hint("wishlist").message.lower()
    assert svc.show_hint("nope").code == "unknown_hint"
    assert svc.claim_tip("11", "seen-join-tutorial") is True
    assert svc.claim_tip("11", "seen-join-tutorial") is False


def test_signup_date_closes_joining_and_leaves_the_gift_deadline():
    svc, database = service()
    svc.create_event("guild", "org", "Party")
    today = date.today()
    assert svc.set_signup_deadline("guild", "11", False, today.isoformat()).code == "forbidden"
    assert svc.set_signup_deadline("guild", "org", False, "tomorrow").code == "bad_date"
    saved = svc.set_signup_deadline("guild", "org", False, today.isoformat())
    assert saved.ok
    assert "through that day" in saved.message
    assert database.get_open_event("guild")["phase"] == "signup"
    assert svc.join("guild", "11", "Ada").ok
    assert svc.close_expired_signups(today) == 0
    assert svc.close_expired_signups(today + timedelta(days=1)) == 1
    assert database.get_open_event("guild")["phase"] == "locked"
    assert svc.join("guild", "22", "Bea").code == "closed"
    assert svc.unlock("guild", "org", False).ok
    assert database.get_open_event("guild")["phase"] == "signup"
    closed = svc.set_signup_deadline("guild", "org", False, (today - timedelta(days=1)).isoformat())
    assert closed.ok
    assert "now closed" in closed.message
    assert database.get_open_event("guild")["phase"] == "locked"
    assert svc.unlock("guild", "org", False).code == "signup_closed"
    cleared = svc.set_signup_deadline("guild", "org", False, "clear")
    assert cleared.ok
    assert database.get_open_event("guild")["phase"] == "locked"
    assert database.get_open_event("guild")["signup_deadline"] is None
    assert svc.unlock("guild", "org", False).ok
    assert svc.join("guild", "22", "Bea").ok
    assert svc.set_deadline("guild", "org", False, "2026-12-20").ok
    assert svc.set_signup_deadline("guild", "org", False, (today - timedelta(days=1)).isoformat()).ok
    event = database.get_open_event("guild")
    assert event["phase"] == "locked"
    assert event["deadline"].isoformat() == "2026-12-20"
    assert event["signup_deadline"] == today - timedelta(days=1)
    status = svc.status("guild")
    assert "Last day to join" in status.message
    assert "Gift deadline" in status.message
    assert "2026-12-20" in status.message
    shown = svc.set_signup_deadline("guild", "org", False, "")
    assert shown.ok
    assert (today - timedelta(days=1)).isoformat() in shown.message
    assert "2026-12-20" in shown.message


async def test_setup_form_and_channel_menu_post_the_welcome():
    import discord

    from views import AnnounceChannelPicker, SetupButton, SetupModal, SetupView

    svc, database = service()
    svc.create_event("1", "2", "Party")

    class Cog:
        def __init__(self):
            self.service = svc

        async def run(self, func, *args, **kwargs):
            return func(*args, **kwargs)

        async def post_open_announcement(self, guild_id, actor_id, is_admin, channel):
            prepared = self.service.prepare_announcement(guild_id, actor_id, is_admin, str(channel.id))
            if not prepared.ok:
                return False, prepared.message
            try:
                await channel.send(embed=prepared.card)
            except discord.Forbidden:
                return False, "I can't post in that channel. Give me permission to send messages there."
            self.service.remember_announcement(guild_id, actor_id, is_admin, str(channel.id))
            return True, f"Posted the open announcement in {channel.mention}."

    class Response:
        def __init__(self):
            self.done = False
            self.messages = []
            self.modals = []

        def is_done(self):
            return self.done

        async def defer(self, ephemeral=False):
            del ephemeral
            self.done = True

        async def send_message(self, message, ephemeral=False):
            del ephemeral
            self.messages.append(message)
            self.done = True

        async def send_modal(self, modal):
            self.modals.append(modal)
            self.done = True

    class Followup:
        def __init__(self):
            self.messages = []

        async def send(self, message, ephemeral=False, embed=None):
            del ephemeral
            self.messages.append((message, embed))

    class Channel:
        def __init__(self, channel_id, fail=False):
            self.id = channel_id
            self.mention = "#general"
            self.fail = fail
            self.sent = []

        async def send(self, **kwargs):
            if self.fail:
                raise discord.Forbidden(SimpleNamespace(status=403, reason="missing"), "no")
            self.sent.append(kwargs)

    class Client:
        def get_cog(self, name):
            assert name == "SecretSantaCog"
            return cog

    cog = Cog()
    client = Client()
    view = SetupView("1", "2")
    button = next(item for item in view.children if isinstance(item, SetupButton))
    opener = SimpleNamespace(
        user=SimpleNamespace(id=2),
        response=Response(),
        followup=Followup(),
        guild=None,
        client=client,
    )
    await button.callback(opener)
    modal = opener.response.modals[0]
    assert isinstance(modal, SetupModal)
    modal.signup._value = date.today().isoformat()
    modal.deadline._value = "2026-12-20"
    modal.cap._value = "$25"
    modal.rules._value = "Handmade only"
    submitted = SimpleNamespace(user=SimpleNamespace(id=2), response=Response(), followup=Followup(), guild=None)
    await modal.on_submit(submitted)
    stored = database.get_open_event("1")
    assert stored["spending_cap"] == "$25"
    assert stored["rules"] == "Handmade only"
    assert "Setup saved" in submitted.followup.messages[-1][0]

    channel = Channel(555)
    guild = SimpleNamespace(get_channel=lambda channel_id: channel if int(channel_id) == 555 else None)

    async def fetch_channel(channel_id):
        return guild.get_channel(channel_id)

    guild.fetch_channel = fetch_channel
    select = next(item for item in view.children if isinstance(item, AnnounceChannelPicker))
    select.item._values = [SimpleNamespace(id=555)]
    posted = SimpleNamespace(
        user=SimpleNamespace(id=2),
        response=Response(),
        followup=Followup(),
        guild=guild,
        client=client,
    )
    await select.callback(posted)
    assert channel.sent
    posted_embed = channel.sent[0]["embed"]
    posted_text = "\n".join(
        [posted_embed.title or "", posted_embed.description or ""]
        + [f"{field.name} {field.value}" for field in posted_embed.fields]
    )
    assert "s!join" in posted_text
    assert database.get_open_event("1")["announce_channel_id"] == "555"
    assert "Posted the open announcement" in posted.followup.messages[-1][0]

    stranger = SimpleNamespace(
        user=SimpleNamespace(id=11),
        response=Response(),
        followup=Followup(),
        guild=guild,
        client=client,
    )
    await select.callback(stranger)
    assert "belongs to the person" in stranger.response.messages[0]

    blocked = Channel(555, fail=True)
    guild.get_channel = lambda channel_id: blocked
    refused = SimpleNamespace(
        user=SimpleNamespace(id=2),
        response=Response(),
        followup=Followup(),
        guild=guild,
        client=client,
    )
    await select.callback(refused)
    assert "can't post" in refused.followup.messages[-1][0]


async def test_setup_button_answers_after_the_sending_view_is_gone():
    """The form still opens when Discord delivers the click to a new process."""
    import discord
    from discord.components import ActionRow
    from discord.ui.view import ViewStore

    from views import SetupButton, SetupView

    view = SetupView("42", "7")
    assert view.timeout is None
    button = next(child for child in view.children if isinstance(child, SetupButton))
    rows = [ActionRow(row) for row in view.to_components()]

    class Response:
        def __init__(self):
            self.done = False
            self.messages = []
            self.modals = []

        def is_done(self):
            return self.done

        async def send_message(self, message, ephemeral=False):
            del ephemeral
            self.messages.append(message)
            self.done = True

        async def send_modal(self, modal):
            self.modals.append(modal)
            self.done = True

    class Client:
        def get_cog(self, name):
            assert name == "SecretSantaCog"
            return SimpleNamespace(name=name)

    interaction = SimpleNamespace(
        user=SimpleNamespace(id=7),
        response=Response(),
        message=SimpleNamespace(id=99, components=rows),
        data={"custom_id": button.custom_id, "component_type": discord.ComponentType.button.value},
        client=Client(),
    )
    store = ViewStore(SimpleNamespace())
    store.add_dynamic_items(SetupButton)
    await store.schedule_dynamic_item_call(
        discord.ComponentType.button.value,
        SetupButton,
        interaction,
        button.custom_id,
        button.template.fullmatch(button.custom_id),
    )
    assert len(interaction.response.modals) == 1
    assert interaction.response.modals[0].guild_id == "42"
    assert interaction.response.modals[0].user_id == "7"


async def test_past_partner_menu_skips_only_the_people_you_choose():
    from views import PastPartnerView

    svc, database = service()
    svc.create_event("guild", "org", "Year One")
    ready_participant(svc, "guild", "11", "Ada")
    ready_participant(svc, "guild", "22", "Bea")
    ready_participant(svc, "guild", "33", "Cara")
    ready_participant(svc, "guild", "44", "Dee")
    assert svc.start("guild", "org", False).ok
    assert svc.finish("guild", "org", False).ok
    svc.create_event("guild", "org", "Year Two")
    joined = svc.join("guild", "11", "Ada")
    assert len(joined.data["past_partners"]) >= 1

    class Cog:
        def __init__(self):
            self.service = svc

        async def run(self, func, *args, **kwargs):
            return func(*args, **kwargs)

    class Response:
        def __init__(self):
            self.done = False
            self.messages = []

        def is_done(self):
            return self.done

        async def defer(self, ephemeral=False):
            del ephemeral
            self.done = True

        async def send_message(self, message, ephemeral=False):
            del ephemeral
            self.messages.append(message)
            self.done = True

    class Followup:
        def __init__(self):
            self.messages = []

        async def send(self, message, ephemeral=False):
            del ephemeral
            self.messages.append(message)

    view = PastPartnerView(Cog(), "guild", "11", joined.data["past_partners"])
    select = view.children[0]
    chosen = joined.data["past_partners"][0]["user_id"]
    select._values = [chosen]
    interaction = SimpleNamespace(user=SimpleNamespace(id="11"), response=Response(), followup=Followup())
    await select.callback(interaction)
    event_id = database.get_open_event("guild")["id"]
    assert ("11", chosen) in database.list_exclusions(event_id)
    for other in joined.data["past_partners"][1:]:
        assert ("11", other["user_id"]) not in database.list_exclusions(event_id)
    assert "won't be paired" in interaction.followup.messages[-1]

    stranger = SimpleNamespace(user=SimpleNamespace(id="22"), response=Response(), followup=Followup())
    await select.callback(stranger)
    assert "belongs to the person" in stranger.response.messages[0]


def test_organizer_locks_spending_cap_and_rules():
    svc, database = service()
    assert svc.block_pair("guild", "org", False, "11", "Ada", "22", "Bea").code == "no_event"
    svc.create_event("guild", "org", "Party")
    assert svc.set_event_copy("guild", "org", False, "rules", "Handmade gifts only").ok
    joined = svc.join("guild", "11", "Ada")
    assert "Rules for this event" in joined.data["welcome"].text()
    assert "Handmade gifts only" in joined.data["welcome"].text()
    assert svc.block_pair("guild", "11", False, "11", "Ada", "22", "Bea").code == "forbidden"
    locked = svc.block_pair("guild", "org", False, "11", "Ada", "22", "Bea")
    assert locked.ok
    assert "cannot remove this" in locked.message
    assert "joined yet" in locked.message
    ready_participant(svc, "guild", "22", "Bea")
    ready_participant(svc, "guild", "33", "Cara")
    ready_participant(svc, "guild", "44", "Dee")
    ready_participant(svc, "guild", "11", "Ada")
    assert svc.unexclude("guild", "11", "22", "Bea").code == "locked"
    assert svc.exclude("guild", "11", "22", "Bea").ok
    assert svc.unexclude("guild", "11", "22", "Bea").code == "locked"
    assert svc.set_event_copy("guild", "11", False, "spending_cap", "$25").code == "forbidden"
    assert svc.set_event_copy("guild", "org", False, "spending_cap", "  $25 ").ok
    assert svc.set_event_copy("guild", "org", False, "rules", "x" * 501).code == "too_long"
    assert svc.start("guild", "org", False).ok
    event_id = database.get_open_event("guild")["id"]
    pairs = {row["giver_id"]: row["receiver_id"] for row in database.list_pairings(event_id)}
    assert pairs["11"] != "22"
    assert pairs["22"] != "11"
    svc.set_text_field("guild", "22", "budget", "$10", False)
    personal = svc.match_info("guild", next(giver for giver, receiver in pairs.items() if receiver == "22"))
    assert "$10" in personal.message
    assert "for this event" not in personal.message
    shared = svc.match_info("guild", next(giver for giver, receiver in pairs.items() if receiver == "11"))
    assert "$25 for this event" in shared.message
    assert "Handmade gifts only" in shared.message
    listed = svc.locked_blocks("guild", "org", False)
    assert "<@11>" in listed.message and "<@22>" in listed.message
    assert svc.unblock_pair("guild", "org", False, "11", "22", "Ada", "Bea").ok
    assert svc.locked_blocks("guild", "org", False).message == "**No organizer locks yet.**"
    assert ("11", "22") not in database.list_exclusions(event_id)

    database.pairings[event_id] = [
        {"giver_id": "11", "receiver_id": "22", "gift_status": "bought", "tracking_url": ""},
        {"giver_id": "22", "receiver_id": "33", "gift_status": "not_bought", "tracking_url": ""},
        {"giver_id": "33", "receiver_id": "44", "gift_status": "not_bought", "tracking_url": ""},
        {"giver_id": "44", "receiver_id": "11", "gift_status": "not_bought", "tracking_url": ""},
    ]
    separated = svc.block_pair("guild", "org", False, "11", "Ada", "22", "Bea")
    assert separated.ok
    assert separated.data["changed"]
    redrawn = {row["giver_id"]: row["receiver_id"] for row in database.list_pairings(event_id)}
    assert redrawn["11"] != "22"
    assert redrawn["22"] != "11"

    tiny, tiny_db = service()
    tiny.create_event("guild", "org", "Party")
    for user_id, name in (("11", "Ada"), ("22", "Bea"), ("33", "Cara")):
        ready_participant(tiny, "guild", user_id, name)
    assert tiny.start("guild", "org", False).ok
    tiny_id = tiny_db.get_open_event("guild")["id"]
    before = tiny_db.list_pairings(tiny_id)
    refused = tiny.block_pair("guild", "org", False, "11", "Ada", "22", "Bea")
    assert refused.code == "no_draw"
    assert tiny_db.list_pairings(tiny_id) == before
    assert tiny_db.list_locked_pairs(tiny_id) == []


def test_budget_notes_tracking_and_reports():
    svc, database = service()
    svc.create_event("guild", "org", "Party")
    ready_participant(svc, "guild", "ada", "Ada")
    ready_participant(svc, "guild", "bea", "Bea")
    ready_participant(svc, "guild", "cara", "Cara")
    saved = svc.set_text_field("guild", "ada", "budget", "  $20  ", True)
    assert saved.ok
    assert saved.data["delete_message"] is True
    assert database.get_participant(database.get_open_event("guild")["id"], "ada")["budget"] == "$20"
    assert svc.set_text_field("guild", "ada", "budget", "x" * 81, False).code == "too_long"
    assert svc.start("guild", "org", False).ok
    event_id = database.get_open_event("guild")["id"]
    database.pairings[event_id] = [
        {"giver_id": "ada", "receiver_id": "bea", "gift_status": "not_bought", "tracking_url": ""},
        {"giver_id": "bea", "receiver_id": "cara", "gift_status": "not_bought", "tracking_url": ""},
        {"giver_id": "cara", "receiver_id": "ada", "gift_status": "not_bought", "tracking_url": ""},
    ]
    match = svc.match_info("guild", "cara")
    assert "Spending limit" in match.message
    assert "$20" in match.message

    assert svc.my_notes("guild", "ada").message == "**No anonymous notes yet.**"
    prepared = svc.prepare_message("guild", "ada", "giftee", "hello there", "https://cdn.example/a.gif")
    assert prepared.data["target_id"] == "bea"
    svc.remember_note(event_id, "ada", prepared.data["target_id"], prepared.data["body"], prepared.data["image"])
    svc.remember_note(event_id, "ada", "bea", "the new note", "")
    svc.remember_note(event_id, "cara", "ada", "from your santa", "")
    ada_notes = svc.my_notes("guild", "ada")
    assert ada_notes.card.title == "Your latest notes"
    assert "To the person you're buying for" in ada_notes.message
    assert "the new note" in ada_notes.message
    assert "hello there" not in ada_notes.message
    assert "From the person buying your gift" in ada_notes.message
    assert "from your santa" in ada_notes.message
    assert "Bea" not in ada_notes.message
    assert "Cara" not in ada_notes.message
    bea_notes = svc.my_notes("guild", "bea")
    assert "From the person buying your gift" in bea_notes.message
    assert "the new note" in bea_notes.message

    assert svc.set_gift_status("guild", "bea", "shipped", "https://track.example/1").code == "bad_transition"
    assert svc.set_gift_status("guild", "bea", "bought").ok
    rejected = svc.set_gift_status("guild", "bea", "shipped", "not a link")
    assert rejected.code == "bad_link"
    assert next(row["gift_status"] for row in database.list_pairings(event_id) if row["giver_id"] == "bea") == "bought"
    shipped = svc.set_gift_status("guild", "bea", "shipped", "https://track.example/1")
    assert shipped.ok
    assert shipped.data["notify_receiver_id"] == "cara"
    assert shipped.data["tracking_url"] == "https://track.example/1"
    later = svc.set_gift_status("guild", "bea", "tracking", "https://track.example/2")
    assert later.data["tracking_url"] == "https://track.example/2"
    stored = next(row for row in database.list_pairings(event_id) if row["giver_id"] == "bea")
    assert stored["gift_status"] == "shipped"
    assert stored["tracking_url"] == "https://track.example/2"

    assert svc.feedback_report("guild", "ada", False).code == "forbidden"
    assert svc.submit_feedback("guild", "42", "problem", "the button failed").ok
    assert svc.submit_feedback("other", "42", "feature", "stickers").ok
    report = svc.feedback_report("guild", "org", False)
    assert report.card.title == "Reports and ideas"
    assert "<@42>" in report.message
    assert "the button failed" in report.message
    assert "stickers" not in report.message
    assert svc.finish("guild", "org", False).ok
    assert "the button failed" in svc.feedback_report("guild", "org", False).message


async def test_profile_form_saves_a_spending_limit():
    from views import ProfileModal

    svc, database = service()
    svc.create_event("guild", "org", "Party")
    ready_participant(svc, "guild", "ada", "Ada")

    class Cog:
        def __init__(self):
            self.service = svc

        async def run(self, func, *args, **kwargs):
            return func(*args, **kwargs)

        async def notify_wishlist(self, payload):
            del payload

    class Response:
        async def defer(self, ephemeral=False):
            del ephemeral

        async def send_message(self, message, ephemeral=False):
            del message, ephemeral

    class Followup:
        def __init__(self):
            self.messages = []

        async def send(self, message, ephemeral=False):
            del ephemeral
            self.messages.append(message)

    info = svc.my_info("guild", "ada")
    modal = ProfileModal(Cog(), "guild", "ada", info.data)
    assert len(modal.children) == 5
    assert modal.budget.label == "Spending limit"
    modal.wishlist._value = "books for Ada"
    modal.budget._value = "about 20"
    modal.do_not_gift._value = ""
    modal.notes._value = ""
    modal.address._value = "99 Private Road"
    saved = SimpleNamespace(user=SimpleNamespace(id="ada"), response=Response(), followup=Followup())
    await modal.on_submit(saved)
    event_id = database.get_open_event("guild")["id"]
    assert database.get_participant(event_id, "ada")["budget"] == "about 20"
    assert saved.followup.messages[0] == "**Profile saved.**"


def test_a_past_match_can_be_skipped_before_they_join():
    svc, database = service()
    svc.create_event("guild", "org", "Party")
    assert svc.exclude("guild", "stranger", "bea", "Bea").code == "not_joined"
    ready_participant(svc, "guild", "a", "Ada")
    saved = svc.skip_past_matches(
        "guild",
        "a",
        [("a", "Ada"), ("bea", "Bea"), ("cal", "Cal")],
    )
    assert saved.ok
    assert "exclu" not in saved.message.lower()
    assert "Bea" in saved.message and "Cal" in saved.message
    assert "joined" in saved.message.lower()
    event_id = database.get_open_event("guild")["id"]
    stored = database.list_exclusions(event_id)
    assert ("a", "bea") in stored and ("bea", "a") in stored
    assert ("a", "cal") in stored and ("cal", "a") in stored
    assert ("a", "a") not in stored
    assert svc.skip_past_matches("guild", "a", [("a", "Ada")]).code == "bad_exclusion"

    ready_participant(svc, "guild", "bea", "Bea")
    ready_participant(svc, "guild", "c", "Cara")
    ready_participant(svc, "guild", "d", "Dee")
    assert svc.start("guild", "org", False).ok
    pairs = {row["giver_id"]: row["receiver_id"] for row in database.list_pairings(event_id)}
    assert pairs["a"] != "bea"
    assert pairs["bea"] != "a"
    later = svc.exclude("guild", "a", "eve", "Eve")
    assert later.ok
    assert "drawn again" in later.message
    cleared = svc.unexclude("guild", "a", "cal", "Cal")
    assert cleared.ok
    assert "paired with you again" in cleared.message
    assert ("a", "cal") not in database.list_exclusions(event_id)


async def test_profile_menu_skips_server_members_who_have_not_joined():
    from types import SimpleNamespace

    from views import PastMatchSelect, ProfileView

    svc, database = service()
    svc.create_event("guild", "org", "Party")
    ready_participant(svc, "guild", "ada", "Ada")

    class Cog:
        def __init__(self):
            self.service = svc

        async def run(self, func, *args, **kwargs):
            return func(*args, **kwargs)

    class Response:
        def __init__(self):
            self.messages = []
            self.done = False

        def is_done(self):
            return self.done

        async def defer(self, ephemeral=False):
            del ephemeral
            self.done = True

        async def send_message(self, message, ephemeral=False):
            del ephemeral
            self.messages.append(message)
            self.done = True

    class Followup:
        def __init__(self):
            self.messages = []

        async def send(self, message, ephemeral=False):
            del ephemeral
            self.messages.append(message)

    closed = ProfileView(Cog(), "guild", "ada", False, in_guild=False)
    assert not any(isinstance(item, PastMatchSelect) for item in closed.children)
    view = ProfileView(Cog(), "guild", "ada", False, in_guild=True)
    select = next(item for item in view.children if isinstance(item, PastMatchSelect))
    assert select.placeholder == "Someone you were paired with before"
    select._values = [
        SimpleNamespace(id="bot", display_name="Helper", bot=True),
        SimpleNamespace(id="bea", display_name="Bea", bot=False),
    ]
    interaction = SimpleNamespace(user=SimpleNamespace(id="ada"), response=Response(), followup=Followup())
    await select.callback(interaction)
    event_id = database.get_open_event("guild")["id"]
    assert ("ada", "bea") in database.list_exclusions(event_id)
    assert ("ada", "bot") not in database.list_exclusions(event_id)
    assert "Bea" in interaction.followup.messages[0]
    assert "exclu" not in interaction.followup.messages[0].lower()

    stranger = SimpleNamespace(user=SimpleNamespace(id="bea"), response=Response(), followup=Followup())
    await select.callback(stranger)
    assert "belongs to the person" in stranger.response.messages[0]


def test_messages_exclusions_and_help_cover_the_remaining_rules():
    svc, _database = service()
    assert resolve_dm_events([]) is None
    assert resolve_dm_events([{"id": 1}, {"id": 2}]) is None
    assert resolve_dm_events([{"id": 7}])["id"] == 7
    help_text = format_help([("join", "Join the event"), ("start", "Draw the names")], "s!")
    assert "Getting started" in help_text
    assert "`s!join` — Join the event" in help_text
    assert "Organizer" in help_text
    assert "`s!start` — Draw the names" in help_text
    assert help_text.index("Getting started") < help_text.index("`s!join`") < help_text.index("Organizer")
    assert "exclude" not in format_help([("join", "Join")], "s!")

    svc.create_event("guild", "org", "Party")
    ready_participant(svc, "guild", "a", "Ada")
    ready_participant(svc, "guild", "b", "Bea")
    assert svc.exclude("guild", "a", "a").code == "bad_exclusion"
    remembered = svc.exclude("guild", "a", "missing", "Someone new")
    assert remembered.ok
    assert ("a", "missing") in _database.list_exclusions(_database.get_open_event("guild")["id"])
    assert svc.prepare_message("guild", "a", "giftee", "hello").code == "bad_phase"
    assert svc.set_deadline("guild", "org", False, "December").code == "bad_date"
    assert svc.start("guild", "org", False).ok

    to_giftee = svc.prepare_message("guild", "a", "giftee", "hello")
    assert to_giftee.data["target_id"] == "b"
    assert to_giftee.data["reply_as"] == "gifter"
    to_gifter = svc.prepare_message("guild", "a", "gifter", "thanks")
    assert to_gifter.data["target_id"] == "b"
    assert to_gifter.data["reply_as"] == "giftee"
    assert svc.prepare_message("guild", "a", "santa", "hello").code == "bad_target"
    assert svc.prepare_message("guild", "a", "giftee", "x" * 1001).code == "too_long"
    assert svc.prepare_message("guild", "a", "giftee", "").code == "empty"
    gif = svc.prepare_message("guild", "a", "giftee", "", "https://cdn.example/cat.gif")
    assert gif.ok
    assert gif.data["body"] == ""
    assert gif.data["image"] == "https://cdn.example/cat.gif"
    assert svc.prepare_message("guild", "a", "giftee", "hi", "javascript:alert(1)").data["image"] == ""
    assert svc.unexclude("guild", "a", "b").code == "missing_exclusion"

    signup = svc.leave("guild", "nobody")
    assert signup.code == "not_joined"


def test_feedback_is_logged_and_the_changelog_shows_once(capsys):
    svc, database = service()
    assert svc.submit_feedback("guild", "ada", "problem", "   ").code == "empty"
    assert svc.submit_feedback("guild", "ada", "problem", "x" * 1001).code == "too_long"
    saved = svc.submit_feedback("guild", "ada", "problem", "the menu\nfailed")
    assert saved.message.startswith("Problem report saved")
    assert database.feedback[0]["body"] == "the menu failed"
    err = capsys.readouterr().err
    assert "INFO secret_santa PROBLEM: server guild user ada: the menu failed" in err
    assert "ERROR" not in err
    idea = svc.submit_feedback("", "ada", "feature", "gif notes")
    assert idea.message.startswith("Feature request saved")
    assert database.feedback[1]["kind"] == "feature"
    assert database.feedback[1]["guild_id"] == ""
    assert svc.claim_changelog("ada") is True
    assert svc.claim_changelog("ada") is False
    assert svc.claim_changelog("bea") is True
    assert svc.claim_tip("ada", "") is False
    assert svc.claim_tip("ada", "profile") is True
    assert svc.claim_tip("ada", "profile") is False
    assert svc.claim_tip("bea", "profile") is True
    assert svc.claim_command_tip("ada", "profile") == "past-match"
    assert svc.claim_command_tip("ada", "profile") == ""
    assert svc.claim_command_tip("bea", "join") == ""
    assert svc.claim_command_tip("cal", "join") == "profile"
    assert "profile" in _private_command_message("exclude")
    assert _private_command_message("include") == _private_command_message("exclude")
    assert _private_command_message("lock") == "Run that command in the server."


def test_rate_limiter_window_and_disabled_mode():
    limiter = RateLimiter(max_commands=2, window=10)
    assert limiter.check("user", 0) == (False, 0)
    assert limiter.check("user", 1) == (False, 0)
    limited, remaining = limiter.check("user", 2)
    assert limited is True
    assert remaining == 8
    assert limiter.check("user", 12) == (False, 0)
    limiter.enabled = False
    assert limiter.check("user", 12) == (False, 0)
    assert limiter.check("user", 12) == (False, 0)
    assert limiter.check("user", 12) == (False, 0)


def test_health_payload_reports_a_down_database():
    body, status = health_payload(False, True)
    assert status == 503
    assert body["database"] is False
    assert body["status"] == "unhealthy"
    body, status = health_payload(True, False)
    assert status == 200
    assert body["bot_ready"] is False
    assert body["status"] == "healthy"


def test_locked_roster_broadcast_and_bad_feedback_are_refused():
    svc, _database = service()
    assert svc.broadcast_recipients("guild", "org", False).code == "no_event"
    svc.create_event("guild", "org", "Party")
    assert svc.broadcast_recipients("guild", "org", False).code == "empty"
    assert svc.missing_profile_targets("guild", "stranger", False).code == "forbidden"
    assert svc.missing_profile_targets("guild", "org", True).data["users"] == []
    ready_participant(svc, "guild", "ada", "Ada")
    svc.join("guild", "bea", "Bea")
    assert svc.lock("guild", "ada", True).ok
    assert svc.leave("guild", "ada").code == "closed"
    assert svc.unlock("guild", "ada", False).code == "forbidden"
    assert svc.unlock("guild", "org", False).ok
    assert svc.lock("guild", "org", False).ok
    assert svc.lock("guild", "org", False).code == "bad_phase"
    assert svc.unlock("guild", "org", False).ok
    assert svc.unlock("guild", "org", False).code == "bad_phase"

    ready = svc.missing_profile_targets("guild", "org", False)
    assert {person["user_id"] for person in ready.data["users"]} == {"bea"}
    recipients = svc.broadcast_recipients("guild", "org", False)
    assert set(recipients.data["user_ids"]) == {"ada", "bea"}

    assert svc.submit_feedback("guild", "ada", "complaint", "hello").code == "bad_kind"
    assert svc.skip_past_matches("guild", "ada", [("ada", "Ada"), ("", "nobody")]) .code == "bad_exclusion"
    try:
        svc.mark_reminded(1, "hourly", date(2026, 12, 1))
    except ValueError as error:
        assert "hourly" in str(error)
    else:
        raise AssertionError("unknown reminder kind was accepted")


def test_root_and_health_share_the_database_status():
    from bot import app, runtime

    runtime["db"] = None
    client = app.test_client()
    for path in ("/", "/health"):
        response = client.get(path)
        assert response.status_code == 503
        assert response.get_json()["status"] == "unhealthy"
        assert response.get_json()["database"] is False
