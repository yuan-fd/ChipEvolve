"""Identity: accounts, sessions, ownership, allowances.

The assertions worth having here are the security-shaped ones.  A login that
leaks which usernames exist, or a resource whose ownership can be reassigned, is
a real defect that no amount of feature testing would surface.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from openroad_platform_identity import (
    AuthSession,
    IdentityStore,
    PBKDF2_ITERATIONS,
    SESSION_SECONDS,
)

PASSWORD = "correct horse battery staple"


@pytest.fixture()
def store(tmp_path: Path) -> IdentityStore:
    return IdentityStore(tmp_path / "identity.db")


# --------------------------------------------------------------------------
# accounts
# --------------------------------------------------------------------------

def test_the_first_account_becomes_the_developer(store: IdentityStore):
    """A fresh deployment needs an administrator without a config file."""
    session, token = store.register("alice", PASSWORD)
    assert session.developer is True
    assert token

    second, _ = store.register("bob", PASSWORD)
    assert second.developer is False


def test_a_username_must_be_well_formed(store: IdentityStore):
    for bad in ("ab", "a" * 33, "-leading", "has space", "has/slash"):
        with pytest.raises(ValueError, match="Username must be"):
            store.register(bad, PASSWORD)


def test_a_short_password_is_refused(store: IdentityStore):
    with pytest.raises(ValueError, match="Password must contain"):
        store.register("alice", "short")
    with pytest.raises(ValueError, match="Password must contain"):
        store.register("alice", "x" * 257)


def test_registering_the_same_username_twice_is_refused(store: IdentityStore):
    store.register("alice", PASSWORD)
    with pytest.raises(ValueError, match="already registered"):
        store.register("alice", PASSWORD)


def test_usernames_are_case_insensitive(store: IdentityStore):
    store.register("Alice", PASSWORD)
    with pytest.raises(ValueError, match="already registered"):
        store.register("alice", PASSWORD)
    session, _ = store.login("ALICE", PASSWORD)
    assert session.username == "Alice"


# --------------------------------------------------------------------------
# login
# --------------------------------------------------------------------------

def test_a_correct_password_logs_in(store: IdentityStore):
    store.register("alice", PASSWORD)
    session, token = store.login("alice", PASSWORD)
    assert session.username == "alice"
    assert store.resolve(token) is not None


def test_a_wrong_password_is_refused(store: IdentityStore):
    store.register("alice", PASSWORD)
    with pytest.raises(ValueError, match="Invalid username or password"):
        store.login("alice", "not the password")


def test_an_unknown_user_gets_the_same_error_as_a_wrong_password(store: IdentityStore):
    """Distinct messages would turn the login form into a username oracle."""
    store.register("alice", PASSWORD)
    with pytest.raises(ValueError) as unknown:
        store.login("nobody", PASSWORD)
    with pytest.raises(ValueError) as wrong:
        store.login("alice", "wrong password")
    assert str(unknown.value) == str(wrong.value)


def test_an_unknown_user_still_costs_a_full_derivation(store: IdentityStore):
    """Timing equalisation.

    Without the dummy derivation, an unknown username returns almost instantly
    while a real one pays ~310k PBKDF2 iterations.  The difference is large
    enough to read off a network response, and it enumerates accounts.
    """
    store.register("alice", PASSWORD)

    started = time.monotonic()
    store._dummy_password_check(PASSWORD)  # noqa: SLF001 - the behaviour under test
    dummy_seconds = time.monotonic() - started

    entry = store._connect().execute(  # noqa: SLF001
        "SELECT password_salt FROM identity_users WHERE username = 'alice'"
    ).fetchone()
    started = time.monotonic()
    store._derive(PASSWORD, entry[0], PBKDF2_ITERATIONS)  # noqa: SLF001
    real_seconds = time.monotonic() - started

    # Same order of magnitude; the assertion is deliberately loose because it
    # runs on machines of very different speeds.
    assert dummy_seconds > 0
    ratio = max(dummy_seconds, real_seconds) / max(
        min(dummy_seconds, real_seconds), 1e-9
    )
    assert ratio < 5, f"timing differs by {ratio:.1f}x"


def test_an_unknown_user_login_does_not_create_anything(store: IdentityStore):
    with pytest.raises(ValueError):
        store.login("nobody", PASSWORD)
    assert store.list_users() == []


# --------------------------------------------------------------------------
# sessions
# --------------------------------------------------------------------------

def test_a_session_token_is_not_stored_in_the_clear(store: IdentityStore):
    """A database leak must not hand over usable sessions."""
    _, token = store.register("alice", PASSWORD)
    rows = store._connect().execute(  # noqa: SLF001
        "SELECT token_hash FROM identity_sessions"
    ).fetchall()
    assert rows
    for (token_hash,) in rows:
        assert token_hash != token
        assert len(token_hash) == 64


def test_logout_invalidates_the_token(store: IdentityStore):
    _, token = store.register("alice", PASSWORD)
    assert store.resolve(token) is not None
    store.logout(token)
    assert store.resolve(token) is None


def test_an_expired_session_is_refused_and_removed(store: IdentityStore):
    _, token = store.register("alice", PASSWORD)
    # Age the session past its expiry rather than waiting a week.
    with store._connect() as connection:  # noqa: SLF001
        connection.execute(
            "UPDATE identity_sessions SET expires_at = ?", (time.time() - 1,)
        )
    assert store.resolve(token) is None
    remaining = store._connect().execute(  # noqa: SLF001
        "SELECT COUNT(*) FROM identity_sessions"
    ).fetchone()[0]
    assert remaining == 0


def test_resolving_an_absent_token_is_not_an_error(store: IdentityStore):
    assert store.resolve(None) is None
    assert store.resolve("") is None
    assert store.resolve("not-a-token") is None


def test_resolving_updates_last_seen(store: IdentityStore):
    _, token = store.register("alice", PASSWORD)
    before = store._connect().execute(  # noqa: SLF001
        "SELECT last_seen_at FROM identity_sessions"
    ).fetchone()[0]
    time.sleep(0.01)
    store.resolve(token)
    after = store._connect().execute(  # noqa: SLF001
        "SELECT last_seen_at FROM identity_sessions"
    ).fetchone()[0]
    assert after >= before


def test_sessions_are_independent(store: IdentityStore):
    _, first = store.register("alice", PASSWORD)
    _, second = store.login("alice", PASSWORD)
    assert first != second
    store.logout(first)
    assert store.resolve(second) is not None


def test_the_public_session_projection_has_no_secret(store: IdentityStore):
    session, token = store.register("alice", PASSWORD)
    public = session.public()
    assert public["authenticated"] is True
    assert public["user"]["role"] == "developer"
    serialised = repr(public)
    assert token not in serialised
    assert "hash" not in serialised


# --------------------------------------------------------------------------
# the no-auth identity
# --------------------------------------------------------------------------

def test_ensure_local_user_is_idempotent(store: IdentityStore):
    """Ownership has a foreign key, so no-auth mode still needs a real row."""
    assert store.ensure_local_user() is True
    assert store.ensure_local_user() is False
    users = store.list_users()
    assert [u["username"] for u in users] == ["local-user"]


def test_the_local_user_can_own_resources(store: IdentityStore):
    store.ensure_local_user()
    local_id = store.list_users()[0]["user_id"]
    store.bind_resource("run", "run-1", local_id)
    assert store.owner_of("run", "run-1") == local_id


# --------------------------------------------------------------------------
# resource ownership
# --------------------------------------------------------------------------

def test_ownership_round_trips(store: IdentityStore):
    alice, _ = store.register("alice", PASSWORD)
    store.bind_resource("experiment", "exp-1", alice.user_id)
    assert store.owner_of("experiment", "exp-1") == alice.user_id
    assert store.resources_owned("experiment", alice.user_id) == ["exp-1"]


def test_rebinding_to_the_same_owner_is_a_no_op(store: IdentityStore):
    alice, _ = store.register("alice", PASSWORD)
    store.bind_resource("run", "r1", alice.user_id)
    store.bind_resource("run", "r1", alice.user_id)
    assert store.owner_of("run", "r1") == alice.user_id


def test_ownership_cannot_be_reassigned(store: IdentityStore):
    """Silently reassigning would let a later caller take over an earlier
    caller's experiment."""
    alice, _ = store.register("alice", PASSWORD)
    bob, _ = store.register("bob", PASSWORD)
    store.bind_resource("run", "r1", alice.user_id)
    with pytest.raises(ValueError, match="already owned"):
        store.bind_resource("run", "r1", bob.user_id)
    assert store.owner_of("run", "r1") == alice.user_id


