// Scenario registry. Add a scenario module here and it's automatically
// available to the runner, the default suite (tier 'ci'), and the baseline gate.

import coldStart from './cold-start.mjs'
import firstToken from './first-token.mjs'
import idleCost from './idle-cost.mjs'
import keystroke from './keystroke.mjs'
import multitab from './multitab.mjs'
import profileSwitch from './profile-switch.mjs'
import renderChurn from './render-churn.mjs'
// Fork-added: upstream's `transcript` scenario measures mount cost only, so
// nothing measured scroll smoothness — the thing that actually feels janky on a
// long session. See fork/changelog/entries/.
import scroll from './scroll.mjs'
import sessionSwitch from './session-switch.mjs'
import stream from './stream.mjs'
import streamHistory from './stream-history.mjs'
import submit from './submit.mjs'
import transcript from './transcript.mjs'

export const SCENARIOS = {
  [stream.name]: stream,
  [streamHistory.name]: streamHistory,
  [keystroke.name]: keystroke,
  [transcript.name]: transcript,
  [scroll.name]: scroll,
  [multitab.name]: multitab,
  [renderChurn.name]: renderChurn,
  [idleCost.name]: idleCost,
  [coldStart.name]: coldStart,
  [firstToken.name]: firstToken,
  [submit.name]: submit,
  [sessionSwitch.name]: sessionSwitch,
  [profileSwitch.name]: profileSwitch
}

/** Scenarios safe to run with no LLM credits / no live backend — the default suite. */
export const CI_SCENARIOS = Object.values(SCENARIOS)
  .filter(s => s.tier === 'ci')
  .map(s => s.name)
