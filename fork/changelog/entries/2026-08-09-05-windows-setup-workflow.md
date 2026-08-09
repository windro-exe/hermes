<!-- Copy this file into entries/ as YYYY-MM-DD-NN-short-slug.md and fill every section. -->
<!-- Read fork/changelog/README.md first — the hard rules there are not optional. -->

# The in-GUI Update button had no updater binary to hand off to

**Date:** 2026-08-09
**Type:** Added
**Branch:** feat/windows-setup-workflow


## Why

The desktop app's Update button does not update anything itself. It resolves
`HERMES_HOME/hermes-setup.exe` and hands the job over:

```js
// electron/main.ts
const updater = resolveUpdaterBinary()       // HERMES_HOME/hermes-setup.exe
if (!updater) { ... return { manual: true, command } }
```

with the code's own comment naming the case exactly: *"No staged updater binary —
this is a CLI-installed user (they ran `hermes desktop`, never the Tauri installer
that self-copies hermes-setup.exe into HERMES_HOME)."*

It has to be a separate process. The update rewrites the venv and the app bundle,
so nothing running from inside them can perform it — hence the handoff, the
`releaseBackendLockForUpdate` dance and the shim-lock probing around it.

**Upstream never hits this because that binary IS its Windows download.**
`Hermes-Setup.exe` is the installer, it copies itself into `HERMES_HOME` on the way
through, and `scripts/install.ps1` is written to be driven by it ("Cross-process
driver mode (Hermes-Setup.exe runs each -Stage NAME)").

This fork's Windows installer was built with electron-builder, which packages the
Electron app only — the inner half of upstream's two-layer distribution. The result
runs fine but leaves `hermes-setup.exe` absent, so `resolveUpdaterBinary()` returns
null and the GUI shows "update from your terminal" **permanently**, with no way out
short of building the missing piece. That gap was mine, flagged on 2026-08-08 and
left open until windro hit it repeatedly.

Building it locally is not viable: it is a Rust/Tauri binary needing the MSVC
toolchain, and the target machine has no Rust, no MSVC and no Visual Studio —
Build Tools alone is several GB. `windows-latest` runners ship both preinstalled,
which is the same reasoning that produced `linux-installers.yml`.

## What changed

- **`.github/workflows/windows-setup.yml`** (new) — `workflow_dispatch` only,
  mirroring `linux-installers.yml` in structure, pinned action SHAs and rationale.
  Steps: root `npm ci` (`apps/*` is a workspace member so the installer's frontend
  deps come along), build the installer frontend explicitly so a frontend failure
  is a readable step rather than noise inside the Rust build, then
  `tauri build --bundles nsis`. A `bundle` input can switch to `--no-bundle` for a
  faster raw-binary-only build.

  `--bundles nsis` is required, not stylistic: `tauri.conf.json`'s
  `bundle.targets` is `["app","dmg","appimage"]` with **no Windows entry**, so a
  plain `tauri build` produces no installer. The raw `Hermes-Setup.exe` appears
  either way, and that raw binary is the piece the Update button needs.

  Three verification steps, because a silently-wrong artifact here is worse than a
  failed build: the frontend `dist/index.html` exists (or Tauri embeds nothing);
  the binary exists, is a real PE (`MZ`) and is over 1 MB; and its strings contain
  both `--update` and `windro-exe/hermes` while containing no
  `NousResearch/hermes-agent`. That last one matters — a stale slug would make the
  updater clone upstream over the user's install, the failure recorded in
  `entries/2026-08-09-02-disconnect-upstream.md`.

- **`apps/bootstrap-installer/src-tauri/tauri.conf.json`** — `publisher` and
  `copyright` were "Nous Research", which is inaccurate on a binary built from this
  fork and shown in Windows' Publisher field. Now windro, with upstream credited in
  the copyright line.

## Verified

```
python -c "yaml.safe_load(...)"    -> parses, 11 steps, timeout 60m,
                                      permissions {contents: read}
local action referenced             -> .github/actions/retry/action.yml exists
pinned SHAs                         -> identical to linux-installers.yml for
                                       checkout, setup-node, upload-artifact
tauri.conf.json                     -> valid JSON; copyright U+00A9 intact (169)
grep for exe invocation             -> none; the binary is never executed
```

**Caught in review, before pushing:** the first draft ran `Hermes-Setup.exe --help`
as a smoke test. There is no `--help` — `lib.rs` `AppMode::from_args` treats
anything that is not `--update` as **Install**, so that step would have launched the
installer GUI on a headless runner and hung the job for the full 60-minute timeout,
or begun a real install. Replaced with static checks; the `--update` flag surface is
now asserted by scanning the binary's strings instead.

**Not verified: the workflow has never run.** That is the whole point of it — the
toolchain does not exist locally, so the build cannot be exercised here. What is
verified is everything checkable without executing it: YAML validity, step
structure, referenced paths and actions, pinned SHAs, and the config it consumes.
The Rust build itself, the NSIS bundling, and the resulting binary's behaviour are
all unproven until someone dispatches it.

## Risk / watch for

- **Actions may be disabled for this repo.** `linux-installers.yml`'s own comment
  says Actions was switched off because ~10 workflows trigger on push and the
  notification volume was unacceptable. This workflow cannot fire by itself, but it
  also cannot run at all while Actions is off — that is a repo setting only windro
  can see.
- **`Cargo.lock` is not committed** for `src-tauri`, so every build resolves Rust
  dependencies fresh. Builds are therefore not reproducible and a transitive update
  can change or break the artifact between runs. Upstream does not commit it
  either; committing one generated by this workflow would be the fix if that ever
  bites.
- The binary is **unsigned**. Upstream's is signed; SmartScreen will warn on first
  run, exactly as it does for the electron-builder installer.
- `resolveUpdaterBinary()` looks for lowercase `hermes-setup.exe` while Cargo's
  `[[bin]]` is `Hermes-Setup`. Fine on Windows' case-insensitive filesystem, and
  the only platform that matters here — but do not "fix" the case on a case-
  sensitive system without checking both ends.
- `identifier` is still `com.nousresearch.hermes.setup`. Left deliberately:
  changing it alters the Windows application identity of the setup program, and the
  upgrade/registration consequences were not worth guessing at for a cosmetic win.

## Follow-ups

- Dispatch the workflow, download `Hermes-Setup.exe`, drop it in
  `%LOCALAPPDATA%\hermes\`, and confirm the GUI Update button switches from the
  terminal modal to the real flow. Until that is done this change is untested.
- Once proven, consider making the NSIS bundle from this workflow the fork's
  Windows release artifact instead of the electron-builder one — that is upstream's
  actual shape, and it would stage the updater automatically for anyone installing
  fresh, closing this gap at the source rather than by hand.
- The Accounts-tab Scan/Connect button from
  `entries/2026-08-09-04-kiro-ide-provider.md` is still outstanding and unrelated.
