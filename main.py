"""my-maa —— 基于 MaaFramework 的 Android 游戏自动化工程（Python 驱动 + JSON Pipeline）。

命令：
    python main.py doctor            环境自检（原生库 / adb / 设备 / bundle / OCR 模型）
    python main.py list              列出 bundle 里所有节点与 interface.json 里声明的任务
    python main.py run <entry>       执行一条任务链（entry 为节点名）
    python main.py probe <node>      只看识别结果，不执行动作（安全：不点击）
    python main.py grab <out.png>    截一张当前画面（720p，按 controller 短边缩放）
    python main.py app <package>     启动应用

公共参数：
    --address 127.0.0.1:16384        设备地址（默认读 mastate.json 或内置默认）
    --adb "<adb.exe 路径>"            adb 可执行文件（默认自动探测）
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import maaenv  # noqa: E402  (必须早于 import maa)

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from maa.controller import AdbController  # noqa: E402
from maa.resource import Resource  # noqa: E402
from maa.tasker import Tasker  # noqa: E402
from maa.toolkit import Toolkit  # noqa: E402

STATE_FILE = ROOT / "mastate.json"
DEFAULT_ADB_CANDIDATES = [
    r"D:\Program Files\Netease\MuMu\nx_device\15.0\shell\adb.exe",
    r"C:\Program Files\Netease\MuMuPlayer-12.0\shell\adb.exe",
    r"D:\Program Files\Netease\MuMuPlayer-12.0\shell\adb.exe",
]
DEFAULT_ADDRESS = "127.0.0.1:16384"
# 目标包名：多开模拟器时用它认「这台是不是我们要的那台」
TARGET_PACKAGE = "com.cipaishe.laigu"


# ---------------------------------------------------------------- 基础设施


def load_state() -> dict:
    if STATE_FILE.is_file():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def pick_adb(explicit: str | None) -> str:
    if explicit:
        return explicit
    state = load_state()
    if state.get("adb"):
        return state["adb"]
    for c in DEFAULT_ADB_CANDIDATES:
        if Path(c).is_file():
            return c
    devices = Toolkit.find_adb_devices()
    if devices:
        return str(devices[0].adb_path)
    raise SystemExit("未找到 adb，请用 --adb 指定")


def _connected(adb: str, address: str) -> AdbController | None:
    ctrl = AdbController(adb_path=adb, address=address)
    ctrl.post_connection().wait()
    return ctrl if ctrl.connected else None


def probe_device(adb: str, address: str) -> dict:
    """读一台设备的型号、分辨率、前台包名。多开时靠这个认机，别靠端口。"""
    import subprocess

    def sh(args: list[str]) -> str:
        try:
            r = subprocess.run(
                [adb, "-s", address, *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
            )
            return r.stdout or ""
        except Exception:
            return ""

    focus = ""
    for line in sh(["shell", "dumpsys", "window"]).splitlines():
        if "mCurrentFocus=" in line:
            seg = line.split("u0 ", 1)[-1].strip()
            if "/" in seg:
                focus = seg.split("/")[0].rstrip(" }")
            break
    return {
        "address": address,
        "model": sh(["shell", "getprop", "ro.product.model"]).strip(),
        "size": sh(["shell", "wm", "size"]).strip().replace("Physical size: ", ""),
        "focus": focus,
    }


def _candidates(adb: str, address: str) -> list[str]:
    seen = [address]
    for d in Toolkit.find_adb_devices(adb):
        alt = str(d.address)
        if alt not in seen:
            seen.append(alt)
    return seen


def build_controller(adb: str, address: str, quiet: bool = False) -> AdbController:
    """按「前台是不是目标包」挑设备，不按端口挑。

    MuMu 多开时每台实例各占一个端口（16384 是主实例、16416/5557/7555 可能是另一台），
    光取「第一个能连上的」会连到别的游戏上——2026-11-18 就这么踩过一次：
    TabsDemo 跑到明日方舟的战斗界面上，大厅模板分数从 0.916 掉到 0.21。
    所以顺序是：给定地址 → 前台包名匹配 → 其余能连上的（并打警告）。
    """
    order = _candidates(adb, address)
    fallback: tuple[str, AdbController] | None = None

    for cand in order:
        ctrl = _connected(adb, cand)
        if ctrl is None:
            continue
        info = probe_device(adb, cand)
        if info["focus"] == TARGET_PACKAGE:
            if not quiet:
                tag = "" if cand == address else f"（原地址 {address} 不是目标设备，已改连 {cand}）"
                print(f"已连接 {cand}  {info['model']} {info['size']} 前台={info['focus']}{tag}")
            if cand != address:
                save_state({**load_state(), "address": cand})
            return ctrl
        if fallback is None:
            fallback = (cand, ctrl)
        else:
            ctrl.post_inactive()

    if fallback is not None:
        cand, ctrl = fallback
        info = probe_device(adb, cand)
        if not quiet:
            print(
                f"警告：没有设备的当前前台是 {TARGET_PACKAGE}；"
                f"退而连 {cand}（前台={info['focus'] or '未知'}）。"
                f"先把游戏切到前台再跑任务"
            )
        return ctrl

    raise SystemExit(
        f"连接失败: {address}（先确认模拟器已启动；可用 `main.py doctor --no-connect` 看设备列表）"
    )


def build_resource() -> Resource:
    res = Resource()
    res.post_bundle(str(maaenv.BUNDLE_DIR)).wait()
    if not res.loaded:
        raise SystemExit("bundle 加载失败，检查 resource/pipeline/*.json 语法")
    return res


def build_tasker(res: Resource, ctrl: AdbController) -> Tasker:
    tasker = Tasker()
    tasker.bind(res, ctrl)
    tasker.set_log_dir(str(maaenv.DEBUG_DIR))
    tasker.set_save_draw(True)
    tasker.set_save_on_error(True)
    tasker.set_stdout_level(2)
    return tasker


def attach_agent(res: Resource, ctrl: AdbController, tasker: Tasker, script: str | None = None):
    """用 TCP 模式把 agent 子进程接上，让 Custom 识别/动作在命令行也可用。

    `python main.py run` 自己 build 三件套、不经过 MFAAvalonia，所以默认没有任何
    自定义动作注册——跑到 Custom 节点会报「动作名不存在」。这里按绑定层给的
    AgentClient 反着接一遍：客户端选端口 → 子进程拿这个端口号当 identifier 起
    AgentServer → 绑定 resource → connect。
    """
    from maa.agent_client import AgentClient

    cli = AgentClient.create_tcp(0)
    ident = cli.identifier
    if not ident:
        raise SystemExit("agent 启动失败：拿不到 TCP 端口标识")

    target = Path(script) if script else (maaenv.ROOT / "agent" / "warehouse_agent.py")
    if not target.is_file():
        raise SystemExit(f"agent 启动失败：找不到 {target}")

    proc = subprocess.Popen(
        [sys.executable, str(target), ident],
        cwd=str(maaenv.ROOT),
    )

    # 顺序不能反：MaaAgentClientConnect 会检查 resource 已绑定，先 bind 再 connect。
    if not cli.bind(res):
        proc.kill()
        raise SystemExit("agent 启动失败：custom 动作绑定 resource 失败")

    t0 = time.time()
    while time.time() - t0 < 20:
        if cli.connect() and cli.connected:
            break
        if proc.poll() is not None:
            raise SystemExit(f"agent 进程提前退出，退出码 {proc.returncode}")
        time.sleep(0.3)
    else:
        proc.kill()
        raise SystemExit("agent 启动失败：20s 内没连上")

    try:
        acts = cli.custom_action_list
    except Exception:  # noqa: BLE001 - 绑定成功但列表接口不可用时不当失败
        acts = []
    print(f"agent 已接入 identifier={ident} 自定义动作={acts}")
    return cli, proc


# ---------------------------------------------------------------- 命令


def cmd_doctor(args) -> int:
    print("== 运行时 ==")
    print("  原生库目录:", maaenv.MAAFW_DIR, "|", "OK" if (maaenv.MAAFW_DIR / "MaaFramework.dll").is_file() else "缺失")
    print("  python 绑定:", maaenv.VENDOR_DIR / "maa", "|", "OK" if (maaenv.VENDOR_DIR / "maa" / "__init__.py").is_file() else "缺失")
    ocr_dir = maaenv.BUNDLE_DIR / "model" / "ocr"
    ocr_files = ["det.onnx", "rec.onnx", "keys.txt"]
    print("  OCR 模型:", "OK" if all((ocr_dir / f).is_file() for f in ocr_files) else f"缺失（{ocr_dir}）")

    print("== adb ==")
    adb = pick_adb(args.adb)
    print("  adb:", adb)
    devices = Toolkit.find_adb_devices(adb)
    print(f"  设备数: {len(devices)}")
    for d in devices:
        info = probe_device(adb, str(d.address))
        mark = "  <-- 目标" if info["focus"] == TARGET_PACKAGE else ""
        print(
            f"    - {info['address']}  model={info['model'] or '?'}  "
            f"size={info['size'] or '?'}  前台={info['focus'] or '?'}{mark}"
        )

    print("== bundle ==")
    res = build_resource()
    nodes = res.node_list
    print(f"  加载成功: {res.loaded} | hash={res.hash} | 节点数={len(nodes)}")
    print("  节点:", ", ".join(sorted(nodes)))

    if args.no_connect:
        return 0

    print("== 连接 / 截图 ==")
    ctrl = build_controller(adb, args.address)
    job = ctrl.post_screencap()
    job.wait()
    img = job.get()
    print("  截图尺寸:", None if img is None else img.shape)
    return 0


def cmd_list(args) -> int:
    res = build_resource()
    iface = ROOT / "interface.json"
    print(f"节点（{len(res.node_list)}）:")
    for n in sorted(res.node_list):
        print("  -", n)
    if iface.is_file():
        data = json.loads(iface.read_text(encoding="utf-8"))
        print("\ninterface.json 任务:")
        for t in data.get("task", []):
            print(f"  - {t['name']:<16} entry={t.get('entry')}  {t.get('label', '')}")
    return 0


def cmd_run(args) -> int:
    adb = pick_adb(args.adb)
    ctrl = build_controller(adb, args.address)
    res = build_resource()
    tasker = build_tasker(res, ctrl)

    agent = None
    if getattr(args, "agent", False):
        agent = attach_agent(res, ctrl, tasker, getattr(args, "agent_script", None))

    print(f"执行 entry={args.entry}")
    t0 = time.time()
    detail = tasker.post_task(args.entry).wait().get()
    cost = time.time() - t0

    print(f"结果: succeeded={detail.status.succeeded} failed={detail.status.failed} 用时={cost:.1f}s")
    for n in detail.nodes:
        r = n.recognition
        a = n.action
        if r is None:
            print(f"  [{n.name}] recognition=None")
            continue
        extra = ""
        if r.algorithm == "OCR" and r.best_result is not None:
            extra = f" text={r.best_result.text!r}"
        act = f"act={a.action} ok={a.success}" if a is not None else "act=None"
        print(f"  [{n.name}] reco={r.algorithm} hit={r.hit} box={r.box}{extra} {act}")

    if agent is not None:
        cli, proc = agent
        try:
            cli.disconnect()
        except Exception:  # noqa: BLE001
            pass
        proc.kill()

    if not detail.status.succeeded:
        print("排查：debug/ 下有 maa.log 与失败截图（save_on_error）")
        return 1
    return 0


def cmd_probe(args) -> int:
    adb = pick_adb(args.adb)
    ctrl = build_controller(adb, args.address)
    res = build_resource()
    tasker = build_tasker(res, ctrl)
    detail = tasker.post_recognition(args.node).wait().get()
    print(f"[{args.node}] algo={detail.algorithm} hit={detail.hit} box={detail.box}")
    for res_item in detail.filtered_results or []:
        print("   ", res_item)
    return 0 if detail.hit else 2


def cmd_grab(args) -> int:
    adb = pick_adb(args.adb)
    ctrl = build_controller(adb, args.address)
    job = ctrl.post_screencap()
    job.wait()
    img = job.get()
    if img is None:
        print("截图失败")
        return 1
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.asarray(img)).save(out)
    print(f"已保存 {out}  {img.shape}")
    return 0


def cmd_app(args) -> int:
    adb = pick_adb(args.adb)
    ctrl = build_controller(adb, args.address)
    job = ctrl.post_start_app(args.package)
    job.wait()
    print(f"启动 {args.package}: {job.succeeded}")
    return 0 if job.succeeded else 1


# ---------------------------------------------------------------- 入口


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="main.py", description="my-maa 命令行")
    parser.add_argument("--address", default=load_state().get("address", DEFAULT_ADDRESS))
    parser.add_argument("--adb", default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("doctor")
    p.add_argument("--no-connect", action="store_true")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("list")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("run")
    p.add_argument("entry")
    p.add_argument("--agent", action="store_true", help="以 TCP 模式拉起 agent 子进程，启用 Custom 识别/动作")
    p.add_argument("--agent-script", default=None, help="自定义 agent 入口（默认 agent/warehouse_agent.py）")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("probe")
    p.add_argument("node")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("grab")
    p.add_argument("out")
    p.set_defaults(func=cmd_grab)

    p = sub.add_parser("app")
    p.add_argument("package")
    p.set_defaults(func=cmd_app)

    args = parser.parse_args(argv)
    Toolkit.init_option(str(maaenv.CONFIG_DIR))
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
