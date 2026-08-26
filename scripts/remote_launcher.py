#!/usr/bin/env python3
"""遠隔操作スタックのランチャ。

zenoh / joy / manager を個別に起動・停止する。make remote の対話版で、起動には
make remote と同じ scripts/remote_component.bash を使う (LN-02)。RViz は扱わない。

    python3 scripts/remote_launcher.py

スレッドは2つ。

    メイン    Tk の mainloop、ボタン、500ms の生存確認
    ログ追尾  ログファイルを読んでキューに積む

守る約束は manager と同じ2つ。

    1. Tk のウィジェットに触るのはメインスレッドだけ (LN-31)
    2. 判断は remote_launcher_core の純関数が行う。ここは呼んで描くだけ (LN-32)

仕様: docs/spec/launcher.md
"""

from __future__ import annotations

import os
import queue
import signal
import subprocess
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import remote_launcher_core as core

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
COMPONENT = SCRIPTS / "remote_component.bash"
PORTS_SH = ROOT / "shared" / "vehicle_ports.sh"
OUTPUT = ROOT / "output"

#: make remote が置く PID。生きていれば起動を拒否する (LN-28)。
MAKE_REMOTE_PID = OUTPUT / "remote.pid"

#: 生存確認の間隔 (LN-22)
POLL_MS = 500

#: ログ追尾の間隔
LOG_POLL_S = 0.3

#: TERM を送ってから KILL に切り替えるまで (LN-17)。make remote-stop と同じ。
STOP_TIMEOUT_S = 5.0

#: KILL のあと消えるのを待つ時間
KILL_GRACE_S = 2.0

#: ログ表示に残す行数。運用が長引いても画面が重くならないように切る。
MAX_LOG_LINES = 2000

STATE_COLORS = {
    core.STOPPED: "#6B7280",
    core.STARTING: "#B45309",
    core.RUNNING: "#047857",
    core.STOPPING: "#B45309",
    core.FAILED: "#B91C1C",
}

COMPONENT_LABELS = {"zenoh": "Zenoh", "joy": "Joy", "manager": "Manager"}


