/**
 * A Map with an upper bound, evicting least-recently-used entries.
 *
 * Several stores cache per-tool-call state keyed by `toolCallId` — diff text,
 * disclosure state, dismissal state — and each one grew for the lifetime of the
 * session with no eviction. A long session with many tool calls therefore held
 * every diff it had ever rendered, and `$toolDiffs` holds full diff TEXT, so that
 * is unbounded string retention rather than a few stale booleans.
 *
 * This exists so the fix is one pattern instead of three, and so the next cache
 * added to this codebase has an obvious bounded thing to reach for. Deliberately
 * tiny: no TTL, no stats, no async. LRU via Map insertion order — `get` on a hit
 * re-inserts, so the oldest key is always the least recently used.
 *
 * Not a WeakMap: keys are string ids, not objects, so there is nothing for a
 * WeakMap to key on and nothing for the GC to collect on our behalf.
 */
export class BoundedMap<K, V> {
  private readonly limit: number
  private readonly store = new Map<K, V>()

  constructor(limit: number) {
    if (!Number.isInteger(limit) || limit < 1) {
      throw new Error(`BoundedMap limit must be a positive integer, got ${limit}`)
    }

    this.limit = limit
  }

  get size(): number {
    return this.store.size
  }

  clear(): void {
    this.store.clear()
  }

  delete(key: K): boolean {
    return this.store.delete(key)
  }

  get(key: K): undefined | V {
    if (!this.store.has(key)) {
      return undefined
    }

    // Re-insert to mark as most recently used.
    const value = this.store.get(key) as V
    this.store.delete(key)
    this.store.set(key, value)

    return value
  }

  has(key: K): boolean {
    return this.store.has(key)
  }

  keys(): IterableIterator<K> {
    return this.store.keys()
  }

  set(key: K, value: V): this {
    // Delete first so an existing key moves to the end rather than keeping its
    // original position — otherwise a hot key could be evicted before a cold one.
    this.store.delete(key)
    this.store.set(key, value)

    while (this.store.size > this.limit) {
      const oldest = this.store.keys().next()

      if (oldest.done) {
        break
      }

      this.store.delete(oldest.value)
    }

    return this
  }
}

/**
 * Drop the oldest keys from a plain object used as a keyed record.
 *
 * For stores that keep state in a `Record` inside a nanostores atom, where
 * swapping in a `BoundedMap` would change the store's public shape. Relies on
 * JS string-key insertion order, which is specified.
 */
export function boundRecord<V>(record: Record<string, V>, limit: number): Record<string, V> {
  const keys = Object.keys(record)

  if (keys.length <= limit) {
    return record
  }

  const trimmed: Record<string, V> = {}

  for (const key of keys.slice(keys.length - limit)) {
    trimmed[key] = record[key]
  }

  return trimmed
}
