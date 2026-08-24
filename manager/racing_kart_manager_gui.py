#!/usr/bin/env python3
"""racing_kart_manager の操作 GUI。

manager とは別プロセス。GUI が落ちても manager は joy を流し続ける。

**この GUI は判断をしない。** 押せないボタンは無く、選択の可否も条件も持たない。
唯一の例外は status 途絶とバージョン不一致の検出で、これは manager 自身からは
送れないため gui_gate() で行う。検出しても塞ぐのは選択の表示だけで、ボタンは
押せるままにする。

画面に出るのはボタンだけ。今の選択に対応するボタンを赤くする。

仕様: docs/spec/joy-routing.md
"""

from __future__ import annotations

import json
import signal
import threading
import tkinter as tk

import rclpy
from rclpy.node import Node
from rclpy.qos import (
    QoSDurabilityPolicy,
    QoSHistoryPolicy,
    QoSProfile,
    QoSReliabilityPolicy,
)
from std_msgs.msg import String

from racing_kart_manager_core import (
    SELECTION_ALL,
    SELECTION_NONE,
    command_to_json,
    gui_gate,
)

STATUS_QOS = QoSProfile(
    depth=1,
    history=QoSHistoryPolicy.KEEP_LAST,
    reliability=QoSReliabilityPolicy.RELIABLE,
    durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
)
COMMAND_QOS = QoSProfile(
    depth=10,
    history=QoSHistoryPolicy.KEEP_LAST,
    reliability=QoSReliabilityPolicy.RELIABLE,
)

REFRESH_MS = 100

#: 選択中のボタンの色。ttk ではなく tk.Button を使うのは、Linux の ttk テーマが
#: ボタンの background を無視することがあり、赤くならないため。
SELECTED_BG = "#D32F2F"
SELECTED_FG = "#FFFFFF"

BUTTON_FONT = ("", 14, "bold")


class GuiBridge(Node):
    """ROS との出入りだけ。判断はしない。"""

    def __init__(self) -> None:
        super().__init__("racing_kart_manager_gui")
        self._lock = threading.Lock()
        self._status: dict | None = None
        self._status_at: float | None = None

        self.create_subscription(
            String, "/racing_kart_manager/status", self._on_status, STATUS_QOS
        )
        self._command_publisher = self.create_publisher(
            String, "/racing_kart_manager/command", COMMAND_QOS
        )

    def _now(self) -> float:
        return self.get_clock().now().nanoseconds / 1e9

    def _on_status(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except ValueError:
            self.get_logger().warn("status の JSON を解釈できません")
            return
        with self._lock:
            self._status = payload
            self._status_at = self._now()

    def snapshot(self) -> tuple[dict | None, float | None]:
        """最新の status と、その受信からの経過秒。"""
        with self._lock:
            if self._status_at is None:
                return None, None
            return self._status, self._now() - self._status_at

    def send(self, target: str) -> None:
        message = String()
        message.data = command_to_json(target)
        self._command_publisher.publish(message)
        self.get_logger().info(f"command sent: {message.data}")


class ManagerWindow:
    def __init__(self, bridge: GuiBridge) -> None:
        self.bridge = bridge
        self.root = tk.Tk()
        self.root.title("racing_kart_manager")
        self.root.minsize(480, 96)

        self.frame = tk.Frame(self.root, padx=12, pady=12)
        self.frame.pack(fill="both", expand=True)

        # 対象車両は manager の起動引数で決まる。GUI は台数も車両IDも知らないので、
        # status を受け取ってからボタンを作る。
        self.targets: tuple[str, ...] = ()
        self.buttons: dict[str, tk.Button] = {}
        self.default_colors: tuple[str, str] | None = None

        self.root.after(REFRESH_MS, self._refresh)

    def _rebuild(self, vehicle_ids: tuple[str, ...]) -> None:
        """対象車両が変わったらボタンを作り直す。"""
        for child in self.frame.winfo_children():
            child.destroy()
        self.buttons.clear()

        labels = [(SELECTION_NONE, "未選択")]
        labels += [(vehicle_id, vehicle_id) for vehicle_id in vehicle_ids]
        labels.append((SELECTION_ALL, "全台"))

        for target, text in labels:
            button = tk.Button(
                self.frame,
                text=text,
                font=BUTTON_FONT,
                width=8,
                height=2,
                command=lambda t=target: self.bridge.send(t),
            )
            button.pack(side="left", padx=6)
            self.buttons[target] = button

        if self.default_colors is None and self.buttons:
            sample = next(iter(self.buttons.values()))
            self.default_colors = (sample.cget("background"), sample.cget("foreground"))

        self.targets = vehicle_ids

    def _highlight(self, selection: str | None) -> None:
        """選択中のボタンだけを赤くする。selection が None ならどれも赤くしない。"""
        assert self.default_colors is not None
        default_bg, default_fg = self.default_colors
        for target, button in self.buttons.items():
            if target == selection:
                button.config(
                    background=SELECTED_BG,
                    foreground=SELECTED_FG,
                    activebackground=SELECTED_BG,
                    activeforeground=SELECTED_FG,
                )
            else:
                button.config(
                    background=default_bg,
                    foreground=default_fg,
                    activebackground=default_bg,
                    activeforeground=default_fg,
                )

    def _refresh(self) -> None:
        payload, age = self.bridge.snapshot()
        gate = gui_gate(age, None if payload is None else payload.get("schema_version"))

        if payload is not None:
            vehicle_ids = tuple(payload.get("vehicles", ()))
            if vehicle_ids != self.targets:
                self._rebuild(vehicle_ids)

        if self.buttons:
            # status が古ければ選択は分からない。古い選択を今のものとして
            # 見せるくらいなら、どれも赤くしない。
            self._highlight(payload.get("selection") if gate.usable else None)

        self.root.after(REFRESH_MS, self._refresh)

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    rclpy.init()
    bridge = GuiBridge()

    spinner = threading.Thread(target=rclpy.spin, args=(bridge,), daemon=True)
    spinner.start()

    window = ManagerWindow(bridge)

    # rclpy.init() が SIGTERM を横取りするので、ここで上書きする。そのままだと
    # make remote-stop の TERM で Tk の mainloop が抜けず、GUI だけが残る。
    # ハンドラは _refresh の after() で Python に戻る隙 (100ms ごと) に走る。
    def on_terminate(signum, frame) -> None:  # noqa: ARG001
        window.root.quit()

    signal.signal(signal.SIGTERM, on_terminate)
    signal.signal(signal.SIGINT, on_terminate)

    try:
        window.run()
    except KeyboardInterrupt:
        pass
    finally:
        bridge.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
