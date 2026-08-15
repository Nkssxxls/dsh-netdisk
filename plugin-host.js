// ============================================================================
// dsh-netdisk v1.1.0 — Cordis 动态插件 Host 半身
// ----------------------------------------------------------------------------
// 用途: 大模型搜索资源时自动发现并下载百度/夸克/迅雷网盘分享链接。
//       支持 auto(已登录网盘自动后台下载) 与 confirm(询问用户后下载) 两种模式,
//       登录优先: 百度 BaiduPCS-Go 高速通道 / 扫码登录 / 弹窗浏览器登录自动抓 Cookie。
//       本地路径可点击: /dsh-open 路由(点击链接在本地打开资源管理器/默认程序)。
//
// 激活方式(在本会话中):
//   1. 用 cordis_define 工具, kind:"new", idPrefix:"netdk"
//      把本文件的【全部内容】作为 code.host 参数值(内容为 "return {...}" 形式的函数体)。
//   2. 用返回的 pluginId/packageId 调 cordis_run, mode:"run"。
//   3. 更新版本: cordis_define(kind:"existing") + cordis_run(mode:"update")。
//
// 依赖(需先由 install.ps1 部署到 <工作区>/.dsh-netdisk/):
//   - netdisk_helper.py   网盘解析/下载引擎(Python 3.8+, 纯标准库)
//   - browser_login.js    弹窗浏览器登录 CDP 桥(Node 18+)
//   - bin/BaiduPCS-Go.exe 百度高速通道二进制(可选, 缺失时回退 Web 直链)
//
// 运行时要求: Host 提供 subprocess / systemPrompt / webServer(可选, 提供可点击链接) /
//             timer(可选) / sandboxPolicy(可选) 服务。
// ============================================================================
return {
  apply(ctx) {
    const subprocess = ctx.get('subprocess')
    const systemPrompt = ctx.get('systemPrompt')
    const timer = ctx.get('timer')
    const sandboxPolicy = ctx.get('sandboxPolicy')
    const webServer = ctx.get('webServer')
    if (subprocess === undefined || systemPrompt === undefined) return

    const fallbackRoot = (sandboxPolicy !== undefined && typeof sandboxPolicy.workspaceRoot === 'string')
      ? sandboxPolicy.workspaceRoot.replace(/\\/g, '/')
      : 'C:/workspace'

    function cwdFor(agent) {
      try {
        if (agent && agent.session && agent.session.header && typeof agent.session.header.cwd === 'string' && agent.session.header.cwd) {
          return agent.session.header.cwd.replace(/\\/g, '/')
        }
      } catch (e) { /* fall through */ }
      return fallbackRoot
    }
    function helperPathFor(agent) { return cwdFor(agent) + '/.dsh-netdisk/netdisk_helper.py' }
    function defaultDestFor(agent) { return cwdFor(agent) + '/downloads' }

    const state = { mode: 'auto', tasks: new Map(), seq: 0, credCache: null, allowedRoots: new Set(), openBase: '/dsh-open' }

    function addAllowed(p) {
      if (!p) return
      const norm = String(p).replace(/\\/g, '/').toLowerCase().replace(/\/$/, '')
      if (norm) state.allowedRoots.add(norm)
    }
    addAllowed(fallbackRoot)

    // ---------- 本地路径打开路由(点击链接 → 资源管理器/默认程序) ----------
    function openUrlFor(p) {
      return state.openBase + '?path=' + encodeURIComponent(String(p || '').replace(/\\/g, '/'))
    }
    function isAllowedPath(p) {
      const norm = String(p || '').replace(/\\/g, '/').toLowerCase().replace(/\/$/, '')
      if (!norm) return false
      for (const root of state.allowedRoots) {
        if (norm === root || norm.startsWith(root + '/')) return true
      }
      return false
    }
    function escapeHtml(s) {
      return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
    }
    function openHandler(req, res) {
      try {
        const q = String(req.url || '').split('?')[1] || ''
        let path = ''
        let name = ''
        for (const part of q.split('&')) {
          const eq = part.indexOf('=')
          if (eq < 0) continue
          const k = part.slice(0, eq)
          const v = part.slice(eq + 1)
          if (k === 'path') { try { path = decodeURIComponent(v) } catch (e) { path = v } }
          if (k === 'name') { try { name = decodeURIComponent(v) } catch (e) { name = v } }
        }
        res.setHeader('Content-Type', 'text/html; charset=utf-8')
        if (!path) { res.writeHead(400); res.end('<html><body style="font-family:Segoe UI,sans-serif;padding:24px"><h3>缺少 path 参数</h3></body></html>'); return }
        if (!isAllowedPath(path)) { res.writeHead(403); res.end('<html><body style="font-family:Segoe UI,sans-serif;padding:24px"><h3>路径不在当前会话允许范围内, 已拒绝</h3><p>' + escapeHtml(path) + '</p></body></html>'); return }
        const label = name || path
        Promise.resolve().then(async () => {
          let exe = 'C:\\Windows\\explorer.exe'
          try { exe = await subprocess.resolveExecutable('explorer.exe') || exe } catch (e) {}
          try {
            subprocess.spawn({ argv: [exe, path], cwd: cwdFor(undefined), stdio: { stdin: 'ignore', stdout: 'ignore', stderr: 'ignore' }, graceMs: 3000 })
          } catch (e) {}
        })
        res.writeHead(200)
        res.end('<html><body style="font-family:Segoe UI,sans-serif;padding:24px"><h3>&#9989; 已在本地打开</h3><p style="word-break:break-all">' + escapeHtml(label) + '</p><p>已调起资源管理器(文件夹)或默认程序(文件), 可关闭此页。</p></body></html>')
      } catch (e) {
        try { res.writeHead(500, { 'Content-Type': 'text/plain; charset=utf-8' }) } catch (e2) {}
        res.end('open error: ' + String((e && e.message) || e))
      }
    }
    if (webServer !== undefined) {
      try {
        const dispose = webServer.register({ kind: 'exact', path: '/dsh-open', handler: openHandler })
        ctx.effect(() => dispose)
        state.openBase = '/dsh-open'
      } catch (e) {
        const dispose = webServer.register({ kind: 'exact', path: '/netdisk-open', handler: openHandler })
        ctx.effect(() => dispose)
        state.openBase = '/netdisk-open'
      }
    }

    // ---------- python helper 执行 ----------
    async function resolvePython() {
      try { return await subprocess.resolveExecutable('python') } catch (e) { return undefined }
    }

    function collect(handle) {
      const out = handle.collected && handle.collected.stdout ? handle.collected.stdout.readFrom(0) : { text: '' }
      const err = handle.collected && handle.collected.stderr ? handle.collected.stderr.readFrom(0) : { text: '' }
      return { out: out.text || '', err: err.text || '' }
    }

    async function runHelper(action, payload, timeoutMs, agent) {
      const python = await resolvePython()
      if (python === undefined) return { ok: false, error: '未找到 python 可执行文件(需要 Python 3.8+ 且在 PATH 中)' }
      const helperPath = helperPathFor(agent)
      const cwd = cwdFor(agent)
      const handle = subprocess.spawn({
        argv: [python, helperPath, action, JSON.stringify(payload)],
        cwd: cwd,
        stdio: {
          stdin: 'ignore',
          stdout: { maxBytes: 4 * 1024 * 1024 },
          stderr: { maxBytes: 256 * 1024 },
        },
        graceMs: 5000,
      })
      let timedOut = false
      let disposeTimer
      const timeoutPromise = new Promise((resolve) => {
        if (timer !== undefined) {
          disposeTimer = timer.timeout(() => { timedOut = true; handle.terminate(); resolve() }, timeoutMs)
        } else {
          resolve()
        }
      })
      try { await Promise.race([handle.done, timeoutPromise]) } catch (e) { /* spawn 级失败 */ }
      if (disposeTimer) { try { disposeTimer() } catch (e) {} }
      const c = collect(handle)
      if (timedOut) return { ok: false, error: '执行超时(' + Math.round(timeoutMs / 1000) + 's)已终止。stderr 尾部: ' + (c.err || '').slice(-600) }
      try { return JSON.parse(c.out) } catch (e) {
        return { ok: false, error: 'helper 输出解析失败: ' + ((c.out || c.err || '').slice(-800)) }
      }
    }

    async function getCredentials(agent) {
      if (state.credCache === null) {
        const r = await runHelper('whoami', {}, 20000, agent)
        state.credCache = (r && r.ok && r.credentials) ? r.credentials : { baidu: { configured: false }, quark: { configured: false }, xunlei: { configured: false } }
      }
      return state.credCache
    }

    function startBackgroundTask(action, payload, label, onDone, agent) {
      const id = 'nd' + (++state.seq)
      const job = { id: id, action: action, label: label || '', status: 'running', startedAt: Date.now(), result: null, handle: undefined, stdoutText: '', stderrText: '' }
      state.tasks.set(id, job)
      const helperPath = helperPathFor(agent)
      const cwd = cwdFor(agent)
      const settle = (status, result) => {
        job.status = status
        job.result = result
        if (job.handle) {
          const c = collect(job.handle)
          job.stdoutText = (c.out || '').slice(-4000)
          job.stderrText = (c.err || '').slice(-2000)
        }
        if (onDone) { try { onDone(status, result) } catch (e) {} }
      }
      resolvePython().then((python) => {
        if (python === undefined) { settle('failed', { ok: false, error: '未找到 python' }); return }
        let handle
        try {
          handle = subprocess.spawn({
            argv: [python, helperPath, action, JSON.stringify(payload)],
            cwd: cwd,
            stdio: { stdin: 'ignore', stdout: { maxBytes: 4 * 1024 * 1024 }, stderr: { maxBytes: 256 * 1024 } },
            graceMs: 5000,
          })
        } catch (e) { settle('failed', { ok: false, error: String(e && e.message ? e.message : e) }); return }
        job.handle = handle
        handle.done.then(() => {
          const c = collect(handle)
          job.stdoutText = (c.out || '').slice(-4000)
          job.stderrText = (c.err || '').slice(-2000)
          try {
            const parsed = JSON.parse(c.out)
            job.result = parsed
            job.status = parsed && parsed.ok ? 'done' : 'failed'
          } catch (e) {
            job.result = { ok: false, error: '输出解析失败: ' + ((c.out || c.err || '').slice(-500)) }
            job.status = 'failed'
          }
          if (onDone) { try { onDone(job.status, job.result) } catch (e) {} }
        }).catch((e) => settle('failed', { ok: false, error: String(e && e.message ? e.message : e) }))
      }).catch((e) => settle('failed', { ok: false, error: String(e && e.message ? e.message : e) }))
      return job
    }

    const jsonRender = (_args, value) => [{ type: 'text', text: JSON.stringify(value, null, 2) }]

    function withOpenUrls(result) {
      if (!result || typeof result !== 'object') return result
      if (result.dest && typeof result.dest === 'string') {
        addAllowed(result.dest)
        result.open_url = openUrlFor(result.dest)
        result.open_url_label = '打开下载文件夹'
      }
      const dls = result.downloaded
      if (Array.isArray(dls)) {
        for (const d of dls) {
          if (d && typeof d === 'object' && d.path && typeof d.path === 'string' && d.size > 0) {
            addAllowed(d.path)
            d.open_url = openUrlFor(d.path)
          }
        }
      }
      return result
    }

    harness.registerTool(ctx, harness.defineTool({
      name: 'netdisk_login',
      description: '保存某个网盘的登录态 Cookie(用于规避匿名限速/风控)。用户需先在浏览器登录对应网盘, F12 → Network → 任选一个请求 → 复制完整 Cookie 值。夸克: 登录 pan.quark.cn 后复制; 百度: 登录 pan.baidu.com 后复制(需包含 BDUSS 与 STOKEN); 迅雷: 登录 pan.xunlei.com 后复制。若用户嫌复制麻烦: 百度用 netdisk_login_qr 扫码或 netdisk_login_browser 弹窗登录, 夸克/迅雷用 netdisk_login_browser。',
      parameters: {
        provider: { type: 'string', required: true, enum: ['baidu', 'quark', 'xunlei'], description: '要登录的网盘' },
        cookie: { type: 'string', required: true, description: '浏览器中复制的完整 Cookie 字符串' },
      },
      output: { schema: { type: 'json' }, render: jsonRender },
      async execute(args, exec) {
        addAllowed(cwdFor(exec && exec.agent))
        const r = await runHelper('login', { provider: args.provider, cookie: args.cookie }, 20000, exec && exec.agent)
        state.credCache = null
        return r
      },
    }))

    harness.registerTool(ctx, harness.defineTool({
      name: 'netdisk_login_qr',
      description: '百度网盘扫码登录: 生成登录二维码, 用户用手机百度 APP 扫码确认后自动保存登录态(无需复制 Cookie)。返回二维码图片链接与后台等待任务 id; 用户扫码后调用 netdisk_status 查看登录结果。注意: 扫码登录态可被 BaiduPCS-Go 识别, 但 pan API 可能触发 9019 风控; 若下载报 9019, 改用 netdisk_login_browser 弹窗登录获取完整浏览器 Cookie。',
      parameters: {
        provider: { type: 'string', enum: ['baidu'], description: '要扫码登录的网盘, 当前支持 baidu' },
      },
      output: { schema: { type: 'json' }, render: jsonRender },
      async execute(args, exec) {
        addAllowed(cwdFor(exec && exec.agent))
        const r = await runHelper('login_qr_start', { provider: (args.provider || 'baidu') }, 30000, exec && exec.agent)
        if (!r || !r.ok) return r
        const job = startBackgroundTask('login_qr_wait', { sign: r.sign, timeout: 240 }, '百度扫码登录等待', (status, result) => {
          if (status === 'done') state.credCache = null
        }, exec && exec.agent)
        return { ok: true, qr_image_url: r.qr_image_url, expire_seconds: r.expire_seconds, wait_job_id: job.id,
                 hint: '请把 qr_image_url 作为图片链接告知用户: 打开链接, 用手机百度 APP 扫码并在手机上确认登录。完成后调用 netdisk_status 查看登录结果。' }
      },
    }))

    harness.registerTool(ctx, harness.defineTool({
      name: 'netdisk_login_browser',
      description: '百度/夸克/迅雷网盘浏览器登录: 在用户桌面弹出独立的 Edge 浏览器窗口打开网盘登录页(百度 pan.baidu.com / 夸克 pan.quark.cn / 迅雷 pan.xunlei.com), 用户在窗口内登录(扫码/验证码均可), 插件自动检测登录成功并抓取保存完整 Cookie, 全程无需复制。百度的完整浏览器 Cookie 可解除 API 风控(9019)。任务在后台等待(最多 10 分钟), 用 netdisk_status 查看结果。',
      parameters: {
        provider: { type: 'string', required: true, enum: ['baidu', 'quark', 'xunlei'], description: '要登录的网盘' },
        timeout: { type: 'number', description: '等待登录的超时分钟数, 默认 10' },
      },
      output: { schema: { type: 'json' }, render: jsonRender },
      async execute(args, exec) {
        addAllowed(cwdFor(exec && exec.agent))
        const job = startBackgroundTask('login_browser', { provider: args.provider, timeout: args.timeout || 10 }, '浏览器登录 ' + args.provider + '(等待用户在弹出窗口中登录)', (status, result) => {
          if (status === 'done') state.credCache = null
        }, exec && exec.agent)
        const names = { baidu: '百度(pan.baidu.com)', quark: '夸克(pan.quark.cn)', xunlei: '迅雷(pan.xunlei.com)' }
        return { ok: true, job_id: job.id,
                 hint: '浏览器窗口已弹出(独立 Edge 实例): 请用户在其中登录 ' + (names[args.provider] || args.provider) + ', 登录成功后插件会自动抓取保存 Cookie。完成后调用 netdisk_status 查看结果。' }
      },
    }))

    harness.registerTool(ctx, harness.defineTool({
      name: 'netdisk_probe',
      description: '解析百度网盘/夸克网盘/迅雷网盘分享链接并列出其中文件(名称、大小、目录结构)。下载前用它确认内容; 也可用于验证提取码是否正确。若已保存过登录态, 自动使用登录态。',
      parameters: {
        url: { type: 'string', required: true, description: '分享链接, 如 https://pan.baidu.com/s/1xxx?pwd=abcd 、 https://pan.quark.cn/s/xxxx 、 https://pan.xunlei.com/s/VNxxx?pwd=abcd' },
        passcode: { type: 'string', description: '提取码; URL 中已带 ?pwd= 时可省略' },
        cookie: { type: 'string', description: '可选, 临时覆盖登录 Cookie; 通常省略(自动用已保存的登录态)' },
        timeout: { type: 'number', description: '超时秒数, 默认 120' },
      },
      output: { schema: { type: 'json' }, render: jsonRender },
      async execute(args, exec) {
        addAllowed(cwdFor(exec && exec.agent))
        return await runHelper('probe', { url: args.url, passcode: args.passcode || '', cookie: args.cookie || '', recursive: true, timeout: args.timeout || 120 }, ((args.timeout || 120) * 1000) + 20000, exec && exec.agent)
      },
    }))

    harness.registerTool(ctx, harness.defineTool({
      name: 'netdisk_download',
      description: '下载百度/夸克/迅雷网盘分享链接中的文件到本地目录。自动使用已保存的登录态(百度优先 BaiduPCS-Go, 失败回退 xpan 直链(需完整浏览器 Cookie); 夸克必需登录 Cookie)。默认同步等待完成; 传 background=true 时转为后台任务并立即返回任务 id, 配合 netdisk_status 查询进度。结果中的 open_url 是可点击打开本地文件夹/文件的链接。',
      parameters: {
        url: { type: 'string', required: true, description: '网盘分享链接' },
        passcode: { type: 'string', description: '提取码; URL 中已带 ?pwd= 时可省略' },
        cookie: { type: 'string', description: '可选, 临时覆盖登录 Cookie; 通常省略(自动用已保存的登录态)' },
        dest: { type: 'string', description: '保存目录, 默认 <工作区>/downloads' },
        filter: { type: 'string', description: '文件名过滤(子串或 glob), 如 "*.zip" 或 "教程"; 留空下载全部文件(受 max_files 限制)' },
        max_files: { type: 'number', description: '最多下载文件数, 默认 10' },
        background: { type: 'boolean', description: 'true 时后台下载并立即返回任务 id; 默认 false 同步等待' },
        timeout: { type: 'number', description: '同步模式的超时秒数, 默认 600' },
      },
      output: { schema: { type: 'json' }, render: jsonRender },
      async execute(args, exec) {
        addAllowed(cwdFor(exec && exec.agent))
        const dest = args.dest || defaultDestFor(exec && exec.agent)
        addAllowed(dest)
        const payload = { url: args.url, passcode: args.passcode || '', cookie: args.cookie || '', dest: dest, filter: args.filter || '', max_files: args.max_files || 10 }
        if (args.background === true) {
          const job = startBackgroundTask('download', payload, '下载 ' + args.url, undefined, exec && exec.agent)
          return { ok: true, background: true, job_id: job.id, dest: dest, open_url: openUrlFor(dest), open_url_label: '打开下载文件夹', hint: '用 netdisk_status 查询任务进度' }
        }
        const r = await runHelper('download', payload, Math.max(60, args.timeout || 600) * 1000, exec && exec.agent)
        return withOpenUrls(r)
      },
    }))

    harness.registerTool(ctx, harness.defineTool({
      name: 'netdisk_status',
      description: '查看网盘后台任务状态(running/done/failed, 含下载、扫码登录与浏览器登录任务)、各网盘登录态(已登录/未登录)与当前模式(auto/confirm)。任务的 open_url 字段是可点击打开本地文件夹/文件的链接。',
      parameters: {},
      output: { schema: { type: 'json' }, render: jsonRender },
      async execute(_args, exec) {
        addAllowed(cwdFor(exec && exec.agent))
        const creds = await getCredentials(exec && exec.agent)
        const list = []
        for (const job of state.tasks.values()) {
          const entry = { job_id: job.id, action: job.action, label: job.label, status: job.status, started_at: job.startedAt, result: job.result, stderr_tail: (job.stderrText || '').slice(-1500) }
          if (job.result && typeof job.result === 'object' && job.result.dest) {
            entry.open_url = openUrlFor(job.result.dest)
            entry.open_url_label = '打开下载文件夹'
          }
          list.push(entry)
        }
        const dd = defaultDestFor(exec && exec.agent)
        addAllowed(dd)
        return {
          mode: state.mode,
          login_status: { baidu: creds.baidu && creds.baidu.configured ? '已登录' : '未登录', quark: creds.quark && creds.quark.configured ? '已登录' : '未登录', xunlei: creds.xunlei && creds.xunlei.configured ? '已登录' : '未登录' },
          running: list.filter((j) => j.status === 'running').length,
          tasks: list,
          downloads_open_url: openUrlFor(dd),
          downloads_open_url_label: '打开下载文件夹',
          login_guide: '未登录的网盘: netdisk_login_browser 弹出浏览器窗口登录自动抓取 Cookie(推荐); 百度也可 netdisk_login_qr 扫码。',
          tip: '后台任务在插件停止或更新时会被终止',
        }
      },
    }))

    harness.registerTool(ctx, harness.defineTool({
      name: 'netdisk_mode',
      description: '查询或切换网盘自动下载模式: auto(默认, 搜索结果中已登录网盘的分享链接自动转为后台下载) / confirm(发现链接时提示先询问用户确认)。',
      parameters: {
        mode: { type: 'string', enum: ['auto', 'confirm'], description: '要切换到的模式; 省略则只查询当前模式' },
      },
      output: { schema: { type: 'json' }, render: jsonRender },
      async execute(args, _exec) {
        if (args.mode === 'auto' || args.mode === 'confirm') state.mode = args.mode
        return {
          mode: state.mode,
          explain: state.mode === 'auto'
            ? 'auto: web_search 结果中出现的网盘分享链接, 已登录的自动转为后台下载(每链接最多 5 个文件); 未登录的会提示引导用户登录'
            : 'confirm: 发现网盘分享链接时, 结果文本会提示你先用 ask_user_question 询问用户确认',
        }
      },
    }))

    const LINK_RE = /https?:\/\/pan\.(?:baidu\.com\/s\/1[A-Za-z0-9_-]{5,30}|quark\.cn\/s\/[A-Za-z0-9_-]{6,60}|xunlei\.com\/s\/VN[A-Za-z0-9_-]{8,80})(?:\?pwd=([A-Za-z0-9]{4,6}))?/g

    function providerOf(url) {
      if (url.indexOf('pan.baidu.com') >= 0) return 'baidu'
      if (url.indexOf('pan.quark.cn') >= 0) return 'quark'
      return 'xunlei'
    }

    function extractLinks(text) {
      const seen = new Set()
      const links = []
      let m
      while ((m = LINK_RE.exec(text)) !== null) {
        const raw = m[0]
        if (!seen.has(raw)) {
          seen.add(raw)
          links.push({ url: raw, passcode: m[1] || '' })
        }
        if (links.length >= 20) break
      }
      return links
    }

    ctx.on('tools/post-execute', async (exec, result, next) => {
      const decision = await next()
      try {
        if (exec === undefined || exec.name !== 'web_search') return decision
        if (result === undefined || result.isError) return decision
        const base = (decision && decision.kind === 'accept' && Array.isArray(decision.content)) ? decision.content : result.content
        if (!Array.isArray(base)) return decision
        const text = base.map((b) => (b && b.type === 'text' ? b.text : '')).join('\n')
        const links = extractLinks(text)
        if (links.length === 0) return decision
        const agent = exec.agent
        const creds = await getCredentials(agent)
        const loginLinks = links.filter((l) => {
          const c = creds[providerOf(l.url)]
          return c && c.configured
        })
        const anonLinks = links.filter((l) => !loginLinks.includes(l))
        const dest = defaultDestFor(agent)
        addAllowed(dest)
        if (state.mode === 'auto') {
          const started = []
          for (const link of loginLinks.slice(0, 3)) {
            const job = startBackgroundTask('download', { url: link.url, passcode: link.passcode || '', dest: dest, filter: '', max_files: 5 }, '自动下载 ' + link.url, undefined, agent)
            started.push({ job_id: job.id, url: link.url })
          }
          const parts = []
          if (started.length > 0) {
            parts.push('【网盘自动下载】netdisk 插件(模式 auto): 已自动启动 ' + started.length + ' 个登录态后台下载任务(每链接最多 5 个文件, 保存到 ' + dest + '):\n' + started.map((s) => ' - 任务 ' + s.job_id + ' : ' + s.url).join('\n') + '\n请在结束本轮回复前调用 netdisk_status 查看进度, 并在回复中向用户报告下载结果。')
          }
          if (anonLinks.length > 0) {
            parts.push('【网盘登录引导】发现 ' + anonLinks.length + ' 个链接所在网盘尚未登录, 未自动下载(避免匿名限速/风控):\n' + anonLinks.slice(0, 5).map((l) => ' - ' + l.url).join('\n') + '\n请在回复中询问用户是否现在登录(推荐): netdisk_login_browser 弹窗登录(百度/夸克/迅雷均可), 百度也可 netdisk_login_qr 扫码。')
          }
          if (parts.length === 0) return decision
          return { kind: 'accept', content: base.concat([{ type: 'text', text: '\n\n' + parts.join('\n\n') }]), additionalContexts: decision && decision.additionalContexts }
        }
        const banner = { type: 'text', text: '\n\n【网盘下载确认】netdisk 插件(模式 confirm) 从搜索结果中发现 ' + links.length + ' 个网盘分享链接:\n' + links.map((l) => ' - ' + l.url + (l.passcode ? ' (提取码 ' + l.passcode + ')' : '')).join('\n') + '\n请先用 ask_user_question 询问用户是否需要下载这些资源; 用户同意后再调用 netdisk_download 下载(建议 background=true)。未登录的网盘需先引导用户登录。' }
        return { kind: 'accept', content: base.concat([banner]), additionalContexts: decision && decision.additionalContexts }
      } catch (e) {
        return decision
      }
    })

    systemPrompt.section({
      name: 'tool:netdisk',
      order: 118,
      text: '# 网盘资源下载(netdisk 插件)\n\n本环境提供七个网盘工具: netdisk_probe(解析分享链接列出文件)、netdisk_download(下载)、netdisk_status(查看后台任务与登录状态)、netdisk_mode(切换模式)、netdisk_login(保存登录 Cookie)、netdisk_login_qr(百度扫码登录)、netdisk_login_browser(弹窗浏览器登录自动抓取 Cookie)。\n\n**本地路径可点击链接(重要)**: 插件注册了 /dsh-open 路由。回复中凡涉及本地文件或文件夹路径, 一律以 markdown 链接给出: [打开文件夹](</dsh-open?path=<URL编码后的路径>>) 或 [打开文件](</dsh-open?path=<URL编码后的文件路径>>)。工具结果里的 open_url 字段也是这种链接, 直接作为 markdown 链接地址使用。用户点击后浏览器请求 /dsh-open, 插件会在本地调起资源管理器(文件夹)或默认程序(文件)。\n\n**登录优先协议(重要)**: 插件支持网盘登录态下载, 可避免匿名的限速与风控。用户要下载某网盘的资源前, 先检查该网盘是否已登录(netdisk_status 的 login_status 字段):\n- 已登录: 直接下载。百度优先 BaiduPCS-Go 高速通道, 失败自动回退 xpan 直链(百度 API 可能报 9019 风控, 此时需用户用 netdisk_login_browser 弹窗登录百度获取完整浏览器 Cookie); 夸克必须登录才能下载; 迅雷登录后可尝试。\n- 未登录: 引导用户登录后再下载。推荐: netdisk_login_browser(用户桌面弹出浏览器窗口, 在其中登录, 插件自动抓取 Cookie, 百度/夸克/迅雷均支持); 百度也可 netdisk_login_qr 扫码。\n\n搜索资源时的自动处理协议:\n- 模式 auto(默认): web_search 结果中出现的网盘分享链接, 已登录网盘的会自动转为后台下载任务并注入任务提示; 未登录网盘的会注入登录引导提示。看到提示后, 应在结束本轮回复前调用 netdisk_status 查看进度/登录状态并把结果告知用户。\n- 模式 confirm: 结果文本会提示发现的网盘链接, 你必须先用 ask_user_question 询问用户是否下载; 只有用户同意后才调用 netdisk_download(建议 background=true, 随后用 netdisk_status 跟进)。\n- 手动下载: 用户直接给网盘链接要求下载时, 可先用 netdisk_probe 确认内容, 再用 netdisk_download 下载, 完成后向用户报告保存路径(用可点击链接形式)。\n\n已知限制:\n- 百度匿名下载限速且可能触发风控; 登录态下载若报 9019(need verify), 请引导用户弹窗浏览器登录(netdisk_login_browser)获取完整 Cookie 后重试。\n- 夸克匿名只能浏览文件列表, 下载必须登录 Cookie。\n- 迅雷网盘分享解析通常需要人机验证, 即使登录也可能受限, 遇到失败请如实告知用户。\n- 提取码优先取链接 URL 的 ?pwd= 参数; 没有时向用户询问。',
    })

    ctx.effect(() => () => {
      for (const job of state.tasks.values()) {
        if (job.handle) { try { job.handle.terminate() } catch (e) {} }
      }
    })
  },
}
