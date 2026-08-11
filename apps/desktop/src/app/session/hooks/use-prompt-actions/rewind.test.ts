import { describe, expect, it } from 'vitest'

import { truncateSubmitParams } from './rewind'

describe('truncateSubmitParams', () => {
  it('omits truncation fields when no ordinal is set', () => {
    expect(truncateSubmitParams(undefined)).toEqual({})
  })

  it('always confirms truncation, and adds the empty-edge flag only for ordinal 0', () => {
    // FORK: confirm_truncate is required by backends from 0.20.0 for ANY
    // truncation. Without it every restore/rewind/edit against one failed with
    // "truncate_before_user_ordinal requires confirm_truncate=true".
    expect(truncateSubmitParams(0)).toEqual({
      truncate_before_user_ordinal: 0,
      confirm_truncate: true,
      confirm_empty_truncate: true
    })
    expect(truncateSubmitParams(1)).toEqual({
      truncate_before_user_ordinal: 1,
      confirm_truncate: true
    })
  })

  it('sends no confirmation when there is nothing to truncate', () => {
    // The confirmation must never appear on an ordinary submit — that is exactly
    // what the backend guard exists to catch.
    expect(truncateSubmitParams(undefined)).not.toHaveProperty('confirm_truncate')
  })
})