def test_an_unowned_resource_stays_accessible(store: IdentityStore):
    """Legacy and system-created rows predate ownership and must not lock out
    every user the moment ownership is introduced."""
    alice, _ = store.register("alice", PASSWORD)
    assert store.owns_resource("run", "never-bound", alice.user_id) is True


def test_a_non_owner_is_refused(store: IdentityStore):
    alice, _ = store.register("alice", PASSWORD)
    bob, _ = store.register("bob", PASSWORD)
    store.bind_resource("run", "r1", alice.user_id)
    assert store.owns_resource("run", "r1", alice.user_id) is True
    assert store.owns_resource("run", "r1", bob.user_id) is False


def test_a_developer_may_reach_another_users_resource_when_asked(store: IdentityStore):
    alice, _ = store.register("alice", PASSWORD)
    bob, _ = store.register("bob", PASSWORD)
    assert alice.developer is True and bob.developer is False
    store.bind_resource("run", "r1", bob.user_id)
    assert store.owns_resource("run", "r1", alice.user_id) is False
    assert store.owns_resource("run", "r1", alice.user_id,
                               developer_all=True) is True
    # A member does not get that reach.
    store.bind_resource("run", "r2", alice.user_id)
    assert store.owns_resource("run", "r2", bob.user_id,
                               developer_all=True) is False


