# Alicloud_ECS_CDTPanel

> 本地目录 `alicloud_ECS_CDT`，对应 GitHub 仓库 `Alicloud_ECS_CDTPanel`。
> 仓库地址：https://github.com/gugetang/Alicloud_ECS_CDTPanel

阿里云 ECS / CDT 调度控制台：多账户流量监控、抢占式实例保活、Cloudflare DDNS、
阈值踢出、固定时间切换、按流量负载均衡。单进程 FastAPI + SQLite，前端 React + TS + Vite，
Docker 一键部署（默认 `0.0.0.0:1999`）。

> 从零新写。参考了 `cdt-management`（Python，换班/均衡/DDNS 思路）与
> `AliCloud_CDT-Monitor`（Go，单进程/Worker/ API Key 思路）的公开实现，
> 但代码、数据模型与调度引擎均为本项目重新实现，未直接复制。

## 快速开始

### 一键部署（推荐）

```bash
curl -fsSL https://raw.githubusercontent.com/gugetang/Alicloud_ECS_CDTPanel/main/install.sh | bash
```

脚本会：检测/自动安装 Docker + Docker Compose（apt/yum/dnf/apk，装不上就报错退出）、
克隆仓库到 `/opt/docker/alicloud_ECS_CDT`、拉取 GHCR 镜像并 `docker compose up -d`、
轮询 `/api/health` 最多 60 秒，成功后打印访问地址 `http://<服务器>:1999`。
端口固定 1999，改端口请编辑 `docker-compose.yml` 的 `ports` 一行。

```bash
# 改目录
curl -fsSL https://raw.githubusercontent.com/gugetang/Alicloud_ECS_CDTPanel/main/install.sh | DIR=/srv/cdt bash
# 升级：重跑同一条命令（git 已是最新且容器在跑时不重建）
curl -fsSL https://raw.githubusercontent.com/gugetang/Alicloud_ECS_CDTPanel/main/install.sh | bash
# 卸载（会二次确认是否删数据）
curl -fsSL https://raw.githubusercontent.com/gugetang/Alicloud_ECS_CDTPanel/main/install.sh | UNINSTALL=1 bash
```

> 注意：如需换仓库，可用 `REPO_URL=... bash` 指定仓库地址。
> 本机无 Docker，本仓库只做了静态检查（`bash -n`、Dockerfile/compose 人工核对），
> 未在本机实测 Docker 部署。

### Docker Compose 部署

```bash
git clone https://github.com/gugetang/Alicloud_ECS_CDTPanel.git
cd Alicloud_ECS_CDTPanel
docker compose pull
docker compose up -d
# 查看日志 / 停止
docker compose logs -f Alicloud_ECS_CDTPanel
docker compose down
# 访问 http://服务器:1999
# 改端口：编辑 docker-compose.yml 的 ports 一行，如 - "8888:1999"
```

完整的 `docker-compose.yml`（与仓库根目录文件一致，精简版，只拉 GHCR 镜像，不本地构建）：

```yaml
services:
  Alicloud_ECS_CDTPanel:
    image: ghcr.io/gugetang/alicloud_ecs_cdtpanel:latest
    container_name: Alicloud_ECS_CDTPanel
    restart: unless-stopped
    init: true
    ports:
      - "1999:1999"
    environment:
      TZ: Asia/Shanghai
      CDT_DATA_DIR: /data
    volumes:
      - /opt/docker/cdt-alicloud_ecs_cdtpanel:/data
```

说明：服务名 `Alicloud_ECS_CDTPanel`（容器同名），只拉已发布的 GHCR 镜像
`ghcr.io/gugetang/alicloud_ecs_cdtpanel:latest`（见 `.github/workflows/docker.yml`，
push 到 main 自动构建并推送到 GHCR）。端口固定 `1999:1999`，
数据卷用绝对路径 `/opt/docker/cdt-alicloud_ecs_cdtpanel:/data`
（与一键脚本的代码目录 `/opt/docker/alicloud_ECS_CDT` 无关，数据固定落在
该卷里），`TZ=Asia/Shanghai`、`CDT_DATA_DIR=/data`，
`restart: unless-stopped`，`init: true`。
升级：`docker compose pull && docker compose up -d`（或重跑一键部署命令）。

> 镜像名纠正：GHCR 镜像名必须全小写，且不能带仓库地址的 `.git` 后缀，
> 所以是 `ghcr.io/gugetang/alicloud_ecs_cdtpanel:latest`，
> 不是 `ghcr.io/gugetang/Alicloud_ECS_CDTPanel.git`。
> 首次使用前镜像必须已发布（push 到 main 后 Actions 自动发布）；本地构建方式已移除。

### 常见问题

**容器启动报 `sqlite3.OperationalError: unable to open database file`**

容器里的服务以 UID 10001 运行，但宿主机上的数据卷目录 `/opt/docker/cdt-alicloud_ecs_cdtpanel`
是 Docker 自动创建的，属主是 root，容器写不进去。

新版镜像已自动修复：容器入口脚本（`entrypoint.sh`）启动时先以 root 把 `/data`
改属主为 10001，再切到 10001 用户运行服务，`docker compose pull && docker compose up -d`
直接拉取启动，不用手动处理。一键部署脚本也会在 `up` 之前提前改好属主（双保险）。

只有旧版镜像（没有入口脚本的版本）才需要手动修：

```bash
sudo chown -R 10001:10001 /opt/docker/cdt-alicloud_ecs_cdtpanel
docker compose restart   # 或 docker restart Alicloud_ECS_CDTPanel
```

### DDNS 设置与时区

全局设置页新增「DDNS 设置」卡片：

- **Cloudflare API Token**：用于 DDNS 解析（Zone / 记录 ID 自动发现，
  IP 不变不改）。输入框显示“已配置”时留空保存保持不变，只有填了新值才更新。
- **时区**（下拉菜单，从 `GET /api/timezones` 拉取，加载失败时回退内置列表）：
  默认 `Asia/Shanghai`（取容器环境变量 `TZ`，缺省 `Asia/Shanghai`）。
  下拉旁显示当前时区。保存后立即生效，下次调度按新时区走，不用重启。
