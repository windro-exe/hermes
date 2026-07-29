// Transcript scroll smoothness. Fork-added scenario — upstream's `transcript`
// scenario measures MOUNT cost only, and nothing measured scrolling, which is
// the thing that actually feels janky on a long session.
//
// HOW IT DRIVES THE SCROLL, and what that does and doesn't cover:
//
// A rAF loop inside the page moves `scrollTop` one step per animation frame and
// dispatches a `wheel` event each step. That exercises everything the app itself
// does per scrolled frame — the library's wheel handler and scroll handler, React
// work, style/layout/paint of newly revealed content, sticky repositioning,
// content-visibility passes, and backdrop-filter re-sampling behind the composer.
//
// It deliberately does NOT measure the browser's own compositor-thread scrolling,
// because that is not where this app's jank comes from and because CDP's input
// APIs could not drive it here: `Input.dispatchMouseEvent` with type 'mouseWheel'
// does not move the container at all (an earlier version of this scenario
// reported a perfect 165fps because nothing had scrolled), and
// `Input.synthesizeScrollGesture` hung. The run fails loudly if the container
// did not actually move, so a silently-idle measurement cannot be reported as a
// good result again.
//
// Metrics (lower is better): frame p95/p99, counts of frames over 16.7ms and
// 33ms, longtask count and max. `scroll_frames_over_16` is the headline — on a
// 60Hz display every frame over ~16.7ms is a visibly dropped one.

import { SELECTORS, sleep } from '../lib/cdp.mjs'
import { frameHistogram, percentile } from '../lib/stats.mjs'

const V = JSON.stringify(SELECTORS.threadViewport)

const LONGTASKS = `
  (() => {
    window.__SCLT__ = { entries: [], stop: false }
    try {
      const po = new PerformanceObserver((list) => {
        if (window.__SCLT__.stop) return
        for (const e of list.getEntries()) {
          window.__SCLT__.entries.push({ duration: e.duration, startTime: e.startTime })
        }
      })
      po.observe({ entryTypes: ['longtask'] })
      window.__SCLT__.po = po
    } catch {}
    return 'armed'
  })()
`

const drive = (steps, pxPerStep) => `
  new Promise(resolve => {
    const el = document.querySelector(${V})
    if (!el) return resolve(JSON.stringify({ err: 'viewport not found' }))
    const start = el.scrollTop
    const times = []
    let last = performance.now()
    let n = 0
    const step = () => {
      const now = performance.now()
      times.push(now - last)
      last = now
      // Untrusted wheel events don't scroll on their own, but they DO run the
      // library's wheel listener — which is the code path being measured.
      el.dispatchEvent(new WheelEvent('wheel', { deltaY: -${pxPerStep}, bubbles: true, cancelable: true }))
      el.scrollTop = Math.max(0, el.scrollTop - ${pxPerStep})
      n += 1
      if (n < ${steps} && el.scrollTop > 0) return requestAnimationFrame(step)
      resolve(JSON.stringify({ times, start, end: el.scrollTop, moved: start - el.scrollTop }))
    }
    requestAnimationFrame(step)
  })
`

const COLLECT_LT = `
  (() => {
    window.__SCLT__.stop = true
    try { window.__SCLT__.po && window.__SCLT__.po.disconnect() } catch {}
    return JSON.stringify(window.__SCLT__.entries)
  })()
`

export default {
  name: 'scroll',
  tier: 'ci',
  description: 'Transcript scroll smoothness: per-frame cost of scrolling a long session.',
  async run(cdp, opts = {}) {
    const turns = Number(opts.turns ?? 200)
    const steps = Number(opts.steps ?? 240)
    const pxPerStep = Number(opts.pxPerStep ?? 40)
    const settleMs = Number(opts.settleMs ?? 2000)
    // Frames to discard at the head of the window (rAF warm-up).
    const warmupFrames = Number(opts.warmupFrames ?? 5)

    await cdp.send('Runtime.enable')

    // Mount the transcript BEFORE arming anything, so the measurement window
    // holds scroll work only, not the one-off mount cost.
    await cdp.eval(`window.__PERF_DRIVE__.loadTranscript(${turns})`)
    await sleep(settleMs)

    const mounted = Number(await cdp.eval('window.__PERF_DRIVE__.snapshotMsgs()'))

    if (mounted !== turns * 2) {
      throw new Error(`expected ${turns * 2} mounted messages, got ${mounted}`)
    }

    // Park at the bottom, where a real session sits, so the first upward step
    // also exercises the library's escape-from-autoscroll-lock path.
    await cdp.eval(`(() => {
      const el = document.querySelector(${V})
      if (el) el.scrollTop = el.scrollHeight
    })()`)
    await sleep(400)

    await cdp.eval(LONGTASKS)

    const result = JSON.parse(await cdp.eval(drive(steps, pxPerStep)))

    if (result.err) {
      throw new Error(result.err)
    }

    const longtasks = JSON.parse(await cdp.eval(COLLECT_LT))

    await cdp.eval('window.__PERF_DRIVE__.reset()')

    if (result.moved < 200) {
      throw new Error(
        `the transcript did not scroll (scrollTop ${result.start} -> ${result.end}). ` +
          'Without movement the frame numbers describe an idle page, not scrolling.'
      )
    }

    const frames = result.times.slice(warmupFrames)
    const ltDurations = longtasks.map(e => e.duration)
    const windowS = frames.reduce((a, b) => a + b, 0) / 1000

    return {
      metrics: {
        scroll_frame_p95_ms: Math.round(percentile(frames, 0.95) * 10) / 10,
        scroll_frame_p99_ms: Math.round(percentile(frames, 0.99) * 10) / 10,
        scroll_frames_over_16: frames.filter(f => f > 16.7).length,
        scroll_frames_over_33: frames.filter(f => f > 33).length,
        scroll_longtasks_n: longtasks.length,
        scroll_longtask_max_ms: Math.round((ltDurations.length ? Math.max(...ltDurations) : 0) * 10) / 10
      },
      detail: {
        turns,
        steps,
        pxPerStep,
        movedPx: result.moved,
        frames: frames.length,
        windowS: Math.round(windowS * 10) / 10,
        avgFps: windowS ? Math.round((frames.length / windowS) * 10) / 10 : 0,
        frameHistogram: frameHistogram(frames),
        frame_p50_ms: Math.round(percentile(frames, 0.5) * 10) / 10,
        frame_max_ms: Math.round(Math.max(...frames) * 10) / 10
      }
    }
  }
}