def fleet_ports() -> "dict[str, int | None]":
    """車両IDを shared/vehicle_ports.sh に引かせる。

    ポート表の唯一の出どころはあちらで、ランチャは複製を持たない (LN-08)。
    """
    program = (
        f'source "{PORTS_SH}"\n'
        'for vehicle in "$@"; do\n'
        '  port="$(zenoh_port_for_vehicle_id "$vehicle" 2>/dev/null)" || port=""\n'
        '  echo "$vehicle $port"\n'
        "done\n"
    )
    try:
        result = subprocess.run(
            ["bash", "-c", program, "bash", *core.FLEET],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    ports: "dict[str, int | None]" = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if not parts:
            continue
        ports[parts[0]] = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    return ports


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def make_remote_alive() -> bool:
    """make remote が動いているか (LN-28)。"""
    try:
        pid = int(MAKE_REMOTE_PID.read_text().strip())
    except (OSError, ValueError):
        return False
    return process_alive(pid)


@dataclass
class Child:
    """起動した1つの構成要素。

    プロセスグループごと持つ (LN-16)。`ros2 run` は joy_node を subprocess で起こし、
    run_zenoh.bash は車両ごとにサブシェルを起こすので、親だけを kill すると孤児が残る。
    """

    process: subprocess.Popen
    #: start_new_session=True で起こしているので、子がそのままグループリーダーになる。
    pgid: int
    deadline: "float | None" = None
    killed: bool = False
    warned: bool = False
    vehicles: "tuple[str, ...]" = field(default_factory=tuple)


class RemoteLauncher:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("Remote Launcher")
        root.minsize(960, 640)

        ports = fleet_ports()
        missing = core.missing_vehicles(ports)
        if missing:
            messagebox.showerror(
                "車両IDが引けません",
                "shared/vehicle_ports.sh から次の車両のポートが引けませんでした:\n"
                f"  {', '.join(missing)}\n\n"
                "ポート表と食い違ったまま起動すると、繋がらない相手を選べてしまいます。",
            )
            raise SystemExit(1)
        self.ports = ports

        self.log_dir = self._prepare_log_dir()

        self.states = {name: core.STOPPED for name in core.COMPONENTS}
        self.children: "dict[str, Child]" = {}
        self.exit_codes: "dict[str, int]" = {}
        self.restart_pending: "set[str]" = set()

        # 対象車両に既定値は置かない。GUI の「全台」も緊急停止の宛先もここで決まる。
        self.vehicle_vars = {v: tk.BooleanVar(value=False) for v in core.FLEET}
        self.brake_enabled = tk.BooleanVar(value=False)
        self.brake_value = tk.StringVar(value="20")

        self.log_queue: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self._log_offsets: "dict[str, int]" = {}
        self._tail_targets: "list[tuple[str, str]]" = []
        self._tail_stop = threading.Event()
        self.log_widgets: "dict[str, ScrolledText]" = {}

        self._build_ui()
        self._rebuild_log_tabs()
        self._refresh()

        root.protocol("WM_DELETE_WINDOW", self._on_close)
        threading.Thread(target=self._tail_logs, daemon=True).start()
        root.after(POLL_MS, self._poll_children)
        root.after(150, self._drain_log_queue)

        self._note(f"[launcher] ログ: {self.log_dir / 'remote'}")
        if make_remote_alive():
            self._note("[launcher] make remote が動いています。停止するまで起動できません。")

    # --- 起動まわりの準備 ----------------------------------------------------

    def _prepare_log_dir(self) -> Path:
        """セッションのログ置き場を1つ作る (LN-25)。

        make remote と同じ規則で、output/latest/remote を張り替える。再起動しても
        同じファイルに追記する。切り替えると1回の運用のログが散らばる。
        """
        log_dir = OUTPUT / time.strftime("%Y%m%d-%H%M%S")
        (log_dir / "remote").mkdir(parents=True, exist_ok=True)
        latest = OUTPUT / "latest"
        latest.mkdir(parents=True, exist_ok=True)
        link = latest / "remote"
        try:
            if link.is_symlink() or link.exists():
                link.unlink()
            link.symlink_to(log_dir / "remote")
        except OSError:
            pass
        return log_dir

    def _pid_file(self, component: str) -> Path:
        return OUTPUT / f"launcher-{component}.pid"

    # --- 画面 ---------------------------------------------------------------

    def _build_ui(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Status.TLabel", font=("", 12, "bold"))
        style.configure("Header.TLabel", font=("", 11, "bold"))

        top = ttk.Frame(self.root, padding=(12, 10, 12, 4))
        top.pack(fill=tk.X)

        ttk.Label(top, text="対象車両", style="Header.TLabel").pack(side=tk.LEFT)
        self.vehicle_boxes = {}
        for vehicle in core.FLEET:
            box = ttk.Checkbutton(
                top,
                text=vehicle,
                variable=self.vehicle_vars[vehicle],
                command=self._on_vehicle_toggle,
            )
            box.pack(side=tk.LEFT, padx=(8, 0))
            self.vehicle_boxes[vehicle] = box
        self.vehicle_note = ttk.Label(top, text="")
        self.vehicle_note.pack(side=tk.LEFT, padx=(12, 0))

        brake = ttk.Frame(self.root, padding=(12, 0, 12, 8))
        brake.pack(fill=tk.X)
        ttk.Checkbutton(
            brake, text="ブレーキ試験", variable=self.brake_enabled, command=self._refresh
        ).pack(side=tk.LEFT)
        self.brake_entry = ttk.Entry(brake, textvariable=self.brake_value, width=6)
        self.brake_entry.pack(side=tk.LEFT, padx=(6, 2))
        ttk.Label(brake, text="%  (B を押している間だけ一定ブレーキ)").pack(side=tk.LEFT)

        ttk.Button(brake, text="すべて停止", command=self._stop_all).pack(side=tk.RIGHT)
        ttk.Button(brake, text="すべて起動", command=self._start_all).pack(
            side=tk.RIGHT, padx=(0, 6)
        )

        panels = ttk.Frame(self.root, padding=(12, 0, 12, 8))
        panels.pack(fill=tk.X)

        self.status_labels = {}
        self.detail_labels = {}
        self.start_buttons = {}
        self.stop_buttons = {}
        self.restart_buttons = {}

        for column, component in enumerate(core.COMPONENTS):
            frame = ttk.LabelFrame(panels, text=COMPONENT_LABELS[component], padding=10)
            frame.grid(row=0, column=column, sticky=tk.NSEW, padx=(0 if column == 0 else 8, 0))
            panels.columnconfigure(column, weight=1)

            status = ttk.Label(frame, text="", style="Status.TLabel")
            status.pack(anchor=tk.W)
            detail = ttk.Label(frame, text="", foreground="#6B7280")
            detail.pack(anchor=tk.W, pady=(0, 8))

            row = ttk.Frame(frame)
            row.pack(fill=tk.X)
            start = ttk.Button(
                row, text="起動", command=lambda c=component: self._start(c)
            )
            start.pack(side=tk.LEFT, expand=True, fill=tk.X)
            stop = ttk.Button(row, text="停止", command=lambda c=component: self._stop(c))
            stop.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(6, 0))
            restart = ttk.Button(
                frame, text="再起動", command=lambda c=component: self._restart(c)
            )
            restart.pack(fill=tk.X, pady=(6, 0))

            self.status_labels[component] = status
            self.detail_labels[component] = detail
            self.start_buttons[component] = start
            self.stop_buttons[component] = stop
            self.restart_buttons[component] = restart

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))

    def _rebuild_log_tabs(self) -> None:
        """対象車両ぶんの zenoh タブを作り直す。集合を変えられるのは停止中だけ。"""
        for tab in self.notebook.tabs():
            self.notebook.forget(tab)
        for widget in list(self.log_widgets.values()):
            widget.master.destroy()
        self.log_widgets.clear()
        self._log_offsets.clear()

        tabs = core.log_tabs(self._selected_vehicles())
        for key, _ in tabs:
            self._add_tab(key)
        # ランチャ自身の記録。プロセスの生き死にはここに出る。
        self._add_tab("launcher")
        self._tail_targets = tabs

    def _add_tab(self, key: str) -> None:
        frame = ttk.Frame(self.notebook)
        widget = ScrolledText(frame, height=16, state=tk.DISABLED, wrap=tk.NONE)
        widget.pack(fill=tk.BOTH, expand=True)
        self.notebook.add(frame, text=key)
        self.log_widgets[key] = widget

    def _refresh(self) -> None:
        """状態から画面を作り直す。ここ以外でウィジェットの見た目を触らない。"""
        idle = core.can_change_vehicles(self.states)
        for vehicle, box in self.vehicle_boxes.items():
            box.state(["!disabled"] if idle else ["disabled"])
        selected = self._selected_vehicles()
        if not idle:
            self.vehicle_note.config(text="起動中は変更できません")
        elif not selected:
            self.vehicle_note.config(text="1台以上選んでください")
        else:
            self.vehicle_note.config(text="")

        self.brake_entry.state(["!disabled"] if self.brake_enabled.get() else ["disabled"])

        for component in core.COMPONENTS:
            state = self.states[component]
            label = core.STATE_LABELS[state]
            if state == core.FAILED and component in self.exit_codes:
                label += f" (exit {self.exit_codes[component]})"
            self.status_labels[component].config(
                text=f"● {label}", foreground=STATE_COLORS[state]
            )
            self.detail_labels[component].config(text=self._detail(component))

            can_start = (
                core.start_blocked_reason(component, state, selected, False) is None
            )
            self.start_buttons[component].state(["!disabled"] if can_start else ["disabled"])
            self.stop_buttons[component].state(
                ["!disabled"] if core.can_stop(state) else ["disabled"]
            )
            self.restart_buttons[component].state(
                ["!disabled"] if (can_start or core.can_stop(state)) else ["disabled"]
            )

    def _detail(self, component: str) -> str:
        child = self.children.get(component)
        vehicles = child.vehicles if child else self._selected_vehicles()
        if component == "zenoh":
            return f"{len(vehicles)} bridge(s): {' '.join(vehicles)}" if vehicles else "-"
        if component == "joy":
            return "joy_node"
        detail = " ".join(vehicles) if vehicles else "-"
        if child is None and self.brake_enabled.get():
            detail += f"  brake {self.brake_value.get()}%"
        return detail

    # --- 操作 ---------------------------------------------------------------

    def _selected_vehicles(self) -> "tuple[str, ...]":
        return core.ordered([v for v, var in self.vehicle_vars.items() if var.get()])

    def _on_vehicle_toggle(self) -> None:
        if not core.can_change_vehicles(self.states):
            # ボタンは disabled にしてあるが、念のため元に戻す (LN-10)。
            messagebox.showinfo(
                "変更できません",
                "起動しているものがあります。対象車両を変えるには一度すべて停止してください。",
            )
            return
        self._rebuild_log_tabs()
        self._refresh()

    def _brake_test(self) -> "float | None":
        if not self.brake_enabled.get():
            return None
        return core.parse_brake_test(self.brake_value.get())

    def _start(self, component: str) -> bool:
        vehicles = self._selected_vehicles()
        reason = core.start_blocked_reason(
            component, self.states[component], vehicles, make_remote_alive()
        )
        if reason:
            messagebox.showwarning("起動できません", reason)
            return False
        try:
            brake = self._brake_test()
        except ValueError as exc:
            messagebox.showwarning("入力を確認してください", str(exc))
            return False

        command = [
            str(COMPONENT),
            component,
            str(self.log_dir),
            *core.component_args(component, vehicles, brake),
        ]
        try:
            # プロセスグループを分ける (LN-16)。停止はグループごと畳む。
            process = subprocess.Popen(
                command,
                cwd=str(ROOT),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            messagebox.showerror("起動できません", str(exc))
            return False

        self.children[component] = Child(
            process=process, pgid=process.pid, vehicles=vehicles
        )
        self.exit_codes.pop(component, None)
        self.states[component] = core.transition(self.states[component], "start")
        self._write_pid(component, process.pid)
        self._note(f"[launcher] {component} を起動しました (PID {process.pid})")
        self._refresh()
        return True

    def _stop(self, component: str, confirm: bool = True) -> bool:
        if not core.can_stop(self.states[component]):
            return False
        if confirm and not messagebox.askyesno(
            "停止の確認", core.STOP_WARNING.format(component=COMPONENT_LABELS[component])
        ):
            return False
        child = self.children.get(component)
        if child is None:
            self.states[component] = core.STOPPED
            self._refresh()
            return True
        self.states[component] = core.transition(self.states[component], "stop")
        child.deadline = time.monotonic() + STOP_TIMEOUT_S
        self._signal(child, signal.SIGTERM)
        self._note(f"[launcher] {component} に TERM を送りました")
        self._refresh()
        return True

    def _restart(self, component: str) -> None:
        if core.can_stop(self.states[component]):
            # 停止の完了を待ってから起動する (LN-18)。_poll_children が繋ぐ。
            if self._stop(component):
                self.restart_pending.add(component)
            return
        self._start(component)

    def _start_all(self) -> None:
        # zenoh を先に上げる (LN-05)。core.COMPONENTS の並びがその順になっている。
        for component in core.COMPONENTS:
            if self.states[component] in core.IDLE_STATES and not self._start(component):
                return

    def _stop_all(self) -> None:
        live = [c for c in core.COMPONENTS if core.can_stop(self.states[c])]
        if not live:
            return
        names = " / ".join(COMPONENT_LABELS[c] for c in live)
        if not messagebox.askyesno("停止の確認", core.STOP_WARNING.format(component=names)):
            return
        for component in live:
            self._stop(component, confirm=False)

    def _signal(self, child: Child, sig: int) -> None:
        try:
            os.killpg(child.pgid, sig)
        except ProcessLookupError:
            pass
        except OSError as exc:
            self._note(f"[launcher] シグナルを送れませんでした: {exc}")

    def _write_pid(self, component: str, pid: int) -> None:
        try:
            self._pid_file(component).write_text(f"{pid}\n")
        except OSError:
            pass

    def _clear_pid(self, component: str) -> None:
        try:
            self._pid_file(component).unlink()
        except OSError:
            pass

    # --- 生存確認 -----------------------------------------------------------

    def _poll_children(self) -> None:
        for component in list(core.COMPONENTS):
            child = self.children.get(component)
            if child is None:
                continue
            code = child.process.poll()
            if code is None:
                if self.states[component] == core.STARTING:
                    self.states[component] = core.transition(self.states[component], "alive")
                elif self.states[component] == core.STOPPING:
                    self._escalate(component, child)
                continue

            previous = self.states[component]
            self.states[component] = core.transition(previous, "exited")
            self.exit_codes[component] = code
            self.children.pop(component, None)
            self._clear_pid(component)
            if self.states[component] == core.FAILED:
                # 落ちた子は上げ直さない (LN-19)。赤いまま残して人間に任せる。
                self._note(f"[launcher] {component} が落ちました (exit {code})")
            else:
                self._note(f"[launcher] {component} を停止しました")
            if component in self.restart_pending:
                self.restart_pending.discard(component)
                self._start(component)

        self._refresh()
        self.root.after(POLL_MS, self._poll_children)

    def _escalate(self, component: str, child: Child) -> None:
        """TERM で終わらないものを KILL に上げる (LN-17)。make remote-stop と同じ手順。"""
        if child.deadline is None or time.monotonic() < child.deadline:
            return
        if not child.killed:
            child.killed = True
            child.deadline = time.monotonic() + KILL_GRACE_S
            self._note(f"[launcher] {component}: TERM で終わらないので KILL します")
            self._signal(child, signal.SIGKILL)
        elif not child.warned:
            child.warned = True
            self._note(f"[launcher] {component}: まだ残っています。make ps で確認してください")

    # --- ログ ---------------------------------------------------------------

    def _tail_logs(self) -> None:
        """ログ追尾。ここは別スレッドなのでウィジェットに触らない (LN-31)。"""
        while not self._tail_stop.is_set():
            for key, filename in list(self._tail_targets):
                path = self.log_dir / "remote" / filename
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                offset = self._log_offsets.get(key, 0)
                if size < offset:
                    offset = 0
                if size == offset:
                    continue
                try:
                    with path.open("rb") as handle:
                        handle.seek(offset)
                        chunk = handle.read(size - offset)
                except OSError:
                    continue
                self._log_offsets[key] = offset + len(chunk)
                self.log_queue.put((key, chunk.decode("utf-8", "replace")))
            self._tail_stop.wait(LOG_POLL_S)

    def _note(self, text: str) -> None:
        self.log_queue.put(("launcher", text + "\n"))

    def _drain_log_queue(self) -> None:
        while True:
            try:
                key, text = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self._append_log(key, text)
        self.root.after(150, self._drain_log_queue)

    def _append_log(self, key: str, text: str) -> None:
        widget = self.log_widgets.get(key)
        if widget is None:
            return
        widget.configure(state=tk.NORMAL)
        widget.insert(tk.END, text)
        lines = int(widget.index("end-1c").split(".")[0])
        if lines > MAX_LOG_LINES:
            widget.delete("1.0", f"{lines - MAX_LOG_LINES}.0")
        widget.see(tk.END)
        widget.configure(state=tk.DISABLED)

    # --- 終了 ---------------------------------------------------------------

    def _on_close(self) -> None:
        live = [
            c
            for c in core.COMPONENTS
            if core.can_stop(self.states[c]) or self.states[c] == core.STOPPING
        ]
        if not live:
            self._shutdown()
            return
        names = " / ".join(COMPONENT_LABELS[c] for c in live)
        if not messagebox.askyesno(
            "終了",
            f"起動中のものがあります: {names}\n\n"
            "すべて停止して終了しますか？\n"
            "（停止せずに閉じると、誰も見ていない joy が車両へ流れ続けます）",
        ):
            return
        for component in live:
            self._stop(component, confirm=False)
        self.root.after(300, self._wait_and_destroy, time.monotonic() + 12.0)

    def _wait_and_destroy(self, deadline: float) -> None:
        if not self.children or time.monotonic() > deadline:
            self._shutdown()
            return
        self.root.after(300, self._wait_and_destroy, deadline)

    def _shutdown(self) -> None:
        self._tail_stop.set()
        for component in core.COMPONENTS:
            self._clear_pid(component)
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    RemoteLauncher(tk.Tk()).run()


if __name__ == "__main__":
    main()