- **影响范围**：夜间窗口、固定时刻、间隔锚点、每日开关机、日报推送时间
  都按此时区执行；日报日期（`[AliCloud每日报告]` 里的日期）也按此时区算。
- **与容器 TZ 的关系**：compose 里的 `TZ: Asia/Shanghai` 只作后备默认值；
  UI 里改了时区后以 UI 设置为准，容器 TZ 不再起作用。

### 本地

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m app.main            # 监听 0.0.0.0:1999，数据在 ./data
# 或：uvicorn app.main:app --host 0.0.0.0 --port 1999
# 改端口：CDT_LISTEN_PORT=8888 python -m app.main
```

首次访问 `http://服务器:1999`，设置管理员密码（可选，未设置时不强制登录）。

`./data/cdt.db` + `./data/master.key` 必须一起备份——丢了 `master.key`，
已加密的 AccessKey Secret 无法解密。

## 调度规则

| 规则 | 说明 |
|---|---|
| 排序分配 | 按流量使用率升序；域名按优先级依次分给最低账户；VPS 少于域名时按顺序循环 |
| 保活 | 被分配的 VPS 为 Stopped → 下发开机，确认 Running 且拿到公网 IP 后才切 DNS |
| 阈值踢出（默认不停机） | 到达阈值默认不停机，只是跳出 DDNS：使用率 ≥ 流量保护阈值（默认 95%）→ **先**退出分配池（本轮不再被选中），域名**先**切到下一个最低账户（DNS 在前），**然后**才按阈值动作（见下表三选一）决定是否停机。默认 `none`=踢出DDNS，只踢出、不停机，实例保持 Running，用量回落后自动恢复入池；`stop`=普通停机；`stop_charging`=节省停机。同一账户多域名只停一次；全池超阈值 → 域名指备用地址。通知「流量保护」/「排除操作完成」 |
| 负载均衡 | 差值模式 `percent`（默认）：当班使用率 − 候选使用率 ≥ 切换差值（默认 10%）→ 切 DNS；`gb` 模式：当班已用 GB − 候选已用 GB ≥ 切换差值（默认 10 GB）→ 切 DNS，与百分比无关。例：100 GB vs 88 GB，差 12 GB ≥ 10 GB 切，% 差仅 6% 时 percent 模式不切。每账户可单独覆盖差值模式/数值 |
| 按时间切换 | 模式 `time_switch_mode`：`""` 自动（夜间 + 固定时刻都可用）、`off` 关闭、`night_only` 仅夜间、`interval` 仅间隔、`fixed_time` 仅固定时刻。夜间窗口（默认 00:00–04:00）内每晚第一次有效检查不论差值切到最低，每晚仅一次；固定每日切换时刻（如 `03:00`）当天仅一次；间隔切换按上次切换锚点每 N 小时切一次，N 可选 1/2/3/4/6/12/24 小时或自定义分钟数（如 90 分钟），整点对齐（1h=每小时，6h=00/06/12/18），锚点持久化在 flags，重启后继续计时、不重复切 |
| 备用地址 | 无可分配 VPS 时域名指到备用地址并通知；有 VPS 可用时恢复并通知 |
| 保守原则 | 流量或实例状态未知时不踢出、不切换、不停机，只记异常事件 |

## 分组与切换模式（一个分组只选一种模式）

切换模式按**分组**设置，不再是全局开关。每个分组是一个 IP 池 +
最多 1 个域名，创建/编辑分组时单选一种模式，不同分组可以选不同模式。

| 模式 | 含义 | 触发切换的条件 |
|---|---|---|
| `time` 按时间切换 | 只按时间切 | 夜间窗口（00:00–04:00，每晚一次）/ 间隔（1/2/3/4/6/12/24 小时或自定义分钟数）/ 每天固定时刻到点时切，不论流量差值 |
| `load_balance` 按负载均衡 | 只按流量差值切 | 差值模式 `percent` 时使用率差 ≥ 10% 切；`gb` 模式时已用流量差 ≥ 10 GB 切，与百分比无关；时间到点也不切 |

**例子：分组A = IP1、IP2、IP3、IP4，模式按时间切换。** IP1 用到
192/200 GiB（96% ≥ 95% 阈值）→ 先踢出池子、不再参与 DDNS 解析，
域名切到用量最低的 IP2；阈值动作三选一（默认踢出DDNS、不停机，
`stop`=停机，`stop_charging`=节省停机）。停机永远在 DNS 切换之后，
同一账户只停一次。分组B 可以同时是按负载均衡模式，两个分组各自独立
调度、互不干扰，夜间/间隔/差值旗标按组分别记录。

**1 分组 = 1 域名：** 一个分组最多绑定 1 个域名，一个域名只属于 1 个
分组。新建分组时不强制立即绑定域名（可先建分组再绑）；一旦绑了域名就
不能再绑第二个（API 返回 400“该分组已配置域名 xxx，一个分组只能配置
一个域名”，数据库 partial UNIQUE 索引是第二道防线）；删除域名后该分组
可再绑新域名；删除分组前必须先删除/移走其账户与域名（否则返回 400），
默认分组（id=1）禁止删除。分组内调度就是“1 个域名 ← 该组 IP 池里用量
最低的可分配 IP”，不再循环分配；多域名循环分配只保留在无分组 legacy
调用里（直接调 `plan_reconcile()` 不传 `groups` 时）。

**分组管理页布局：** 不设默认分组（新建分组时切换模式必选，无默认选中）；
“+ 新建分组”按钮在页面右上角，点开是内联表单（分组名 + 模式按键 +
创建），不用 `prompt()`；每个分组卡片里模式用按键切换，时间/差值参数
用内联表单改（间隔按键、差值输入、固定时刻、夜间窗口），保存后生效。
默认分组（id=1）禁止删除是数据库迁移层的保护，界面上不再给任何分组
打“默认”标记。

