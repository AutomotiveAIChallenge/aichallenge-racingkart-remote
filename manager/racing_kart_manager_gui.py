"""racing_kart_manager の操作 GUI。

manager と同じプロセスで動く。メインスレッドが Tk の mainloop を回し、joy の中継は
ROS の実行スレッドが行う (REQ-03)。**Tk のウィジェットに触るのはメインスレッドだけ。**
Tkinter はスレッドセーフではないので、joy のコールバックから画面を触ってはならない。

ノードとのやりとりは2つだけ。

    上段の選択ボタン    node.selection を書く。100ms ごとの再描画が読む
    下段の一斉ボタン    node.request_command() を呼ぶ。ROS スレッドがキューから取る

画面に出るのはボタンだけ。今の選択に対応するボタンを赤くする。一斉指令は状態を持たない
ので押した瞬間に光らせるだけで、指令が届いたかどうかは表示しない (§9)。

仕様: docs/spec/joy-routing.md
"""

from __future__ import annotations

import tkinter as tk

from racing_kart_manager_core import (
    COMMAND_RACE_FINISH,
    COMMAND_RACE_START,
    SELECTION_ALL,
    SELECTION_NONE,
)

REFRESH_MS = 100

#: 選択中のボタンの色。ttk ではなく tk.Button を使うのは、Linux の ttk テーマが
#: ボタンの background を無視することがあり、赤くならないため。
SELECTED_BG = "#D32F2F"
SELECTED_FG = "#FFFFFF"

#: 一斉指令のボタンの色。選択ボタンと違って状態ではなく操作なので、常時この色にして
#: 「押すと何かが起きる」ほうの列だと分かるようにする。
COMMAND_COLORS = {
    COMMAND_RACE_START: "#2E7D32",
    COMMAND_RACE_FINISH: "#EF6C00",
}
COMMAND_FG = "#FFFFFF"

#: 押したことが分かるように、この時間だけ色を反転させる。指令が車両へ届いたことを
#: 意味しない。届いたかどうかは manager には分からない (§9)。
FLASH_BG = "#FFFFFF"
FLASH_MS = 400

BUTTON_FONT = ("", 14, "bold")


class ManagerWindow:
    """上段が選択ボタン、下段が一斉指令ボタンのウィンドウ。"""

    def __init__(self, node) -> None:
        self.node = node
        self.root = tk.Tk()
        title = "racing_kart_manager"
        if node.brake_test is not None:
            # 何%が仕込まれているかを走行前に目で確認できるようにする (§11)
            title += f" (brake test {node.brake_test * 100:g}%)"
        self.root.title(title)
        self.root.minsize(480, 200)

        frame = tk.Frame(self.root, padx=12, pady=12)
        frame.pack(fill="both", expand=True)

        selection_row = tk.Frame(frame)
        selection_row.pack(fill="x")

        command_row = tk.Frame(frame)
        command_row.pack(fill="x", pady=(12, 0))

        # 対象車両は起動引数で確定しているので、ボタンは1回作るだけでよい。
        labels = [(SELECTION_NONE, "未選択")]
        labels += [(vehicle_id, vehicle_id) for vehicle_id in node.vehicles]
        labels.append((SELECTION_ALL, "全台"))

        self.buttons: dict[str, tk.Button] = {}
        for target, text in labels:
            button = tk.Button(
                selection_row,
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

        # 両端に離して置く。隣り合わせだと、レース中に開始と終了を押し間違える。
        for command, text, side in (
            (COMMAND_RACE_START, "レース開始", "left"),
            (COMMAND_RACE_FINISH, "レース終了", "right"),
        ):
            color = COMMAND_COLORS[command]
            button = tk.Button(
                command_row,
                text=text,
                font=BUTTON_FONT,
                width=12,
                height=2,
                background=color,
                foreground=COMMAND_FG,
                activebackground=color,
                activeforeground=COMMAND_FG,
            )
            button.config(command=lambda c=command, b=button: self._command(c, b))
            button.pack(side=side, padx=6)

        self._highlight(node.selection)
        self.root.after(REFRESH_MS, self._refresh)

    def _select(self, target: str) -> None:
        """ボタンのコールバック。メインスレッドから node.selection を書く唯一の場所。"""
        self.node.selection = target

    def _command(self, command: str, button: tk.Button) -> None:
        """一斉指令のボタン。ノードのキューへ積むだけで、送出は ROS スレッドが行う。

        レース開始は選択を全台にする (REQ-28)。レース中は全台選択が運用の既定であり、
        そのあとの手元の joy (Y / X / 緊急停止の解除) がそのまま全車へ効く。
        """
        if command == COMMAND_RACE_START:
            self.node.selection = SELECTION_ALL
        self.node.request_command(command)
        self._flash(button, COMMAND_COLORS[command])

    def _flash(self, button: tk.Button, color: str) -> None:
        """押したことを目に見せる。after で戻すので、この関数もメインスレッドだけ。"""
        button.config(
            background=FLASH_BG,
            foreground=color,
            activebackground=FLASH_BG,
            activeforeground=color,
        )
        self.root.after(
            FLASH_MS,
            lambda: button.config(
                background=color,
                foreground=COMMAND_FG,
                activebackground=color,
                activeforeground=COMMAND_FG,
            ),
        )

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
