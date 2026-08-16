/**
 * Tests for electron/update-remote.ts — the remote-detection helpers that
 * keep passive update checks off the SSH origin for official installs.
 *
 * Run with: node --test electron/update-remote.test.ts
 * (Wired into npm test:desktop:platforms in package.json.)
 *
 * Why this matters: an install can carry
 * origin=git@github.com:windro-exe/hermes.git. A background
 * `git fetch origin` then authenticates over SSH and, with a FIDO2/passkey
 * key, triggers an unexplained hardware-touch prompt. isOfficialSshRemote
 * must reliably recognize our own SSH remote (in every URL form,
 * case-insensitively) so the caller can swap in the anonymous HTTPS path —
 * while NOT misclassifying other repos, other hosts, or the HTTPS remote (which
 * never prompts and should keep the normal fetch path).
 *
 * FORK: "official" throughout means THIS fork. These assertions previously named
 * NousResearch/hermes-agent, so a fork install got no HTTPS substitution and
 * could still raise a hardware prompt on a passive check.
 */

import assert from 'node:assert/strict'

import { test } from 'vitest'

import {
  canonicalGitHubRemote,
  isOfficialSshRemote,
  isSshRemote,
  OFFICIAL_REPO_CANONICAL,
  OFFICIAL_REPO_HTTPS_URL
} from './update-remote'

test('canonicalGitHubRemote normalizes SSH and HTTPS forms to the same value', () => {
  assert.equal(canonicalGitHubRemote('git@github.com:windro-exe/hermes.git'), OFFICIAL_REPO_CANONICAL)
  assert.equal(canonicalGitHubRemote('git@github.com:windro-exe/hermes'), OFFICIAL_REPO_CANONICAL)
  assert.equal(canonicalGitHubRemote('ssh://git@github.com/windro-exe/hermes.git'), OFFICIAL_REPO_CANONICAL)
  assert.equal(canonicalGitHubRemote('https://github.com/windro-exe/hermes.git'), OFFICIAL_REPO_CANONICAL)
  // Case-insensitive: an uppercased owner still canonicalizes to the same repo.
  assert.equal(canonicalGitHubRemote('git@github.com:WINDRO-EXE/hermes.git'), OFFICIAL_REPO_CANONICAL)
  // Trailing slashes are stripped.
  assert.equal(canonicalGitHubRemote('https://github.com/windro-exe/hermes/'), OFFICIAL_REPO_CANONICAL)
})

test('canonicalGitHubRemote does not map upstream onto this fork', () => {
  // FORK guard: upstream must canonicalize to something else entirely, or an
  // upstream-pointed install would be treated as ours.
  assert.notEqual(canonicalGitHubRemote('git@github.com:NousResearch/hermes-agent.git'), OFFICIAL_REPO_CANONICAL)
})

test('canonicalGitHubRemote is empty for falsy input', () => {
  assert.equal(canonicalGitHubRemote(''), '')
  assert.equal(canonicalGitHubRemote(null), '')
  assert.equal(canonicalGitHubRemote(undefined), '')
})

test('isSshRemote detects scp-like and ssh:// forms only', () => {
  assert.equal(isSshRemote('git@github.com:windro-exe/hermes.git'), true)
  assert.equal(isSshRemote('ssh://git@github.com/windro-exe/hermes.git'), true)
  assert.equal(isSshRemote('https://github.com/windro-exe/hermes.git'), false)
  assert.equal(isSshRemote(''), false)
  assert.equal(isSshRemote(null), false)
})

test('isOfficialSshRemote is true only for our own repo over SSH', () => {
  assert.equal(isOfficialSshRemote('git@github.com:windro-exe/hermes.git'), true)
  assert.equal(isOfficialSshRemote('git@github.com:windro-exe/hermes'), true)
  assert.equal(isOfficialSshRemote('ssh://git@github.com/windro-exe/hermes.git'), true)
  // Case-insensitive owner/repo match.
  assert.equal(isOfficialSshRemote('git@github.com:WINDRO-EXE/hermes.git'), true)
})

test('isOfficialSshRemote does NOT match other repos, hosts, or HTTPS', () => {
  // Someone else's repo over SSH is their remote, not ours, so the
  // SSH-avoidance swap must not apply.
  assert.equal(isOfficialSshRemote('git@github.com:someuser/hermes.git'), false)
  // FORK: upstream is explicitly not ours.
  assert.equal(isOfficialSshRemote('git@github.com:NousResearch/hermes-agent.git'), false)
  // Same repo name on a different host is not our repo.
  assert.equal(isOfficialSshRemote('git@gitlab.com:windro-exe/hermes.git'), false)
  // HTTPS to our repo never prompts for SSH/FIDO2, so it keeps the normal fetch
  // path — must not be flagged as an SSH remote needing substitution.
  assert.equal(isOfficialSshRemote('https://github.com/windro-exe/hermes.git'), false)
  assert.equal(isOfficialSshRemote(''), false)
  assert.equal(isOfficialSshRemote(null), false)
})

test('OFFICIAL_REPO_HTTPS_URL canonicalizes to OFFICIAL_REPO_CANONICAL', () => {
  // Invariant: the URL we substitute in must be the same repo we detect.
  assert.equal(canonicalGitHubRemote(OFFICIAL_REPO_HTTPS_URL), OFFICIAL_REPO_CANONICAL)
})