**成员实例多选参与 DDNS：** 每个分组卡片的“成员实例”区把已添加的
实例列成按键（名称前是状态点，点一下选中 / 取消，可多选；选中蓝框、
未选灰框）。被选中的实例参与 DDNS 域名分配；取消选中的实例不参与
分配、不进排队，状态点显示灰色“未参与”（仍被域名指向时除外，仍显示
在线，等下次调度切走）。新建实例默认参与（`ddns_enabled=1`），开关
存在账户表里，随备份一起导出/恢复；接口用 `PUT /api/accounts/{id}`
传 `ddns_enabled: 0/1`。

**不受切换模式影响（各分组、各模式都生效）：** 阈值踢出、保活（停机拉起）、
首次分配、备用地址（无可分配 VPS 时切备用地址）。

**迁移说明：** 旧库无分组时启动自动建 `groups` 表和默认分组（id=1，
名“默认分组”，模式 `load_balance`，与截图默认的差值 10% 规则一致）；
旧账户/域名 `group_id` 为 NULL 的归默认分组；若旧库有多个未分组域名，
第 1 个留默认分组，其余每个自动新建一个分组（保证 1 分组=1 域名）。
旧的全局 `switch_mode`（time/load_balance/both，默认 both）仍保留在
settings 表，仅作为**新建分组的默认模式**与无分组 legacy 调用的回退，
不再直接控制分组账户；`/api/status` 返回分组列表及其模式与分配情况。
全局设置页的切换模式选择器已移除，改为提示去「分组管理」设置。

默认值：月度额度 200 GiB、保护阈值 95%、切换差值 percent 10% / gb 10 GB、
API 刷新时间 5 分钟、夜间优化开启（00:00–04:00）、时间切换模式自动、
间隔 6 小时、备用地址 `fallback.example.xyz`（请改成自己的）。

新字段：分组表 `groups`（`switch_mode` time/load_balance，
`interval_hours`、`custom_interval_minutes`、`fixed_switch_time`、
`night_window_start/end`、`night_optimize_enabled`、`switch_diff_mode/gb/pct`、
`time_switch_mode`，全部可空=继承全局）；账户/域名表新增 `group_id`。
`/api/groups` 提供分组 CRUD；`/api/status` 返回 `groups`。

## 状态点（全站统一）

每个账户在运行概览、账户管理页、分组管理页成员列表、域名管理页当前
记录快照里都有一个状态点，含义固定。后端 `/api/status`、`/api/accounts`、
`/api/groups` 返回的每个账户都带派生字段 `status_dot`
（`green` | `yellow` | `gray` | `red` | `green_pulse` | `red_pulse`）、
`status_label`、`status_detail`，另外还有 `serving`（是否正被域名指向）
与 `queue_rank`（排队位次），前端直接用，不再自己算阈值。
运行概览页有图例：绿=在线、黄=排队中、灰=已跳出、灰=未参与、红=停机/离线、闪烁绿=启动中、闪烁红=停止中、灰=未知。

颜色用 Google 品牌色，尺寸直径约 1mm（4px，1mm ≈ 3.78px @96dpi）：
绿 `#34A853` / 黄 `#FBBC04` / 红 `#EA4335` / 灰 `#9AA0A6`；
启动中是 Google 绿底色 + 光晕的高亮闪烁绿点，停止中是 Google 红闪烁点，
光晕扩散约 2–3px。颜色在 `web/src/styles.css` 里用 `--dot-green` /
`--dot-yellow` / `--dot-red` / `--dot-gray` 变量集中定义，各状态点 class
引用变量；后端 `status_dot` 值不变。

| 点 | 含义 | 条件 |
|---|---|---|
| 🟢 绿点（实心） | 在线 | 实例 Running、未被踢出、流量已知，且当前正被某个域名指向（正在服务） |
| 🟡 黄点 | 排队中 | 实例 Running、未被踢出、可分配，但当前未被任何域名指向，正在池子里排队待命（按使用率升序排队等接替）。detail 注明排队位次，如“第 2 位（按使用率升序），可随时接替” |
| ⚪ 灰点 | 已跳出 | 达到保护阈值被踢出池子，不再参与 DDNS 解析。detail 如“使用率 96.0% ≥ 阈值 95%，已跳出池子；用量回落后自动恢复” |
| 🔴 红点 | 停机/离线 | 实例 Stopped（已停机）或状态未知/查询不到（离线） |
| 🟢 高亮闪烁绿点 | 启动中 | 实例处于 Starting/Pending 等过渡态，前端用 CSS 脉冲动画（亮绿带光晕），不要与实心绿点混淆 |
| 🔴 闪烁红点 | 停止中 | 实例处于 Stopping（顺延定义：用户只明确了启动中用闪烁绿，停止中顺延为闪烁红） |
| ⚪ 灰点 | 未知 | 实例 Running 但流量未知（保守不动） |

**优先级（一个账户只显示一个点，按此顺序判断）：** 启动中（闪烁绿）＞
停止中（闪烁红）＞ 停机/离线（红）＞ 已跳出（灰）＞ 在线（绿）＞ 排队（黄）＞
未知（灰）。注意：“已跳出且已停机”显示红点，但 detail 里同时写
“已跳出且已停机”，不丢跳出信息；“已跳出且 Running”显示灰点。

## 服务器名称可修改

账户管理页每台实例一个卡片，点右上角 ⚙ 打开卡片设置，改“备注”
（服务器显示名）后点右下角“保存更改”。名称必填、1–64 字符、同一分组内
不重名（重名返回 400，前端提示具体错误）。改名只改显示名称，不影响
实例 ID、AccessKey、分组归属、历史流量与事件；改名事件会记一条日志
“旧名 → 新名”。运行概览、域名管理、分组管理、日志里显示的名称都
读同一 `name` 字段，随改名同步更新。

## 主题与登录方式

**主题：** 页眉右上角下拉框切换 白 / 黑 / 跟随系统（默认跟随系统）。
色值角色：**底色是磨砂玻璃**（亮色 70% 透明、夜间磨砂灰 50% 透明）；
**蓝色是按键边框色**；**浅蓝是按键选中色**（选中按键：蓝色边框 +
浅蓝主底色 `#dbeafe` + 蓝字，亮/夜间一致），未选态是灰框灰字。
操作按钮（保存、调度、发送测试等）同样是蓝框 + 透明底 + 蓝字，
悬停时浅蓝；蓝色只作边框/文字色，不作填充色（危险操作红按钮除外）。
**尺寸一律不变**：字号、间距、状态点直径（4px）都与旧版一致，
只换颜色与质感。

