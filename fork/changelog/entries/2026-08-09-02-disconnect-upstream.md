<!-- Copy this file into entries/ as YYYY-MM-DD-NN-short-slug.md and fill every section. -->
<!-- Read fork/changelog/README.md first — the hard rules there are not optional. -->

# `hermes update` could replace the whole fork with upstream's code

**Date:** 2026-08-09
**Type:** Fixed
**Branch:** fix/disconnect-upstream


## Why

A live install corrupted itself on 2026-08-09. Symptoms: `git` reported HEAD at
`318da8b` (a fork commit), while on disk `AGENTS.md` had none of the FORK-RULES
block, `plugins/model-providers/kiro/` was absent despite being tracked at that
commit, upstream-only files such as `hermes_cli/_scan_venv_blockers.py` were
present, and 3645 files differed from HEAD with real content changes. The Kiro
provider "disappeared" from the app. `hermes update` could not repair it, because
git believed the checkout was already current.

Root cause, `hermes_cli/main.py` `_update_via_zip()`:

```python
zip_url = f"https://github.com/NousResearch/hermes-agent/archive/refs/heads/{branch}.zip"
```

Its own comment says when it runs: *"Used on Windows when git file I/O is broken
(antivirus, NTFS filter drivers)."* On Windows that is routine -- the desktop app
holds `.pyd` files open. So the sequence was: run `hermes update` → git file I/O
blocked → silent fallback to the ZIP path → **download upstream's source archive
and extract it over the fork**. `.git` was untouched, so nothing looked wrong.

This is the same class of bug as
`entries/2026-08-08-02-installer-fork-slug.md`, which fixed three hardcoded
slugs in the install scripts and the desktop bootstrap. That entry missed this
one, and this one is worse: the others pointed installs at a stale repo, while
this replaces the working tree wholesale with a different project's code.

A second latent defect sat two lines away. GitHub source archives extract to
`<repo-name>-<ref>/`, and the code hardcoded `hermes-agent-<branch>`. The fork is
named `hermes`, so that path never existed and it only worked by falling through
to a "pick any directory" guess loop.

The common cause across all of these is that the repository identity was
copy-pasted per call site instead of imported. So the fix centralises it.

## What changed

**New fork-owned file, `hermes_fork.py`** -- one source of truth for repo
identity. `FORK_OWNER`/`FORK_NAME`/`FORK_SLUG`, the HTTPS/SSH/web/releases URLs,
`fork_archive_url()`, `fork_raw_url()`, plus `UPSTREAM_*` constants kept **only
for recognising** an install pointed at upstream. Imports nothing but
`__future__`, so it cannot introduce a cycle anywhere it is used.

**The eight runtime connections to upstream:**

- **`hermes_cli/main.py`** -- `_update_via_zip()` now builds its URL with
  `fork_archive_url()` and derives the extracted directory name from `FORK_NAME`.
  `OFFICIAL_REPO_URLS`/`OFFICIAL_REPO_URL` now derive from `hermes_fork` instead
  of being written out. `_sync_with_upstream_if_needed()` is a hard `return`
  no-op: it offered to add an `upstream` remote and fast-forward onto it, which
  is the automated sync AGENTS.md records as deliberately removed -- and it
  prompted `[Y/n]`, defaulting to yes. The body is left below the return so the
  divergence from upstream stays a one-line diff.
- **`hermes_cli/model_catalog.py`** -- the catalog is fetched from
  `fork_raw_url("website/static/api/model-catalog.json")`. That manifest is
  tracked in-repo, so the URL is always populated. The fallback chain is now
  empty: upstream's chain was site-first with raw GitHub as backup, and there is
  nothing to fall back TO here that isn't upstream. A failed fetch falls through
  to the in-repo copy, which is the correct behaviour for a fork.
