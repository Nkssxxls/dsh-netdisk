#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
netdisk_helper.py — 网盘分享链接解析与下载（仅 Python 标准库）

支持:
  - 百度网盘 pan.baidu.com/s/1xxx?pwd=xxxx
      匿名: 提取码验证 + 分享列表 + sharedownload 直链(限速/风控)
      登录: 配置 Cookie 后用 BaiduPCS-Go 高速转存下载(推荐, 避免限速风控)
  - 夸克网盘 pan.quark.cn/s/xxxx
      匿名: stoken + detail 可浏览列表; 下载必须登录 Cookie
  - 迅雷网盘 pan.xunlei.com/s/VNxxx?pwd=xxxx
      captcha token 流程; 完整下载需登录态 Cookie(仍可能受限)

用法:
  python netdisk_helper.py probe    '<json>'
  python netdisk_helper.py download '<json>'
  python netdisk_helper.py login    '<json>'   # 保存登录凭证
  python netdisk_helper.py whoami   '{}'       # 查看凭证状态

stdout 只输出一行 JSON 结果; stderr 输出进度与日志。

JSON 协议:
  probe   输入: {"url": str, "passcode": str?, "cookie": str?, "recursive": bool?, "timeout": int?}
          输出: {"ok": bool, "provider": str?, "share_id": str?, "files": [{name,size,isdir,id,path}], "error": str?}
  download 输入: {"url": str, "passcode": str?, "cookie": str?, "dest": str?, "filter": str?, "max_files": int?, "timeout": int?}
          输出: {"ok": bool, "provider": str?, "downloaded": [{name,path,size}], "error": str?}
  login   输入: {"provider": "baidu"|"quark"|"xunlei", "cookie": str}
          输出: {"ok": bool, "saved": {provider: bool}}
  whoami  输出: {"ok": bool, "credentials": {provider: {"configured": bool, "preview": str}}}
