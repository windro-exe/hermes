<!-- Copy this file into entries/ as YYYY-MM-DD-NN-short-slug.md and fill every section. -->
<!-- Read fork/changelog/README.md first — the hard rules there are not optional. -->

# `hermes_fork` was missing from py-modules, which would break packaged builds

**Date:** 2026-08-09
**Type:** Fixed
**Branch:** fix/package-hermes-fork


## Why

`entries/2026-08-09-02-disconnect-upstream.md` added `hermes_fork.py`, a
root-level single-file module, and had `hermes_cli/main.py` import it at module
level. It was never added to `[tool.setuptools] py-modules`.

That list is not decoration. Its own comment says: *"Top-level single-file modules
(not packages). Without this, uv2nix's sealed venv is missing hermes_constants,
run_agent, etc."* Anything absent from it is dropped from a packaged build.

The failure mode is the worst kind: invisible in development. Development and the
installer both use an **editable** install (`__editable__.hermes_agent-*.pth`
installs a finder that maps back to the source tree), so every root-level `.py`
resolves whether declared or not. Everything passed -- tests, ruff, a live update,
`hermes --version`. But in a Nix/uv2nix sealed venv or the Docker image,
`hermes_fork.py` would not be present, `import hermes_fork` would raise, and
because `hermes_cli/main.py` imports it at module scope, **the entire CLI would
fail to start**. Not a degraded feature -- a dead binary.

It surfaced only because the installed copy was probed for `import hermes_fork`
from a directory that was not the repo root, which produced the same
`ModuleNotFoundError` a packaged build would. That was luck. Hence the guard below.

## What changed

- **`pyproject.toml`** -- `"hermes_fork"` appended to `py-modules`, with a comment
  recording why omission is silent.
- **`tests/hermes_cli/test_fork_upstream_disconnect.py`** -- new `TestPackaging`
  with three assertions:
  - `hermes_fork` is declared;
  - **generally**, every root-level module that any non-test source file imports
    is declared -- this is the invariant that was violated, and it catches the
    next one automatically rather than the next one being found by luck;
  - the reverse, that every declared name still has a file, catching a delete or
    rename that would break the build the other way.

## Verified

```
pytest tests/hermes_cli/test_fork_upstream_disconnect.py -q   -> 23 passed
ruff check tests/hermes_cli/test_fork_upstream_disconnect.py  -> All checks passed
```

Scan across the tree before the fix: `hermes_fork` was the only root module
imported by source and not declared. After it: none.

The packaging fix was proved by building the artefact, not by reading the list.
Plain `pip wheel .` is deliberately refused by this project (*"Hermes is
distributed via the shell installer, Docker image, or Nix"*), and the Nix
derivation sets `HERMES_NIX_BUILD=1` to allow it, so the build was run that way:

```
HERMES_NIX_BUILD=1 pip wheel . --no-deps
  -> hermes_agent-0.19.0-py3-none-any.whl
  root-level modules shipped: batch_runner, cli, hermes_bootstrap, hermes_constants,
    hermes_fork, hermes_logging, hermes_state, hermes_time, mcp_serve, model_tools,
    run_agent, toolset_distributions, toolsets, trajectory_compressor, utils
  hermes_fork.py present: True
```

Not verified: an actual Nix build or Docker image. Neither toolchain is available
on this machine. The wheel is the same setuptools path both consume, and it now
contains the file, but the end-to-end derivation was not run.

## Risk / watch for

- **Every future root-level `.py` needs a `py-modules` entry.** The new general
  test enforces it, but only for modules imported by non-test source. A root
  module imported *only* by tests would still slip through -- acceptable, since
  tests do not ship, but worth knowing if that ever stops being true.
- The wheel reports version `0.19.0` while the editable install reports `0.20.0`.
  Not investigated; unrelated to this change, but do not read the wheel's version
  as authoritative.
- Editable installs will keep masking this class of bug. Any change that adds a
  root-level module should be checked against a `HERMES_NIX_BUILD=1` wheel, not
  against the dev venv.

## Follow-ups

- None. This closes a defect introduced by `2026-08-09-02`; that entry's own
  follow-ups still stand.
