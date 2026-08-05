// throwaway: find the long session row in the sidebar + confirm target url
import { CDP, discoverTarget } from './lib/cdp.mjs'

const target = await discoverTarget({ match: 'index.html', port: 9222, timeoutMs: 60000 })
console.log('TARGET', target.url)
const cdp = await CDP.open(target.webSocketDebuggerUrl)

console.log(
  await cdp.eval(`JSON.stringify({
    dom: document.getElementsByTagName('*').length,
    bodyHead: (document.body.innerText||'').slice(0,600),
    rowButtons: document.querySelectorAll('[data-slot="row-button"]').length,
    slots: [...new Set([...document.querySelectorAll('[data-slot]')].map(e=>e.getAttribute('data-slot')))].slice(0,60)
  }, null, 1)`)
)

await cdp.close?.()
