# dsh-netdisk v1.1.0

**DeepSeek Harness 网盘资源下载插件成品** —— 大模型搜索资源时，自动发现并下载
百度网盘 / 夸克网盘 / 迅雷网盘的分享链接。登录态下载，规避匿名限速与风控。

## 能力一览

| 能力 | 说明 |
|---|---|
| 7 个模型工具 | probe / download / status / mode / login / login_qr / login_browser |
| 搜索自动下载 | `web_search` 结果中的网盘链接自动转为后台下载（auto 模式）或提示确认（confirm 模式） |
| 登录优先 | 未登录网盘先引导登录，不碰匿名低速通道 |
| 三种登录方式 | 百度扫码 / 弹窗浏览器登录（自动抓 Cookie）/ 手动粘贴 Cookie |
| 百度高速通道 | 登录态走 BaiduPCS-Go 转存+多线程下载（实测 8–12 MB/s，匿名仅约 100 KB/s） |
| **可点击链接** | 回复中的本地路径均为 markdown 链接（`/dsh-open` 路由），点击即在本地打开资源管理器（文件夹）或默认程序（文件） |
| 后台任务 | 下载/登录均为后台任务，`netdisk_status` 查询进度 |

## 目录结构

```
dsh-netdisk-v1.0.0/
├── README.md                 本文档
├── install.ps1               一键部署脚本
├── plugin-host.js            Cordis 插件 Host 半身（cordis_define 的 code.host）
├── netdisk_helper.py         下载引擎（Python 3.8+，纯标准库，无第三方依赖）
├── browser_login.js          弹窗浏览器登录 CDP 桥（Node 18+）
├── credentials.example.json  凭证模板
└── bin/
    └── BaiduPCS-Go.exe       百度高速通道二进制（v4.0.1 windows-x64）
```

## 安装（3 步）

### 1. 环境要求

- Windows 10/11，PowerShell 5.1+
- Python 3.8+（PATH 中可执行 `python`）
- Node.js 18+（弹窗浏览器登录需要；内置 WebSocket/fetch，无需 npm 安装）
- Microsoft Edge（弹窗浏览器登录需要）

### 2. 部署到工作区

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Workspace "D:\你的工作区"
```

不传 `-Workspace` 则部署到当前目录。脚本会：
- 把 `netdisk_helper.py`、`browser_login.js`、`credentials.json` 写入 `<工作区>\.dsh-netdisk\`
- 部署 `BaiduPCS-Go.exe` 到 `<工作区>\.dsh-netdisk\bin\`（发布包已内置；缺失时自动从 GitHub 下载）

### 3. 在 DSH 会话中激活插件

用 `cordis_define` 工具新建插件：

- `plugin.kind` = `"new"`，`idPrefix` = `"netdk"`
- `code.host` = **`plugin-host.js` 的全部内容**（一段 `return {...}` 函数体）

然后用返回的 `pluginId` / `packageId` 调 `cordis_run`（`mode:"run"`）。
更新版本时用 `cordis_define(kind:"existing")` 追加 Package，`cordis_run(mode:"update")`。

> 说明：动态 Cordis 插件是会话级临时扩展（进程重启即失效）。要每次会话自动加载，
> 可把 `plugin-host.js` 的内容整理为一个 agent preset 的 plugin row（见下文「进阶」）。

## 使用

### 登录（每种网盘只需一次）

| 方式 | 命令（对模型说） | 说明 |
|---|---|---|
| 弹窗浏览器登录（推荐） | 「登录百度 / 登录夸克 / 登录迅雷」 | 桌面弹出独立 Edge 窗口，登录后自动抓取 Cookie |
| 百度扫码 | 「百度扫码登录」 | 返回二维码图片链接，手机百度 APP 扫码确认 |
| 粘贴 Cookie | 「登录百度，Cookie 是 ...」 | 兜底方式（F12 → Network → 复制 Cookie） |

### 下载

- 「下载 https://pan.quark.cn/s/xxxx」——先 probe 列文件再下载
- 「帮我找 XX 资源并下载」——搜索 → 自动发现链接 → 自动下载（auto 模式）
- 「切到 confirm 模式」——搜索发现链接后先询问再下载

### 工具速查

| 工具 | 参数 | 说明 |
|---|---|---|
| `netdisk_probe` | url, passcode?, cookie?, timeout? | 列出分享内容（含目录递归、提取码校验） |
| `netdisk_download` | url, passcode?, cookie?, dest?, filter?, max_files?, background?, timeout? | 下载到本地（默认 `<工作区>/downloads`） |
| `netdisk_status` | — | 后台任务 + 登录态 + 模式 |
| `netdisk_mode` | mode? | 切换 auto / confirm |
| `netdisk_login` | provider, cookie | 保存登录 Cookie |
| `netdisk_login_qr` | provider(baidu) | 百度扫码登录 |
| `netdisk_login_browser` | provider, timeout? | 弹窗浏览器登录自动抓 Cookie |

## 架构

```
模型工具(netdisk_*) ──► Cordis 插件(Host) ──spawn──► netdisk_helper.py ──HTTP──► 三家网盘
                            │  ▲                          │
                            │  └── tools/post-execute     ├─ BaiduPCS-Go.exe(百度高速)
                            │       钩子: web_search 结果  ├─ browser_login.js(CDP 登录)
                            │       提取链接→自动下载       └─ credentials.json(登录凭证)
                            └── systemPrompt section(登录优先协议 + 自动处理协议)
