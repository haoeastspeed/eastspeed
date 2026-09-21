# 东方神速 East Speed

[![Build](https://github.com/haoeastspeed/eastspeed/actions/workflows/build.yml/badge.svg)](https://github.com/haoeastspeed/eastspeed/actions/workflows/build.yml)

> 中文名 **东方神速**，对外英文名 **East Speed**；程序/进程内部名 **DongFangSpeed**（用于安装目录、`%APPDATA%`、注册表，规避中文路径兼容问题）。

一个用 **Python + PyQt5 自研下载引擎**的 Windows 多线程下载管理器，目标是对标并超越 IDM（Internet Download Manager）的核心体验：多连接动态分段、断点续传、队列调度、浏览器接管、BT/磁力、整站抓取。下载引擎为纯 Python 实现（不依赖 aria2/libcurl），代码可读、可测、可改造。

## 开源与构建

- **源代码仓库**：<https://github.com/haoeastspeed/eastspeed>（GPL-3.0 许可证，欢迎 Issue / PR）。
- **持续集成**：推送到 `main` 或提交 `v*` 标签时，GitHub Actions 在 Windows + Python 3.12 上自动安装依赖、跑测试、用 PyInstaller 打包单文件/便携版并执行 `--selftest`，定义见 [`.github/workflows/build.yml`](.github/workflows/build.yml)。
- **代码签名**：正式 Release 计划经 SignPath Foundation 的免费开源证书签名（私钥 HSM 托管、受 Windows 信任），接入与发布步骤见 [SIGNING.md](SIGNING.md)。

## 三种发行形态（普通用户免装 Python）

仓库 `dist/` 提供三种即用形态，**均已内置 libtorrent（BT/磁力）与 h2（HTTP/2），最终用户无需安装任何 Python 或运行库**：

| 形态 | 文件 | 说明 |
|---|---|---|
| 安装包 | `DongFangSpeed-Setup-1.0.0.exe` | IDM 式 Inno Setup 向导：开始菜单/桌面快捷方式、卸载项，装到 `Program Files` |
| 便携版 | `东方神速-便携版.zip`（解压为 `DongFangSpeed/`） | 单目录绿色版，内含 `browser_extension/`，拷到 U 盘即用，启动最快 |
| 单文件版 | `DongFangSpeed.exe` | 仅一个 exe，首次运行自动解压；浏览器扩展释放到**固定目录** `%LOCALAPPDATA%\DongFangSpeed\browser_extension`，浏览器只需加载一次即永久生效，启动稍慢 |

> 从源码自行打包见文末「打包为 exe / 安装包」。

## 功能清单

| 能力 | 状态 | 说明 |
|---|---|---|
| 多连接分段下载（最多 32 连接，默认 16） | ✅ | HTTP/HTTPS，`Range` 请求并行 |
| 动态分段 + 连接复用 | ✅ | 连接空闲即从最大未完成区间尾部领取新块，同一 TCP 连接连续下载 |
| 动态自适应分块 + 工作窃取 | ✅ | 守护线程按实时速度调整运行时块大小（目标 0.25s/块，clamp 1–4 倍基础块）；某连接拖慢在途块尾部时，空闲连接从最慢块尾部劈块接管，随机断流/高抖动下也不留尾部缺口 |
| 预取双缓冲 | ✅ | 每条连接的网络接收与磁盘写入用有界队列解耦（预读 16×64KiB、队列满自动背压），慢盘或杀软扫描不再拖慢网络吞吐，多连接内存占用有上限 |
| 多镜像源聚合 | ✅ | 新建任务「高级选项 → 镜像源」可填同一文件的多个 URL；连接粘性绑定源、故障自动切换并熔断、慢源持有的块被快源工作窃取，从而聚合多镜像带宽；自动剔除大小不一致或不支持断点续传的镜像 |
| 断点续传 | ✅ | 块位图持久化到 `*.pdlmeta`，暂停、断电、程序崩溃后可续传 |
| 重启恢复任务列表 | ✅ | 任务索引保存在 `%APPDATA%/DongFangSpeed/tasks.json` |
| 服务器不支持 Range 自动降级 | ✅ | 自动回退单线程整文件下载 |
| 失败重试（指数退避） | ✅ | 默认 10 次，429/5xx/断连自动重连 |
| 多任务队列与并发控制 | ✅ | 默认同时下载 3 个任务，可配置 |
| 全局限速 | ✅ | KB/s 粒度，所有连接共享配额 |
| 代理 / Basic / NTLM / PAC / Cookie / Referer / UA | ✅ | 独立「代理」选项卡：固定代理（HTTP/SOCKS）+ Basic 与 NTLM/Negotiate 域账号认证、PAC 自动配置脚本（Windows 经 WinHTTP 解析）；另支持单任务高级选项。Digest 认证、FTP 经 HTTP CONNECT 隧道暂不支持 |
| 系统证书库 / 内网 HTTPS | ✅ | 默认经 truststore 使用 **Windows 受信任根证书库**（含企业 AD/组策略推送的内网 CA），访问公司内网 HTTPS/云盘不再报 `unable to get local issuer certificate`；「设置 → 高级」可一键「忽略 HTTPS 证书错误」兜底自签站点，证书失败给出中文解决指引 |
| 剪贴板监听 | ✅ | 复制下载链接时弹窗询问（常见文件扩展名与 .m3u8 匹配） |
| 系统托盘 | ✅ | 关闭最小化到托盘、全部暂停/继续、退出 |
| 单实例运行 | ✅ | 全局只允许一个进程：再次启动/双击下载链接时不会开第二个窗口，而是自动把已运行的主窗口从最小化/托盘恢复并前置，避免多进程抢占桥接端口、配置与下载任务 |
| Chrome/Edge/Firefox 浏览器扩展 | ✅ | Chromium 版 Manifest V3、Firefox 版 MV2：自动接管浏览器下载、请求头/Cookie 透传、m3u8/mp4 媒体嗅探、网页视频悬浮「下载视频」按钮、手动下载，见下文 |
| 浏览器扩展本地桥接 | ✅ | `127.0.0.1` HTTP 接口 + Token 鉴权 + CORS，扩展经 `/pair` 自动配对免填 Token，离线不劫持、接管弹确认窗 |
| m3u8 / HLS 视频下载 | ✅ | 多码率自动选最高、AES-128 解密、fMP4/TS、多线程分片、断点续传，见下文 |
| 卡死连接健康监测 | ✅ | 连接连续 N 秒收不到数据即强制断开，该分段回池被其他连接抢占重下（默认 15 秒，可调） |
| 磁盘空间预检 | ✅ | 下载前核对剩余空间，不足直接报错，不再下到一半盘满 |
| 站点连接数例外 | ✅ | 按域名单独设置连接数（`域名=连接数`，支持子域后缀匹配），对付限同 IP 连接数的站点 |
| 自动分类整理 | ✅ | 开关开启后按类型存入 视频/音频/文档/程序/压缩包 子文件夹（HLS 归视频） |
| 批量下载 | ✅ | 一次粘贴多条链接（每行一条），批量建任务，可全部开始或全部稍后 |
| 新建即探测文件名/大小 | ✅ | 粘贴链接后后台线程探测响应头，自动同步服务器文件名（Content-Disposition/URL）、大小、是否支持多线程断点，弹窗实时显示且不卡界面；探测失败不影响下载 |
| 列表列自定义 | ✅ | 各列宽度可自由拖动（宽度自动记忆）、列标题可拖动重排；右键表头勾选显示/隐藏列（文件名列固定显示），显隐选择自动记忆，菜单可一键重置 |
| 实时速度曲线 | ✅ | 主窗口底部自绘最近 120 秒总速度折线图 |
| 完成通知与提示音 | ✅ | 任务完成/失败弹出系统托盘通知，可选提示音，可在设置中关闭 |
| 计划任务 | ✅ | 每天定时「全部开始 / 全部暂停」（HH:MM，00:00 表示不设置） |
| 开机自动启动 | ✅ | 「设置 → 界面与集成」勾选后写入当前用户注册表 Run 键（无需管理员），开机以 `--minimized` 后台驻留托盘、不弹主窗口 |
| 全部完成后动作 | ✅ | 「设置 → 计划任务」可选所有任务完成后：不操作 / 退出程序 / 睡眠 / 休眠 / 关机；执行前弹 60 秒倒计时，可随时取消（关机另留 30 秒系统缓冲，`shutdown /a` 可撤销）；存在暂停/失败任务时不触发 |
| 一键清除已完成 | ✅ | 菜单「下载 → 清除全部已完成任务」，批量清空已完成项并保留文件（对应 IDM Delete Completed） |
| ffmpeg 路径可配置 | ✅ | 设置中指定 ffmpeg，或留空在 PATH 中查找 |
| IDM 风格界面 | ✅ | 彩色大图标工具栏、左侧分类栏、文件类型彩色图标、绿色进度条、底部速度曲线 |
| FTP / FTPS 下载 | ✅ | 匿名/账号密码、显式 AUTH TLS 加密、多连接分段、断点续传、限速、自动分类（FTPS 每分块独立加密连接以兼容服务器限制） |
| HTTP/2 下载 | ✅ | 可选开启（需 `httpx[h2]`），多路复用/头部压缩，服务器不支持时自动回退 HTTP/1.1，多连接 Range 照常 |
| 网页资源抓取 | ✅ | 菜单「任务 → 下载网页资源」，有限深度（本页 / +1 层）、默认仅同站、尊重 robots.txt，按类型勾选后批量下载 |
| MD5 / SHA 校验 | ✅ | 新建任务可选 MD5/SHA-1/SHA-256/SHA-512 并填期望值，完成后自动比对，不符即报错并保留文件；右键可复制实际哈希 |
| 真实占盘式预分配 | ✅ | 可选开关；Windows 经 `FSCTL_SET_ZERO_DATA` 让内核零填充并真实占满簇，默认仍用稀疏文件以省空间 |
| 完成后杀毒钩子 | ✅ | 默认调用系统自带 Windows Defender（`MpCmdRun`）扫描，检出威胁自动删除并报错；无扫描器则静默跳过 |
| blob: 流接管 | ✅ 实验性 | 扩展钩子 `URL.createObjectURL`，在弹窗列出页面生成的普通 Blob 并经 `/add-blob` 落盘；限制见下文 |
| BT / 磁力 | ✅ | 基于 libtorrent 2.x；**发行版 exe 已内置，开箱即用**。DHT/PEX/LSD/UPnP/NAT-PMP、端口范围、DHT 引导节点；添加本地多文件 `.torrent` 时可勾选要下载的文件（全选/只选视频），磁力链接默认全量下载。源码运行需 Python 3.11/3.12（3.13/3.14 暂无官方预编译 wheel，缺库时界面给出明确提示，不影响其他功能） |
| 自动更新 | ✅ | 帮助菜单「检查更新」+ 启动静默检查；读取可配置的 `latest.json`，后台下载安装包并做 SHA256 校验，确认后静默覆盖安装。更新源留空则不发起任何请求，见下文 |
| 代码签名 | ✅ 工具链 | 提供 `tools/sign_exe.py`（signtool + RFC3161 时间戳），构建时设 `DFS_PFX`/`DFS_PFX_PASSWORD` 即自动签名；正式分发需 OV/EV 证书，见下文 |
| HTTP/3 (QUIC) | 🚧 路线图 | Python 生态（aioquic）尚不成熟，暂不启用 |

## 为什么多连接能加速（以及什么时候不能）

普通下载开 1 条 TCP 连接从头读到尾。很多服务器（或中间链路）会对**单条连接**限速，此时并行开 N 条连接、各下载文件的不同字节区间，最后拼接，即可获得数倍速度。

IDM 自称的关键技术是**动态分段**：不是下载前固定切 N 段，而是连接一旦空闲，就找到当前最大的未完成区间劈分接手，并且**复用已有连接**、不再重新握手。

本项目的等价实现（`core/ranges.py`）：

1. 文件按固定块（默认 2 MiB）划分，维护两个区间集合：`completed`（已完成，持久化）与 `allocated`（已被某连接领取）。
2. worker 完成当前块后，在「最大连续未完成区间」的**尾部领取一个块**，留在池中的前部区间可继续被其他连接劈分。
3. 每个 worker 持有独立文件句柄，在自己的字节区间内 `seek + write`，互不干扰；HTTP 连接通过 `requests.Session` keep-alive 复用。
4. 小块粒度保证快连接持续多领、慢连接不长期霸占大段——这正是动态分段相对"固定切 N 段"的优势。

**速度上限的诚实说明**：

- 若服务器对**单 IP 总带宽**限速，或你的宽带本身已跑满，任何多线程工具都无法再提速；
- 若服务器不支持 `Range`（返回 200 而非 206），只能单线程下载；
- 实测：对"每连接限速"的服务器，8 连接可聚合出约 **5.8 倍**速度（见测试）；对单连接不限速的镜像站，提升有限（实测 1.4 倍，瓶颈在本机带宽），这与 IDM 的行为一致。

## 项目结构

```
pydownloader/
├── main.py                  # 启动入口
├── run.bat                  # Windows 双击启动
├── build_exe.bat            # PyInstaller 打包脚本
├── requirements.txt
├── core/                    # 纯 Python 下载引擎，不依赖 Qt
│   ├── config.py            # 全局设置与持久化
│   ├── probe.py             # 探测大小/Range 支持/文件名（GET bytes=0-0）
│   ├── ranges.py            # 区间集合 + 动态分段分配器
│   ├── speed.py             # 滑动窗口测速 + 限速器
│   ├── category.py          # 文件类型分类、自动归类目录、域名匹配
│   ├── task.py              # 单任务状态机、worker 线程、卡死看门狗、磁盘预检、meta 持久化
│   ├── task_options.py      # 单任务覆盖参数
│   ├── hls.py               # m3u8/HLS：解析、AES-128 解密、分片下载、合并
│   ├── ftp_task.py          # FTP/FTPS：URL 解析、AUTH TLS、REST 分段、断点续传
│   ├── http2.py             # httpx[h2] 兼容 requests 子集的 HTTP/2 会话薄封装
│   ├── torrent.py           # BT/磁力（libtorrent 可选适配，缺库友好报错）
│   ├── grabber.py           # 网页资源抓取：有限深度、同站过滤、robots、类型筛选
│   ├── checksum.py          # MD5/SHA-1/SHA-256/SHA-512 流式哈希与校验
│   ├── prealloc.py          # Windows 真实占盘预分配（FSCTL_SET_ZERO_DATA）
│   ├── antivirus.py         # Windows Defender 杀毒钩子（MpCmdRun）
│   ├── proxy_support.py     # 代理：Basic/NTLM(Negotiate) 认证、PAC（WinHTTP）解析
│   ├── updater.py           # 自动更新：latest.json、流式下载、SHA256、启动安装包
│   ├── tls.py               # 系统证书库（truststore）挂载、证书错误识别与中文提示
│   ├── engine.py            # 任务队列、并发调度、重启恢复（按协议分发任务）
│   ├── bridge.py            # 浏览器扩展本地 HTTP 桥接（/ping、/pair 自动配对、/add、/add-blob + Token）
│   ├── branding.py          # 品牌常量（东方神速 / DongFangSpeed、版本、图标路径）
│   ├── app_paths.py         # 冻结/源码路径解析、浏览器扩展释放
│   ├── ext_install.py       # 扩展一键安装：稳定目录释放、浏览器探测、Edge 命令行加载与桌面快捷方式
│   ├── autostart.py         # 开机自启（Windows 当前用户注册表 Run 键，--minimized 后台驻留）
│   ├── power.py             # 全部完成后电源动作：关机/休眠/睡眠（shutdown.exe / SetSuspendState）
│   └── errors.py
├── gui/                     # PyQt5 界面
│   ├── app.py               # 装配：引擎/信号桥/托盘/剪贴板/桥接
│   ├── main_window.py       # 主窗口、分类栏、表格、菜单、工具栏、通知、计划任务
│   ├── add_dialog.py        # 新建下载对话框（后台探测文件名/大小/断点、开始/稍后/校验）
│   ├── batch_dialog.py      # 批量下载对话框（每行一条链接）
│   ├── grab_dialog.py       # 网页资源抓取对话框（扫描/类型/深度/勾选批量下载）
│   ├── settings_dialog.py   # 设置（六选项卡：下载/高级/提醒整理/计划/界面集成/代理）
│   ├── ext_install_dialog.py # 浏览器扩展一键安装向导（Edge 一键加载/Chrome 引导侧载）
│   ├── torrent_files_dialog.py # BT 多文件种子勾选对话框（全选/只选视频）
│   ├── update_dialog.py     # 自动更新：后台检查/下载进度/校验/安装提示
│   ├── speedchart.py        # 底部实时总速度曲线（QPainter 自绘）
│   ├── models.py            # 任务表格模型 + 绿色进度条委托
│   ├── theme.py             # IDM 风格 QSS 浅色主题
│   ├── icons.py             # QPainter 运行时绘制的彩色矢量图标与文件类型图标
│   ├── tray.py              # 系统托盘
│   └── utils.py             # 格式化（分类复用 core.category）
├── browser_extension/       # Chrome/Edge 扩展（Manifest V3）
│   ├── manifest.json
│   ├── background.js        # 下载接管 + webRequest 媒体嗅探 + blob 管理
│   ├── content_hook.js      # MAIN world：钩子 URL.createObjectURL 记录 blob
│   ├── content_bridge.js    # ISOLATED world：读取 blob 并 POST /add-blob
│   ├── content_video.js     # ISOLATED world：网页 <video> 悬浮「下载视频」按钮
│   ├── popup.html / .css / .js   # 开关、媒体/blob 列表、手动下载、连接配置
│   └── icons/               # 16/32/48/128 图标（tools/make_brand_icons.py 生成）
├── assets/                  # 品牌图标 app.ico（多尺寸）/ app.png
├── installer/
│   └── dongfangspeed.iss    # Inno Setup 安装包脚本
├── tools/
│   ├── make_brand_icons.py  # Pillow 生成应用与扩展品牌图标
│   ├── build_exe.py         # PyInstaller 打包（portable / single / both，含可选自动签名）
│   ├── build_firefox_ext.py # Chromium MV3 扩展转换为 Firefox MV2 并打 zip
│   ├── sign_exe.py          # signtool 代码签名（PFX + RFC3161 时间戳）
│   ├── e2e_ext.py           # 扩展端到端：真实 Edge 加载扩展→自动配对→onCreated 接管→下载校验
│   ├── e2e_offline.py       # 程序离线时扩展不劫持、浏览器原生下载正常
│   ├── e2e_video_btn.py     # 网页视频悬浮按钮真机端到端
│   └── diag_ext.py          # CDP 诊断扩展后台（配对/下发/下载记录）
└── tests/
    ├── test_ranges.py       # 区间与分配器单元测试
    ├── test_e2e.py          # 端到端：完整性/暂停续传/崩溃恢复/降级/提速
    ├── test_bridge.py       # 桥接：ping / Token 鉴权 / 提交下载 / 非法 URL
    ├── test_hls.py          # HLS：明文/多码率/AES-128/fMP4/续传/直播拒绝
    ├── test_enhance.py      # 增强：卡死抢占/磁盘预检/站点例外/自动分类(HTTP+HLS)
    ├── test_ftp.py          # FTP/FTPS：URL 解析/匿名多连接/认证/暂停续传/TLS
    ├── test_http2.py        # HTTP/2：hypercorn h2 服务器多连接 Range + 版本断言
    ├── test_grabber.py      # 整站抓取：深度/同站过滤/类型/robots
    ├── test_round4.py       # 校验/预分配/杀毒/BT 缺库降级/blob 桥接/协议分发
    ├── test_torrent.py      # BT/磁力：本机做种 .torrent/磁力真实下载、文件勾选、会话释放
    ├── test_proxy.py        # 代理：Basic/NTLM 407 握手、PAC 指令与 WinHTTP 解析
    ├── test_updater.py      # 自动更新：版本比较、清单、下载、SHA256 校验
    ├── test_tls.py          # 系统证书库/忽略证书开关：自签 HTTPS 默认拒绝+提示、关闭可下载
    ├── test_gui_selection.py# 列表每秒刷新不丢选中、错误任务按钮可用
    ├── test_round12.py      # 开机自启注册表、完成后电源动作、清除已完成、扩展固定目录统一
    ├── test_live.py         # 真实外网下载验证（手动运行）
    ├── smoke_gui.py         # GUI 实例化与截图（--real 使用真实桌面）
    ├── range_server.py      # 支持 Range、可按连接限速的测试服务器
    ├── hls_server.py        # 内存 HLS 测试服务器（TS/AES/master/fMP4/直播）
    ├── ftp_server.py        # pyftpdlib 匿名/认证/TLS 测试服务器（自签证书）
    └── h2_server.py         # hypercorn HTTP/2 + Range 测试服务器（自签 TLS）
```

## 快速开始

环境要求：Windows + Python 3.10+ 即可运行全部下载/界面功能；**若要在源码环境使用 BT/磁力，请使用 Python 3.11 或 3.12**（libtorrent 在 3.13/3.14 暂无官方 wheel）。日常开发用高版本 Python 不受影响，只是 BT 会走"缺库友好提示"分支。

```bat
:: 1) 安装依赖（BT/磁力建议在 3.11/3.12 环境）
pip install -r requirements.txt

:: 2) 运行
python main.py
::    或双击 run.bat
```

首次启动后默认下载目录为 `~/Downloads/东方神速`，配置与任务索引在 `%APPDATA%/DongFangSpeed/`。

### 常用操作

- **新建下载**：工具栏「新建下载」，粘贴链接后程序会在后台自动探测并预填**服务器文件名、文件大小、是否支持多线程断点**（不卡界面）；可改保存目录、文件名、连接数；需要登录的资源可在「高级选项」填用户名/密码、Cookie、Referer。
- **调整任务列表列**：直接拖动列右边界改宽度、拖动列标题改顺序；**右键表头**可勾选显示/隐藏哪些列（文件名列固定显示），设置自动记忆，右键菜单「重置列」恢复默认。
- **暂停/继续**：工具栏按钮、右键菜单，或双击任务行（下载中⇄暂停，已完成则打开文件）。
- **删除**：右键可选择「仅删除任务」或「删除任务及文件」。
- **批量下载**：菜单「任务 → 批量下载…」，一次粘贴多条链接（每行一条），自动去重后批量建任务。
- **自动分类**：在「设置 → 智能整理与提醒」勾选后，文件按类型存入 视频/音频/文档/程序/压缩包 子文件夹（m3u8 视频归视频，"其他"留在根目录）。
- **站点例外**：「设置 → 下载 → 站点连接数例外」每行写 `域名=连接数`（如 `example.com=4`），自动匹配该域及其子域。
- **计划任务**：「设置 → 计划任务」设定每天定时全部开始/暂停的时间，00:00 表示不设置该动作；同页可设「全部下载完成后」自动退出/睡眠/休眠/关机（执行前有 60 秒倒计时可取消）。
- **开机自启**：「设置 → 界面与集成」勾选「开机自动启动」，开机后程序在系统托盘后台运行（不弹主窗口），浏览器随时可接管下载；不勾选则不写任何开机项。
- **清除已完成**：菜单「下载 → 清除全部已完成任务」可一键清空所有已完成项（保留文件）。
- **设置**：默认目录、连接数、并发数、全局限速、卡死判定时长、代理、ffmpeg 路径、分类/通知/声音、剪贴板/托盘行为、桥接开关。
- **内网/自签 HTTPS 报证书错误**：程序默认经 truststore 使用 **Windows 系统证书库**，公司内网 CA（通常已由 IT 通过域/组策略推送）会被自动信任，无需关闭校验。若仍提示 `unable to get local issuer certificate`，二选一：①【推荐】把企业根证书安装到 Windows“受信任的根证书颁发机构”；②在「设置 → 高级」勾选「忽略 HTTPS 证书错误」（仅限你确信安全的内网/自签站点）。错误弹窗本身也会给出这两条指引。

## 测试

```bat
:: 单元 + 本地服务器端到端测试（无需外网）
python -m unittest tests.test_ranges tests.test_e2e tests.test_bridge tests.test_hls ^
  tests.test_enhance tests.test_ftp tests.test_http2 tests.test_grabber tests.test_round4 ^
  tests.test_ext_install -v

:: 真实外网下载（1 连接 vs 16 连接，哈希校验）
python tests/test_live.py

:: GUI 冒烟截图（输出到 tests/output/）
python tests/smoke_gui.py            :: offscreen（无字体环境截图缺文字属正常）
python tests/smoke_gui.py --real     :: 真实桌面渲染
```

已验证结果（开发机）：

- **144 项单元/端到端测试在 Python 3.12 全部通过**（2 项按可选依赖缺失自动 skip），普通下载、HLS、FTP/FTPS、HTTP/2、BT、代理、自动更新、内网 HTTPS 证书合并产物 SHA-256 均与源一致；`test_dynamic_segments` 覆盖自适应分块、工作窃取、随机断流（drop=40）、确定性慢连接、连接上限、高延迟抖动（预取双缓冲）等病态网络，`test_mirrors` 覆盖快镜像聚合加速、主源全断故障转移、大小不符/不支持 Range 镜像剔除；其中 `test_round12` 覆盖开机自启注册表读写、完成后电源动作命令、仅清除已完成任务、扩展固定目录两模块一致与幂等释放；
- 单实例（命名管道锁）真机验证：首个实例运行时，第二、三个实例均在数秒内自动退出（退出码 0），系统中始终只有一个进程，且已有窗口被唤醒前置；列显隐/列宽/重排的持久化与重置由 `test_gui_selection` 覆盖；
- 内网/自签 HTTPS（`tests/test_tls.py`）：自签服务器默认校验失败且错误消息含中文解决指引；勾选「忽略 HTTPS 证书错误」后可正常下载；truststore 挂载后对真实企业内网云盘（Windows 已信任其根 CA）握手通过；
- BT/磁力（`tests/test_torrent.py`）：本机做种真实下载 `.torrent` 与磁力（ut_metadata）、多文件勾选、会话关闭释放均通过；
- 代理（`tests/test_proxy.py`）：URL 内嵌/显式 Basic 凭据、错误密码 407、NTLM 完整 407 握手下载、PAC 指令解析与 WinHTTP 真实 PAC 解析均通过；
- 自动更新（`tests/test_updater.py`）：版本比较、发现新版/同版不提示、坏清单报错、下载 + SHA256 进度回调、哈希不符拒绝并删除文件均通过；
- 暂停后续传、模拟崩溃后从 `pdlmeta` 恢复续传，文件完整；
- HLS：明文 TS、master 选最高码率、AES-128 解密、fMP4 初始化段拼接、暂停续传、直播拒绝均通过；
- 增强能力：服务端只发响应头不发数据时，看门狗在阈值后断开并把分段重新分配，最终文件哈希仍与源一致；磁盘空间不足在下载前即报错；站点例外连接数在探测后正确生效；HTTP 文件与 HLS 均按类型进入对应子文件夹；
- FTP/FTPS：匿名与账号密码登录、多连接分段、暂停续传、`ftps://` 显式 AUTH TLS 加密链路均通过（FTPS 采用每分块独立加密连接以兼容服务器）；
- HTTP/2：在 hypercorn（ALPN h2）服务器上多连接 Range 下载完整，且断言实际协商版本为 HTTP/2；
- 网页抓取：有限深度、同站过滤、类型筛选、robots.txt 生效均通过；校验和四种算法、真实预分配占盘、Defender 扫描干净文件、blob 桥接落盘与坏 Token 403、BT 缺库明确报错、协议分发均通过；
- 桥接：`/ping`、`/pair` 无 Origin 与扩展来源返回 Token 而网页 http(s) 来源 403、无 Token 提交 `/add` 返回 403、带 Token 提交下载并校验哈希、接管确认窗接受/取消、非法 URL 返回 400、`/add-blob` 流式落盘；
- 列表交互：任务列表每秒刷新时保持选中不丢、错误任务的继续/重下按钮可用（`test_gui_selection`）；
- 扩展自动接管端到端（真实 Edge 153，`tools/e2e_ext.py`）：独立实例 `--load-extension` 加载扩展后，扩展经 `/pair` 自动拿到 Token；在扩展后台用 `chrome.downloads.download` 创建真实下载（等价于用户点链接，同样触发 `onCreated`），扩展取消浏览器侧下载并 `POST /add`，任务真实下载完成且 SHA-256 一致（`tools/diag_ext.py` 可进一步通过 CDP 查看后台配对状态与下载记录）；
- 网页视频悬浮按钮端到端（真实 Edge，`tools/e2e_video_btn.py`）：测试页 `<video>` 鼠标悬停后浮现「下载视频」按钮，点击显示「已发送到东方神速」，桥接接管 mp4 直链，下载完成 SHA-256 一致；离线不接管回退浏览器原生下载（`tools/e2e_offline.py`）亦通过；
- 扩展一键安装（Chrome/Edge 153 实测）：Edge 命令行 `--load-extension` 加载生效（独立配置前后扩展 ID diff 新增本扩展），桌面快捷方式可正确生成并兼容被重定向的桌面；Chrome 品牌版 137+ 已封堵命令行侧载（加恢复 flag 也无效），向导改为一键打开扩展页、复制路径并定位文件夹的最少点击引导；
- 对每连接限速 1 MB/s 的服务器：单连接 8.14 s vs 8 连接 1.41 s（**5.8x**）；
- 外网华为云镜像 26.7 MB 文件：1 连接 13.31 MB/s、16 连接 19.01 MB/s，两次下载哈希一致。

## 打包为 exe / 安装包

推荐在 **Python 3.11/3.12** 环境打包（这样 BT/磁力会被内置进 exe）：

```bat
:: 安装打包依赖（3.11/3.12）
pip install -r requirements.txt pyinstaller pillow

:: 构建可执行程序（tools/build_exe.py）
python tools/build_exe.py portable   :: 便携版 → dist\DongFangSpeed\（启动快，含 browser_extension）
python tools/build_exe.py single     :: 单文件版 → dist\DongFangSpeed.exe（首次运行把扩展释放到固定目录）
python tools/build_exe.py both       :: 两者都打（默认）
```

构建脚本会 `--collect-all libtorrent`（内置 BT）、收集 h2/hpack/hyperframe（HTTP/2）、
收集 requests_ntlm/pyspnego（NTLM 代理认证）、cryptography/cffi（含原生绑定）与
truststore（Windows 系统证书库，信任企业内网 CA），
并把 `browser_extension/` 与 `assets/`（品牌图标）打进程序。打包后（单文件/安装版）
首次运行会把浏览器扩展释放到**固定目录** `%LOCALAPPDATA%\DongFangSpeed\browser_extension`，
仅当 manifest 版本号变化（程序升级）时才覆盖；该路径不随 exe 位置改变，因此浏览器
**只需在该固定路径加载一次即永久生效**，单文件版即使每次从不同路径启动也无需重新加载。
便携版另在程序目录内置一份 `browser_extension/` 作为离线兜底。

**自检**：打包后可运行 `DongFangSpeed.exe --selftest`（不弹窗），退出码 0 表示图标、
浏览器扩展均就绪，日志另列出 `BT / HTTP2 / NTLM / UPDATER / TRUSTSTORE` 各可选模块是否可用
（GUI 版另在 `%TEMP%\\DongFangSpeed_selftest_diag.txt` 写诊断）。

**稳定性设计**：启动时在主线程预热导入可选/原生模块，规避单文件版多线程首次导入冻结模块
可能导致的卡死；主线程与工作线程未捕获异常会写入 `%APPDATA%/DongFangSpeed/crash.log`
（排查闪退时可提供此文件）。单文件版退出时若偶发 “Failed to remove temporary directory
（_MEI…）”提示，是 PyInstaller 临时解压目录被占用所致，不影响功能与已下载文件；介意可改用
启动更快、无临时解压的**安装包/便携版**。

**IDM 式安装包**：使用 [Inno Setup 6](https://jrsoftware.org/isdl.php) 编译
`installer/dongfangspeed.iss`（中文/英文向导、开始菜单与桌面快捷方式、卸载项、LZMA2 固实压缩）：

```bat
:: 先确保已构建便携版，再编译（ISCC 路径按实际安装位置）
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\dongfangspeed.iss
:: 产物：dist\DongFangSpeed-Setup-1.0.0.exe
```

品牌图标由 `tools/make_brand_icons.py`（Pillow 4 倍超采样绘制）生成到 `assets/app.ico`、
`assets/app.png` 与扩展图标；品牌常量集中在 `core/branding.py`，冻结路径与扩展释放在
`core/app_paths.py`。

### 自动更新

更新源是一个可配置的 `latest.json`（GitHub Releases / Gitee / 自建静态服务器均可）：

```json
{
  "version": "1.0.1",
  "url": "https://你的站点/DongFangSpeed-Setup-1.0.1.exe",
  "sha256": "安装包的 SHA256（十六进制，强烈建议填写）",
  "notes": "本版更新说明，会显示在更新提示框中"
}
```

- 在「设置 → 高级」填写 **更新清单地址**（即上述 `latest.json` 的 URL），可勾选「启动时自动检查更新」；**地址留空时程序绝不发起任何更新请求**。
- 帮助菜单「检查更新…」可手动检查；发现新版本后后台线程下载（不卡界面、带进度），完成后做 SHA256 校验，校验不符会删除安装包并报错，防止下载损坏或被篡改。
- 确认安装后以 `/silent /closeapplications /norestart` 静默启动 Inno 安装包覆盖安装并退出当前版本。
- 核心逻辑在 `core/updater.py`（版本比较、清单拉取、流式下载、校验、启动安装包），界面在 `gui/update_dialog.py`，均有本地 HTTP 端到端测试（`tests/test_updater.py`）。

### 代码签名

**正式分发优先使用免费的 SignPath Foundation 开源签名**：完整接入步骤见 [SIGNING.md](SIGNING.md)。它对合规开源项目免费、私钥在 HSM 托管、签名受 Windows 信任，并要求产物由 GitHub Actions 从公开源码构建（`.github/workflows/build.yml` 已预留签名/发布作业），个人开源无需自购 OV/EV 证书。全新签名哈希的 SmartScreen 信誉仍需一定下载量积累。

未签名的 exe 会触发 Windows SmartScreen 与浏览器“不安全文件”提示。仓库另外提供本地签名工具
`tools/sign_exe.py`（自动定位 Windows Kits 的 `signtool.exe`，SHA256 摘要 + RFC3161
时间戳，多个时间戳服务器自动容错）：

```bat
:: 方式一：构建时自动签名（设置证书环境变量后，build_exe 末尾自动签 dist 产物）
set DFS_PFX=C:\path\to\code-signing.pfx
set DFS_PFX_PASSWORD=证书密码
python tools\build_exe.py both

:: 方式二：单独签名指定文件（安装包也可）
python tools\sign_exe.py dist\DongFangSpeed.exe dist\DongFangSpeed-Setup-1.0.0.exe ^
  --pfx C:\path\to\code-signing.pfx --password 证书密码
```

> **自签证书只能验证签名流程，不被 Windows 信任**（签名属性里仍显示未知/不受信任，
> SmartScreen 依旧拦截）。要消除 SmartScreen，需向 DigiCert/Sectigo/GlobalSign 等机构
> 购买 **OV 或 EV 代码签名证书**（EV 通常配合硬件 USB Token），并积累一定的文件信誉。
> 安装包请在 ISCC 编译生成后再执行签名（`sign_exe.py` 默认目标已含 Setup 安装包）。

## 浏览器扩展（Chrome / Edge）

`browser_extension/` 是一个 Manifest V3 扩展，功能对标 IDM 的浏览器监控模块：

- **自动接管下载（免配置）**：扩展装好、本程序在运行即可，**无需手动复制 Token**——扩展首次连接经 `GET /pair` 自动配对。浏览器触发下载时，扩展先确认本程序在线且已授权，再取消浏览器内置下载，把 URL、文件名、Cookie、Referer 发送过来（需要登录的资源也能下载）；`blob:` / `data:` 地址不接管。**本程序未运行或未配对时绝不劫持**，下载自动回退浏览器自带下载，不再“点了没反应”。
- **接管确认窗（类 IDM，可关）**：默认每次接管弹出「浏览器下载接管」对话框，预填文件名/来源/Cookie，可改目录后开始或“稍后/取消”；在「设置 → 界面与集成」可关闭以静默接管。
- **媒体嗅探**：通过 `webRequest` 监听页面中的 `.m3u8 / .mp4 / .mp3` 等请求，角标提示数量，弹窗里一键下载（HLS 地址会自动交给 m3u8 引擎）。
- **网页视频悬浮下载按钮**：鼠标悬停在页面任意 `<video>` 上时，右上角浮现 IDM 风格的「下载视频」按钮，点击即接管该视频；普通 mp4 直链直接下载，MSE/m3u8/blob 播放页自动回退到本页媒体嗅探结果（优先 m3u8）。受 EME/Widevine 等 DRM 保护的视频无法下载，按钮会明确提示。
- **手动下载**与**接管/嗅探总开关**、桥接端口（Token 一般留空即自动配对）、连接测试。

**Firefox 版**：运行 `python tools/build_firefox_ext.py` 会把扩展转换为 Manifest V2
（event background、`browser_action`、`gecko.id`），产出 `dist/firefox_extension/`
与 `dist/东方神速-Firefox扩展-<版本>.zip`。临时加载：Firefox 打开 `about:debugging`
→「此 Firefox」→「临时载入附加组件」，选 zip 或其中的 `manifest.json`（临时扩展重启
浏览器后失效）。正式长期分发需在 [addons.mozilla.org(AMO)](https://addons.mozilla.org/)
提交签名审核；Firefox 版保留下载接管、媒体嗅探与视频悬浮按钮，MAIN world 的 blob
高级钩子在 Firefox 降级（blob 接管以 Chromium 版为准）。

**推荐：一键安装。** 打开「设置 → 界面与集成 → 一键安装浏览器扩展…」，向导会把扩展
释放到固定目录 `%LOCALAPPDATA%\DongFangSpeed\browser_extension` 并自动探测已装浏览器：

- **Microsoft Edge**：点「一键安装并启动 Edge」，向导短暂关闭 Edge 后以加载扩展的方式
  重启，并在桌面生成「东方神速 - Edge（已加载扩展）」快捷方式；以后双击该快捷方式启动
  Edge，扩展即自动加载（Edge 当前仍支持 `--load-extension`）。出现“关闭开发人员模式下
  的扩展”提示时选「以后再说 / 保留」。
- **Google Chrome**：Chrome 137 起在品牌版移除了命令行侧载开关，个人电脑无法被第三方
  静默安装。向导会一键打开 `chrome://extensions`、把扩展文件夹路径复制到剪贴板并在资源
  管理器定位该文件夹，你只需：开启右上角「开发者模式」→ 点「加载已解压的扩展程序」→
  在弹出框直接粘贴路径并确定；加载一次后扩展随浏览器长期保留（顶部提示停用开发者模式
  扩展时选「保留」）。

> **为什么不能完全静默**：未上架 Chrome 网上应用店 / Edge 加载项的扩展，Chrome 137+
> 封堵了命令行 `--load-extension`，Edge 的自托管强制安装（`ExtensionInstallForcelist`）
> 又要求设备加入 Active Directory 域 / MDM。个人电脑上唯一“零操作”的正道是把扩展上架
> 商店（需开发者账号与审核）；本向导已把侧载压缩到最少点击。

**手动加载（兜底）**：打开「设置 → 界面与集成 → 打开扩展文件夹」会直接定位到固定目录
`%LOCALAPPDATA%\DongFangSpeed\browser_extension`（单文件/安装版）；随后在
`edge://extensions` 或 `chrome://extensions` 开启「开发者模式」，点「加载已解压的扩展程序」，
选择该文件夹。**该路径固定不变，只需加载一次即随浏览器长期保留**；程序升级只覆盖目录内容、
路径不变，无需重新加载（便携版可加载程序目录内的 `browser_extension`，但建议改用上述固定目录，
以免移动 U 盘后失效）。

加载扩展后，确认「设置 → 界面与集成」已勾选「启用浏览器扩展本地桥接」（默认开启），
保持本程序在运行，然后点一下工具栏的扩展图标，弹窗显示绿色「已连接」即可——**Token
会自动配对，弹窗里的 Token 框一般留空，无需手动复制**。只有当自动配对被安全软件拦截、
或需要手工对接时，才需把「设置 → 界面与集成」里的 Token 复制进弹窗。

> Token 只保存在本机 `%APPDATA%/DongFangSpeed/bridge.token`，桥接仅监听 `127.0.0.1`。
> 自动配对 `GET /pair` 只对扩展来源（`chrome-extension://` 或无 Origin 的后台请求）
> 返回 Token，普通网页 `http(s)://` 来源一律 403；`/add`、`/add-blob` 仍必须带正确
> Token，否则 403，网页无法冒用本机程序发起下载。

### 桥接 HTTP API

程序在 `127.0.0.1:<端口，默认 8765>` 提供接口（已开启 CORS）：

- `GET /ping` → `{"ok": true, "name": "东方神速"}`（公开，用于探活）
- `GET /pair` → `{"ok": true, "token": "...", "name": "东方神速"}`：免填 Token 的本机自动配对。简单 GET、不触发 CORS 预检；仅扩展来源（`chrome-extension://` 或无 `Origin` 的后台请求）返回 Token，普通网页 `http(s)://` 来源返回 403。
- `POST /add`，请求头 `X-PyDL-Token: <token>`，JSON 体：

```json
{
  "url": "https://example.com/file.zip",
  "filename": "可选文件名",
  "referer": "可选来源页",
  "cookies": "可选 Cookie 串",
  "save_dir": "可选保存目录",
  "headers": { "可选自定义请求头": "..." }
}
```

URL 以 `.m3u8` 结尾时自动分发到 HLS 引擎，`ftp://` / `ftps://` 分发到 FTP 引擎。

- `POST /add-blob`，请求头 `X-PyDL-Token: <token>`、`X-Blob-Name: <文件名>`、
  `Content-Type: application/octet-stream`，请求体为原始二进制。服务端流式落盘
  （1 MiB 分块、自动分类、重名自动编号），返回 `{"ok": true, "path": ...}`，
  长度不符返回 400。供扩展接管页面 `blob:` 资源使用。

## blob: 流接管（实验性）

部分网站把动态生成的内容（前端导出的文件、内存里合成的媒体）做成 `blob:` URL，
这类地址不是网络链接，外部下载器无法直接访问。扩展通过两个 content script 处理：

1. `content_hook.js` 运行在页面主世界（MAIN world），包装 `URL.createObjectURL`，
   记录页面创建的普通 `Blob`（忽略 `MediaSource` 分片流）；
2. `content_bridge.js` 运行在隔离世界，在扩展弹窗「本页生成的 Blob」列表中点
   「保存」时，于页面同源下 `fetch(blobUrl)` 读出二进制，POST 到 `/add-blob` 落盘。

**已知限制（界面中亦有提示）**：blob 只在创建它的页面、且未被 `revokeObjectURL`
释放时可读；大体积 Blob 会整体读入浏览器内存，建议仅用于中小文件；受 EME/Widevine
等 DRM 保护的媒体（MSE 分片本身即密文）**不会、也无法绕过**，本项目不提供解密。

## m3u8 / HLS 视频下载

新建任务时链接填 `.m3u8` 地址（或通过扩展嗅探一键下载）即自动启用 HLS 引擎（`core/hls.py`）：

- **Master 播放列表**：自动选择 `BANDWIDTH` 最高的清晰度；
- **加密**：支持 `AES-128-CBC`（自动下载密钥，未给 IV 时按媒体序号生成）；
- **封装**：支持 MPEG-TS 与 fMP4（`EXT-X-MAP` 初始化段）；
- 分片多线程下载、独立 part 文件、进度持久化，支持暂停/续传/崩溃恢复；
- **合并**：若系统装有 [ffmpeg](https://ffmpeg.org/) 并在 PATH 中，优先 remux 为 `.mp4`（TS 自动处理 `aac_adtstoasc`）；未安装 ffmpeg 时按原始封装无损直拼（TS → `.ts`，fMP4 → `.mp4`），不影响画面内容。

已知限制（会在界面/日志中明确提示）：

- 不支持 **SAMPLE-AES** 加密与**直播流**（播放列表无 `EXT-X-ENDLIST`）；
- 媒体分片级独立的 `EXT-X-BYTERANGE`（非 `EXT-X-MAP`）暂未解析；
- 带时效签名的分片 URL，若长时间暂停后签名过期，续传会重新下载失败分片（重新新建任务即可）。

## 路线图

已完成慢连接卡死抢占、磁盘预检、站点例外、自动分类、批量下载、速度曲线、完成通知、计划任务、ffmpeg 配置，以及 FTP/FTPS、HTTP/2、网页资源抓取、blob 接管、MD5/SHA 校验、真实占盘预分配、完成后杀毒钩子、BT/磁力（DHT/PEX/UPnP/文件勾选，发行版内置）、代理 Basic/NTLM/PAC、网页视频悬浮按钮、Firefox 版扩展、自动更新框架与代码签名工具链。后续方向：

1. HTTP/3 (QUIC)：待 Python 3.13+ 上 aioquic 生态成熟后启用（当前在 3.14 无可靠轮子）；
2. 扩展上架：Firefox 提交 AMO 签名、Chromium 上架 Edge 加载项/Chrome 网上应用店，实现普通用户零操作安装；正式 OV/EV 代码签名以消除 SmartScreen；
3. HLS 内嵌字幕/多音轨选择、媒体分片级 `EXT-X-BYTERANGE`、DASH 支持；
4. 代理 Digest 认证、FTP 经 HTTP CONNECT 隧道；BT 磁力链接的边下边选文件；
5. Brotli/zstd 内容编码协商、站点限速智能自适应；更大体积 blob 的分片中转（当前实验方案整体读入浏览器内存，建议中小文件）。

## 法律与合规提示

- 本项目采用 **GNU 通用公共许可证 v3.0（GPL-3.0）**，与界面框架 PyQt5 的 GPLv3 许可一致；可自由使用、学习与修改，分发程序或衍生作品（含网络分发）时须同样以 GPL-3.0 开源并保留版权声明，详见 [LICENSE](LICENSE)。
- “下载时动态分段/连接复用”是 IDM 厂商 Tonec 的商业化技术区域，**在美国等司法辖区可能存在相关专利**。本项目为学习与研究目的的独立实现；若计划公开发布或商业分发，请自行完成自由实施（FTO）检索，规避受保护的具体算法权利要求。
- 请遵守目标网站的服务条款与 robots 规则、版权法及当地法律，不要用于绕过访问控制或下载侵权内容。
- 本项目**不提供、也不会实现**对 DRM（如 Widevine/PlayReady/FairPlay）及 SAMPLE-AES 加密媒体的解密或绕过；blob 接管仅保存页面中未加密的普通 Blob。整站抓取默认仅同站、有限深度并尊重 robots.txt，请勿用于高并发抓取他人服务器。

## 许可证

[GNU General Public License v3.0 (GPL-3.0)](LICENSE)
