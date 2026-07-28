<!-- Copy this file into entries/ as YYYY-MM-DD-NN-short-slug.md and fill every section. -->
<!-- Read fork/changelog/README.md first — the hard rules there are not optional. -->

# <One line: what changed, in plain words>

**Date:** YYYY-MM-DD
**Type:** Added | Fixed | Changed | Removed | Performance | Docs
**Branch:** <branch it landed on>

<!-- Do NOT put the commit sha here. The entry ships in the same commit it would
     describe, so any sha you write is either a guess or goes stale the moment the
     commit is amended or rebased. Find it with:
       git log --oneline -- fork/changelog/entries/<this-file>.md  -->


## Why

What was wrong or missing, and how you know. Include the symptom a human would
notice, not just the technical defect. If a measurement or a doc led you here, quote
it.

If this corrects an earlier entry, name it: `Supersedes entries/<file>.md`.

## What changed

For each file touched:

- **`path/to/file`** — the mechanism, in words, before the line number. What it did
  before, what it does now, and why that is the right shape. Describe the change as
  "one added block, no removals" or similar — **do not quote line counts** (see the
  README for why).

If you touched an **upstream** file, state it plainly and explain why a fork-owned
file wouldn't do. Every upstream line this fork touches is a future merge conflict.

## Verified

The commands you actually ran, and their actual output. Not what you expect them to
print.

```bash
# example
venv/Scripts/python.exe -m pytest tests/ -q        # -> N passed
cd apps/desktop && npx tsc -p . --noEmit           # -> clean
```

Then, separately, what you could **not** verify and why. Be specific: "did not
measure frame time — needs a CPU profile on the target machine" is useful; silence
is not.

## Risk / watch for

How this could come back. The edge case you left uncovered. The upstream file that
would break this if it moves or gets renamed. The assumption that is true today and
might not be tomorrow.

## Follow-ups

Known-but-not-done, with enough detail that someone else could pick it up. Write
"none" if there are none.