- **`hermes_cli/config.py`** -- the `model_catalog.url` default likewise.
- **`hermes_cli/banner.py`** -- `_UPSTREAM_REPO_URL` and
  `_OFFICIAL_REPO_CANONICAL` now name the fork. Not cosmetic: `_check_via_rev()`
  runs `git ls-remote` against that URL and compares to the **local** revision to
  decide whether an update exists. Pointed at upstream, a fork's revision can
  never match, so it contacted NousResearch on every check and reported "update
  available" permanently. `_RELEASE_URL_BASE` also points here -- upstream's
  docstring says "forks don't get a link", but this fork publishes its own
  releases, so linking upstream's tags described code the user isn't running.
- **`tools/skills_hub.py`** -- `OptionalSkillSource.OFFICIAL_REPO` is the fork,
  because it labels the `optional-skills/` tree that ships in *this* repo and the
  index builds source URLs from it. `HERMES_INDEX_URL` points at the fork, and
  `_load_hermes_index()` gained an early return gated on
  `FORK_PUBLISHES_SKILLS_INDEX` (see Risk).
- **`apps/desktop/electron/update-remote.ts`** -- the official-repo constants.
  These decide whether a passive check swaps SSH for anonymous HTTPS to avoid a
  FIDO2 hardware-touch prompt; naming upstream meant the swap never fired for a
  fork install.
- **`apps/desktop/src/app/settings/about-settings.tsx`** -- the About panel's
  release-notes link.
- **`apps/bootstrap-installer/src-tauri/src/install_script.rs`** -- the Windows
  Tauri installer fetched its install script from upstream's
  `raw.githubusercontent.com`. raw does not follow renames, so a fork ref is a
  hard 404 and a mutable ref like `main` silently returns *upstream's* installer,
  which then clones upstream over the user's install.

**New guard suite, `tests/hermes_cli/test_fork_upstream_disconnect.py`** (20
tests). This bug class has now recurred twice and failed silently both times, so
the guard is the durable part of this change: it asserts every *fetched* URL names
the fork, that `_update_via_zip` uses `fork_archive_url` and derives its extract
directory from `FORK_NAME`, that `_sync_with_upstream_if_needed` makes no git call
and raises no prompt, that upstream recognition still works for the informational
banner, and that the three hand-duplicated slugs in TypeScript/Rust have not
drifted. Added in a follow-up commit on the same branch after the first push.

Tests updated where they encoded the old behaviour:
`tests/hermes_cli/test_update_check.py`, `tests/hermes_cli/test_model_catalog.py`,
`tests/tools/test_skills_hub.py`, `apps/desktop/electron/update-remote.test.ts`.
Where a test covered a mechanism rather than a constant, the mechanism kept its
coverage by injecting a chain or flag rather than deleting the test -- see
`TestFallbackChain`, which now also asserts the shipped chain never reaches
upstream. Four new guard tests assert the absence of `nousresearch` in the URLs
that used to contain it.

## Verified

```
ruff check <all touched files>                         -> All checks passed
pytest tests/hermes_cli/test_banner.py test_update_check.py test_model_catalog.py
       tests/tools/test_skills_hub.py
       tests/scripts/test_build_skills_index_health.py -> 219 passed, 4 pre-existing failures
npm run test:desktop:platforms (vitest, electron)      -> 775 passed, 19 pre-existing failures
                                                          update-remote.test.ts passes
```

The four Python and nineteen desktop failures were confirmed **pre-existing** by
stashing this branch and re-running against unmodified `main`: identical files,
identical counts. They are `test_banner.py::…hyperlinked_to_release`, two
`TestCheckForSkillUpdates` content-hash tests, `…preserves_binary_assets`, and on
the desktop side `ssh-connection`, `ssh-config`, `update-relaunch`,
`desktop-installation`, `windows-hermes-path`. Not touched here.

The ZIP path -- the actual bug -- was verified against the live service rather
than by reading the diff:

```
fork_archive_url("main") -> https://github.com/windro-exe/hermes/archive/refs/heads/main.zip
  HTTP 200, 67,626,186 bytes
  archive root dir      : ['hermes-main']     (matches FORK_NAME; 'hermes-agent-main' would not)
  contains plugins/model-providers/kiro/ : True
  contains fork/changelog/               : True
```

