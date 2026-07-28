"""Guards for windro's fork perf patches — see fork/changelog/.

These exist because both patches are single lines inside large upstream files.
An upstream merge that rewrites the surrounding block can drop either one with
no error and no other test failing: the code still works, just slower. That is
exactly the failure mode the fork changelog warns about, so it gets a test.

Fork-owned file. Upstream has no tests/fork/, so this cannot conflict.
"""

from __future__ import annotations

import pytest


class TestSessionHistoryIsPooled:
    """session.history must run on the RPC thread pool, not the reader thread.

    Measured on a real 878-message state.db: 22ms in the DB plus 93ms in
    _history_to_messages = 116ms. Inline, that is 116ms of head-of-line
    blocking on the socket that also carries prompt.submit and
    session.interrupt.
    """

    def test_session_history_is_in_long_handlers(self):
        from tui_gateway import server

        assert "session.history" in server._LONG_HANDLERS, (
            "session.history was removed from _LONG_HANDLERS — it will run "
            "inline on the reader thread and block prompt.submit / "
            "session.interrupt for the duration of a full transcript rebuild. "
            "See fork/changelog/entries/ for the measurement."
        )

    def test_its_siblings_are_still_pooled(self):
        """If upstream ever un-pools these, our reasoning needs revisiting."""
        from tui_gateway import server

        for name in ("session.resume", "session.list", "session.compress"):
            assert name in server._LONG_HANDLERS, (
                f"{name} is no longer pooled upstream — the justification for "
                "pooling session.history was consistency with these siblings, "
                "so re-check that reasoning."
            )


class TestProjectTreeUsesCompactRows:
    """The project tree must not drag system_prompt blobs out of the DB.

    _project_tree_row never reads system_prompt, and compact_rows is the flag
    that omits it. Measured: 242KB carried for 5 rows on a real 13MB state.db,
    1.09ms -> 0.44ms.
    """

    def test_project_tree_inputs_requests_compact_rows(self, monkeypatch):
        from tui_gateway import server

        seen: dict[str, object] = {}

        class FakeDB:
            def list_sessions_rich(self, **kwargs):
                seen.update(kwargs)
                return []

        # Stop the real implementation after the query we care about: the git
        # warm-up and projects.db reads are irrelevant here and touch the disk.
        monkeypatch.setattr(
            server.git_probe, "warm_roots", lambda *a, **k: None, raising=False
        )

        class _Stop(RuntimeError):
            pass

        def _boom(*a, **k):
            raise _Stop

        import hermes_cli.projects_db as pdb

        monkeypatch.setattr(pdb, "connect_closing", _boom, raising=False)

        with pytest.raises(_Stop):
            server._project_tree_inputs(
                FakeDB(), session_limit=50, include_discovered=False
            )

        assert seen, "list_sessions_rich was never called — test needs updating"
        assert seen.get("compact_rows") is True, (
            "_project_tree_inputs stopped passing compact_rows=True, so every "
            "sidebar project-tree refresh now reads and discards every "
            "system_prompt blob. See fork/changelog/entries/."
        )

    def test_compact_rows_still_excludes_system_prompt(self):
        """Our patch is only useful while compact_rows means what we think."""
        from hermes_state import SessionDB

        assert "system_prompt" in SessionDB._SESSION_COMPACT_EXCLUDED, (
            "compact_rows no longer excludes system_prompt — the project-tree "
            "patch may now be pointless, or may need a different flag."
        )

    def test_project_tree_row_does_not_read_system_prompt(self):
        """If the row builder ever needs it, compact_rows would break it.

        Checks the function body only. The docstring legitimately names the
        column — it says the projection drops the heavy columns on purpose,
        which is why fetching them was waste in the first place.
        """
        import ast
        import inspect

        from tui_gateway import server

        fn = ast.parse(inspect.getsource(server._project_tree_row)).body[0]
        body = fn.body[1:] if ast.get_docstring(fn) else fn.body
        code = "\n".join(ast.unparse(node) for node in body)

        assert "system_prompt" not in code, (
            "_project_tree_row now reads system_prompt, which compact_rows "
            "omits — the project-tree patch must be reverted or reworked."
        )


