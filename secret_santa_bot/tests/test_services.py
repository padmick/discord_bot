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
        self.exclusions = set()
        self.pairings = {}
        self.feedback = []
        self.seen_changelog = {}
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

    def list_actionable_events(self):
        return [dict(event) for event in self.events.values() if event["phase"] != "done"]

    def update_event(self, event_id, **fields):
        self.events[event_id].update(fields)

    def delete_event(self, event_id):
        self.events.pop(event_id, None)
        self.participant_order = [key for key in self.participant_order if key[0] != event_id]
        for key in [key for key in self.participants if key[0] == event_id]:
            del self.participants[key]
        self.exclusions = {item for item in self.exclusions if item[0] != event_id}
        self.pairings.pop(event_id, None)

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
            item
            for item in self.exclusions
            if not (item[0] == event_id and (item[1] == user_id or item[2] == user_id))
        }
        self.pairings[event_id] = [
            row
            for row in self.pairings.get(event_id, [])
            if row["giver_id"] != user_id and row["receiver_id"] != user_id
        ]
        return True

    def add_exclusion_pair(self, event_id, user_id, other_id):
        self.exclusions.add((event_id, str(user_id), str(other_id)))
        self.exclusions.add((event_id, str(other_id), str(user_id)))

    def remove_exclusion_pair(self, event_id, user_id, other_id):
        before = len(self.exclusions)
        self.exclusions.discard((event_id, str(user_id), str(other_id)))
        self.exclusions.discard((event_id, str(other_id), str(user_id)))
        return before - len(self.exclusions)

    def list_exclusions(self, event_id):
        return {(giver, receiver) for stored_event, giver, receiver in self.exclusions if stored_event == event_id}

    def replace_pairings(self, event_id, pairs):
        self.replace_calls += 1
        self.pairings[event_id] = [
            {"giver_id": str(giver), "receiver_id": str(receiver), "gift_status": status}
            for giver, receiver, status in pairs
        ]

    def list_pairings(self, event_id):
        return [dict(row) for row in self.pairings.get(event_id, [])]

    def set_gift_status(self, event_id, giver_id, status):
        updated = 0
        for row in self.pairings.get(event_id, []):
            if row["giver_id"] == str(giver_id):
                row["gift_status"] = status
                updated += 1
        return updated

    def add_feedback(self, guild_id, user_id, kind, body):
        self.feedback.append(
            {"guild_id": str(guild_id or ""), "user_id": str(user_id), "kind": kind, "body": body}
        )

    def claim_changelog(self, user_id, version):
        user_id = str(user_id)
        current = self.seen_changelog.get(user_id, 0)
        if int(version) <= current:
            return False
        self.seen_changelog[user_id] = int(version)
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

    assert svc.join("guild", "org", "Organizer").ok
    assert svc.join("guild", "org", "Renamed").ok
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
        {"giver_id": "c", "receiver_id": "d", "gift_status": "bought"},
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


def test_root_and_health_share_the_database_status():
    from bot import app, runtime

    runtime["db"] = None
    client = app.test_client()
    for path in ("/", "/health"):
        response = client.get(path)
        assert response.status_code == 503
        assert response.get_json()["status"] == "unhealthy"
        assert response.get_json()["database"] is False