**按键式选择（全站统一）：** 除页眉主题切换（下拉框）外，所有选择
（切换模式、阈值动作、恢复方式、站点、日报参与、差值模式、时间切换
模式、间隔、API 刷新、通知渠道启用）都用按键，不用下拉框或单选圆点：
选中蓝框（2px 蓝边 + 蓝字 + 浅蓝主底色 `#dbeafe`，亮/夜间一致）、
未选灰框灰字；选中态内边距少 1px，抵掉边框多出的 1px，外形尺寸与
未选态一致。顶部导航的当前标签页也是同样的选中样式（蓝框 + 浅蓝底）。

**登录：** 首次打开面板先填写管理员信息（管理员名称 + 密码，至少
6 位），之后可用以下任一方式登录：

| 方式 | 说明 |
|---|---|
| 密码 | 管理员密码登录；失败 5 次锁定 15 分钟 |
| 指纹 | WebAuthn（平台指纹 / 安全密钥）。登录后在全局设置“登录与安全”里点“注册指纹”，下次登录页可用指纹登录。需 HTTPS 或 localhost 访问 |
| Google | 需在全局设置“登录方式”里填 Google OAuth 应用的 Client ID / Secret |
| GitHub | 同上，填 GitHub OAuth 应用的 Client ID / Secret |

**OAuth 配置：** 在 Google Cloud Console（创建 OAuth 客户端，类型“Web
应用”）或 GitHub Settings → Developer settings → OAuth Apps 里创建应用，
回调地址填 `{面板地址}/api/auth/oauth/google/callback`（GitHub 把
`google` 换成 `github`，如 `http://服务器:1999/api/auth/oauth/github/callback`），
再把 Client ID / Secret 填到全局设置“登录方式”里（Secret 加密存储，
读出不回显；未配置时登录页对应按钮置灰）。首次用第三方账号登录会绑定
为管理员身份，之后只允许同一账号登录；密码登录与第三方登录共用同一
管理员身份。会话有效期 14 天，登出后失效；未设置管理员时接口不设防
（首次向导状态），设置后全部接口要求登录（Cookie `cdt_session` 或
`Authorization: Bearer <token>`）。

运行概览顶部三个卡片：**实例状态**（在线台数大字 + 排队/已跳出/
停机/离线/未参与 breakdown + 调度运行/暂停与 API 刷新、DNS 确认数，
三个卡片的大数字字号一致，单位与数字同大）、**本月已用流量**、
**本月费用**。顶部导航从左到右：运行概览、账户管理、分组管理、
设置、日志；日志页在设置后面，列最近的事件记录（最新在前，
`GET /api/logs`，最多 100 条）。

## 服务器卡片与单台操作

运行概览的“服务器”区可切 **卡片 / 列表** 两种视图（右上角按键切换，
默认卡片），只显示当前服务器，不再另设“当前 DNS 指向”卡片。
服务器卡片是监控卡样式（见下“服务器卡片”）：

- **卡片：** 顶部是状态点 + 服务器名 + 状态标签，右上角 ⚙ 设置按钮；
  下面是流量用量进度条（绿 <80%、黄 ≥80%、红 ≥保护阈值）；再下面是
  六项指标（小字标签在上、数值在下）：地域、IP、流量、使用率、费用、
  站点；右下角是四个图标操作按钮（刷新 / 启动 / 重启 / 停机，悬停显示
  名称）。账户管理页的卡片是同一形式，指标换成实例、IP、流量、使用率、
  费用、阈值，下面再附一行阈值动作 / 恢复 / 定时 / 日报配置摘要。
- **列表：** 每行后面是同样的四个图标操作按钮。

单台操作接口（见“API”）：`刷新`重新查该台的 ECS 状态 + CDT 流量 +
BSS 费用并写回；`启动`调 StartInstance；`重启`调 RebootInstance；
`停机`调 StopInstance（普通停机，KeepCharging）。操作会记一条事件
（手动启动/重启/停机）。注意：若被停的实例仍在被域名指向，保活可能
在下次调度把它重新拉起；停机前会二次确认。操作进行中图标按钮置灰，
悬停显示“刷新中…”等状态。

**卡片设置（⚙）：** 点卡片右上角 ⚙ 打开卡片设置面板，里面是实例
配置表单（与“实例配置”同款）与“复制配置摘要”；**删除实例放在卡片
设置最下面**（红色按钮，二次确认，删除后域名重新分配）。账户管理页
的卡片同样是右上角 ⚙ 打开设置、删除在最下面。

## 备份与恢复

全局设置页最下面的“备份与恢复”卡片：

- **下载备份：** 导出全部数据为一个 JSON 文件（账户、域名、分组、
  设置、通知配置、指纹凭证、最近 500 条事件、最近 5000 条流量记录，
  以及加密主密钥）。文件名形如 `cdt-backup-20261122-1400.json`。
- **恢复备份：** 选择备份文件后点“恢复备份”，二次确认后整体替换
  当前数据（一个事务内完成，先校验再写；版本不对、缺字段直接拒绝，
  不动现有数据）。管理员密码也会回到备份时的那一个，当前登录会话
  不受影响；恢复后建议检查一遍再调度。

备份文件里有加密主密钥，恢复后 AK Secret 仍可解密使用，所以**备份
文件等同于全部凭据，请妥善保管，不要外发**。接口见“API”：
`GET /api/backup/export`（下载）、`POST /api/backup/restore`（恢复）。
换机器/重装后恢复同样有效：把备份文件拷过去，在新面板里恢复即可。

## 消息推送（每日财报）

每天在推送时间（默认 `00:00`，可自定义）推送一次每日财报，只推已启用的
Telegram / 邮件 / Webhook 渠道。设置项（settings 表，可在通知设置页修改，
保存后立即生效，下次推送按新时间走）：

