#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Srun 校园网认证客户端 —— 「移动有线」(@ydyx) 自动登录
================================================================
逆向来源: http://172.16.245.50/srun_portal_pc (Srun Portal v2.00.20211028)
登录方式: 移动有线 = 用户名后追加域名后缀 "@ydyx"

认证流程(两步 JSONP):
  1) GET /cgi-bin/get_challenge?username=<用户名@ydyx>&ip=<ip>
        -> 返回 {challenge, client_ip, error:"ok", ...}
  2) GET /cgi-bin/srun_portal?action=login&username=<用户名@ydyx>
        &password={MD5}<HMAC-MD5(password,challenge)>
        &ac_id=&ip=&chksum=<SHA1>&info=<{SRBX1}+自定义base64(XXTEA(json))>
        &n=200&type=1&os=&name=&double_stack=0
        -> 返回 {error:"ok", ...}

关键加密细节(全部从服务器原始 JS 精确复刻):
  * 自定义 base64 字母表: "LVoJPiCN2R8G90yg+hmFHuacZ1OWMnrsSTXkYpUq/3dlbfKwv6xztjI7DeBE45QA"
  * xEncode = XXTEA, delta = 0x9E3779B9
  * info 内层 JSON 字段顺序: username, password, ip, acid, enc_ver("srun_bx1")
  * chkstr = challenge+(user+@ydyx)+challenge+hmd5+challenge+ac_id+challenge+ip
             +challenge+"200"+challenge+"1"+challenge+info
  * chksum = sha1(chkstr).hex();  hmd5 = hmac_md5(key=challenge, msg=password).hex()

纯标准库实现, 无任何第三方依赖。
用法:
  python srun_login.py             # 检测并登录(若已在线则跳过)
  python srun_login.py --check     # 仅查询当前是否在线
  python srun_login.py --logout    # 注销
  python srun_login.py --force     # 强制重新登录
  python srun_login.py --loop 30   # 每 30 秒检测一次, 断网自动重连(常驻)
