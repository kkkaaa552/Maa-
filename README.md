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

1. 到 [Releases](../../releases) 下载 `MAWH-v*-win-x86_64.zip`
   （`MAWH` = Maa + WuHua。GitHub 上传资产时会剥掉文件名里的非 ASCII，所以磁盘上是
   这个 ASCII 名；release 页面上显示的中文名「物华弥新MAA-v*」是资产的 label，同一个文件）
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

发布包已内置嵌入式 Python 3.13 与全部依赖，**不需要你自己装 Python**。

---

## 许可

- 本项目代码：**GPL-3.0**，全文见 [LICENSE](LICENSE)
- `libs/` `runtimes/` `plugins/` 与 `物华弥新MAA.exe` 来自
  [MFAAvalonia](https://github.com/MaaXYZ/MFAAvalonia)（GPL-3.0），随本项目一同分发
- `resource/model/ocr/` 来自
  [MaaCommonAssets](https://github.com/MaaXYZ/MaaCommonAssets)（PP-OCRv6，按其原始许可）
- 运行期依赖按其自身许可分发：`maafw`（LGPL-3.0）、`MaaAgentBinary`（AGPL-3.0）、
  `opencv-python`（MIT）、`numpy`（BSD-3-Clause）、`Pillow`（HPND）、`jsonschema`（MIT），
  各自许可原文随包保留在 `python/Lib/site-packages/*.dist-info/licenses/`
- 游戏素材（图标、模板图）版权归《物华弥新》开发运营方所有，本项目仅作技术研究与个人使用
