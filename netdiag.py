# -*- coding: utf-8 -*-
"""
校园网一键诊断
===============
双击 网络诊断.bat 即运行。收集:
  1) WiFi 关联状态与信号强度 (netsh wlan show interfaces)
  2) IP / 网关 / DNS (ipconfig /all)
  3) ping 校园门户 172.16.245.50
  4) ping 默认网关
并给出"是链路问题还是认证问题"的初步判断, 结果写入同目录 网络诊断报告.txt 并自动打开。
"""
import subprocess, os, re, time, sys

PORTAL = "172.16.245.50"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "网络诊断报告.txt")


def sh(cmd, timeout=20):
    """运行 cmd 命令, 中文 Windows 输出按 GBK 解码。"""
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        try:
            return r.stdout.decode("gbk")
        except UnicodeDecodeError:
            return r.stdout.decode("utf-8", errors="replace")
    except Exception as e:
        return "[执行失败] " + str(e)


def ping(host):
    """返回 (是否有回复, 丢包率%, 原始输出)。"""
    out = sh(["ping", "-n", "4", host])
    replied = "TTL=" in out or "时间=" in out or "time=" in out
    m = re.search(r"(\d+)%\s*丢失", out) or re.search(r"Lost\s*=\s*(\d+)", out, re.I)
    loss = int(m.group(1)) if m else (0 if replied else 100)
    return replied, loss, out


def main():
    L = []
    L.append("=" * 56)
    L.append("  校园网诊断报告   " + time.strftime("%Y-%m-%d %H:%M:%S"))
    L.append("=" * 56)

    # 1) WiFi 状态与信号
    wlan = sh(["netsh", "wlan", "show", "interfaces"])
    L.append("\n【1】WiFi 关联状态 (netsh wlan show interfaces)")
    L.append("-" * 56)
    L.append(wlan.strip() or "(无输出)")

    connected = bool(re.search(r"状态\s*[:：]\s*已连接|State\s*[:：]\s*connected", wlan, re.I))
    sig = re.search(r"信号\s*[:：]\s*(\d+)\s*%|Signal\s*[:：]\s*(\d+)\s*%", wlan, re.I)
    signal = int(sig.group(1) or sig.group(2)) if sig else None

    # 2) IP / 网关
    ipc = sh(["ipconfig", "/all"])
    L.append("\n【2】IP / 网关 / DNS (ipconfig /all)")
    L.append("-" * 56)
    L.append(ipc.strip())

    gws = re.findall(r"(?:默认网关|Default Gateway)[^\n]*?[:：]\s*([0-9a-fA-F:.]+(?:%\d+)?)",
                     ipc, re.I)
    gws = [g for g in gws if g and not g.startswith("::")]   # 去空
    apipa = "169.254." in ipc
    gw = gws[0] if gws else None

    # 3) ping 门户
    L.append("\n【3】Ping 校园门户  " + PORTAL)
    L.append("-" * 56)
    p_portal, loss_p, out_p = ping(PORTAL)
    L.append(out_p.strip())

    # 4) ping 网关
    L.append("\n【4】Ping 默认网关  " + (gw or "(未检测到)"))
    L.append("-" * 56)
    if gw:
        p_gw, loss_g, out_g = ping(gw)
        L.append(out_g.strip())
    else:
        p_gw, loss_g = False, 100
        L.append("(未从 ipconfig 检测到默认网关)")

    # 5) 判断
    L.append("\n【5】初步判断")
    L.append("-" * 56)
    if not connected:
        L.append("✗ WiFi 未关联到任何网络。请先连校园 WiFi 再跑本诊断。")
    elif p_portal:
        L.append("✓ 校园门户可达!  →  这说明链路正常, 直接用[校园网登录]程序登门户即可上网。")
    elif apipa or not gw:
        L.append("✗ 没拿到有效 IP(169.254.x) 或无网关 → 关联/DHCP 失败, 属【链路/驱动/电源】问题:")
        L.append("   · 设备管理器 → 无线网卡 → 属性 → 电源管理 → 取消「允许计算机关闭此设备」")
        L.append("   · 更新无线网卡驱动(联想/Intel 官网)")
        L.append("   · 强制锁定 2.4G 或 5G 频段分别试")
    elif signal is not None and signal < 40:
        L.append("⚠ 信号偏弱({0}%) → 链路质量差: 靠近 AP / 锁频段 / 关电源管理 / 换位置。".format(signal))
    elif p_gw:
        L.append("⚠ 网关通但门户不通: 可能不在校园网内(如手机热点), 或门户临时故障。")
        L.append("   若当前是手机热点 → 门户 172.16.245.50 是内网地址, 从热点到不了, 属正常。")
        L.append("   若确在校园 WiFi → 等几秒让关联稳定, 再用登录程序登门户。")
    else:
        L.append("✗ 网关也不通 → 网卡接口异常(驱动/电源/关联抖动):")
        L.append("   · 关网卡电源管理 + 更新驱动")
        L.append("   · 信号「满格但连上无信号」多是非对称链路(AP 听不到你), 靠近 AP 或改用网线最稳。")

    report = "\n".join(L)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)
    print("\n报告已保存: " + OUT)
    try:
        os.startfile(OUT)
    except Exception:
        pass


if __name__ == "__main__":
    main()
