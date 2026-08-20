"""テストの共通データとビルダ。

racing_kart_manager_core は ROS に依存しないので、このテスト群は ROS を
起動せずに動く。実行例:

    uv run --with pytest pytest manager/tests -q

仕様: docs/spec/joy-routing.md
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from racing_kart_manager_core import (  # noqa: E402
    NO_INPUT_AXES,
    NUM_BUTTONS,
    JoyValue,
)

#: テスト既定の対象車両。台数依存のケースだけ明示的に別の並びを渡す。
VEHICLES: tuple[str, ...] = ("A2", "A3", "A7")

NO_BUTTONS: tuple[int, ...] = (0,) * NUM_BUTTONS

#: 無操作。アクセル・ブレーキは +1.0 が無操作であることに注意。
JOY_NO_INPUT = JoyValue(axes=NO_INPUT_AXES, buttons=NO_BUTTONS)

#: アクセル全開・右ステアリング・ギアD
JOY_FULL = JoyValue(
    axes=(0.7, 0.0, +1.0, 0.0, 0.0, -1.0, 0.0, +1.0),
    buttons=NO_BUTTONS,
)

#: ゼロ埋め。driver はアクセル50%・ブレーキ50%と解釈するので、無操作値の代わりに
#: これを送ってはならない。
JOY_ZEROS = JoyValue(axes=(0.0,) * 8, buttons=NO_BUTTONS)


def joy_with_buttons(*indices: int, base: JoyValue = JOY_NO_INPUT) -> JoyValue:
    """指定した index のボタンだけを押した joy を作る。"""
    buttons = list(base.buttons)
    for i in indices:
        buttons[i] = 1
    return JoyValue(axes=base.axes, buttons=tuple(buttons), stamp_ns=base.stamp_ns)