| 设置项 | 默认值 | 说明 |
|---|---|---|
| `daily_report_enabled` | `1`（开启） | 关闭后不再推送 |
| `daily_report_time` | `00:00`（HH:MM，24 小时制） | 每天这个时间推送一次 |
| `notify_daily_report` | `1`（开启） | 财报事件总开关 |

**触发规则（不重复、不补推）：** 调度器每轮检查一次。用 flags 表记
`daily_report_done:<YYYY-MM-DD>`（当天已推）和 `daily_report_last_check`
（上次检查时刻）。只有当本次检查跨过了当日推送时刻
（上次检查时刻 ＜ 当日推送时刻 ≤ 本次检查时刻）且当天未推过时才推；
改时间后若新时刻已过（上次检查已在新时刻之后），当天不补推，等明天；
全新数据库第一次检查不补推；调度暂停或财报开关关闭时不推，但仍更新
上次检查时刻，避免恢复后立刻补推。通知设置页各渠道框体右下角有
“发送测试财报”按钮（`POST /api/notify/test-daily`，跳过开关与时间、
仍只走已启用渠道）。

**手动推送（不占用定时一次）：** 通知设置页的“立即推送日报”按钮调
`POST /api/notify/send-daily`，随时可推、按已启用渠道真发、记事件
“手动推送日报”。手动推送不读也不写 `daily_report_done:<日期>` 标志，
所以不占用/不消耗当天的定时一次：手动推完，定时时间到了照常再推一次；
反过来定时已推过，当天仍可手动再推。手动推送同样按实例 `daily_report`
过滤（跟随全局看全局开关、单独开启强制推、单独关闭排除）。

**推送内容（每账户 ≤7 行）：** 标题 `[AliCloud每日报告]`、日期
（推送当天）、逐账户：名称 + 规格（CPU/内存，如 `2C0.5G`；取实例规格，
无规格数据时省略括号不造假）、状态（用新状态点文案：在线/排队/已跳出/
离线/启动中，不写 Running 英文）、公网 IP、流量“已用 GB（使用率%）”、
本月费用（按币种，USD 用 `$`、CNY 用 `¥`）、余额、评论 `✅`/`⚠️`
（✅=正常；⚠️=任一异常：超阈值被踢出、离线、流量未知、费用为负，并在
评论后附一句原因，如 `⚠️ 超阈值已跳出`）。

**字体与排版（Telegram 不支持调字号，用结构代替）：** 标题加粗、关键数字
（流量、费用）加粗、不要整条全加粗、不要用超大 emoji 堆叠；每账户之间
空一行 + 分隔线（`──────`）隔开；总长度控制在 Telegram 4096 字符以内，
账户多时分条发送（每条最多 5 个账户，多条时标题后标注“（第 x/y 部分）”）。
邮件 HTML 模板字号：标题 16px、正文 14px、日期/辅助 12px，行高 1.6，
卡片宽度自适应、手机端不溢出。通知设置页已取消推送预览（`GET
/api/notify/daily-preview` 接口保留，仅供调试）。

**通知设置布局：** 通知设置是“设置”页的第三个子标签。E-Mail 与
Telegram 各一个框体，框体左上角依次列排列启用/停用按键（选中蓝、未选灰）；每日财报开关与推送时间、通知
测试、测试财报、立即推送日报都放在各自框体内；**发送测试按键在框体
右下角**，且只发该渠道（`POST /api/notify/test`、
`/api/notify/test-daily`、`/api/notify/send-daily` 均支持 `channel`
参数：`email` / `telegram` / `webhook`）。通知事件（流量保护、DNS
指向变化、备用地址、排除操作、查询异常、故障恢复）开关在下方单独
一个卡片里，应用于所有已启用渠道。

示例推送文本（虚构数据）：

```
[AliCloud每日报告]
日期：2026-11-22

👤 Demo-HK-1 (2C0.5G)
🌐 状态：在线（🟢）
IP：203.0.113.10
流量：37.77 GB (18.89%)
💰 本月费用：$0.47
💰 余额：$0.00
评论：✅

──────

👤 Demo-HK-2
🌐 状态：已跳出（⚪）
IP：203.0.113.11
流量：192.00 GB (96.00%)
💰 本月费用：$0.36
💰 余额：$0.00
评论：⚠️ 超阈值已跳出（使用率 96.0% ≥ 阈值）
```

## 全局设置

“设置”页的第一个子标签（从左到右：全局设置、域名管理、通知设置），
放三项核心设置，都在显眼位置：

| 设置 | 字段 | 默认值 | 说明 |
|---|---|---|---|
| 流量保护阈值 | `protection_threshold_pct` | 95% | 使用率 ≥ 阈值即踢出池子（见“阈值动作”） |
| API 刷新时间 | `api_refresh_minutes` | 5 分钟 | 多久查一次阿里云 CDT/ECS/BSS 状态并重新评估；可选 5/10/15/30/60/120 分钟或自定义分钟数。保存后下次调度按新间隔走 |
| 阈值动作 | `exclusion_action` | `none`（踢出DDNS） | 三选一（见下），默认不停机 |
| 阈值恢复 | `threshold_recovery_action` | `start`（恢复时拉起） | 见“阈值恢复默认拉起” |

DDNS 设置（Cloudflare Token + 时区）在**域名管理**页，不在全局设置；
网页访问框体已移除（监听地址/端口由启动参数决定，默认 `0.0.0.0:1999`）。
登录与安全（改密码、注册指纹、Google/GitHub OAuth 配置）在全局设置页
最下面的“登录与安全”卡片里，见“主题与登录方式”。

**API 刷新时间合并说明：** 旧的 `check_interval_minutes`（检查间隔）
与新的 `api_refresh_minutes` 是同一项，已合并，不留两个互相打架的间隔
设置。保存任一字段时另一字段同步更新；旧库只存了 `check_interval_minutes`
时，读取时把旧值作为新字段的值（迁移）；调度器主循环间隔读新字段。
`/api/status` 的 `scheduler` 同时返回两个字段（新字段为准）。

## 实例配置

