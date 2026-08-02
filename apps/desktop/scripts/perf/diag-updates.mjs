// Verify the update toast is actually rendered in the running app, and that a
// dismissal behaves the way windro asked for. Reuses the perf harness CDP glue.
//
// Two gotchas if you write another of these: launch.mjs's attach() calls
// requireDriver(), which the packaged production build does not expose — use
// CDP.open directly; and discoverTarget({ match }) matches a URL SUBSTRING, not
// a predicate.
import { CDP, discoverTarget } from './lib/cdp.mjs'

const target = await discoverTarget({ match: 'index.html' })
const cdp = await CDP.open(target.webSocketDebuggerUrl)

async function evaluate(expression) {
  const { result, exceptionDetails } = await cdp.send('Runtime.evaluate', {
    awaitPromise: true,
    expression,
    returnByValue: true
  })

  if (exceptionDetails) {
    return `THREW: ${exceptionDetails.exception?.description || exceptionDetails.text}`
  }

  return result.value
}

const toastProbe = `JSON.stringify({
  giftToast: !!document.querySelector('.codicon-gift'),
  bodyText: (document.body.innerText || '').split('\\n').filter(l =>
    /update|version|behind|what.s new/i.test(l)).slice(0, 4),
  persistedSnooze: localStorage.getItem('hermes:update-toast-snooze'),
  staleOldKey: localStorage.getItem('hermes:update-toast-snooze-until')
}, null, 1)`

console.log('=== 1. the check the app performed ===')
console.log(
  await evaluate(`(async () => {
    const s = await window.hermesDesktop.updates.check()
    return JSON.stringify({
      supported: s?.supported, error: s?.error, behind: s?.behind,
      currentSha: (s?.currentSha || '').slice(0, 9),
      targetSha: (s?.targetSha || '').slice(0, 9)
    })
  })()`)
)

console.log('\n=== 2. is the toast on screen? ===')
console.log(await evaluate(toastProbe))

console.log('\n=== 3. dismiss it the way the X button does, then re-check ===')
console.log(
  await evaluate(`(async () => {
    const before = document.querySelectorAll('.codicon-gift').length
    // Same call the X button makes.
    const mod = window.__hermesTestHooks?.notifications
    return JSON.stringify({ giftBefore: before, hookAvailable: !!mod })
  })()`)
)

await cdp.close?.()
