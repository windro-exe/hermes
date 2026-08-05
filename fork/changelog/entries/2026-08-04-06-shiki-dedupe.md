# Renderer bundle 27.23 MB -> 18.23 MB by deduping Shiki

**Date:** 2026-08-04
**Type:** Changed
**Branch:** `main`

## Why

windro asked what makes the UI slow, and to measure rather than guess. A startup CPU
profile of the packaged app put **3140 ms (20.2%)** in `(program)` — V8 parsing and
compiling — against a single 27.23 MB chunk.

A sourcemap-attributed build then showed what was in it:

```
15.19 MB  36.9%  @shikijs/langs
 2.76 MB   6.7%  @shikijs/themes
 2.68 MB   6.5%  src/app          <- our own code
 2.68 MB   6.5%  mermaid
 1.22 MB   3.0%  @shikijs/engine-oniguruma
```

Shiki was **46.6% of the bundle**, and `emacs-lisp.mjs` (0.75 MB) plus the oniguruma
WASM blob (0.59 MB) each appeared **twice**: the app is on Shiki 4 while
`@streamdown/code` pins `shiki: ^3.19.0`, so npm nests a second copy — 347 language
grammars shipped twice.

## What changed

**`apps/desktop/vite.config.ts`** — `resolve.dedupe` extended to `shiki` and the
`@shikijs/*` packages, the same treatment `react`/`react-dom` already had two lines
above.

Fixed at the bundler, not in npm, because **npm overrides do not apply in this
workspace**. Both the flat `"shiki": "^4.0.2"` and the nested
`"@streamdown/code": {"shiki": ...}` forms were tried, with `npm install` and
`npm install --package-lock-only`; npm rewrote neither the lockfile nor the tree and
printed nothing. The duplication only costs anything in the bundle, so that is where it
is solved.

## Verified

By re-attributing a sourcemap build:

| | before | after |
|---|---|---|
| `@shikijs/langs` | 15.19 MB | 7.61 MB |
| `@shikijs/themes` | 2.76 MB | 1.38 MB |
| `@shikijs/engine-oniguruma` | 1.22 MB | 0.61 MB |
| `emacs-lisp` sources | 2 | 1 |
| shipped bundle | 27.23 MB | **18.23 MB** |

Typecheck clean, code/diff/markdown rendering tests 11 passing, fork guards 115 passing.

## Risk / watch for

- **This forces Shiki v4 on `@streamdown/code`, which declares v3.** Build, typecheck
  and its tests pass, but "renders a fenced code block in a live session" was not
  exercised. That is the check to do before trusting it.
- A retracted finding, recorded so it is not rediscovered: an earlier pass claimed
  9.77 MB of inline SVG. That was a regex over-matching — the "largest SVG" began at a
  regex literal in source (`<svg[\s>]/i.test(n)`) and ran to the next `</svg>`. There is
  no SVG bloat.

## Follow-ups

- **Code splitting is the bigger win and is blocked.** `codeSplitting: false` is set
  deliberately; the comment explains that Shiki's many dynamic chunks can OOM
  electron-builder. Enabling it cut the entry chunk to **3.85 MB** (568 chunks) —
  a 79% reduction in launch-time parse — and then electron-builder failed even with
  `--max-old-space-size=16384`. The comment was right. Making it work needs chunk
  *grouping*, not a flag flip.
- `use-stick-to-bottom`'s `get scrollTop` costs 379 ms of forced layout reads, plus
  172 ms in `getBoundingClientRect`, measured at startup with no session open — a floor,
  not a ceiling.
