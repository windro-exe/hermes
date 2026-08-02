// One-off diagnostic: ask the LIVE renderer what the update check actually
// returns and why the toast did or did not fire. Reuses the perf harness's CDP
// attach so this needs no new dependency.
import { CDP, discoverTarget } from './lib/cdp.mjs'

// Connect straight to the page target. Deliberately NOT launch.mjs's attach():
// that calls requireDriver(), which the packaged production app does not expose.
// match is a URL substring, not a predicate (see discoverTarget).
const target = await discoverTarget({ match: 'index.html' })
const cdp = await CDP.open(target.webSocketDebuggerUrl)

async function evaluate(expression) {
  const { result, exceptionDetails } = await cdp.send('Runtime.evaluate', {
    awaitPromise: true,
    expression,
    returnByValue: true
  })

  if (exceptionDetails) {
    return { error: exceptionDetails.text, detail: exceptionDetails.exception?.description }
  }

  return result.value
}

console.log('=== 1. is the updates bridge exposed to the renderer? ===')
console.log(
  await evaluate(`JSON.stringify({
    hermesDesktop: typeof window.hermesDesktop,
    updates: typeof window.hermesDesktop?.updates,
    check: typeof window.hermesDesktop?.updates?.check
  })`)
)

console.log('\n=== 2. what does the real check return right now? ===')
console.log(
  await evaluate(`(async () => {
    try {
      const s = await window.hermesDesktop.updates.check()
      return JSON.stringify({
        supported: s?.supported, error: s?.error, message: s?.message,
        behind: s?.behind, currentSha: (s?.currentSha||'').slice(0,9),
        targetSha: (s?.targetSha||'').slice(0,9), branch: s?.branch,
        currentBranch: s?.currentBranch, dirty: s?.dirty,
        hermesRoot: s?.hermesRoot
      }, null, 1)
    } catch (e) { return 'THREW: ' + (e?.message || String(e)) }
  })()`)
)

console.log('\n=== 3. persisted snooze state ===')
console.log(
  await evaluate(`JSON.stringify({
    newKey: localStorage.getItem('hermes:update-toast-snooze'),
    oldKey: localStorage.getItem('hermes:update-toast-snooze-until'),
    allUpdateKeys: Object.keys(localStorage).filter(k => /update/i.test(k))
  }, null, 1)`)
)

console.log('\n=== 4. is a toast currently in the DOM? ===')
console.log(
  await evaluate(`JSON.stringify({
    toastNodes: document.querySelectorAll('[data-slot*="toast"], [role="status"], [data-sonner-toast]').length,
    giftIcon: document.body.innerHTML.includes('codicon-gift'),
    bodyMentionsUpdate: /update/i.test(document.body.innerText || '')
  }, null, 1)`)
)

await cdp.close?.()