class TestCredentialPoolMemo:
    """One picker build must not ask the same provider the same question twice.

    Measured on a real config: 78 _credential_pool_is_usable calls across 41
    distinct providers, so 37 were pure repeats, and each one re-read and
    re-parsed auth.json. That was 193ms of a ~560ms payload build.
    """

    def test_repeat_lookups_hit_the_memo(self, monkeypatch):
        from hermes_cli import model_switch as ms

        calls: list[str] = []

        class FakePool:
            def has_credentials(self):
                return True

            def has_available(self):
                return True

        import agent.credential_pool as cp

        monkeypatch.setattr(
            cp, "load_pool", lambda provider: (calls.append(provider), FakePool())[1]
        )

        ms._reset_credential_pool_memo()
        for _ in range(5):
            assert ms._credential_pool_is_usable("openrouter") is True

        assert calls == ["openrouter"], (
            "_credential_pool_is_usable is no longer memoized within a build — "
            f"load_pool ran {len(calls)} times for one provider. Each call "
            "re-reads auth.json from disk. See fork/changelog/entries/."
        )

    def test_memo_does_not_leak_across_builds(self, monkeypatch):
        """An `auth add` must be visible on the very next picker open."""
        from hermes_cli import model_switch as ms

        state = {"available": False}

        class FakePool:
            def has_credentials(self):
                return True

            def has_available(self):
                return state["available"]

        import agent.credential_pool as cp

        monkeypatch.setattr(cp, "load_pool", lambda provider: FakePool())

        ms._reset_credential_pool_memo()
        assert ms._credential_pool_is_usable("openrouter") is False

        # Credential added between builds.
        state["available"] = True
        ms._reset_credential_pool_memo()

        assert ms._credential_pool_is_usable("openrouter") is True, (
            "the credential-pool memo survived a build boundary — auth changes "
            "would not appear until the process restarts. _reset_credential_pool_memo "
            "must be called at the top of list_authenticated_providers."
        )

    def test_build_resets_the_memo(self):
        """The reset must actually be wired into the build entry point."""
        import ast
        import inspect

        from hermes_cli import model_switch as ms

        src = inspect.getsource(ms.list_authenticated_providers)
        assert "_reset_credential_pool_memo()" in src, (
            "list_authenticated_providers no longer resets the credential-pool "
            "memo. Without the reset the memo either never fills (slow) or "
            "never clears (stale auth state)."
        )
        # Parse to be sure it is a real call, not just a mention in a comment.
        fn = ast.parse(src).body[0]
        calls = {
            node.func.id
            for node in ast.walk(fn)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "_reset_credential_pool_memo" in calls


class TestCustomEndpointModelCache:
    """The picker probes the current custom endpoint on every normal open.

    That probe had no cache, so a slow endpoint charged its full latency per
    open. cached_api_models mirrors cached_provider_model_ids but keys on the
    endpoint URL, since a custom endpoint has no registry slug.
    """

    def test_second_call_is_served_from_cache(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))

        from hermes_cli import models as m

        fetches: list[str] = []

        def fake_fetch(api_key, base_url, timeout=5.0, api_mode=None, headers=None):
            fetches.append(base_url)
            return ["model-a", "model-b"]

        monkeypatch.setattr(m, "fetch_api_models", fake_fetch)

        url = "http://127.0.0.1:9999/v1"
        first = m.cached_api_models("", url)
        second = m.cached_api_models("", url)

        assert first == second == ["model-a", "model-b"]
        assert len(fetches) == 1, (
            "cached_api_models hit the network twice — the custom-endpoint "
            "model cache is not working, so every picker open pays the probe."
        )

    def test_force_refresh_bypasses_the_cache(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))

        from hermes_cli import models as m

        fetches: list[str] = []

        def fake_fetch(api_key, base_url, timeout=5.0, api_mode=None, headers=None):
            fetches.append(base_url)
            return ["model-a"]

        monkeypatch.setattr(m, "fetch_api_models", fake_fetch)

        url = "http://127.0.0.1:9999/v1"
        m.cached_api_models("", url)
        m.cached_api_models("", url, force_refresh=True)

        assert len(fetches) == 2, (
            "force_refresh no longer bypasses the cache, so an explicit picker "
            "refresh would not pick up new models from the endpoint."
        )

    def test_stale_entry_beats_an_empty_fetch(self, monkeypatch, tmp_path):
        """Network blip during a picker open should not empty the model list."""
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))

        from hermes_cli import models as m

        result: list[list[str] | None] = [["model-a"]]

        monkeypatch.setattr(
            m,
            "fetch_api_models",
            lambda *a, **k: result[0],
        )

        url = "http://127.0.0.1:9999/v1"
        assert m.cached_api_models("", url) == ["model-a"]

        result[0] = None  # endpoint went away
        assert m.cached_api_models("", url, force_refresh=True) == ["model-a"], (
            "a failed refresh now returns an empty model list instead of the "
            "last known good one — the picker would look broken during a blip."
        )

    def test_rotating_the_key_invalidates(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))

        from hermes_cli import models as m

        fetches: list[str | None] = []
        monkeypatch.setattr(
            m,
            "fetch_api_models",
            lambda api_key, base_url, **k: (fetches.append(api_key), ["m"])[1],
        )

        url = "http://127.0.0.1:9999/v1"
        m.cached_api_models("key-one", url)
        m.cached_api_models("key-two", url)

        assert len(fetches) == 2, (
            "the cache fingerprint ignores the api key, so a rotated "
            "credential would keep serving the old model list."
        )

    def test_picker_path_uses_the_cache(self):
        """The probe branch must call the cached wrapper, not the raw fetch."""
        import inspect

        from hermes_cli import model_switch as ms

        src = inspect.getsource(ms.list_authenticated_providers)
        assert "cached_api_models" in src, (
            "the custom-endpoint probe no longer routes through "
            "cached_api_models — every picker open pays the endpoint latency."
        )


