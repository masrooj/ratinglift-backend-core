"""Tests for the startup seeder registry."""
from __future__ import annotations

from app.db import seed as seed_module


def test_run_seeders_invokes_every_registered_seeder(monkeypatch):
    calls: list[str] = []

    class _FakeSession:
        def close(self):
            calls.append("closed")

    fake_session = _FakeSession()
    monkeypatch.setattr(seed_module, "SessionLocal", lambda: fake_session)

    def seeder_a(db):
        assert db is fake_session
        calls.append("a")

    def seeder_b(db):
        assert db is fake_session
        calls.append("b")

    monkeypatch.setattr(seed_module, "SEEDERS", [("a", seeder_a), ("b", seeder_b)])

    seed_module.run_seeders()

    assert calls == ["a", "b", "closed"]


def test_run_seeders_continues_when_one_fails(monkeypatch):
    calls: list[str] = []

    class _FakeSession:
        def close(self):
            calls.append("closed")

    monkeypatch.setattr(seed_module, "SessionLocal", lambda: _FakeSession())

    def bad(db):
        calls.append("bad")
        raise RuntimeError("boom")

    def good(db):
        calls.append("good")

    monkeypatch.setattr(seed_module, "SEEDERS", [("bad", bad), ("good", good)])

    seed_module.run_seeders()

    assert calls == ["bad", "good", "closed"]


def test_seed_admins_deduplicates_within_admins_list(monkeypatch):
    from app.modules.auth import bootstrap as bootstrap_module

    # Same email twice in ADMINS => second entry must be skipped without DB call.
    entry = bootstrap_module.AdminSeed(
        email="dup@example.com",
        password="ValidPass123!",
        full_name="Dup",
        role="SUPER_ADMIN",
    )
    monkeypatch.setattr(bootstrap_module, "ADMINS", [entry, entry])

    seed_one_calls: list[str] = []

    def fake_seed_one(db, e):
        seed_one_calls.append(e.email)
        return None  # simulate already present

    monkeypatch.setattr(bootstrap_module, "_seed_one", fake_seed_one)

    class _DB:
        pass

    bootstrap_module.seed_admins(_DB())  # type: ignore[arg-type]
    assert seed_one_calls == ["dup@example.com"]  # only once