The last two are the proof it is the fork's tree and not upstream's: neither path
exists in `NousResearch/hermes-agent`.

**Not verified: a real end-to-end `hermes update` through the ZIP fallback.**
Triggering it requires git file I/O to actually break, which cannot be induced
on demand. The URL, the archive contents and the extracted directory name are all
verified above; the extract-and-swap code below them is unchanged from upstream
and was not re-exercised.

**Not run: `tests/hermes_cli/test_cmd_update.py`.** AGENTS.md forbids it -- it
spawns real `hermes gateway run` processes and leaks them. It was run once during
this work by mistake and left 14 strays, which then made unrelated tests fail via
the venv-holder guard; they were killed. Do not run it. This does mean the file
with the densest coverage of `hermes update` is untested here, which is a real gap
inherited from that rule rather than created by this change.

## Risk / watch for

- **The Skills Hub aggregated index is now inert.** `HERMES_INDEX_URL` points at
  the fork, but `FORK_PUBLISHES_SKILLS_INDEX` is `False`, so `_load_hermes_index()`
  returns None without a request. Generating the index locally produced **~32 MB**
  (79,662 skills aggregated from clawhub, skills.sh, lobehub and others), which is
  too much to commit: `install.ps1` shallow-clones the repo so every install would
  download it, and it would be stale on arrival. `HermesIndexSource` is one of
  eleven sources and the other ten query their own upstreams directly, so the hub
  still works -- with fewer results. To enable: run
  `scripts/build_skills_index.py`, publish the output where `fork_raw_url` points,
  flip the flag.
- **Three slugs are still necessarily duplicated**, because TypeScript and Rust
  cannot import `hermes_fork.py`: `update-remote.ts`, `about-settings.tsx`,
  `install_script.rs`. Each carries a comment saying so. If the fork ever moves
  accounts again, grep for `windro-exe` -- `hermes_fork.py` alone is not enough.
- **The dead code after the `return` in `_sync_with_upstream_if_needed`** is
  deliberate, to keep the upstream diff to one line. Ruff accepts it. If anyone
  "tidies" it away, re-read this entry first -- deleting it makes the next
  upstream merge noisier, not cleaner.
- **`_check_via_rev` now ls-remotes the fork**, which means the update banner
  compares against a repo that actually contains the local revision. Watch that
  it reports "up to date" rather than permanently "behind"; that was the old
  symptom and is the fastest way to spot a regression here.
- Upstream constants are retained on purpose so `_is_fork()` still works -- it
  drives the informational "Updating from fork" line. Anything that *fetches*
  from them is the bug.

## Follow-ups

- **The `.pyd` lock guard should be strengthened.** A guard already exists
  (`_detect_venv_python_processes` → `sys.exit(2)`) and is why a plain `hermes
  update` refuses while the app runs. But the ZIP fallback is reached on git I/O
  failure, which is a *different* trigger; the corruption happened despite the
  guard. Worth making the ZIP path refuse outright rather than silently replacing
  a working tree.
- **Deliberately NOT changed, so nobody assumes it was missed:**
  - the **Nous Portal inference provider** (`inference-api.nousresearch.com`,
    `plugins/model-providers/nous/`, billing, credits). That is a paid inference
    service, unrelated to the repo; removing it would delete a working provider.
  - the **app identity** `com.nousresearch.hermes` (`appId`,
    `setAppUserModelId`, the Tauri setup identifier). Changing it breaks the
    upgrade path, taskbar pinning and macOS permission grants on installs that
    already registered under it.
  - **docs URLs** pointing at `hermes-agent.nousresearch.com/docs`. This fork
    publishes no docs site, so repointing them would produce dead links.
  - **issue references in comments** (`See NousResearch/hermes-agent#47072`).
    Those record where a fix came from and are provenance, not coupling.
- `website/static/api/model-catalog.json` is now load-bearing for this fork's
  model picker. If it is ever deleted, the catalog silently falls back to the
  in-repo snapshot resolved by `local_catalog_path()`.