"""
import sys
import os
import re
import json
import time
import uuid
import shutil
import subprocess
import random
import urllib.request
import urllib.parse
import urllib.error
import http.cookiejar
import ssl
import fnmatch

for _stream in (sys.stdout, sys.stderr):
    try:
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ---------------------------------------------------------------- utilities

BAIDU_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
QUARK_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) quark-cloud-drive/3.14.2 Chrome/112.0.5615.165 "
            "Electron/24.1.3.8 Safari/537.36 Channel/pckk_other_ch")
XL_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE

DEFAULT_TIMEOUT = 40


def log(msg):
    print("[netdisk] " + msg, file=sys.stderr, flush=True)


def make_opener(cookie_jar=None, headers=None, timeout=None, use_cookies=True):
    handlers = []
    cj = cookie_jar
    if use_cookies:
        if cj is None:
            cj = http.cookiejar.CookieJar()
        handlers.append(urllib.request.HTTPCookieProcessor(cj))
    handlers.append(urllib.request.HTTPSHandler(context=_ctx))
    opener = urllib.request.build_opener(*handlers)
    opener.addheaders = list((headers or {}).items())
    return opener, cj


def http_request(opener, url, method="GET", data=None, headers=None, timeout=None):
    """data: dict(表单) 或 bytes 或 str; headers: dict 覆盖。返回 (status, body_bytes, final_url)。"""
    to = timeout or DEFAULT_TIMEOUT
    hdrs = {}
    if headers:
        hdrs.update(headers)
    body = None
    if data is not None:
        if isinstance(data, dict):
            body = urllib.parse.urlencode(data).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
        elif isinstance(data, str):
            body = data.encode("utf-8")
        else:
            body = data
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    try:
        with opener.open(req, timeout=to) as resp:
            return resp.status, resp.read(), resp.geturl()
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.geturl()


def clean_name(name):
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name or "file")
    name = name.strip().strip(".")
    return name[:180] or "file"


def sanitize_links(text):
    return text


def fmt_size(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return (f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}")
        n /= 1024
    return "0B"


# ---------------------------------------------------------------- credentials

HELPER_DIR = os.path.dirname(os.path.abspath(__file__))
CRED_FILE = os.path.join(HELPER_DIR, "credentials.json")
QR_DIR = os.path.join(HELPER_DIR, "qr_sessions")
PROVIDERS = ("baidu", "quark", "xunlei")


def load_credentials():
    try:
        with open(CRED_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def save_credentials(data):
    tmp = CRED_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CRED_FILE)


def do_login(args):
    provider = (args.get("provider") or "").strip().lower()
    cookie = (args.get("cookie") or "").strip()
    if provider not in PROVIDERS:
        return {"ok": False, "error": f"provider 必须是 {PROVIDERS} 之一"}
    if len(cookie) < 8:
        return {"ok": False, "error": "cookie 太短, 请复制浏览器中的完整 Cookie"}
    creds = load_credentials()
    creds[provider] = {"cookie": cookie}
    save_credentials(creds)
    return {"ok": True, "saved": {provider: True},
            "hint": "已保存登录凭证, 后续该网盘的下载将使用登录态"}


def do_whoami(_args):
    creds = load_credentials()
    out = {}
    for p in PROVIDERS:
        entry = creds.get(p) or {}
        cookie = (entry.get("cookie") or "")
        out[p] = {
            "configured": bool(cookie),
            "preview": (cookie[:24] + "...(共" + str(len(cookie)) + "字符)") if cookie else "",
        }
    return {"ok": True, "credentials": out,
            "hint": "未配置的网盘请先让用户登录该网盘并复制 Cookie, 用 login 保存(见 netdisk_login 工具)"}


def cred_cookie(provider, explicit):
    if explicit:
        return explicit
    creds = load_credentials()
    entry = creds.get(provider) or {}
    return entry.get("cookie", "") or ""


# ---------------------------------------------------------------- baidu qr login

def parse_jsonp(text):
    text = text.strip()
    i = text.find("(")
    j = text.rfind(")")
    if i >= 0 and j > i:
        text = text[i + 1:j]
    return json.loads(text)


def normalize_baidu_url(raw):
    raw = str(raw or "").replace("\\/", "/").strip()
    if raw.startswith("//"):
        return "https:" + raw
    if raw.startswith("http"):
        return raw
    return "https://" + raw


def do_login_qr_start(args):
    provider = (args.get("provider") or "baidu").strip().lower()
    if provider != "baidu":
        return {"ok": False, "error": "扫码登录当前仅支持百度网盘(baidu); 夸克/迅雷请先用 netdisk_login 的 Cookie 方式"}
    cj = http.cookiejar.MozillaCookieJar()
    opener, _ = make_opener(cookie_jar=cj, headers={"User-Agent": BAIDU_UA,
                                                    "Accept-Language": "zh-CN,zh;q=0.9"}, timeout=30)
    # 先访问网盘首页种 BAIDUID
    try:
        http_request(opener, "https://pan.baidu.com/", headers={"Referer": "https://pan.baidu.com/"})
    except Exception:
        pass
    # 创建二维码
    gid = str(uuid.uuid4()).upper()
    tt = int(time.time() * 1000)
    url = ("https://passport.baidu.com/v2/api/getqrcode?lp=pc&qrloginfrom=pc&gid=" + gid +
           "&oauthLog=&apiver=v3&tpl=dev&logPage=login&tt=" + str(tt) + "&callback=bdqr" + str(tt))
    st, body, _ = http_request(opener, url, headers={"Referer": "https://pan.baidu.com/"})
    try:
        qr = parse_jsonp(body.decode("utf-8", "replace"))
    except Exception:
        return {"ok": False, "error": "百度二维码接口响应解析失败: " + body.decode("utf-8", "replace")[:200]}
    if qr.get("errno") != 0 or not qr.get("sign"):
        return {"ok": False, "error": f"百度二维码创建失败 errno={qr.get('errno')}"}
    sign = qr["sign"]
    img_url = normalize_baidu_url(qr.get("imgurl", ""))
    os.makedirs(QR_DIR, exist_ok=True)
    cj.save(os.path.join(QR_DIR, sign + ".cookies"), ignore_discard=True, ignore_expires=True)
    with open(os.path.join(QR_DIR, sign + ".json"), "w", encoding="utf-8") as f:
        json.dump({"sign": sign, "gid": gid, "imgurl": img_url,
                   "created": time.time(), "provider": provider}, f, ensure_ascii=False)
    return {"ok": True, "provider": provider, "sign": sign,
            "qr_image_url": img_url, "expire_seconds": 180,
            "hint": "请用户打开二维码图片链接, 用手机百度 APP 扫码并在手机上确认登录。"
                    "扫码后调用 netdisk_status 查看登录结果(插件会自动等待扫码)"}


def do_login_qr_wait(args):
    sign = (args.get("sign") or "").strip()
    timeout = int(args.get("timeout") or 180)
    if not sign:
        return {"ok": False, "error": "缺少 sign 参数"}
    sess_file = os.path.join(QR_DIR, sign + ".json")
    ck_file = os.path.join(QR_DIR, sign + ".cookies")
    if not os.path.exists(sess_file):
        return {"ok": False, "error": "二维码会话不存在或已过期, 请重新生成"}
    try:
        with open(sess_file, "r", encoding="utf-8") as f:
            sess = json.load(f)
    except Exception:
        return {"ok": False, "error": "二维码会话文件损坏"}
    cj = http.cookiejar.MozillaCookieJar()
    try:
        cj.load(ck_file, ignore_discard=True, ignore_expires=True)
    except Exception:
        pass
    opener, _ = make_opener(cookie_jar=cj, headers={"User-Agent": BAIDU_UA}, timeout=30)

    deadline = time.time() + timeout
    last_status = ""
    while time.time() < deadline:
        tt = int(time.time() * 1000)
        url = ("https://passport.baidu.com/channel/unicast?channel_id=" + sign +
               "&gid=&tpl=dev&_sdkFrom=1&apiver=v3&tt=" + str(tt) + "&callback=bdqrpoll" + str(tt))
        try:
            st, body, _ = http_request(opener, url, headers={"Referer": "https://pan.baidu.com/"})
            raw = parse_jsonp(body.decode("utf-8", "replace"))
            channel_v = raw.get("channel_v")
            ch = {}
            if isinstance(channel_v, str) and channel_v and channel_v != "null":
                try:
                    ch = json.loads(channel_v)
                except Exception:
                    ch = {}
            elif isinstance(channel_v, dict):
                ch = channel_v
            status = str(ch.get("status", ""))
            if status == "0":
                bduss = str(ch.get("v", "") or "")
                if not bduss:
                    return {"ok": False, "error": "扫码已确认, 但未返回登录凭据"}
                log("扫码确认成功, 正在换取 STOKEN ...")
                # 授权页 URL(STOKEN 接口的 Referer 校验要求 openapi 授权页)
                auth_url = ("https://openapi.baidu.com/oauth/2.0/authorize?response_type=code"
                            "&client_id=netdisk_helper&redirect_uri=" +
                            urllib.parse.quote("https://pan.baidu.com/", safe="") +
                            "&scope=" + urllib.parse.quote("basic,netdisk", safe="") +
                            "&display=tv&qrcode=1&force_login=1")
                u_val = str(ch.get("u", "") or "")
                if not u_val.lower().startswith("http") or "openapi.baidu.com" not in u_val:
                    u_val = auth_url
                # bdusslogin 建立会话
                tt2 = int(time.time() * 1000)
                bu = ("https://passport.baidu.com/v2/api/bdusslogin?tt=" + str(tt2) +
                      "&bduss=" + urllib.parse.quote(bduss, safe="") +
                      "&u=" + urllib.parse.quote(u_val, safe="") +
                      "&qrcode=1&tpl=dev&callback=bdusslogin" + str(tt2))
                http_request(opener, bu, headers={"Referer": auth_url})
                # 访问授权页建立完整 OAuth 会话(跟随跳转)
                try:
                    http_request(opener, u_val, headers={"Referer": auth_url,
                                                         "User-Agent": BAIDU_UA})
                except Exception:
                    pass
                # STOKEN
                tt3 = int(time.time() * 1000)
                st3, b3, _ = http_request(opener,
                    "https://passport.baidu.com/v3/login/api/auth?tpl=dev&return_type=2&callback=logaback" + str(tt3),
                    headers={"Referer": auth_url})
                stoken = ""
                try:
                    ares = parse_jsonp(b3.decode("utf-8", "replace"))
                    stoken = str(ares.get("stoken", "") or "")
                except Exception:
                    stoken = ""
                if not stoken:
                    return {"ok": False, "error": "扫码成功但获取 STOKEN 失败(响应异常): " + b3.decode("utf-8", "replace")[:220]}
                # 组装 Cookie
                pairs = []
                seen = set()
                for c in cj:
                    key = c.name
                    if key not in seen:
                        seen.add(key)
                        pairs.append(key + "=" + c.value)
                if "BDUSS" not in seen:
                    pairs.append("BDUSS=" + bduss)
                if "STOKEN" not in seen:
                    pairs.append("STOKEN=" + stoken)
                cookie_str = "; ".join(pairs)
                # 验证有效性
                ok_verify, verify_msg = verify_baidu_cookie(cookie_str)
                if not ok_verify:
                    return {"ok": False, "error": "百度凭证校验失败: " + verify_msg}
                creds = load_credentials()
                creds["baidu"] = {"cookie": cookie_str}
                save_credentials(creds)
                try:
                    os.remove(sess_file)
                    os.remove(ck_file)
                except Exception:
                    pass
                return {"ok": True, "provider": "baidu", "login": True,
                        "hint": "百度登录成功(扫码), Cookie 已保存, 下载将走 BaiduPCS-Go 高速通道"}
            elif status == "1" and last_status != "1":
                last_status = "1"
                log("用户已扫码, 等待在手机上确认...")
            elif status == "2":
                return {"ok": False, "error": "二维码已过期, 请重新生成"}
        except ValueError:
            pass
        except Exception as e:
            log(f"轮询异常: {type(e).__name__}: {e}")
        time.sleep(3)
    return {"ok": False, "error": f"等待扫码超时({timeout}s), 二维码已失效"}


def verify_baidu_cookie(cookie_str):
    # 首选: 用 BaiduPCS-Go 自身校验(它也是最终下载通道)
    exe = find_baidupcs()
    if exe:
        rc, out, err = run_bpc(exe, ["login", "-cookies=" + cookie_str], 90)
        combined = out + err
        if ("失败" in combined) or ("错误代码" in combined) or ("成功" not in combined):
            return False, combined[:300]
        return True, ""
    # 回退: 百度官方接口
    try:
        req = urllib.request.Request(
            "https://pan.baidu.com/rest/2.0/membership/user?method=query&app_id=250528&format=json",
            headers={"User-Agent": BAIDU_UA, "Cookie": cookie_str,
                     "Referer": "https://pan.baidu.com/"})
        with urllib.request.urlopen(req, timeout=20, context=_ctx) as r:
            raw = r.read().decode("utf-8", "replace")
        j = json.loads(raw)
        if j.get("errno") == 0:
            return True, ""
        return False, raw[:300]
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------- browser login (quark/xunlei)

def do_login_browser(args):
    """启动本地 Edge(独立实例)打开网盘登录页, 用户登录后自动抓取 Cookie 保存。"""
    provider = (args.get("provider") or "").strip().lower()
    if provider not in ("quark", "xunlei", "baidu"):
        return {"ok": False, "error": "浏览器登录支持 quark / xunlei / baidu"}
    script = os.path.join(HELPER_DIR, "browser_login.js")
    if not os.path.exists(script):
        return {"ok": False, "error": "缺少 browser_login.js, 请重新部署 .dsh-netdisk 目录"}
    port = random.randint(9301, 9699)
    profile = os.path.join(QR_DIR, "browser_" + provider)
    max_wait = int(args.get("timeout") or 600)
    try:
        proc = subprocess.run(["node", script, provider, str(port), profile, str(max_wait * 1000)],
                              capture_output=True, timeout=max_wait + 90)
        out = proc.stdout
        if isinstance(out, bytes):
            try:
                out = out.decode("utf-8")
            except Exception:
                try:
                    out = out.decode("gbk", "replace")
                except Exception:
                    out = str(out)
        lines = [ln for ln in (out or "").strip().splitlines() if ln.strip().startswith("{")]
        r = json.loads(lines[-1]) if lines else {}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "等待浏览器登录超时(" + str(max_wait) + "s)"}
    except FileNotFoundError:
        return {"ok": False, "error": "未找到 node 可执行文件(需要 Node.js 18+)"}
    except Exception as e:
        return {"ok": False, "error": f"浏览器登录脚本执行失败: {type(e).__name__}: {e}"}
    if r.get("ok") and r.get("cookie"):
        creds = load_credentials()
        creds[provider] = {"cookie": r["cookie"]}
        save_credentials(creds)
        return {"ok": True, "provider": provider, "login": True,
                "cookie_count": r.get("cookie_count"),
                "hint": "登录成功, Cookie 已自动保存, 之后该网盘下载将使用登录态"}
    return {"ok": False, "provider": provider, "error": r.get("error") or "浏览器登录失败"}


# ---------------------------------------------------------------- baidupcs-go

def find_baidupcs():
    """在 .dsh-netdisk/bin 下查找 BaiduPCS-Go.exe。"""
    base = os.path.join(HELPER_DIR, "bin")
    if not os.path.isdir(base):
        return None
    for root, dirs, files in os.walk(base):
        for name in files:
            if name.lower() == "baidupcs-go.exe":
                return os.path.join(root, name)
    return None


def run_bpc(exe, args, timeout):
    """运行 BaiduPCS-Go, 返回 (exit_code, stdout_text, stderr_text)。"""
    cmd = [exe] + args
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
        out = proc.stdout
        err = proc.stderr
        if isinstance(out, bytes):
            try:
                out = out.decode("utf-8")
            except Exception:
                try:
                    out = out.decode("gbk", "replace")
                except Exception:
                    out = str(out)
        if isinstance(err, bytes):
            try:
                err = err.decode("utf-8", "replace")
            except Exception:
                err = str(err)
        return proc.returncode, out, err
    except subprocess.TimeoutExpired:
        return -99, "", "BaiduPCS-Go 执行超时"
    except FileNotFoundError:
        return -98, "", "未找到 BaiduPCS-Go 可执行文件"
    except Exception as e:
        return -97, "", f"BaiduPCS-Go 执行失败: {type(e).__name__}: {e}"


def baidupcs_login_and_transfer(cookie, share_url, passcode, dest, filter_str, max_files, timeout):
    """用 BaiduPCS-Go 登录 + 转存 + 下载分享内容(登录态高速通道)。
    先用 Python 走一遍 verify 流程拿 BDCLND 等会话 Cookie, 与登录 Cookie 合并后再交给 BaiduPCS-Go。"""
    exe = find_baidupcs()
    if exe is None:
        return {"ok": False, "error": "未找到 BaiduPCS-Go 二进制(.dsh-netdisk/bin/BaiduPCS-Go.exe), "
                                      "无法使用百度登录态下载; 请重新放置或使用匿名模式"}

    # 0. Python 侧先完成提取码验证, 收集会话 Cookie(BDCLND 等) 与登录 Cookie 合并
    combined_cookie = cookie
    try:
        _files, _meta, _opener = baidu_parse(share_url, passcode, min(timeout, 120), cookie)
        cj = _meta.get("cj")
        if cj is not None:
            extras = []
            names = set()
            for part in cookie.split(";"):
                if "=" in part:
                    names.add(part.strip().split("=", 1)[0])
            for c in cj:
                if c.name and c.name not in names and c.value:
                    extras.append(f"{c.name}={c.value}")
                    names.add(c.name)
            if extras:
                combined_cookie = cookie + "; " + "; ".join(extras)
                log(f"合并会话 Cookie: 新增 {len(extras)} 项")
    except Exception as e:
        log(f"预验证流程跳过(不影响主流程): {e}")

    # 1. 登录
    rc, out, err = run_bpc(exe, ["login", "-cookies=" + combined_cookie], min(timeout, 90))
    combined = out + err
    log(f"BaiduPCS-Go login rc={rc} out={out[:200]!r}")
    if rc != 0 or ("失败" in combined) or ("错误代码" in combined) or ("成功" not in combined):
        return {"ok": False, "error": "百度登录失败(BaiduPCS-Go): " + combined[:500]
                + " | 请确认 Cookie 包含有效的 BDUSS 与 STOKEN(浏览器登录 pan.baidu.com 后 F12 → Network 复制)"}

    # 2. 设置保存目录
    dest_norm = dest.replace("\\", "/")
    run_bpc(exe, ["config", "set", "-savedir", dest_norm], 60)

    # 3. 转存 + 自动下载
    args = ["transfer", share_url]
    if passcode:
        args.append(passcode)
    args.append("--download")
    rc, out, err = run_bpc(exe, args, timeout)
    log(f"BaiduPCS-Go transfer rc={rc}\n{out[-1200:]}\n{err[-400:]}")
    if rc != 0 or "失败" in out or "失败" in err:
        return {"ok": False, "error": "百度转存/下载失败(BaiduPCS-Go): " + (out + err)[-800:]}
    # 从输出中找已下载文件
    downloaded = []
    for m in re.finditer(r"([^\s]+)\s*已下载|下载成功[:：]\s*(.+)", out):
        downloaded.append({"name": os.path.basename((m.group(1) or m.group(2) or "").strip()),
                           "path": "", "size": 0})
    return {"ok": True, "provider": "baidu", "via": "baidupcs-go",
            "downloaded": downloaded or [{"name": "(见上方输出)", "path": dest, "size": 0}],
            "dest": dest, "log_tail": out[-800:]}


# ---------------------------------------------------------------- baidu

BAIDU_HOST = "https://pan.baidu.com"
BAIDU_APP_ID = "250528"


def js_obj_to_json(text):
    """把 JS 对象字面量宽松转换为 JSON（单引号字符串、裸键）。"""
    # 1. 单引号字符串 → JSON 字符串
    def _q(m):
        return json.dumps(m.group(1).replace("\\'", "'"))
    text = re.sub(r"'((?:\\.|[^'\\])*)'", _q, text)
    # 2. 抽离双引号字符串
    strs = []
    def _ph(m):
        strs.append(m.group(0))
        return "\x00%d\x00" % (len(strs) - 1)
    text = re.sub(r'"(?:\\.|[^"\\])*"', _ph, text)
    # 3. 裸键加引号
    text = re.sub(r"([{,])\s*([A-Za-z_$][\w$]*)\s*:", r'\1"\2":', text)
    # 4. 还原字符串
    text = re.sub(r"\x00(\d+)\x00", lambda m: strs[int(m.group(1))], text)
    return json.loads(text)


def extract_yundata(page):
    """从百度分享页提取 window.yunData = {...} 的 JS 对象（括号平衡扫描 + 宽松解析）。"""
    idx = page.find("yunData")
    if idx < 0:
        return None
    brace = page.find("{", idx)
    if brace < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(brace, len(page)):
        ch = page[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch in "\"'":
                in_str = False
        elif ch in "\"'":
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                frag = page[brace:i + 1]
                try:
                    return js_obj_to_json(frag)
                except Exception:
                    return None
    return None


BAIDU_ERRNO_MSG = {
    100: "分享不存在",
    115: "分享已失效/被删除",
    116: "分享已过期",
    122: "分享不存在",
    125: "分享已过期",
}


def baidu_parse(url, passcode, timeout, cookie=""):
    """返回 (files, meta) 或抛 ValueError。files: [{name,size,isdir,id,path}]"""
    m = re.search(r"pan\.baidu\.com/s/(1[A-Za-z0-9_-]{5,22})", url)
    if not m:
        # 兼容 https://pan.baidu.com/share/init?surl=XXXX&pwd=YYYY 格式
        m2 = re.search(r"[?&]surl=([A-Za-z0-9_-]{5,30})", url)
        if m2:
            surl_raw = m2.group(1)
            surl = ("1" + surl_raw) if not surl_raw.startswith("1") else surl_raw
        else:
            raise ValueError("不是有效的百度网盘分享链接")
    else:
        surl = m.group(1)
    if not passcode:
        passcode = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("pwd", [""])[0]

    # 页面访问用带 1 前缀的 surl; list 接口的 shorturl 参数用不带 1 的
    surl_page = surl
    surl_short = surl[1:] if surl.startswith("1") else surl

    hdrs = {
        "User-Agent": BAIDU_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    opener, cj = make_opener(headers=hdrs, timeout=timeout)

    def req(url, method="GET", data=None, headers=None):
        """所有请求显式携带登录 Cookie(避免被 jar 的匿名 Cookie 顶掉)。"""
        h = {}
        if headers:
            h.update(headers)
        if cookie:
            h["Cookie"] = cookie
        return http_request(opener, url, method=method, data=data, headers=h)

    def sso_follow(start_url):
        """跟随百度登录态 SSO 跳转链(login redirect 页), 直到回到目标页面。"""
        u = start_url
        for _ in range(8):
            st, body, final = req(u, headers={"Referer": f"{BAIDU_HOST}/disk/home"})
            if "/login" not in final:
                return st, body, final
            m = re.search(r'href="([^"]+)"', body.decode("utf-8", "replace"))
            if not m:
                return st, body, final
            u = urllib.parse.urljoin(BAIDU_HOST + "/", m.group(1))
        return st, body, final

    # 1. 访问分享页拿 yunData(登录态会走 SSO 跳转链; 失败时回退匿名重试一次)
    st, body, final_url = sso_follow(f"{BAIDU_HOST}/s/{surl_page}")
    page = body.decode("utf-8", "replace")
    if "error-404" in page:
        raise ValueError("百度网盘: 页面不存在")
    if "platform-non-found" in page:
        raise ValueError("百度网盘: 分享链接已失效")

    ydata = extract_yundata(page)
    if ydata is None and cookie:
        # 带登录 Cookie 时 SSO 跳转链可能走不完, 回退匿名解析(浏览列表匿名足够)
        log("登录态页面解析失败, 回退匿名解析")
        return baidu_parse(url, passcode, timeout, "")
    if ydata is None:
        # 第二次尝试: 跟随 init 页跳转后再抓
        if "share/init" in final_url:
            st_a, body_a, _ = req(final_url,
                                  headers={"Referer": f"{BAIDU_HOST}/s/{surl_page}"})
            ydata = extract_yundata(body_a.decode("utf-8", "replace"))
    if ydata is None:
        raise ValueError("百度网盘: 分享页数据解析失败(结构变化?), 请更新 helper")
    if ydata.get("share_page_type") == "error" or ydata.get("errno", 0) not in (0,):
        errno = ydata.get("errno")
        raise ValueError("百度网盘: " + BAIDU_ERRNO_MSG.get(errno, f"分享异常 errno={errno}"))
    bdstoken = str(ydata.get("bdstoken", ""))
    uk = str(ydata.get("uk", ""))
    share_uk = str(ydata.get("share_uk", "") or uk)
    shareid = str(ydata.get("shareid", ""))

    randsk = ""
    if passcode:
        if len(passcode) != 4:
            raise ValueError("百度网盘: 提取码应为 4 位")
        # 2. verify
        verify_url = (f"{BAIDU_HOST}/share/verify?shareid={shareid}"
                      f"&time={int(time.time()*1000)}&clienttype=1&uk={share_uk}")
        st2, body2, _ = req(verify_url, method="POST",
                            data={"pwd": passcode, "vcode": "null",
                                  "vcode_str": "null", "bdstoken": bdstoken},
                            headers={"Referer": f"{BAIDU_HOST}/s/{surl_page}",
                                     "User-Agent": BAIDU_UA})
        try:
            vj = json.loads(body2.decode("utf-8", "replace"))
        except Exception:
            raise ValueError("百度网盘: 验证响应解析失败")
        errno = vj.get("errno")
        if errno != 0:
            if errno == -9:
                raise ValueError("百度网盘: 提取码错误")
            if errno == 8001:
                raise ValueError("百度网盘: 触发安全验证(风控), 请稍后再试")
            raise ValueError(f"百度网盘: 提取码验证失败 errno={errno}")
        randsk = vj.get("randsk", "")

        # 3. 再次访问分享页刷新 yunData
        st3, body3, _ = req(f"{BAIDU_HOST}/s/{surl_page}",
                            headers={"Referer": f"{BAIDU_HOST}/share/init?surl={surl_short}"})
        ydata3 = extract_yundata(body3.decode("utf-8", "replace"))
        if ydata3:
            bdstoken = str(ydata3.get("bdstoken", bdstoken))
            share_uk = str(ydata3.get("share_uk", share_uk) or ydata3.get("uk", uk))

    # 4. share/list (shorturl 用不带 1 前缀的)
    files = []
    page_no = 1
    while True:
        list_url = (f"{BAIDU_HOST}/share/list?app_id={BAIDU_APP_ID}&channel=chunlei"
                    f"&clienttype=0&web=1&root=1&shorturl={surl_short}&bdstoken={bdstoken}"
                    f"&page={page_no}&num=100&order=time&desc=1&showempty=0")
        st4, body4, _ = req(list_url, headers={"Referer": f"{BAIDU_HOST}/s/{surl_page}"})
        try:
            lj = json.loads(body4.decode("utf-8", "replace"))
        except Exception:
            raise ValueError("百度网盘: 文件列表解析失败(可能触发风控, 请稍后重试)")
        if lj.get("errno") != 0:
            raise ValueError(f"百度网盘: 获取文件列表失败 errno={lj.get('errno')}")
        lst = lj.get("list") or []
        for f in lst:
            files.append({
                "name": f.get("server_filename", ""),
                "size": int(f.get("size", 0) or 0),
                "isdir": bool(f.get("isdir")),
                "id": str(f.get("fs_id", "")),
                "path": f.get("path", ""),
            })
        total = int(lj.get("total_list", 0) or 0)
        if len(files) >= total or len(lst) < 100:
            break
        page_no += 1

    meta = {"bdstoken": bdstoken, "uk": uk, "share_uk": share_uk,
            "shareid": shareid, "randsk": randsk,
            "surl": surl_page, "surl_short": surl_short, "cj": cj,
            "cookie": cookie}
    return files, meta, opener


def baidu_download_url(opener, meta, fs_id, timeout):
    """获取百度分享文件下载直链: 优先 xpan filemetas(登录态), 兜底 sharedownload。"""
    surl = meta["surl"]
    shareid = meta["shareid"]
    share_uk = meta["share_uk"]
    randsk = meta["randsk"]
    cookie = meta.get("cookie", "")

    # 1. xpan filemetas(登录态下可拿分享文件 dlink)
    if cookie:
        u = (f"https://pan.baidu.com/rest/2.0/xpan/multimedia?method=filemetas&dlink=1"
             f"&fsids=%5B{fs_id}%5D&app_id=250528&web=1")
        st, body, _ = http_request(opener, u, headers={
            "User-Agent": BAIDU_UA, "Referer": f"{BAIDU_HOST}/s/{surl}", "Cookie": cookie})
        try:
            j = json.loads(body.decode("utf-8", "replace"))
        except Exception:
            j = {}
        if j.get("errno") == 0:
            lst = j.get("list") or []
            if lst and lst[0].get("dlink"):
                return lst[0]["dlink"]
        elif j.get("errno") == 9019:
            raise ValueError("百度网盘: 接口风控(need verify)。请用 netdisk_login_browser 弹窗登录百度"
                             "获取完整浏览器 Cookie 后重试(浏览器登录态可解除风控)")
        else:
            log(f"xpan filemetas errno={j.get('errno')} {j.get('errmsg')}, 尝试 sharedownload")

    # 2. 兜底: sharedownload
    dl_url = (f"{BAIDU_HOST}/api/sharedownload?app_id={BAIDU_APP_ID}&channel=chunlei"
              f"&clienttype=0&web=1")
    data = {
        "encrypt": "0",
        "product": "share",
        "uk": share_uk,
        "primaryid": shareid,
        "fid_list": f"[{fs_id}]",
        "extra": json.dumps({"sekey": randsk}, separators=(",", ":")),
    }
    hdrs = {"Referer": f"{BAIDU_HOST}/s/{surl}", "User-Agent": BAIDU_UA}
    if cookie:
        hdrs["Cookie"] = cookie
    st, body, _ = http_request(opener, dl_url, method="POST", data=data, headers=hdrs)
    try:
        j = json.loads(body.decode("utf-8", "replace"))
    except Exception:
        raise ValueError("百度网盘: 直链接口响应解析失败")
    if j.get("errno") != 0:
        show = j.get("show_msg") or ""
        raise ValueError(f"百度网盘: 获取下载直链失败 errno={j.get('errno')} {show}")
    dlink = j.get("dlink") or ""
    if not dlink:
        raise ValueError("百度网盘: 未返回下载直链")
    return dlink


def baidu_fetch(opener, url, dest_path, headers, timeout):
    timeout = max(timeout, 300)  # socket 读超时, 大文件慢速连接至少 5 分钟
    opener.addheaders = list(headers.items())
    req = urllib.request.Request(url, headers=headers)
    try:
        with opener.open(req, timeout=timeout) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            with open(dest_path, "wb") as f:
                while True:
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        pct = done * 100.0 / total
                        print(f"[netdisk]   下载中 {done//1048576}MB/{total//1048576}MB ({pct:.0f}%)", file=sys.stderr)
            return done
    except urllib.error.HTTPError as e:
        raise ValueError(f"百度网盘: 下载失败 HTTP {e.code}（匿名下载大文件可能被风控/限速）")


# ---------------------------------------------------------------- quark

QUARK_BASE = "https://drive-pc.quark.cn"


def quark_parse(url, passcode, timeout, recursive=True, cookie=""):
    m = re.search(r"pan\.quark\.cn/s/([A-Za-z0-9_-]+)", url)
    if not m:
        raise ValueError("不是有效的夸克网盘分享链接")
    pwd_id = m.group(1)
    if not passcode:
        passcode = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("pwd", [""])[0]

    hdrs = {
        "User-Agent": QUARK_UA,
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Origin": "https://pan.quark.cn",
        "Referer": "https://pan.quark.cn/",
    }
    if cookie:
        hdrs["Cookie"] = cookie.strip()
    opener, cj = make_opener(headers=hdrs, timeout=timeout, use_cookies=False)

    # 1. stoken
    q = urllib.parse.urlencode({"pr": "ucpro", "fr": "pc", "uc_param_str": "",
                                "__dt": int(time.time() * 1000) % 9000 + 100,
                                "__t": int(time.time() * 1000)})
    st, body, _ = http_request(opener,
        f"{QUARK_BASE}/1/clouddrive/share/sharepage/token?{q}",
        method="POST", data=json.dumps({"pwd_id": pwd_id, "passcode": passcode}),
        headers={"Content-Type": "application/json"})
    try:
        tj = json.loads(body.decode("utf-8", "replace"))
    except Exception:
        raise ValueError("夸克网盘: token 接口响应解析失败")
    if tj.get("status") != 200 or not tj.get("data"):
        msg = tj.get("message") or tj.get("errmsg") or "未知错误"
        if "验证" in str(msg) or "passcode" in str(msg).lower() or "密码" in str(msg):
            raise ValueError("夸克网盘: 提取码错误")
        raise ValueError(f"夸克网盘: {msg}")
    stoken = tj["data"].get("stoken", "")

    # 2. detail (递归)
    files = []
    dirs_todo = [("0", "")]  # (pdir_fid, relpath)

    def list_dir(pdir_fid, relpath):
        page = 1
        while True:
            q = urllib.parse.urlencode({
                "pr": "ucpro", "fr": "pc", "pwd_id": pwd_id, "stoken": stoken,
                "pdir_fid": pdir_fid, "force": "0", "_page": page, "_size": "50",
                "_fetch_share": "1", "_fetch_total": "1",
                "_sort": "file_type:asc,updated_at:desc",
            })
            st, body, _ = http_request(opener,
                f"{QUARK_BASE}/1/clouddrive/share/sharepage/detail?{q}")
            try:
                dj = json.loads(body.decode("utf-8", "replace"))
            except Exception:
                raise ValueError("夸克网盘: 文件列表接口响应解析失败")
            if dj.get("status") != 200 or not dj.get("data"):
                msg = dj.get("message") or "未知错误"
                raise ValueError(f"夸克网盘: 文件列表失败 {msg}")
            data = dj["data"]
            total = int((data.get("metadata") or {}).get("_total", 0) or 0)
            for f in data.get("list") or []:
                item = {
                    "name": f.get("file_name", ""),
                    "size": int(f.get("size", 0) or 0),
                    "isdir": bool(f.get("dir")),
                    "id": f.get("fid", ""),
                    "path": (relpath + "/" if relpath else "") + f.get("file_name", ""),
                    "token": f.get("share_fid_token", ""),
                }
                files.append(item)
                if recursive and item["isdir"]:
                    dirs_todo.append((f.get("fid"), item["path"]))
            if len(data.get("list") or []) < 50 or len(files) >= total:
                break
            page += 1

    while dirs_todo:
        fid, relpath = dirs_todo.pop(0)
        list_dir(fid, relpath)

    meta = {"pwd_id": pwd_id, "stoken": stoken}
    return files, meta, opener


def quark_download_url(opener, fid, timeout):
    q = urllib.parse.urlencode({"pr": "ucpro", "fr": "pc", "uc_param_str": ""})
    st, body, _ = http_request(opener,
        f"{QUARK_BASE}/1/clouddrive/file/download?{q}",
        method="POST", data=json.dumps({"fids": [fid]}),
        headers={"Content-Type": "application/json"})
    try:
        dj = json.loads(body.decode("utf-8", "replace"))
    except Exception:
        raise ValueError("夸克网盘: 下载接口响应解析失败")
    if dj.get("status") != 200 or not dj.get("data"):
        msg = dj.get("message") or "未知错误"
        if "login" in str(msg).lower() or "share missing" in str(msg):
            raise ValueError("夸克网盘: 下载需要登录态 Cookie（匿名仅可浏览文件列表）。"
                             "请在浏览器登录 pan.quark.cn 后复制 Cookie 传给 cookie 参数")
        raise ValueError(f"夸克网盘: 获取下载地址失败 {msg}")
    durl = dj["data"][0].get("download_url", "")
    if not durl:
        raise ValueError("夸克网盘: 未返回下载地址")
    return durl


def quark_fetch(opener, url, dest_path, timeout):
    timeout = max(timeout, 300)  # socket 读超时, 大文件慢速连接至少 5 分钟
    try:
        with opener.open(url, timeout=timeout) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            with open(dest_path, "wb") as f:
                while True:
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        pct = done * 100.0 / total
                        print(f"[netdisk]   下载中 {done//1048576}MB/{total//1048576}MB ({pct:.0f}%)", file=sys.stderr)
            return done
    except urllib.error.HTTPError as e:
        raise ValueError(f"夸克网盘: 下载失败 HTTP {e.code}")


# ---------------------------------------------------------------- xunlei (v1: captcha 阶段)

XL_CLIENT_ID = "Xqp0kJBXWhwaTpB6"
XL_API = "https://api-pan.xunlei.com/drive/v1"
XL_USER = "https://xluser-ssl.xunlei.com"


def xunlei_parse(url, passcode, timeout, cookie=""):
    m = re.search(r"pan\.xunlei\.com/s/(VN[A-Za-z0-9_-]+)", url)
    if not m:
        raise ValueError("不是有效的迅雷网盘分享链接")
    share_id = m.group(1)
    if not passcode:
        passcode = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("pwd", [""])[0]

    opener, cj = make_opener(headers={"User-Agent": XL_UA}, timeout=timeout)
    device_id = uuid.uuid4().hex
    extra_headers = {"User-Agent": XL_UA, "Content-Type": "application/json",
                     "Origin": "https://pan.xunlei.com", "Referer": "https://pan.xunlei.com/"}
    if cookie:
        extra_headers["Cookie"] = cookie

    # 1. captcha token
    st, body, _ = http_request(opener, f"{XL_USER}/v1/shield/captcha/init",
        method="POST",
        data=json.dumps({
            "client_id": XL_CLIENT_ID,
            "action": "GET:/drive/v1/share",
            "device_id": device_id,
            "captcha_token": "",
            "meta": {"username": "", "phone_number": "", "email": ""},
        }),
        headers=extra_headers)
    try:
        cj2 = json.loads(body.decode("utf-8", "replace"))
    except Exception:
        raise ValueError("迅雷网盘: captcha 接口响应解析失败")
    if cj2.get("url"):
        raise ValueError("迅雷网盘: 该分享需要人机验证, 需人工在浏览器完成验证(当前版本不支持)")
    captcha_token = cj2.get("captcha_token") or ""
    if not captcha_token:
        raise ValueError(f"迅雷网盘: 获取 captcha token 失败: {cj2.get('error_description') or cj2}")

    # 2. share info
    q = urllib.parse.urlencode({"share_id": share_id, "pwd": passcode})
    hdr2 = {"x-captcha-token": captcha_token, "x-device-id": device_id,
            "User-Agent": XL_UA, "Origin": "https://pan.xunlei.com",
            "Referer": "https://pan.xunlei.com/"}
    if cookie:
        hdr2["Cookie"] = cookie
    st2, body2, _ = http_request(opener, f"{XL_API}/share?{q}", headers=hdr2)
    text2 = body2.decode("utf-8", "replace")
    try:
        sj = json.loads(text2)
    except Exception:
        raise ValueError("迅雷网盘: 分享信息接口响应解析失败")
    if st2 == 200 and not sj.get("error"):
        files = []
        for f in (sj.get("files") or []):
            files.append({
                "name": f.get("name", ""),
                "size": int(f.get("size", 0) or 0),
                "isdir": bool(f.get("kind") == "drive#folder"),
                "id": f.get("id", ""),
                "path": f.get("name", ""),
            })
        return files, {"share_id": share_id, "device_id": device_id,
                       "captcha_token": captcha_token, "passcode": passcode}, opener
    detail = ""
    for d in (sj.get("error_details") or []):
        if d.get("detail"):
            detail = d["detail"]
            break
    raise ValueError(
        "迅雷网盘: 分享解析需要登录态(匿名 SSO 签名), v1 暂不支持完整下载。"
        f"接口返回: {sj.get('error')} ({detail or sj.get('error_description') or ''})")


# ---------------------------------------------------------------- main

def detect_provider(url):
    if "pan.baidu.com" in url:
        return "baidu"
    if "pan.quark.cn" in url:
        return "quark"
    if "pan.xunlei.com" in url:
        return "xunlei"
    return None


def matches_filter(name, path, flt):
    if not flt:
        return True
    if flt in name:
        return True
    try:
        if fnmatch.fnmatch(name, flt) or fnmatch.fnmatch(path, flt):
            return True
    except Exception:
        pass
    return False


def do_probe(args):
    url = (args.get("url") or "").strip()
    if not url:
        return {"ok": False, "error": "缺少 url 参数"}
    provider = detect_provider(url)
    if provider is None:
        return {"ok": False, "error": "不支持的网盘链接(仅支持 pan.baidu.com / pan.quark.cn / pan.xunlei.com 分享链接)"}
    passcode = (args.get("passcode") or "").strip()
    cookie = cred_cookie(provider, (args.get("cookie") or "").strip())
    recursive = bool(args.get("recursive", True))
    timeout = int(args.get("timeout") or DEFAULT_TIMEOUT)
    try:
        if provider == "baidu":
            files, meta, _ = baidu_parse(url, passcode, timeout, cookie)
        elif provider == "quark":
            files, meta, _ = quark_parse(url, passcode, timeout, recursive, cookie)
        else:
            files, meta, _ = xunlei_parse(url, passcode, timeout, cookie)
        result = {"ok": True, "provider": provider, "share_id": meta.get("surl")
                  or meta.get("pwd_id") or meta.get("share_id"), "files": files,
                  "total_size": sum(f["size"] for f in files)}
        if not cookie:
            if provider == "quark":
                result["login_note"] = "夸克: 当前为匿名模式, 仅可浏览文件列表; 下载需登录 Cookie(见 netdisk_login)"
            elif provider == "baidu":
                result["login_note"] = "百度: 当前为匿名模式, 下载将被限速且可能触发风控; 建议登录(见 netdisk_login)"
            elif provider == "xunlei":
                result["login_note"] = "迅雷: 当前为匿名模式, 分享解析通常需要登录态; 建议登录(见 netdisk_login)"
        return result
    except ValueError as e:
        return {"ok": False, "provider": provider, "error": str(e)}
    except Exception as e:
        return {"ok": False, "provider": provider, "error": f"未知错误: {type(e).__name__}: {e}"}


def do_download(args):
    url = (args.get("url") or "").strip()
    dest = os.path.abspath(args.get("dest") or os.path.join(os.getcwd(), "downloads"))
    flt = (args.get("filter") or "").strip()
    max_files = int(args.get("max_files") or 10)
    if not url:
        return {"ok": False, "error": "缺少 url 参数"}
    provider = detect_provider(url)
    if provider is None:
        return {"ok": False, "error": "不支持的网盘链接"}
    passcode = (args.get("passcode") or "").strip()
    cookie = cred_cookie(provider, (args.get("cookie") or "").strip())
    timeout = int(args.get("timeout") or 3600)
    os.makedirs(dest, exist_ok=True)

    # 百度 + 登录 Cookie → 优先 BaiduPCS-Go 高速通道; 失败则回退登录态 Web 直链下载
    if provider == "baidu" and cookie:
        r = baidupcs_login_and_transfer(cookie, url, passcode, dest, flt, max_files, timeout)
        if r.get("ok"):
            return r
        log("BaiduPCS-Go 转存失败, 回退登录态 Web 直链下载: " + str(r.get("error"))[:200])

    try:
        if provider == "baidu":
            files, meta, opener = baidu_parse(url, passcode, timeout, cookie)
        elif provider == "quark":
            files, meta, opener = quark_parse(url, passcode, timeout, True, cookie)
        else:
            files, meta, opener = xunlei_parse(url, passcode, timeout, cookie)

        if provider == "quark" and not cookie:
            return {"ok": False, "provider": provider,
                    "error": "夸克网盘: 匿名无法下载(官方限制), 需要登录态 Cookie。"
                             "请在浏览器登录 pan.quark.cn, F12 → Network → 复制请求的 Cookie, "
                             "用 netdisk_login 工具保存后重试"}

        def is_file_entry(f):
            if not f["isdir"]:
                return True
            # 百度单文件分享场景: isdir 标记但名字带扩展名且有大小 → 按文件处理
            name = f.get("name", "")
            return bool(f.get("size", 0) > 0 and re.search(r"\.[A-Za-z0-9]{1,6}$", name))

        targets = [f for f in files if is_file_entry(f) and matches_filter(f["name"], f.get("path", ""), flt)]
        if not targets:
            return {"ok": False, "provider": provider,
                    "error": f"没有匹配的文件(filter={flt or '全部'}, 目录已跳过)"}
        if len(targets) > max_files:
            targets = targets[:max_files]

        downloaded = []
        for f in targets:
            log(f"开始下载 {provider} {f['name']} ({fmt_size(f['size'])})")
            save_path = os.path.join(dest, clean_name(f["name"]))
            # 重名处理
            base, ext = os.path.splitext(save_path)
            n = 1
            while os.path.exists(save_path):
                save_path = f"{base}({n}){ext}"
                n += 1
            tmp_path = save_path + ".part"
            try:
                if provider == "baidu":
                    dlink = baidu_download_url(opener, meta, f["id"], timeout)
                    dl_headers = {"User-Agent": "netdisk;P2SP;2.2.60.26",
                                  "Referer": f"{BAIDU_HOST}/s/{meta['surl']}"}
                    if meta.get("cookie"):
                        dl_headers["Cookie"] = meta["cookie"]
                    size = baidu_fetch(opener, dlink, tmp_path, dl_headers, timeout)
                elif provider == "quark":
                    durl = quark_download_url(opener, f["id"], timeout)
                    size = quark_fetch(opener, durl, tmp_path, timeout)
                else:
                    raise ValueError("迅雷网盘: v1 不支持下载")
                os.replace(tmp_path, save_path)
                downloaded.append({"name": f["name"], "path": save_path, "size": size})
                log(f"完成 {f['name']} -> {save_path}")
            except ValueError as e:
                try:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                except Exception:
                    pass
                log(f"失败 {f['name']}: {e}")
                downloaded.append({"name": f["name"], "path": "", "size": 0,
                                   "error": str(e)})

        ok = any(d.get("size") for d in downloaded)
        result = {"ok": ok, "provider": provider, "downloaded": downloaded,
                  "dest": dest, "error": None if ok else
                  (downloaded[0].get("error") if downloaded else "没有文件可下载")}
        if not cookie and provider in ("baidu", "xunlei"):
            result["login_note"] = "当前为匿名下载(可能限速/风控)。建议登录该网盘后用登录态下载: 浏览器登录后 F12 复制 Cookie, 用 netdisk_login 保存"
        return result
    except ValueError as e:
        return {"ok": False, "provider": provider, "error": str(e)}
    except Exception as e:
        return {"ok": False, "provider": provider, "error": f"未知错误: {type(e).__name__}: {e}"}


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "error": "用法: netdisk_helper.py <probe|download> '<json>'"},
                         ensure_ascii=False))
        sys.exit(2)
    action = sys.argv[1]
    args = None
    if len(sys.argv) > 2:
        try:
            args = json.loads(sys.argv[2])
        except Exception:
            args = None
    if args is None:
        try:
            args = json.load(sys.stdin)
        except Exception:
            args = {}
    if action == "probe":
        out = do_probe(args)
    elif action == "download":
        out = do_download(args)
    elif action == "login":
        out = do_login(args)
    elif action == "whoami":
        out = do_whoami(args)
    elif action == "login_qr_start":
        out = do_login_qr_start(args)
    elif action == "login_qr_wait":
        out = do_login_qr_wait(args)
    elif action == "login_browser":
        out = do_login_browser(args)
    else:
        out = {"ok": False, "error": f"未知 action: {action}"}
    print(json.dumps(out, ensure_ascii=False))
    sys.exit(0 if out.get("ok") else 1)


if __name__ == "__main__":
    main()
