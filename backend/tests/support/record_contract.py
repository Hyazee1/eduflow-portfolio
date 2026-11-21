import pytest

from eduflow.domain.errors import Conflict, NotFound, StaleVersion


class RecordStoreContract:
    def test_round_trip_returns_detached_record(self, store):
        created = store.add(
            "users",
            {
                "id": "contract-user",
                "username": "contract-user",
                "name": "Contract",
                "role": "ADMIN",
                "password_hash": "not-a-credential",
            },
        )
        returned = store.get("users", created["id"])
        returned["name"] = "Changed externally"
        assert store.get("users", created["id"])["name"] == "Contract"

    def test_unknown_record_is_not_found(self, store):
        with pytest.raises(NotFound):
            store.get("users", "unknown-record")

    def test_stale_update_is_rejected(self, store):
        user = store.add(
            "users",
            {
                "id": "contract-user",
                "username": "contract-user",
                "name": "Contract",
                "role": "ADMIN",
                "password_hash": "not-a-credential",
            },
        )
        changed = store.update("users", user["id"], {"name": "Updated"}, user["version"])
        assert changed["version"] == user["version"] + 1
        with pytest.raises(StaleVersion):
            store.update("users", user["id"], {"name": "Lost write"}, user["version"])

    def test_duplicate_id_is_rejected(self, store):
        values = {
            "id": "contract-user",
            "username": "contract-user",
            "name": "Contract",
            "role": "ADMIN",
            "password_hash": "not-a-credential",
        }
        store.add("users", values)
        with pytest.raises(Conflict):
            store.add("users", values)