账户管理页每台实例一个卡片（标题是服务器名，名称前是状态点，右上角
⚙ 设置按钮），点 ⚙ 打开卡片设置，里面是配置表单；删除实例在卡片
设置最下面。顶部「+ 添加实例」同样打开配置表单（不再用 `prompt()`
弹窗问答）。表单字段（按此顺序；必填项标签加粗）：

| 字段 | 说明 |
|---|---|
| 备注（服务器名）* | 文本框，排在最上面；就是服务器显示名/别名，与行内改名共用同一 `name` 字段，1–64 字符、同组不重名 |
| AccessKey ID * | 必填，文本框 |
| AccessKey Secret | 密码框；已配置时显示“已配置”，留空保持不变——只在填写了新值时才更新加密存储，留空绝不清空 |
| 实例 ID * | 必填，文本框 |
| 站点类型 * | 按键二选一：国际站 global（USD）/ 国内站 cn（CNY）；放在地域之前，因为站点决定调用哪套阿里云端点和币种；选站点后自动重新拉取该站点的地域列表 |
| 地域 * | 下拉（见“区域与站点”），默认中国香港 `cn-hongkong`，允许自定义输入 |
| 月度流量额度 *（GiB） | 数字框，默认 200，必须 > 0 |
| 每日定时开关机 | 开关 toggle，默认关；开启后出现每日开机时间 / 每日关机时间（默认 08:00 / 20:00，HH:MM，开机≠关机）。调度器在对应时间开机/关机；错过时间窗口（进程重启/延迟）时在 1 小时内补偿一次，不每天重复执行。**阈值踢出优先于定时开机**：被踢出的实例定时时间到了也不拉起；关机窗口内恢复拉起也不执行（定时关机优先于恢复拉起） |
| 参与日报推送 | 按键三选：跟随全局 / 单独开启 / 单独关闭，默认跟随全局。跟随全局时按全局每日财报开关走；单独开启时即使全局关了也推这台；单独关闭时即使全局开了也不推这台 |
| 阈值动作（三选一） | 按键四选：跟随全局 / 踢出DDNS / 节省停机 / 停机（见下表），账户级覆盖全局 |
| 阈值恢复 | 按键三选：跟随全局 / 恢复时拉起 / 恢复入池但保持停机（见“阈值恢复默认拉起”） |

添加账户框体里**不出现分组**：新建默认进默认分组，编辑保持原分组。
表单底部右下角是“保存更改”按钮：新建调 `POST /api/accounts`，编辑调
`PUT /api/accounts/{id}`；保存成功有反馈，失败显示具体错误（不只弹通用
错误）。卡片设置里的复制按钮复制该实例的配置摘要（名称、实例 ID、地域、
IP）到剪贴板；删除按钮在卡片设置最下面，二次确认后删实例。

后端字段（`accounts` 表）：`daily_schedule_enabled`（0/1，默认 0）、
`daily_on_time`（默认 `"08:00"`）、`daily_off_time`（默认 `"20:00"`）、
`daily_report`（`follow`/`on`/`off`，默认 `follow`）、`recovery_action`
（空=跟随全局，`start`/`keep_stopped`）、`exclusion_action`（空=跟随全局，
默认跟随全局；自定义时存 `none`/`stop_charging`/`stop`，见“阈值动作”）。
创建/更新时校验：时间 HH:MM、开机≠关机、额度 > 0。每日定时开关机按账户独立判断，不影响分组切换模式。

## 区域与站点

地域可选，来源必须是阿里云接口，不只写死一个下拉列表：

- **站点可选：** 实例配置表单的站点类型就是国际站（`global`，USD）/
  国内站（`cn`，CNY）二选一，默认国际站。站点决定调用哪套阿里云端点与
  BSS 币种（USD/CNY），放在地域选择之前；选站点后自动重新拉取该站点的
  地域列表。新建实例表单、API 默认值、种子演示数据三处默认都是
  `global` + `cn-hongkong`（种子里有 1 台国内站实例是特意用来演示双站点的）。
- **地域来源：** 后端调阿里云 ECS `DescribeRegions` 拿该站点下的可用地域
  列表（`GET /api/aliyun/regions?site=global|cn&account_id=<id>`，有已保存
  账户时用该账户的 AK 调；无账户/未填 AK 时允许传 `access_key_id` +
  `access_key_secret` 走一次临时查询，不保存；也支持
  `GET /api/aliyun/regions?account_id=...` 的简化形式），返回
  `[{region_id, local_name, endpoint}]`，按 `local_name` 排序。
- **现有服务器位置发现：** `POST /api/aliyun/discover-regions`（传
  `account_id` 或 AK）先 `DescribeInstances`（分页）汇总该账号下已有实例
  实际所在的地域，去重后返回（标注“已有实例所在地域”），再合并
  `DescribeRegions` 的全量列表（标注“全部可用地域”），前端下拉分两组显示。
- **自动发现：** 实例配置表单填了实例 ID 后点“自动发现”，调
  `POST /api/aliyun/find-instance-region`，按实例 ID 查出该实例真实所在
  地域并自动填入下拉；查不到时报错提示，不静默填错。
- **失败回退：** 接口失败时（AK 错、无权限、网络不通）返回明确错误
  （502 + `detail` + `fallback_regions`），前端回退到内置常用地域列表
  （中国香港 `cn-hongkong`、新加坡 `ap-southeast-1`、东京
  `ap-northeast-1`、硅谷 `us-west-1`、法兰克福 `eu-central-1`、北京
  `cn-beijing`、上海 `cn-shanghai`、深圳 `cn-shenzhen`、杭州
  `cn-hangzhou`），并在下拉下方提示“接口未取到，已用内置列表”。
- **后端实现：** `app/aliyun.py` 的 `describe_regions()` /
  `discover_instance_regions()` / `find_instance_region()`，复用站点的
  endpoint 选择逻辑（国际站与国内站端点不同，BSS 币种随站点走 USD/CNY）。
  注意：阿里云 Python SDK 的请求类在各自的子模块里（如
  `aliyunsdkecs.request.v20140526.DescribeInstancesRequest`），必须从子模块
  import 类——直接 `from aliyunsdkecs.request.v20140526 import
  DescribeInstancesRequest` 得到的是模块，调用会报
  `TypeError: 'module' object is not callable`（2026-11-22 新测试发现，
  已修复）。

