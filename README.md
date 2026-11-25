# 物华弥新 MAA

基于 [MaaFramework](https://github.com/MaaXYZ/MaaFramework) 与
[MFAAvalonia](https://github.com/MaaXYZ/MFAAvalonia) 的《物华弥新》自动化助手。

目前实现的任务：

| 任务 | 状态 | 说明 |
|---|---|---|
| 仓库识别 | ✅ | 进入物资库，逐页滚动识别物料 / 资源 / 票券的全部格子，输出 `report/仓库快照.md` |
| 派遣公司 | 部分 | 制造、入驻 |
| 器者征集 / 均衡材料刷取 / 迷踪盘 / 奖励领取 | 有 pipeline 骨架 | 未全部验证 |

---

## 直接使用（下载发布包）

1. 到 [Releases](../../releases) 下载 `物华弥新MAA-v*-win-x86_64.zip`
2. **解压到纯英文路径**（中文路径可能让 MaaFramework 找不到资源）
3. 双击 `物华弥新MAA.exe`

### 环境要求

| 项 | 要求 |
|---|---|
| 系统 | Windows 10 / 11 x64 |
| 运行时 | [.NET 10 Runtime](https://dotnet.microsoft.com/download/dotnet/10.0)（官方发布包是非自包含构建） |
| 模拟器 | MuMu 12 / MuMu Pro（其它模拟器理论上可以，需手动填 adb 路径） |
| 分辨率 | **1280×720**（MaaFramework 按 720p 画布识别，其它分辨率模板坐标全错） |
| 游戏 | 《物华弥新》国服，包名 `com.cipaishe.laigu` |

首次运行需要在「设置 → 设备」里指定 MuMu 的 `adb.exe` 路径，或先在命令行执行：

```
adb connect 127.0.0.1:16384
```

发布包已内置嵌入式 Python 3.13 与全部依赖，**不需要你自己装 Python**。

---

## 从源码运行（开发）

```powershell
# 1. 建 venv（Python 3.13）
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r tools\requirements.txt

# 2. 直接跑 GUI
.\物华弥新MAA.exe

# 3. 或者用 Python 手工模式调 pipeline（原生库走 runtimes/，maaenv.py 自己找）
.venv\Scripts\python.exe main.py

# 4. 单独验证仓库识别
.venv\Scripts\python.exe agent\warehouse_agent.py --selftest
```

### 目录结构

```
├─ interface.json          Project Interface V2 配置（任务、选项、控制器）
├─ main.py                 Python 手工模式入口（不经 GUI 直接跑任务）
├─ maaenv.py               原生库 bootstrap（设 MAAFW_BINARY_PATH、sys.path）
├─ agent/                  Agent 子进程：自定义动作、OCR 后处理、仓库识别逻辑
├─ tools/                  识别与报表工具
│   ├─ wh_scan.py          图标模板匹配 + 数量窗口定位（核心）
│   ├─ wh_counts.py        数量数字切字与分类（字模来自 data/digits/）
│   ├─ wh_report.py        生成 report/仓库快照.md
│   ├─ build_whitelist.py  从图鉴 xlsx 生成 data/whitelist.json
│   ├─ sync_icons.py       把图鉴 icons 补进 resource/image/物品/
│   ├─ configure.py        校验 / 补齐 OCR 模型
│   └─ build.py            打包发布包
├─ resource/               资源包（MaaFramework 的 bundle 根）
│   ├─ pipeline/           pipeline JSON
│   ├─ image/              模板图（物品/ 是图标库，单位/ 是单位图标）
│   └─ model/ocr/          PP-OCRv6 模型（det.onnx + rec.onnx + keys.txt）
├─ data/                   持久数据
│   ├─ digits/             数字字模 d0~d9.png（数量识别的基准）
│   ├─ refbook/            三份图鉴 xlsx + icons（白名单与图标的可追溯来源）
│   ├─ whitelist.json      允许识别的物品名单（由三份图鉴生成）
│   └─ warehouse/          上一次扫描结果与标注数据
└─ libs/ runtimes/ plugins/  MFAAvalonia 运行库（与官方 v2.16.6 一致）
```

发布包里的目录结构与源码不完全一样，两处需要知道：

- `runtimes/win-x64/native/` 是**唯一**一份 MaaFramework 原生库（见下文「包里只有一套」）；
- `config/config.template.json` 是首装种子而非 `config.json`（见下文「首启配置」）。

另外 `Assets/logo.ico`（窗口标题栏与托盘图标）由 `build.py` 从 `resource/image/logo.png`
现场生成 8 个尺寸，不在仓库里。没有它时 MFAAvalonia 会回落到自带的内嵌 logo。

---

## 打包

```powershell
# 完整包（含嵌入式 Python，压缩后约 223 MB / 解压 502 MB）
python tools\build.py 0.1.0

# 不含 Python（用户需自装，压缩后约 141 MB）
python tools\build.py 0.1.0 --no-python

# 只拼 install/ 不压缩
python tools\build.py 0.1.0 --no-zip
```

产物在 `dist/`。CI 会在打 `v*` tag 时自动跑到 Release，见
[`.github/workflows/install.yml`](.github/workflows/install.yml)。

体积构成（压缩后，v0.1.0 实测）：`python/` 81.4 MB、`libs/` 63.9 MB、`runtimes/` 34.9 MB、
`resource/` 31.6 MB、其他合计约 10.5 MB。

打包逻辑的几个取舍写在 `tools/build.py` 顶部的 docstring 里，改之前先读一遍。

### 包里只有一套 MaaFramework

发布包内 MaaFramework 原生库**只有一份**，在 `runtimes/win-x64/native/`（v5.16.1，21 个 dll）。

这一点值得单独说明，因为它和常见做法不同：

- 项目源码里原本有两套 —— `runtimes/win-x64/native/`（随 MFAAvalonia 发布包分发，v5.14.3）
  和 `python/Lib/site-packages/maa/bin/`（`pip install MaaFw` 装进来的，v5.16.1），各自 86 MB。
- 打包时 `build.py` 的 `step_maa_single_source()` 把 5.16.1 那套覆盖进 `runtimes/`，
  再把 `maa/bin/` 下的 dll 删掉，净省 86.4 MB 解压体积 / 34.5 MB 压缩体积。
- GUI 侧读 `runtimes/`，绑定层是 `MaaFramework.Binding` 5.10.0，按**文件名**（不含版本号）
  定位原生库，实测能正常驱动 5.16.1 —— 日志里 `MaaFramework 版本：v5.16.1`。
- agent 侧（Python）默认读 `maa/bin/`，所以 `agent/warehouse_agent.py` 启动时会把
  `MAAFW_BINARY_PATH` 指向 `runtimes/win-x64/native/`（外部已设了有效路径时不覆盖）。

**改这一块要小心**：把 `maa/bin/` 的 dll 删掉而不设 `MAAFW_BINARY_PATH`，agent 会以
`ModuleNotFoundError` 起不来，且两个自定义动作（作战均衡决策 / 迷踪盘求解）会**静默失效**
—— GUI 上完全看不出来，只有 `logs/` 里有一行 Traceback。`build.py` 的自检里专门有一段
复现这个场景：清空 `MAAFW_BINARY_PATH` 跑 `agent/warehouse_agent.py --selftest`，
输出里有 `ModuleNotFoundError` 就判失败。

### 首启配置走 config.template.json

`config/config.template.json` 是 MFAAvalonia 约定的**首装种子**，不是默认配置：

- 只有当目标目录里不存在任何用户配置（`config/config.json`、`config/instances/` 等）时，
  它才会被转成 `config.json`；
- 老用户解压覆盖新版本时，程序检测到已有配置就只读种子里的背景默认，
  **不会覆盖用户改过的语言 / 主题 / 布局**。

所以打包时写入的是 `config.template.json`（4 个键，110 B），不写 `config.json` ——
写 `config.json` 会让下一次发版把用户的设置直接盖掉。

---

## 仓库识别的实现要点

这一块踩过的坑比代码本身多，记下来省得下次重踩。

### 识别流程

1. `TemplateMatch` 定位「物资库」入口（`物资库.png`，roi 在右下角）→ 点击
2. 依次点开 物料 / 资源 / 票券 三个页签
3. 每页向下滚动，逐屏 `post_screencap` 截图，按 `X0,Y0,PX,PY = 365,130,150,128`
   的 6×4 网格切格
4. 每格用 104×104 缩放的物品图标做归一化互相关（NCC），取最高分为候选
5. 数量区（格内 `x+24..x+108, y+88..y+108`）用 `data/digits/` 的字模逐字识别

### 几个必须知道的常数

| 常数 | 值 | 为什么是这个数 |
|---|---|---|
| `HIT_SCORE` | 0.30 | 图标 NCC 的绝对下限，低于此判「空」 |
| `HIT_MARGIN` | 0.03 | 第一名与第二名的分差下限，低于此判「存疑」 |
| `THR`（`wh_counts.py`） | **155** | 切字灰度阈值。原本是 165，末屏整帧偏暗（峰值仅 203~221）时 `1` 会被当噪声丢掉，把 120 读成 20 |
| `CHROMA_FAMILY_GAP` | 0.01 | 词根族内色度复判的放行门槛。石墨条族 margin 有 0.05~0.15，核定牌族只有 0.0002~0.0021，门槛落在这个空档里 |
| `DIFF_FAMILY_GAP_MIN` | 0.05 | 差异区掩膜的放行门槛，专治「盈级装备匣·远击 / ·战略」这类整图 NCC 0.9598 的族 |

### 三处「看起来能调、其实调不动」的地方

- **`CHROMA_W`（色度权重）**：0.3 → 0.5 只是把「鎏金石墨条 uncertain」换成「游历实绩 uncertain」，总数一分不变。
- **数量窗口 `DY0/DY1`**：放大到 `80..125` 会让连在一起的字粘成一团，读数反而更差。
- **透明底合成底色**：物品图标是 RGBA，合成到固定灰（现在 30）。改成 90 会让鎏金石墨条的 margin 从 0.0166 掉到 0.0046。

### 白名单机制

`data/whitelist.json` 由 `tools/build_whitelist.py` 从三份图鉴 xlsx 生成，共 169 个名字
（`resource/image/物品/` 里有 171 个 png，差的 `改名卡`、`冬谷币2` 在 `deny` 列表里）。

图鉴原件随仓库一起放在 `data/refbook/`（三份 xlsx + 167 个 icons，4.8 MB），
所以重新生成名单**不需要任何外部目录**：

```powershell
# 图鉴更新了：把新的 icons 补进图标库，再重生成白名单
python tools\sync_icons.py --check          # 先看差多少
python tools\sync_icons.py                  # 补进 resource/image/物品/
python tools\build_whitelist.py             # 重生成 data/whitelist.json
```

若要用一份新的图鉴导出（不在 `data/refbook/` 里），两个脚本都支持 `--src`：

```powershell
python tools\sync_icons.py   --src "D:\某处\导出目录"
python tools\build_whitelist.py --src "D:\某处\导出目录"
```

`wh_scan.load_icons()` 只加载白名单内的模板。名单外的物品额外用
`resource/image/_deny/` 里的负样本模板判定，命中后**静默跳过**（不识别、不报告），
避免它们被强配给白名单里最像的名字从而污染数量统计。

要临时识别一个图鉴没收录的物品，往 `tools/build_whitelist.py` 的 `EXTRA` 里加一行
（值是该物品归属的分类），再在 `resource/image/物品/` 下放一张同名 png，然后重跑白名单脚本。

---

## 许可

- 本项目代码：见 [LICENSE](LICENSE)
- `libs/` `runtimes/` `plugins/` 与 `物华弥新MAA.exe` 来自
  [MFAAvalonia](https://github.com/MaaXYZ/MFAAvalonia)（GPL-3.0），随本项目一同分发
- `resource/model/ocr/` 来自
  [MaaCommonAssets](https://github.com/MaaXYZ/MaaCommonAssets)（PP-OCRv6，按其原始许可）
- 游戏素材（图标、模板图）版权归《物华弥新》开发运营方所有，本项目仅作技术研究与个人使用