class TestPickerPrewarm:
    """The prewarm must cover the current custom endpoint and run on the gateway."""

    def test_prewarm_probes_the_current_custom_endpoint(self, monkeypatch):
        from hermes_cli import model_switch as ms

        seen: dict[str, object] = {}

        def fake_list(**kwargs):
            seen.update(kwargs)
            return []

        monkeypatch.setattr(ms, "list_authenticated_providers", fake_list)

        ms._picker_prewarm_done.clear()
        thread = ms.prewarm_picker_cache_async()
        assert thread is not None
        thread.join(timeout=30)

        assert seen.get("probe_current_custom_provider") is True, (
            "prewarm_picker_cache_async no longer probes the current custom "
            "endpoint, so users whose active provider is a custom endpoint get "
            "no benefit from the prewarm — the first real picker open pays it."
        )
        assert not seen.get("probe_custom_providers"), (
            "prewarm now probes every saved custom endpoint. Offline saved "
            "endpoints must stay untouched or the warm thread blocks on each."
        )

    def test_gateway_entry_calls_the_prewarm(self):
        """Without this the Desktop picker never gets a warm cache."""
        import ast
        import inspect

        from tui_gateway import entry

        src = inspect.getsource(entry.main)
        assert "prewarm_picker_cache_async" in src, (
            "tui_gateway/entry.py:main no longer starts the picker prewarm. "
            "The Desktop model pill and picker both call model.options, so the "
            "first open pays the full cold cost."
        )
        fn = ast.parse(src.lstrip()).body[0]
        called = {
            node.func.id
            for node in ast.walk(fn)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "prewarm_picker_cache_async" in called
