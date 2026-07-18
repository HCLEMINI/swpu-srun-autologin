# SWPU 校园网自动登录客户端

> 针对 **西南石油大学** 校园网认证设计的桌面客户端。
> 解决学校更新网络后，官方客户端在**有线连接上"秒连秒断"**、无法稳定使用的问题。

纯 Python 标准库实现，提供图形界面（可打包为单文件 EXE），支持**开机自启、断连自动重连、系统托盘静默运行**。

---

## 背景

西南石油大学校园网升级后，官方拨号/认证客户端在**有线网络**下频繁出现 *连上即断、断而复连* 的"秒连秒断"现象，严重时根本无法稳定上网。

本项目**绕开官方客户端**，直接与校园网 Srun 认证门户通信完成认证，并通过常驻后台的连通性监测在掉线时自动重连，从而获得稳定的联网体验。

## 技术路线（简述）

- **接入方式**：可直接连接校园 **WiFi（移动无线）**；**插入网线并选择"移动有线"认证后，即走真正的有线网络**，带宽与稳定性均为有线品质。
- **认证原理**：逆向 Srun Portal 认证流程，复刻其加密协议，由程序直接发起认证请求，不依赖官方客户端。
  - 两步 JSONP 认证：`get_challenge`（获取令牌）→ `srun_portal`（提交登录）。
  - 加密链：`自定义 base64` + `XXTEA` + `HMAC-MD5` + `SHA1`（均与官方前端逐字节对齐验证）。
- **实现**：Python 标准库 + Tkinter GUI；PyInstaller 打包为单文件 EXE。无第三方网络框架。

## 功能特性

| 特性 | 说明 |
|------|------|
| 🔁 自动登录 / 断连重连 | 后台周期探活，掉线自动重连（失败自动退避，不刷屏） |
| 🪟 图形界面 | 状态灯、账号密码、线路选择、检测间隔、日志 |
| 📌 系统托盘 | 最小化即缩回托盘；关闭按钮真正退出；可静默启动到托盘 |
| ⏯ 开机自启 | **任务计划（用户登录时触发）**，以当前用户运行、自动缩到托盘联网（启用时需一次 UAC） |
| 🔄 多线路 | 移动无线 / 移动有线 / 电信 / 学生 / 教师，界面下拉可切 |
| 🧩 单文件 EXE | 双击即用，无需安装 Python 环境 |

## 快速开始

### 方式一：直接用打包好的 EXE（普通用户）

1. 双击 `dist\SrunAutoLogin.exe`（首次需先用方式二打包一次）。
2. 填写**学号、密码**，选择**线路**（默认「移动无线」），点【保存设置】。
3. 勾选「**开机自启（登录时）**」再保存 → 弹出一次 UAC，点【是】。
   此后**每次登录** Windows，程序自动启动并缩到托盘，静默联网 + 断连重连。

### 方式二：从源码运行 / 自行打包

```bash
# 1) 安装依赖(仅打包需要 pyinstaller/pystray/pillow; 运行 GUI 仅需标准库)
pip install pyinstaller pystray pillow

# 2) 直接运行 GUI
python srun_gui.py

# 3) 或打包为单文件 EXE(Windows 双击 build_exe.bat 亦可, 自动 UPX 压缩到约 15MB)
pyinstaller --noconfirm --onefile --windowed --clean \
    --hidden-import=pystray._win32 --collect-submodules pystray \
    --exclude-module numpy --exclude-module scipy --exclude-module pandas \
    --exclude-module matplotlib \
    --name SrunAutoLogin srun_gui.py
```

> 体积优化：`build_exe.bat` 会自动下载 UPX 压缩工具并排除未使用的重模块（numpy/scipy 等会被 Pillow 顺带拉入，实际用不到），把 EXE 从约 31MB 压到 **约 15MB**。

### 配置

复制 `config.example.json` 为 `config.json`，填入账号信息（也可在 GUI 内填写并保存）：

```json
{
  "server": "172.16.245.50",
  "ac_id": "1",
  "username": "你的学号",
  "password": "你的密码",
  "domain": "@yd",
  "check_interval": 20
}
```

> `config.json` 含个人密码，已在 `.gitignore` 中忽略，**请勿提交或外传**。

## 线路对照

GUI「线路」下拉对应以下域名后缀（登录即用户名追加该后缀）：

| 显示 | 后缀 | 说明 |
|------|------|------|
| 移动无线 | `@yd` | 校园 WiFi |
| 移动有线 | `@ydyx` | **插网线的真正有线网络** |
| 电信 | `@dxwx` | 电信线路 |
| 学生 | `@stu` | 学生账号 |
| 教师 | `@tch` | 教师账号 |

## 托盘与窗口行为

- **最小化按钮 `_`** → 缩回系统托盘（窗口与任务栏完全隐藏）。
- **关闭按钮 `X`** → **真正退出程序**。
- **左键单击托盘图标** / 右键「显示窗口」→ 唤出界面；右键「退出」→ 关闭程序。
- 命令行 `SrunAutoLogin.exe --minimized` 可静默启动到托盘；`SrunAutoLogin.exe --headless` 为无界面服务模式。

## 开机自启：登录时启动（任务计划）

开机自启由 **Windows 任务计划** 实现，触发条件为 **"用户登录时"（ONLOGON）**，以**当前用户**身份运行，登录后自动把程序缩到托盘并联网：

- 📍 **登录时触发**：桌面会话就绪后立刻启动 GUI（`--minimized` 缩到托盘），登录即可见托盘图标。
- 🔑 **当前用户运行**：无需 Windows 密码、无需 SYSTEM 权限。
- ⚠️ **创建/删除任务需一次 UAC**：本机任务计划根目录受保护，需管理员权限创建任务（弹一次 UAC，仅启用/关闭时）。

> 任务由 GUI「保存设置」时自动创建/移除；也可手动用 `schtasks` 管理，任务名 `SrunAutoLogin`。
> 另有无界面服务模式 `SrunAutoLogin.exe --headless`（不弹窗，仅联网+断连重连），供特殊场景手动使用。

## 命令行（核心库 `srun_login.py`）

```bash
python srun_login.py            # 检测并登录(已在线则跳过)
python srun_login.py --check    # 仅查询在线状态
python srun_login.py --logout   # 注销
python srun_login.py --force    # 强制重新登录
python srun_login.py --loop 30  # 常驻自愈:每 30 秒检测,断连重连
```

## 目录结构

```
├── srun_gui.py          # GUI 主程序(Tkinter + 托盘 + 后台监控)
├── srun_login.py        # 核心认证库 + 命令行
├── build_exe.bat        # 一键打包 EXE
├── config.example.json  # 配置模板(复制为 config.json 使用)
├── .gitignore
├── LICENSE
└── README.md
```

## 免责声明

- 本项目仅供**西南石油大学**在校学生个人学习与日常联网使用，旨在解决官方客户端稳定性问题。
- 程序仅执行与浏览器完全等价的认证请求，**不绕过计费、不破解任何系统**。
- 使用者需对自己的账号及合规使用负责；作者不对任何因使用本程序产生的后果承担责任。

## License

[MIT](LICENSE)