```

- **插件（plugin-host.js）**：注册 7 个工具；挂 `tools/post-execute` 瀑布监听 `web_search`
  结果，按模式（auto/confirm）自动下载或提示；注入中文提示词段。
- **下载引擎（netdisk_helper.py）**：纯标准库实现三家网盘分享解析与下载；
  凭证管理（credentials.json）；百度扫码登录（passport 二维码协议 + channel/unicast 轮询）；
  弹窗登录调度（调 browser_login.js）。
- **浏览器桥（browser_login.js）**：CDP 启动独立 Edge → 用户登录 → Network.getAllCookies
  轮询检测登录标志 Cookie → 抓取全部 Cookie。
- **百度高速通道**：BaiduPCS-Go 二进制 `login -cookies` + `transfer --download`（转存+多线程下载），
  失败自动回退 xpan `filemetas` 直链 / `sharedownload`。

## 已知限制（模型会自动如实告知用户）

- 百度匿名约 100KB/s 且易风控；登录后高速通道实测 8–12MB/s。
- 百度扫码登录态对部分 pan API 会触发 9019（need verify）——改用弹窗浏览器登录即可解除。
- 夸克匿名只能浏览文件列表，下载必须登录。
- 迅雷分享解析常需人机验证，即使登录也可能受限。
- 提取码优先取 URL 的 `?pwd=` 参数。

## 故障排查

| 症状 | 处理 |
|---|---|
| `helper 输出解析失败: can't open file ...netdisk_helper.py` | 插件取错工作目录（多工作区场景）：确认当前会话 cwd 下存在 `.dsh-netdisk/`，或重跑 install.ps1 |
| 百度下载 `9019 need verify` | 弹窗浏览器登录百度（完整 Cookie 解除风控） |
| 百度 `转存失败: 请确认...STOKEN` | BaiduPCS-Go 对旧版分享页兼容问题，已自动回退 Web 直链；确保已用弹窗登录 |
| 夸克下载 `require login` | 夸克登录（弹窗或 Cookie） |
| 弹窗没出现 | 检查 Edge 路径；`browser_login.js` 硬编码 `C:\Program Files (x86)\Microsoft\Edge\...`，按需修改 |
| 二维码过期 | 重新说「登录百度」生成新码 |

## 进阶：挂载为 agent preset（跨会话持久）

1. 在你的 preset 目录（`${DSH_HOME}/.agent-presets/<id>/cordis.yml`）中注册
   `tool-cordis` 所在的主机组合（或直接依赖 dynamic-cordis 提供的 `harness`），
   然后新增一行 plugin row，引用本发布包的 `plugin-host.js` 内容。
2. 具体行形态以目标 Harness 版本的插件装载器为准；请咨询对应版本文档或让模型协助转换。

## 版本历史

- **v1.1.0**（本包）：
  - 新增 `/dsh-open` 路由：回复中的本地路径为可点击 markdown 链接，点击后在本地打开资源管理器/默认程序；工具结果附带 `open_url` 字段
  - 修复下载超时默认值（后台任务 40s→1h）、路径白名单动态化（会话 cwd/下载目录）
- **v1.0.0**：首版成品。
  - 三家网盘分享解析/下载、auto/confirm 模式、登录优先协议
  - 百度扫码登录、弹窗浏览器登录（百度/夸克/迅雷）、BaiduPCS-Go 高速通道
  - 百度新格式短链（share/init）、9019 风控回退路径
