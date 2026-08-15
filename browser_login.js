// browser_login.js — 通过 CDP 控制 Edge 完成网盘浏览器登录并自动抓取 Cookie
// 用法: node browser_login.js <quark|xunlei> <port> <profileDir> [maxWaitMs]
// stdout: 单行 JSON {ok, provider, cookie, cookie_count, error}
const { spawn } = require('child_process')
const fs = require('fs')
const path = require('path')

const provider = process.argv[2]
const port = parseInt(process.argv[3], 10)
const profileDir = process.argv[4]
const maxWaitMs = parseInt(process.argv[5] || '600000', 10)
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'
const LOGIN_URLS = { quark: 'https://pan.quark.cn/', xunlei: 'https://pan.xunlei.com/', baidu: 'https://pan.baidu.com/' }
const KEY_COOKIES = {
  quark: ['__puus', '__pus', 'kxu', 'kps'],
  xunlei: ['PANPASSPORT', 'sessionid', 'loginpantoken'],
  baidu: ['PTOKEN', 'PANPSC'],
}
const POLL_MS = 4000

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

function fail(msg) {
  console.log(JSON.stringify({ ok: false, error: msg }))
  process.exit(1)
}

function cookieDomainOk(domain) {
  if (provider === 'quark') return /(^|\.)quark\.cn$/.test(domain)
  if (provider === 'xunlei') return /(^|\.)xunlei\.com$/.test(domain)
  if (provider === 'baidu') return /(^|\.)baidu\.com$/.test(domain)
  return false
}

function killTree(pid) {
  try { spawn('taskkill', ['/F', '/T', '/PID', String(pid)], { stdio: 'ignore' }) } catch (e) {}
}

async function main() {
  if (!LOGIN_URLS[provider]) fail('provider 必须是 quark 或 xunlei')
  fs.mkdirSync(profileDir, { recursive: true })
  const edge = spawn(EDGE, [
    '--remote-debugging-port=' + port,
    '--user-data-dir=' + profileDir,
    '--no-first-run',
    '--no-default-browser-check',
    '--no-sandbox',
    '--new-window',
    LOGIN_URLS[provider],
  ], { stdio: 'ignore', detached: true })

  // 等 CDP 就绪
  let wsUrl = null
  const cdpDeadline = Date.now() + 40000
  while (Date.now() < cdpDeadline) {
    await sleep(1000)
    try {
      const res = await fetch('http://127.0.0.1:' + port + '/json')
      const targets = await res.json()
      const page = targets.find((t) => t.type === 'page' && t.webSocketDebuggerUrl &&
        (t.url.includes('pan.quark.cn') || t.url.includes('pan.xunlei.com') || t.url.includes('pan.baidu.com') || t.url === 'about:blank'))
      if (page) { wsUrl = page.webSocketDebuggerUrl; break }
    } catch (e) { /* CDP 未就绪 */ }
  }
  if (!wsUrl) { killTree(edge.pid); fail('无法连接浏览器 CDP(Edge 启动失败?)') }

  const ws = new WebSocket(wsUrl)
  let idSeq = 0
  const pending = new Map()
  function cdp(method, params) {
    return new Promise((resolve, reject) => {
      const id = ++idSeq
      pending.set(id, { resolve, reject })
      ws.send(JSON.stringify({ id, method, params: params || {} }))
      setTimeout(() => { if (pending.has(id)) { pending.delete(id); reject(new Error('cdp timeout: ' + method)) } }, 15000)
    })
  }
  ws.onmessage = (ev) => {
    let msg
    try { msg = JSON.parse(typeof ev.data === 'string' ? ev.data : String(ev.data)) } catch (e) { return }
    if (msg.id && pending.has(msg.id)) {
      const p = pending.get(msg.id)
      pending.delete(msg.id)
      msg.error ? p.reject(new Error(msg.error.message)) : p.resolve(msg.result)
    }
  }
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = () => reject(new Error('websocket 连接失败')) })
  await cdp('Network.enable')

  async function getCookies() {
    const r = await cdp('Network.getAllCookies')
    return (r.cookies || []).filter((c) => cookieDomainOk(c.domain))
  }

  // 基线: 等待 25 秒让页面与页面自身 Cookie 稳定
  await sleep(25000)
  let baseline = 0
  try { baseline = (await getCookies()).length } catch (e) { baseline = 0 }
  process.stderr.write('[browser-login] 浏览器已打开 ' + LOGIN_URLS[provider] + ' , 请在窗口中登录(基线 cookie ' + baseline + ' 个)\n')

  const start = Date.now()
  while (Date.now() - start < maxWaitMs) {
    await sleep(POLL_MS)
    let cookies = []
    try { cookies = await getCookies() } catch (e) { continue }
    const names = cookies.map((c) => c.name)
    const hit = KEY_COOKIES[provider].some((k) => names.includes(k))
    if (hit && cookies.length >= baseline + 2) {
      const cookieStr = cookies.map((c) => c.name + '=' + c.value).join('; ')
      killTree(edge.pid)
      console.log(JSON.stringify({ ok: true, provider: provider, cookie: cookieStr, cookie_count: cookies.length, detected_by: 'key-cookie' }))
      process.exit(0)
    }
  }
  killTree(edge.pid)
  fail('等待登录超时(' + Math.round(maxWaitMs / 60000) + '分钟)')
}

main().catch((e) => fail(String((e && e.message) || e)))
