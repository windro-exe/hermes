// throwaway: real-load measurement — DOM/heap, CPU profile while wheel-scrolling, LoAF
import { CDP, discoverTarget, sleep } from './lib/cdp.mjs'
import { writeFileSync } from 'node:fs'

const target = await discoverTarget({ match: 'index.html', port: 9222, timeoutMs: 60000 })
const cdp = await CDP.open(target.webSocketDebuggerUrl)

const EXPAND = process.argv.includes('--expand')

// ---------- 0. which session, how many messages ----------
const load0 = await cdp.eval(`JSON.stringify({
  hash: location.hash,
  turnPairs: document.querySelectorAll('[data-slot="aui_turn-pair"]').length,
  userMsgs: document.querySelectorAll('[data-slot="aui_user-message-root"]').length,
  asstMsgs: document.querySelectorAll('[data-slot="aui_assistant-message-root"]').length,
  hasShowEarlier: [...document.querySelectorAll('button')].some(b=>/show earlier/i.test(b.textContent||'')),
  dom: document.getElementsByTagName('*').length
})`)
console.log('LOAD0', load0)

if (EXPAND) {
  // click every "Show earlier messages" until gone — full transcript in DOM
  for (let i = 0; i < 12; i++) {
    const clicked = await cdp.eval(`(() => {
      const b = [...document.querySelectorAll('button')].find(b=>/show earlier/i.test(b.textContent||''))
      if (!b) return false
      b.click(); return true
    })()`)
    if (!clicked) break
    await sleep(900)
  }
  console.log('EXPANDED', await cdp.eval(`JSON.stringify({
    turnPairs: document.querySelectorAll('[data-slot="aui_turn-pair"]').length,
    dom: document.getElementsByTagName('*').length
  })`))
}

await sleep(1200)

// ---------- 1. DOM + heap ----------
await cdp.send('Performance.enable')
const m = (await cdp.send('Performance.getMetrics')).metrics
const metrics = Object.fromEntries(m.map(x => [x.name, x.value]))
const dom = await cdp.eval(`document.getElementsByTagName('*').length`)

console.log('METRICS', JSON.stringify({
  dom,
  JSHeapUsedSizeMB: +(metrics.JSHeapUsedSize / 1048576).toFixed(1),
  JSHeapTotalSizeMB: +(metrics.JSHeapTotalSize / 1048576).toFixed(1),
  Nodes: metrics.Nodes,
  LayoutObjects: metrics.LayoutObjects,
  JSEventListeners: metrics.JSEventListeners,
  RecalcStyleDuration: +(metrics.RecalcStyleDuration * 1000).toFixed(1),
  LayoutDuration: +(metrics.LayoutDuration * 1000).toFixed(1),
  RecalcStyleCount: metrics.RecalcStyleCount,
  LayoutCount: metrics.LayoutCount,
  ScriptDuration: +(metrics.ScriptDuration * 1000).toFixed(1)
}))

// ---------- 2. LoAF observer ----------
const obsSetup = await cdp.eval(`(() => {
  window.__LOAF__ = []; window.__LOAFKIND__ = null
  const push = (kind) => (list) => { for (const e of list.getEntries())
    window.__LOAF__.push({ kind, start: e.startTime, dur: e.duration,
      blocking: e.blockingDuration ?? Math.max(0, e.duration - 50),
      style: e.styleAndLayoutStart ? +(e.startTime + e.duration - e.styleAndLayoutStart).toFixed(1) : null,
      render: e.renderStart ? +(e.startTime + e.duration - e.renderStart).toFixed(1) : null }) }
  try { new PerformanceObserver(push('loaf')).observe({ type: 'long-animation-frame', buffered: true }); window.__LOAFKIND__='long-animation-frame' }
  catch (e) {
    try { new PerformanceObserver(push('longtask')).observe({ type: 'longtask', buffered: true }); window.__LOAFKIND__='longtask' }
    catch (e2) { window.__LOAFKIND__ = 'none: ' + e2.message }
  }
  return window.__LOAFKIND__
})()`)
console.log('LOAF_KIND', obsSetup)

// viewport rect for wheel targeting
const rect = JSON.parse(await cdp.eval(`(() => {
  const el = document.querySelector('[data-slot="aui_thread-viewport"]')
  const r = el.getBoundingClientRect()
  return JSON.stringify({ x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2),
    scrollHeight: el.scrollHeight, clientHeight: el.clientHeight, scrollTop: el.scrollTop })
})()`))
console.log('VIEWPORT', JSON.stringify(rect))

await cdp.eval(`window.__LOAF__.length = 0`)

// ---------- 3. CPU profile while wheel-scrolling ----------
await cdp.send('Profiler.enable')
await cdp.send('Profiler.setSamplingInterval', { interval: 100 })
await cdp.send('Profiler.start')

const t0 = Date.now()
let dir = -1 // start scrolling up (away from bottom)
let n = 0
while (Date.now() - t0 < 8000) {
  await cdp.send('Input.dispatchMouseEvent', {
    type: 'mouseWheel', x: rect.x, y: rect.y, deltaX: 0, deltaY: dir * 220,
    button: 'none', clickCount: 0, modifiers: 0
  })
  n++
  // reverse direction every ~1.6s so we don't pin at an edge
  if (n % 24 === 0) dir = -dir
  await sleep(16)
}
const { profile } = await cdp.send('Profiler.stop')
const wheelEvents = n
const scrollDurMs = Date.now() - t0

writeFileSync('scripts/perf/__probe-scroll.cpuprofile', JSON.stringify(profile))

const loaf = JSON.parse(await cdp.eval(`JSON.stringify(window.__LOAF__)`))
const after = JSON.parse(await cdp.eval(`(() => {
  const el = document.querySelector('[data-slot="aui_thread-viewport"]')
  return JSON.stringify({ scrollTop: el.scrollTop, scrollHeight: el.scrollHeight })
})()`))

console.log('SCROLL_META', JSON.stringify({ wheelEvents, scrollDurMs, after }))
console.log('LOAF_RAW', JSON.stringify(loaf))

await cdp.close?.()
