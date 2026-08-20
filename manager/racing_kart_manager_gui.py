"""racing_kart_manager の操作 GUI。

manager と同じプロセスで動く。メインスレッドが Tk の mainloop を回し、joy の中継は
ROS の実行スレッドが行う (REQ-03)。**Tk のウィジェットに触るのはメインスレッドだけ。**
Tkinter はスレッドセーフではないので、joy のコールバックから画面を触ってはならない。

ノードとのやりとりは `node.selection` の読み書きだけ。ボタンが書き、100ms ごとの
再描画が読む。

画面に出るのはボタンだけ。今の選択に対応するボタンを赤くする。

仕様: docs/spec/joy-routing.md
"""

from __future__ import annotations

import tkinter as tk

from racing_kart_manager_core import SELECTION_ALL, SELECTION_NONE

REFRESH_MS = 100

#: 選択中のボタンの色。ttk ではなく tk.Button を使うのは、Linux の ttk テーマが
#: ボタンの background を無視することがあり、赤くならないため。
SELECTED_BG = "#D32F2F"
SELECTED_FG = "#FFFFFF"

BUTTON_FONT = ("", 14, "bold")


class ManagerWindow:
    """選択ボタンだけのウィンドウ。"""

    def __init__(self, node) -> None:
        self.node = node
        self.root = tk.Tk()
        title = "racing_kart_manager"
        if node.brake_test is not None:
            # 何%が仕込まれているかを走行前に目で確認できるようにする (§11)
            title += f" (brake test {node.brake_test * 100:g}%)"
        self.root.title(title)
        self.root.minsize(480, 96)

        frame = tk.Frame(self.root, padx=12, pady=12)
        frame.pack(fill="both", expand=True)

        # 対象車両は起動引数で確定しているので、ボタンは1回作るだけでよい。
        labels = [(SELECTION_NONE, "未選択")]
        labels += [(vehicle_id, vehicle_id) for vehicle_id in node.vehicles]
        labels.append((SELECTION_ALL, "全台"))

        self.buttons: dict[str, tk.Button] = {}
        for target, text in labels:
            button = tk.Button(
                frame,
                text=text,
                font=BUTTON_FONT,
                width=8,
                height=2,
                command=lambda t=target: self._select(t),
            )
            button.pack(side="left", padx=6)
            self.buttons[target] = button

        sample = next(iter(self.buttons.values()))
        self._default_colors = (sample.cget("background"), sample.cget("foreground"))

        self._highlight(node.selection)
        self.root.after(REFRESH_MS, self._refresh)

    def _select(self, target: str) -> None:
        """ボタンのコールバック。メインスレッドから node.selection を書く唯一の場所。"""
        self.node.selection = target

    def _highlight(self, selection: str) -> None:
        default_bg, default_fg = self._default_colors
        for target, button in self.buttons.items():
            selected = target == selection
            button.config(
                background=SELECTED_BG if selected else default_bg,
                foreground=SELECTED_FG if selected else default_fg,
                activebackground=SELECTED_BG if selected else default_bg,
                activeforeground=SELECTED_FG if selected else default_fg,
            )

    def _refresh(self) -> None:
        """100ms ごと。node.selection を読むだけで、ノードの中身は触らない。"""
        self._highlight(self.node.selection)
        self.root.after(REFRESH_MS, self._refresh)

    def run(self) -> None:
        self.root.mainloop()
