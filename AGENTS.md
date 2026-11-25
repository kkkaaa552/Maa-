# 项目约定（给 AI 协作者 / 后续接手的人）

## 唯一改动落点

**所有增删功能、改配置、改 pipeline、改 agent 代码，只改这个目录：**

    D:\mixian\my-maa

不要改下面这些地方——它们是构建产物或运行副本，改了会被下次打包覆盖，而且会让
「源码到底是什么样」这件事失去唯一真相：

| 路径 | 性质 | 说明 |
|---|---|---|
| `D:\物华弥新maa\` | 发行目录（解压后的发布包） | 用于实机跑任务、看界面效果，是 `build.py` 的产物，不是源 |
| `D:\mixian\my-maa\install\` | 构建中间产物 | `build.py` 的输出目录，`EXCLUDE_TOP` 里排除自己 |
| `D:\mixian\my-maa\dist\` | 压缩包产物 | `build.py` 打出来的 zip |

要更新发行目录，走构建：

```
cd D:\mixian\my-maa
python tools\build.py 0.1.0 win x86_64
```

## 改完怎么验证

源码工程根目录本身就能跑，不必先打包：

```
D:\mixian\my-maa\物华弥新MAA.exe
```

它读本目录的 `interface.json`、`resource\`、`config\`、`agent\`。注意
`interface.json` 的 `agent.child_exec` 在源码里是 `.venv/Scripts/python.exe`，
所以第一次跑之前要有可用的 `.venv`（Python 3.13 + `tools\requirements.txt`）。
`build.py:643` 打包时会把它改写成 `./python/python.exe`。

改完必须重启客户端才生效：`config\` 与 `interface.json` 在进程启动时读入，
没有热重载。

## 两份 interface.json 的关系

- 源：`D:\mixian\my-maa\interface.json`
- 产物：`D:\mixian\my-maa\install\interface.json`（`step_interface()` 生成，
  会自动改写 `child_exec`、覆盖 `version`、缩进统一成 2 空格 + 尾随换行）

两份文件允许存在、也只允许存在这两处差异，都是 `build.py` 有意造成的：

| 键 | 源码值 | 产物值 |
|---|---|---|
| `agent.child_exec` | `.venv/Scripts/python.exe` | `./python/python.exe` |
| `license` | `GPL-3.0` | `MIT`（历史遗留，不影响功能） |

除此之外结构必须完全一致。`step_interface()` 不做任何字段过滤，所以源码里写了
什么就会原样进包。

## 界面上的「?」是怎么来的（别再重复排查）

`interface.json` 里 `resource` / `controller` / `option`（含 option 的 case）的
`description` 只要非空，MFAAvalonia 就会在对应位置画一个「?」——那是
`TooltipBlock.axaml` 里硬编码的 `Text="?"`，纯绘制，不在 UIA 树里，只有截图能看到。
没有运行时开关能关，要清就删这些对象的 `description` 字段。

`task` 与 `preset` 的 `description` 不出「?」：task 的喂「任务说明」面板，preset 的
喂顶部 tab 卡片里的内联文本。`group` 的只在「添加任务」对话框的分组标题旁出现。

删 description 别直接删行——会留下悬空逗号让 JSON 解析失败。用 `json.load` /
`json.dumps` 往返；若必须按行删，删完用正则收掉 `,` + 换行 + 空白 + `}` 或 `]`
之前的尾随逗号，`json.loads` 通过再写盘。

## 改动纪律

- 改任何 `*.json` / `*.py` 之前先备份成 `<文件名>.bak-YYYYMMDD-HHMMSS`，同目录。
- 写盘前 `json.loads` / `ast.parse` 校验，通过了再落。
- 中文路径一律 `-LiteralPath`（PowerShell），或直接用 Python 的 `io.open`。
- 判断「跑的是不是新代码」看 python 子进程的 `StartTime` 是否晚于文件 `mtime`，
  不要只看日志有没有新行。
- 在 PowerShell 里用 `@"..."@` 双引号 here-string 写文件时，内容里的反引号会被当
  转义符吃掉后一个字符（`` `b`` → 退格、`` `a`` → 响铃、`` `t``/`` `n``/`` `r`` 同理），
  写 Markdown 带行内代码的内容会悄悄损坏。写文件用单引号 here-string 或直接调
  编辑工具，别用双引号 here-string。

## 关键文件

| 文件 | 作用 |
|---|---|
| `interface.json` | Task / Option / Preset / Controller / Resource 声明 |
| `resource\pipeline\*.json` | 各任务的 MaaFramework pipeline（1280×720 画布） |
| `agent\warehouse_agent.py` | agent 入口，注册 custom action |
| `agent\combat_balance.py` | 作战均衡决策 + 轮换（custom action 名 `作战均衡决策`） |
| `agent\mizongpan.py` | 迷踪盘求解（custom action 名 `迷踪盘求解`） |
| `tools\build.py` | 打包脚本，`step_*` 系列 |
| `HANDOFF-*.md` | 各阶段交接记录，改动前值得先扫一眼 |

## 当前已知事实

- 仓库快照 `data\warehouse\*.json` 是**静态文件**，刷完不更新。所以均衡决策收敛
  靠的是 `combat_balance.py` 里的 `DONE` 集合，不是重读库存。要跟上真实库存得在
  轮次之间重跑「仓库识别」任务。
- 实测读数（2026-11-24T16:52 快照）：冬谷币 2,371,600 / 深度社会学 75 / 核定牌按
  等级 I=106 II=19 III=28 IV=203 V=82。冬谷币低于阈值 270 万，所以单独一个
  「全达标才轮换」的条件永远不会触发，轮换必须有第二条触发路径。
- 界面分辨率必须是 1280×720，否则模板坐标全错。