首次运行若缺少 config.json 会自动生成模板, 请填入账号密码后再次运行。
"""

import hashlib
import hmac
import json
import os
import sys
import time
import logging
import platform
import urllib.request
import urllib.parse
import urllib.error

# Windows 控制台默认 GBK, 强制 UTF-8 以正确显示中文日志
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# --------------------------------------------------------------------------- #
#  常量(来自服务器 JS, 切勿修改)
# --------------------------------------------------------------------------- #
SRUN_BASE64_ALPHA = "LVoJPiCN2R8G90yg+hmFHuacZ1OWMnrsSTXkYpUq/3dlbfKwv6xztjI7DeBE45QA"
SRUN_PADCHAR = "="
XXTEA_DELTA = 0x9E3779B9
MASK32 = 0xFFFFFFFF
N_DEFAULT = 200        # JS: var n = 200
TYPE_DEFAULT = 1       # JS: var type = 1
ENC_VER = "srun_bx1"   # JS: var enc = "s"+"run"+"_bx1"

# 连通性探测端点(Windows NCSI, 联网时返回固定字符串, 被强制门户劫持时则不同)
PROBE_URL = "http://www.msftconnecttest.com/connecttest.txt"
PROBE_MAGIC = "Microsoft Connect Test"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("srun")


# --------------------------------------------------------------------------- #
#  Srun 加密原语 —— 精确复刻 jquery.srun.portal.js / all.min.js
# --------------------------------------------------------------------------- #
def _s(a: str, append_len: bool):
    """对应 JS 的 s(a, b): 字符串 -> uint32 数组(小端), b=True 时末尾追加原始字节长度。"""
    c = len(a)
    v = []
    for i in range(0, c, 4):
        b0 = ord(a[i]) if i < c else 0
        b1 = ord(a[i + 1]) if i + 1 < c else 0
        b2 = ord(a[i + 2]) if i + 2 < c else 0
        b3 = ord(a[i + 3]) if i + 3 < c else 0
        v.append((b0 | (b1 << 8) | (b2 << 16) | (b3 << 24)) & MASK32)
    if append_len:
        v.append(c & MASK32)
    return v


def _l(a) -> str:
    """对应 JS 的 l(a, false): uint32 数组 -> 字符串(按字节还原)。"""
    out = []
    for x in a:
        out.append(chr(x & 0xFF))
        out.append(chr((x >> 8) & 0xFF))
        out.append(chr((x >> 16) & 0xFF))
        out.append(chr((x >> 24) & 0xFF))
    return "".join(out)


def x_encode(text: str, key: str) -> str:
    """XXTEA 加密, 与 JS xEncode(str, key) 逐位等价。"""
    if text == "":
        return ""
    v = _s(text, True)
    k = _s(key, False)
    if len(k) < 4:
        k = k + [0] * (4 - len(k))          # JS: k.length = 4 (空位当 0)

    n = len(v) - 1
    z = v[n]
    y = v[0]
    d = 0
    q = 6 + 52 // (n + 1)

    for _ in range(q):
        d = (d + XXTEA_DELTA) & MASK32
        e = (d >> 2) & 3
        for p in range(n):
            y = v[p + 1]
            term1 = ((z >> 5) ^ ((y << 2) & MASK32)) & MASK32
            term2 = (((y >> 3) ^ ((z << 4) & MASK32)) ^ (d ^ y)) & MASK32
            term3 = (k[(p & 3) ^ e] ^ z) & MASK32
            m = (term1 + term2 + term3) & MASK32
            z = v[p] = (v[p] + m) & MASK32
        p = n                                  # JS for 循环结束后 p == n
        y = v[0]
        term1 = ((z >> 5) ^ ((y << 2) & MASK32)) & MASK32
        term2 = (((y >> 3) ^ ((z << 4) & MASK32)) ^ (d ^ y)) & MASK32
        term3 = (k[(p & 3) ^ e] ^ z) & MASK32
        m = (term1 + term2 + term3) & MASK32
        z = v[n] = (v[n] + m) & MASK32
    return _l(v)


def srun_base64_encode(s: str) -> str:
    """Srun 自定义字母表的 base64 编码, 与 jQuery.base64.encode 逐字符等价。"""
    if len(s) == 0:
        return s
    out = []
    imax = len(s) - (len(s) % 3)
    i = 0
    while i < imax:
        b10 = (ord(s[i]) << 16) | (ord(s[i + 1]) << 8) | ord(s[i + 2])
        out.append(SRUN_BASE64_ALPHA[b10 >> 18])
        out.append(SRUN_BASE64_ALPHA[(b10 >> 12) & 63])
        out.append(SRUN_BASE64_ALPHA[(b10 >> 6) & 63])
        out.append(SRUN_BASE64_ALPHA[b10 & 63])
        i += 3
    rem = len(s) - imax
    if rem == 2:
        b10 = (ord(s[i]) << 16) | (ord(s[i + 1]) << 8)
        out.append(SRUN_BASE64_ALPHA[b10 >> 18])
        out.append(SRUN_BASE64_ALPHA[(b10 >> 12) & 63])
        out.append(SRUN_BASE64_ALPHA[(b10 >> 6) & 63])
        out.append(SRUN_PADCHAR)
    elif rem == 1:
        b10 = ord(s[i]) << 16
        out.append(SRUN_BASE64_ALPHA[b10 >> 18])
        out.append(SRUN_BASE64_ALPHA[(b10 >> 12) & 63])
        out.append(SRUN_PADCHAR)
        out.append(SRUN_PADCHAR)
    return "".join(out)


def hmac_md5_hex(message: str, key: str) -> str:
    """对应 JS 的 pwd = md5(password, challenge) = CryptoJS.HmacMD5(msg, key)。"""
    return hmac.new(key.encode("utf-8"), message.encode("utf-8"), hashlib.md5).hexdigest()


def sha1_hex(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
#  HTTP 工具
# --------------------------------------------------------------------------- #
def _http_get(url: str, timeout: float = 6.0) -> str:
    parsed = urllib.parse.urlparse(url)
    referer = "{}://{}/srun_portal_pc?ac_id=1&theme=basic".format(
        parsed.scheme, parsed.netloc)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0 Safari/537.36",
            "Referer": referer,
            "Accept": "*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        # 服务器可能以 GBK 返回中文提示
        raw = resp.read()
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _parse_srun_response(text: str) -> dict:
    """兼容 JSONP 包装(含 callback)与纯 JSON 两种返回。

    注意: Srun 的 CGI 在缺少 callback 参数时只回 "ok" (纯文本), 不是 JSON。
    因此调用方必须带上 callback 参数, 这里才能拿到 callback({...}) 形式的 JSONP。
    """
    text = text.strip()
    if text.startswith("{"):
        return json.loads(text)
    # 形如 callback({...}); 或 jQuery_xxx({...});
    if "(" in text and ")" in text:
        body = text[text.index("(") + 1: text.rindex(")")]
        return json.loads(body)
    raise RuntimeError("无法解析服务器响应(非 JSON/JSONP): {!r}".format(text[:200]))


# --------------------------------------------------------------------------- #
#  连通性探测
# --------------------------------------------------------------------------- #
def is_online(timeout: float = 4.0) -> bool:
    """通过 NCSI 探测当前是否已联网(可访问外网)。"""
    try:
        body = _http_get(PROBE_URL, timeout=timeout)
        return body.strip() == PROBE_MAGIC
    except Exception:
        return False


# --------------------------------------------------------------------------- #
#  认证客户端
# --------------------------------------------------------------------------- #
class SrunClient:
    def __init__(self, server: str, ac_id: str, username: str, password: str,
                 domain: str = "@ydyx"):
        self.base = "http://{}".format(server.rstrip("/"))
        self.ac_id = str(ac_id)
        self.username = username.strip()
        self.password = password
        self.domain = domain if domain else ""
        self.full_username = self.username + self.domain
        self.os_name = platform.system() or "Windows"
        self.platform_name = "PC"

    @staticmethod
    def _new_callback() -> str:
        # 模拟 jQuery JSONP 的回调名; 服务器据此把响应包成 callback({...})
        return "srun{}".format(int(time.time() * 1000))

    # ---- get_challenge ---- #
    def get_challenge(self, ip: str = "") -> dict:
        params = {
            "callback": self._new_callback(),
            "username": self.full_username,
            "ip": ip,
        }
        url = "{}/cgi-bin/get_challenge?{}".format(
            self.base, urllib.parse.urlencode(params))
        text = _http_get(url)
        data = _parse_srun_response(text)
        if data.get("error") != "ok":
            raise RuntimeError("获取 challenge 失败: {}".format(
                data.get("error") or data))
        return data

    # ---- login ---- #
    def login(self) -> dict:
        challenge_resp = self.get_challenge()
        token = challenge_resp["challenge"]
        ip = challenge_resp.get("client_ip", "") or ""

        hmd5 = hmac_md5_hex(self.password, token)

        info_obj = {
            "username": self.full_username,
            "password": self.password,
            "ip": ip,
            "acid": self.ac_id,
            "enc_ver": ENC_VER,
        }
        # JS JSON.stringify 无空格; 字段顺序保持上面顺序
        info_json = json.dumps(info_obj, separators=(",", ":"), ensure_ascii=False)
        info_field = "{SRBX1}" + srun_base64_encode(x_encode(info_json, token))

        chkstr = (
            token + self.full_username
            + token + hmd5
            + token + self.ac_id
            + token + ip
            + token + str(N_DEFAULT)
            + token + str(TYPE_DEFAULT)
            + token + info_field
        )
        chksum = sha1_hex(chkstr)

        params = {
            "callback": self._new_callback(),
            "action": "login",
            "username": self.full_username,
            "password": "{MD5}" + hmd5,
            "ac_id": self.ac_id,
            "ip": ip,
            "chksum": chksum,
            "info": info_field,
            "n": N_DEFAULT,
            "type": TYPE_DEFAULT,
            "os": self.os_name,
            "name": self.platform_name,
            "double_stack": 0,
        }
        url = "{}/cgi-bin/srun_portal?{}".format(
            self.base, urllib.parse.urlencode(params))
        text = _http_get(url)
        return _parse_srun_response(text)

    # ---- logout ---- #
    def logout(self, ip: str = "") -> dict:
        challenge_resp = self.get_challenge()
        ip = ip or challenge_resp.get("client_ip", "") or ""
        # 注销也走 srun_portal, action=logout, 带 challenge 相关校验
        info_obj = {
            "username": self.full_username,
            "ip": ip,
            "acid": self.ac_id,
            "enc_ver": ENC_VER,
        }
        token = challenge_resp["challenge"]
        info_field = "{SRBX1}" + srun_base64_encode(
            x_encode(json.dumps(info_obj, separators=(",", ":")), token))
        chkstr = (
            token + self.full_username
            + token + self.ac_id
            + token + ip
            + token + info_field
        )
        params = {
            "callback": self._new_callback(),
            "action": "logout",
            "username": self.full_username,
            "ac_id": self.ac_id,
            "ip": ip,
            "chksum": sha1_hex(chkstr),
            "info": info_field,
            "n": N_DEFAULT,
            "type": TYPE_DEFAULT,
        }
        url = "{}/cgi-bin/srun_portal?{}".format(
            self.base, urllib.parse.urlencode(params))
        text = _http_get(url)
        return _parse_srun_response(text)


# --------------------------------------------------------------------------- #
#  配置加载
# --------------------------------------------------------------------------- #
CONFIG_TEMPLATE = {
    "server": "172.16.245.50",
    "ac_id": "1",
    "username": "你的学号/账号",
    "password": "你的密码",
    "domain": "@ydyx",
}


def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(CONFIG_TEMPLATE, f, ensure_ascii=False, indent=2)
        log.error("已生成配置模板: %s", CONFIG_PATH)
        log.error("请打开它填入账号(username)、密码(password)后重新运行。")
        sys.exit(0)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    missing = [k for k in ("username", "password") if not cfg.get(k)
               or cfg[k].startswith("你的")]
    if missing:
        log.error("config.json 中 %s 尚未填写, 请先编辑 %s",
                  ",".join(missing), CONFIG_PATH)
        sys.exit(0)
    cfg.setdefault("server", "172.16.245.50")
    cfg.setdefault("ac_id", "1")
    cfg.setdefault("domain", "@ydyx")
    return cfg


# --------------------------------------------------------------------------- #
#  主流程
# --------------------------------------------------------------------------- #
def do_login(client: SrunClient, force: bool) -> int:
    """返回 0=在线, 1=已成功登录, 2=登录失败。"""
    if not force and is_online():
        log.info("当前已联网, 跳过登录。")
        return 0
    try:
        resp = client.login()
    except Exception as e:
        log.error("登录请求异常: %s", e)
        return 2

    err = resp.get("error")
    if err == "ok":
        # 二次校验: 确认真的联网
        if is_online(timeout=6.0):
            log.info("✅ 登录成功 (移动有线 @ydyx)")
            return 1
        log.info("✅ 服务器返回登录成功 (error=ok)")
        return 1
    # 已在线: ip_already_online_error / user_already_online_error
    msg = resp.get("error_msg") or resp.get("error") or ""
    log.warning("登录返回: error=%s, message=%s", err, msg)
    return 2


def do_logout(client: SrunClient) -> int:
    try:
        resp = client.logout()
    except Exception as e:
        log.error("注销请求异常: %s", e)
        return 2
    log.info("注销返回: %s", resp.get("error"))
    return 0


def main():
    args = sys.argv[1:]
    loop_mode = False
    loop_interval = 30
    force = False
    for a in args:
        if a == "--check":
            online = is_online()
            log.info("当前状态: %s", "已联网 ✅" if online else "未联网 ❌")
            sys.exit(0 if online else 1)
        elif a == "--logout":
            cfg = load_config()
            client = SrunClient(**{k: cfg[k] for k in
                                   ("server", "ac_id", "username", "password", "domain")})
            sys.exit(do_logout(client))
        elif a == "--force":
            force = True
        elif a.startswith("--loop"):
            loop_mode = True
            if "=" in a:
                loop_interval = max(5, int(a.split("=", 1)[1]))
            try:
                idx = args.index("--loop")
                if idx + 1 < len(args) and args[idx + 1].isdigit():
                    loop_interval = max(5, int(args[idx + 1]))
            except ValueError:
                pass

    cfg = load_config()
    client = SrunClient(**{k: cfg[k] for k in
                           ("server", "ac_id", "username", "password", "domain")})

    if not loop_mode:
        code = do_login(client, force)
        sys.exit(0 if code in (0, 1) else 1)

    # 常驻自愈模式: 断网自动重连
    log.info("进入自愈模式, 每 %d 秒检测一次 (Ctrl+C 退出)", loop_interval)
    while True:
        try:
            if is_online():
                log.debug("在线, 无需操作")
            else:
                log.warning("检测到断网, 尝试重连...")
                do_login(client, force=True)
        except KeyboardInterrupt:
            log.info("已退出自愈模式")
            break
        except Exception as e:
            log.error("循环异常: %s", e)
        time.sleep(loop_interval)


if __name__ == "__main__":
    main()