## 阈值动作（三选一）

到达阈值默认不停机，只是跳出 DDNS。到阈值后的动作是三选一的单选
（radio），不是多选；全局设置和账户级（实例配置表单）都用同一组三选一、
同一套文案，默认选中第 1 项：

| # | 名称 | 内部值 | 行为 |
|---|---|---|---|
| 1（默认） | 踢出DDNS（默认，不停机） | `none` | 只把实例踢出池子、不再参与 DDNS 解析，域名切到下一台；不停机，实例继续跑 |
| 2 | 节省停机 | `stop_charging` | 先切 DNS，再停机并释放计算资源、只保留磁盘按量计费（StopCharging） |
| 3 | 停机 | `stop` | 先切 DNS，再普通停机（Stop） |

三档互斥，选了一档另外两档自动取消；保存时只存一档的值。内部值沿用
现有 `exclusion_action` 的 `none` / `stop_charging` / `stop` 三值（`none`
即第 1 项），不为改名全库重命名；API 校验同时接受别名 `evict_ddns`
（等价于 `none`），收到时归一化存 `none`，保证旧数据和旧调用不坏。
旧文案“无（只踢出、不停机）”已统一改成“踢出DDNS”。停机永远在切完 DNS
之后，同一台只停一次（多域名共享同一账户时去重）。

**账户级：跟随全局（默认）/ 自定义：** 账户级先分两档——“跟随全局”
（默认，新建账户不填就是跟随全局，`accounts.exclusion_action` 存空字符
串 `""` 或 `null`）；“自定义”时再从同一组三选一里选一档，存
`none` / `stop_charging` / `stop`。引擎取有效值时：账户级为空则用全局值，
否则用账户级值。全局改成停机后，跟随全局的账户随之生效为停机；自定义
为踢出DDNS 的账户不受全局改动影响。旧账户的 `exclusion_action='none'`
保持原样（它是之前“默认踢出DDNS”的显式值，行为不变，不批量改成跟随
全局；全局也是踢出DDNS 时，两者行为相同）。前端表单里跟随全局时显示
当前全局值是多少（例如“跟随全局：当前为 踢出DDNS”）。

## 阈值恢复默认拉起

实例因到阈值被踢出池子后，当用量回落时默认自动恢复入池，并且如果该
实例当时是停机状态（因阈值动作选了停机/节省停机而停的），恢复时默认
调用开机把它拉起（`StartInstance`），再重新参与 DDNS 分配。阈值动作
选“踢出DDNS”的实例本身一直在跑，恢复时拉起是空操作，不重复调用开机。

- **开关：** 全局设置 `threshold_recovery_action`，`start`（默认，恢复时
  拉起）/ `keep_stopped`（恢复入池但保持停机，等保活/手动再拉起），在
  全局设置页“阈值”区域显示；账户级可单独覆盖（`accounts.recovery_action`
  字段，空=跟随全局）。
- **防抖动：** 用量回落判定用滞后值——只有使用率回落到 **阈值 − 2%** 以下
  才算恢复（比如阈值 95%，回落到 93% 以下才恢复拉起），避免在 95% 上下
  反复横跳踢出又拉起；同一实例恢复后 1 小时内不再因同一原因重复拉起
  （用 flags 记 `recovered_at`）。
- **事件：** 恢复时记事件“阈值恢复，已拉起：\<名>（使用率 X% < 恢复线 Y%）”；
  若 `recovery_action=keep_stopped`，记事件“阈值恢复，已入池，保持停机：\<名>”。
- **与每日定时开关机的关系：** 每日定时关机窗口内，恢复拉起也不执行
  （定时关机优先于恢复拉起）；定时开机窗口内恢复拉起正常执行。

## 流量更新后自动重新加入 DDNS

保证：任何实例因到阈值被踢出后，只要流量数据一更新、用量回落到可恢复
范围，就必须自动重新加入 DDNS 池子，不需要手动点“恢复/重新加入”，也不
会出现“用量已经回落了、但还一直被排除在池子外”的状态。所有实例都适用，
没有例外。

- **触发点：** 每次 API 刷新（按 `api_refresh_minutes` 拉取 CDT/BSS 流量后）
  都重新评估全部被踢出实例；调度器的每一轮调度开始前也先做一次恢复评估，
  再算分配。流量更新的来源包括：CDT 用量下降、月度额度重置（新月份已用
  流量清零/大幅下降）、账户级阈值被调高、额度被调大。
- **恢复条件：** 沿用恢复线——使用率回落到 **阈值 − 2%** 以下即恢复
  （阈值 95% 时回落到 93% 以下）；同时加一个兜底：若某实例被踢出超过
  **24 小时**且最新一次流量更新显示用量低于阈值（哪怕没到恢复线，如
  94%），也自动恢复并记事件说明是“超时兜底恢复”，避免恢复线把实例永久
  卡在外面。恢复时清除 `evicted` 标志、清除已停机标记的恢复待办，按
  `threshold_recovery_action` 执行拉起（默认拉起；三选一选了踢出DDNS
  的实例本来就在跑，不调开机）。
- **恢复后立即参与分配：** 恢复的实例在同一轮调度里就重新进入候选池，
  按用量最低优先的规则参与域名分配，不要等下一轮；若它是用量最低的，
  同一轮就把它重新指回去。dry-run 也体现这一点（`threshold_recovery`
  字段列出本轮恢复的实例，`assignments` 已是恢复后的分配）。
- **可观测性：** 每次流量更新后若有实例恢复，记事件“流量更新，已自动
  重新加入 DDNS：\<名>（使用率 X% < 恢复线 Y%）”；状态点随之从灰点
  “已跳出”变回绿点/黄点（在线/排队）；前端账户页不留“已跳出”残留。
- **兜底与防护：** 流量接口本次查不到（unknown）时不恢复也不误判，保持
  原状态；同一实例恢复后 1 小时内不再重复记恢复事件；月度重置导致全部
  实例同时恢复时，逐个恢复、逐个记事件，不要只恢复第一个。
