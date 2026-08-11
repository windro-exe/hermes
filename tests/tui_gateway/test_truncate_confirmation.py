"""The prompt.submit truncation guard must refuse unconfirmed history loss.

FORK regression test.

The original guard only caught ordinal 0 — the total-wipe edge. A stale client
carrying a leftover NON-zero ordinal on an ordinary submit still silently dropped
every turn from that point on, persisted via ``replace_messages``: the same class
of unrecoverable loss, just partial instead of total.

Backends from 0.20.0 require ``confirm_truncate=true`` for ANY truncation. Ours did
not, and this fork's own client did not send it — so every restore/rewind/edit
against such a backend failed with:

    truncate_before_user_ordinal requires confirm_truncate=true; an ordinary
    prompt.submit must not drop session history

Both sides are fixed: the client always sends ``confirm_truncate`` alongside an
ordinal, and the gateway requires a confirmation for any truncation while still
accepting ``confirm_empty_truncate`` from clients that predate the new flag.

Tests call the real predicate (``tui_gateway.server.truncation_confirmed``) rather
than re-implementing it, so they cannot drift from the behaviour they describe.
"""

from __future__ import annotations

import pytest

from tui_gateway.server import truncation_confirmed


class TestTruncationConfirmed:
    def test_an_ordinal_with_no_confirmation_is_refused(self):
        """The reported bug: a truncating submit carrying no confirmation."""
        assert truncation_confirmed({"truncate_before_user_ordinal": 3}) is False

    def test_no_params_at_all_is_refused(self):
        assert truncation_confirmed({}) is False

    def test_confirm_truncate_is_accepted(self):
        assert truncation_confirmed({"confirm_truncate": True}) is True

    def test_legacy_confirm_empty_truncate_still_accepted(self):
        """Clients predating confirm_truncate must keep working.

        Dropping this would break rewind for anyone who has not rebuilt the
        desktop app yet.
        """
        assert truncation_confirmed({"confirm_empty_truncate": True}) is True

    def test_both_flags_together_are_fine(self):
        """What this repo's client now sends for ordinal 0."""
        assert truncation_confirmed({"confirm_truncate": True, "confirm_empty_truncate": True}) is True

    @pytest.mark.parametrize("falsy", [False, None, "", "false", "0", "no"])
    def test_falsy_confirmations_do_not_satisfy_the_guard(self, falsy):
        """Sending the key with a falsy value has not confirmed anything.

        This is the shape a client bug would most plausibly take — the field
        present but unset — so it must not pass.
        """
        assert truncation_confirmed({"confirm_truncate": falsy}) is False

    @pytest.mark.parametrize("truthy", [True, "true", "1", "yes"])
    def test_truthy_string_forms_are_honoured(self, truthy):
        """JSON-RPC clients may send strings; is_truthy_value is the shared rule."""
        assert truncation_confirmed({"confirm_truncate": truthy}) is True

    def test_an_unrelated_confirmation_key_does_not_count(self):
        """Only the two known flags confirm; a near-miss name must not pass."""
        assert truncation_confirmed({"confirm": True, "truncate": True}) is False