def test_an_invalid_resource_type_is_refused(store: IdentityStore):
    alice, _ = store.register("alice", PASSWORD)
    with pytest.raises(ValueError, match="invalid resource_type"):
        store.bind_resource("", "r1", alice.user_id)
    with pytest.raises(ValueError, match="invalid resource_type"):
        store.bind_resource("x" * 65, "r1", alice.user_id)


def test_an_empty_resource_id_is_refused(store: IdentityStore):
    alice, _ = store.register("alice", PASSWORD)
    with pytest.raises(ValueError, match="resource_id is required"):
        store.bind_resource("run", "", alice.user_id)


# --------------------------------------------------------------------------
# allowances
# --------------------------------------------------------------------------

def test_an_allowance_runs_out_and_reports_the_remainder(store: IdentityStore):
    alice, _ = store.register("alice", PASSWORD)
    assert store.consume_allowance(alice.user_id, "spec", limit=2) == (True, 1)
    assert store.consume_allowance(alice.user_id, "spec", limit=2) == (True, 0)
    assert store.consume_allowance(alice.user_id, "spec", limit=2) == (False, 0)


def test_allowances_are_per_feature(store: IdentityStore):
    alice, _ = store.register("alice", PASSWORD)
    store.consume_allowance(alice.user_id, "spec", limit=1)
    assert store.consume_allowance(alice.user_id, "spec", limit=1) == (False, 0)
    assert store.consume_allowance(alice.user_id, "batch", limit=1) == (True, 0)


def test_a_new_window_resets_the_allowance(store: IdentityStore):
    """Ageing the window row, rather than sleeping for one.

    ``consume_allowance`` requires a window of at least 60 seconds, so waiting
    for a real rollover would make the suite a minute slower for no extra
    coverage.  Moving the recorded window back is the same event observed
    directly.
    """
    alice, _ = store.register("alice", PASSWORD)
    assert store.consume_allowance(alice.user_id, "spec", limit=1,
                                   window_seconds=60) == (True, 0)
    assert store.consume_allowance(alice.user_id, "spec", limit=1,
                                   window_seconds=60) == (False, 0)

    with store._connect() as connection:  # noqa: SLF001
        connection.execute(
            "UPDATE identity_feature_usage SET window_id = window_id - 1"
        )

    # The previous window is now in the past, so this is a fresh allowance.
    assert store.consume_allowance(alice.user_id, "spec", limit=1,
                                   window_seconds=60) == (True, 0)


def test_an_invalid_allowance_is_refused(store: IdentityStore):
    alice, _ = store.register("alice", PASSWORD)
    with pytest.raises(ValueError, match="invalid feature allowance"):
        store.consume_allowance(alice.user_id, "spec", limit=0)
    with pytest.raises(ValueError, match="invalid feature allowance"):
        store.consume_allowance(alice.user_id, "spec", limit=1, window_seconds=5)


# --------------------------------------------------------------------------
# durability
# --------------------------------------------------------------------------

def test_accounts_survive_a_reopen(tmp_path: Path):
    path = tmp_path / "identity.db"
    first = IdentityStore(path)
    _, token = first.register("alice", PASSWORD)
    reopened = IdentityStore(path)
    assert reopened.resolve(token) is not None
    session, _ = reopened.login("alice", PASSWORD)
    assert session.username == "alice"


def test_the_session_lifetime_is_a_week(store: IdentityStore):
    _, token = store.register("alice", PASSWORD)
    expires = store._connect().execute(  # noqa: SLF001
        "SELECT expires_at FROM identity_sessions WHERE token_hash = ?",
        (store._token_hash(token),),  # noqa: SLF001
    ).fetchone()[0]
    assert expires - time.time() == pytest.approx(SESSION_SECONDS, abs=5)