- **实现位置：** `app/scheduler.py` 的 `_evaluate_threshold_recovery()`
  （纯计算：恢复线 + 24 小时兜底，在 `run_once()` 里分配计算之前调用，
  恢复的账户从 `previously_evicted_ids` 里去掉）与
  `_persist_threshold_recovery()`（真实轮次在执行 Plan 之后调用：清旗标、
  记事件、按需拉起；dry-run 不调用）；`app/engine.py` 的滞后判断
  （`RECOVERY_HYSTERESIS_PCT = 2.0`，`previously_evicted_ids` 参数）与
  `app/api.py` 的 `_rate_and_threshold()`（状态点与引擎用同一滞后规则）。

## 配置流程

1. **账户管理** → 点「+ 添加实例」，在实例配置表单里填备注（服务器名，
   排最上面）、AccessKey ID/Secret、实例 ID、站点、地域、月度流量额度；
   可再配每日定时开关机、日报推送、阈值动作与恢复方式（见“实例配置”
   一节；添加框体里没有分组）。需 RAM 权限：
   `AliyunECSFullAccess`、`AliyunCDTReadOnlyAccess`、
   `AliyunBSSReadOnlyAccess`（缺 BSS 只影响费用显示）。
2. **设置 → 域名管理**（子标签，从左到右依次是 全局设置、域名管理、
   通知设置）→ 添加域名（Zone/记录 ID 自动发现），按箭头调优先级；
   DDNS 设置（Cloudflare Token + 时区）也在本页。
3. **设置 → 全局设置** → 调度参数、夜间窗口、备用地址、登录与安全、
   备份与恢复。
4. **设置 → 通知设置** → E-Mail / Telegram 各一个框体，通知专用代理默认 `http://127.0.0.1:7070`。
5. 回「运行概览」点「调度试算」（dry-run，不调任何 VPS/DNS），确认无误后点「立即调度」。

## API（节选）

| 接口 | 说明 |
|---|---|
| `GET /api/health` | 健康检查 |
| `GET /api/auth/status` | 登录状态：管理员是否已设置、各登录方式是否可用 |
| `POST /api/auth/setup` | 首次设置管理员（名称 + 密码） |
| `POST /api/auth/login`，`POST /api/auth/logout` | 密码登录 / 登出 |
| `POST /api/auth/change-password` | 修改管理员密码 |
| `GET /api/auth/oauth/{google,github}/login`，`GET /api/auth/oauth/{google,github}/callback` | Google / GitHub 第三方登录 |
| `POST /api/auth/webauthn/register/begin|finish`，`POST /api/auth/webauthn/authenticate/begin|finish`，`GET /api/auth/webauthn/list`，`DELETE /api/auth/webauthn/{id}` | 指纹登录（WebAuthn）注册 / 登录 / 管理 |
| `GET /api/backup/export` | 下载备份（整库 JSON 文件） |
| `POST /api/backup/restore` | 恢复备份（整体替换当前数据） |
| `GET /api/status` | 概览：流量合计、费用合计、账户、域名、最近活动 |
| `GET/POST/PUT/DELETE /api/accounts` | 账户管理 |
| `POST /api/accounts/{id}/refresh` | 刷新单台云状态（ECS 状态 + CDT 流量 + BSS） |
| `POST /api/accounts/{id}/start`，`POST /api/accounts/{id}/restart`，`POST /api/accounts/{id}/stop` | 单台启动 / 重启 / 停机 |
| `GET/POST/PUT/DELETE /api/domains`，`POST /api/domains/reorder` | 域名管理与排序 |
| `GET/PUT /api/settings` | 全局设置 |
| `POST /api/schedule/dry-run` | 调度试算（零外部调用） |
| `POST /api/schedule/run` | 立即调度 |
| `POST /api/schedule/pause` | 暂停/恢复调度 |
| `POST /api/cloud/status` | 刷新云端快照 |
| `GET /api/aliyun/regions?site=global\|cn&account_id=<id>` | 该站点下的可用地域列表（DescribeRegions）；无账户时可用临时 `access_key_id` + `access_key_secret` 查询，不保存 |
| `POST /api/aliyun/discover-regions` | 已有实例所在地域 + 全部可用地域（前端下拉分两组） |
| `POST /api/aliyun/find-instance-region` | 按实例 ID 自动发现其真实所在地域 |
| `GET /api/notify/daily-preview`，`POST /api/notify/test-daily` | 每日财报预览 / 发送测试财报 |
| `POST /api/notify/send-daily` | 手动推送日报（随时可推，不占用当天定时一次，记事件“手动推送日报”） |
| `POST /api/notify/test` | 发送测试通知（`channel` 参数可只发一个渠道） |
| `GET /api/logs`，`GET /api/jobs/{id}` | 日志 / 任务结果 |

## 安全

- 管理员密码 PBKDF2-HMAC-SHA256（260k 轮），登录失败 5 次锁定 15 分钟；
  会话 token 32 字节随机、14 天有效期，登出即失效。
- 第三方登录首次绑定管理员身份，之后只允许同一账号；指纹凭证
  （WebAuthn）存公钥与 sign_count，私钥永不离开用户设备。
- AccessKey Secret 用 AES-GCM 加密，主密钥在 `data/master.key`。
- 公网部署请在前面套 HTTPS 反向代理，只开放 443；不要把 `data/`、Token 提交到 Git。
- DDNS 只在 IP 真的变化时 PUT；同名多条记录时报告冲突，不自动删改。

## 开发与测试

```bash
pip install -r requirements.txt
python -m pytest -q
python scripts/seed_demo.py   # 写入虚构演示数据（example.xyz / 203.0.113.x 测试网段）
cd web && npm ci && npm run build   # 产物输出到 app/static，后端直接托管
```

## 目录

```
app/        后端：main/api/db/aliyun/engine/scheduler/ddns/notify/security/settings/oauth/backup
web/        前端：React 18 + TS + Vite（运行概览/账户/域名/设置/通知）
scripts/    seed_demo.py（虚构演示数据）
tests/      引擎/DDNS/安全/API/登录/单台操作/备份测试
Dockerfile  python:3.13-slim 多阶段构建（前端 Node 22 + 后端 slim）
```
