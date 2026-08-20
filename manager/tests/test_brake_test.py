"""ブレーキ試験のテスト (T-35 〜 T-40)。

仕様: docs/spec/joy-routing.md §11

車両のブレーキ入力に対する減速度を測るための実験用の機能。B を押している間だけ、
ステアを自動に保ったまま一定のブレーキを入れる。既定では機能そのものが無い。
"""

from __future__ import annotations

import pytest

from conftest import JOY_FULL, VEHICLES, joy_with_buttons
from racing_kart_manager_core import (
    AXIS_ACCEL,
    AXIS_BRAKE,
    BUTTON_BRAKE_TEST,
    BUTTON_X,
    EMERGENCY_BUTTONS,
    NO_INPUT_AXES,
    SELECTION_ALL,
    SELECTION_NONE,
    JoyValue,
    brake_axis_value,
    brake_test_engaged,
    transform,
    with_brake_test,
)

RATIO = 0.2

#: B を押した joy。アクセル全開・右ステア・ギアD を踏んだ状態から
PRESSED = joy_with_buttons(BUTTON_BRAKE_TEST, base=JOY_FULL)


def driver_ratio(axis_value: float) -> float:
    """車両側の読み方。clamp((1.0 - axes[i]) / 2.0, 0, 1)。"""
    return min(max((1.0 - axis_value) / 2.0, 0.0), 1.0)


# ==========================================================================
# 差し替える中身
# ==========================================================================


@pytest.mark.parametrize("ratio", [0.0, 0.1, 0.2, 0.5, 1.0])
def test_t35_brake_axis_round_trips_through_the_driver_formula(ratio):
    """T-35: 指定した比率が車両側でそのまま読める (REQ-25)。

    軸の値は driver の clamp((1.0 - axes[i]) / 2.0, 0, 1) の逆。ここがずれると
    「20% のつもりが 40%」のような取り違えが、実車で初めて分かることになる。
    """
    assert driver_ratio(brake_axis_value(ratio)) == pytest.approx(ratio)


def test_t36_engages_steer_only_and_cuts_the_throttle():
    """T-36: X を立て、ブレーキを指定値に、アクセルを無操作値にする (REQ-25)。

    X で車両側は AUTONOMOUS_STEER_ONLY になり、ステアは Autoware のまま
    アクセルとブレーキが joy 側へ移る。アクセルを落とさないと、トリガーを
    踏んでいたときにブレーキと同時に入る。
    """
    out = with_brake_test(PRESSED, RATIO)

    assert out.buttons[BUTTON_X] == 1
    assert driver_ratio(out.axes[AXIS_BRAKE]) == pytest.approx(RATIO)
    assert out.axes[AXIS_ACCEL] == NO_INPUT_AXES[AXIS_ACCEL]


def test_t36b_leaves_everything_else_alone():
    """T-36: ステア・ギア・緊急停止には触れない (REQ-25)。"""
    joy = joy_with_buttons(BUTTON_BRAKE_TEST, EMERGENCY_BUTTONS[0], base=JOY_FULL)

    out = with_brake_test(joy, RATIO)

    assert out.axes[0] == joy.axes[0]  # ステア
    assert out.axes[6:] == joy.axes[6:]  # ギア (Dpad)
    for index in EMERGENCY_BUTTONS:
        assert out.buttons[index] == joy.buttons[index]
    assert out.stamp_ns == joy.stamp_ns


# ==========================================================================
# 効かせる条件
# ==========================================================================


def test_t37_engages_only_while_the_button_is_held():
    """T-37: 押している間だけ (REQ-26)。離せば元のブレーキ軸に戻る。"""
    assert brake_test_engaged(PRESSED, "A3", VEHICLES, RATIO) is True
    assert brake_test_engaged(JOY_FULL, "A3", VEHICLES, RATIO) is False


@pytest.mark.parametrize("selection", [SELECTION_ALL, SELECTION_NONE])
def test_t38_needs_a_single_vehicle_selected(selection):
    """T-38: 単車選択のときだけ効く (REQ-26)。

    全台選択中に全車が同時に急制動するのは事故のもと。
    """
    assert brake_test_engaged(PRESSED, selection, VEHICLES, RATIO) is False


def test_t39_absent_unless_configured():
    """T-39: 起動引数を渡さなければ機能そのものが無い (REQ-27)。

    B を押しても、他のボタンと同じく選択車へ素通しするだけになる。
    """
    assert brake_test_engaged(PRESSED, "A3", VEHICLES, None) is False

    outgoing = transform(PRESSED, "A3", VEHICLES)
    assert outgoing["A3"].axes == PRESSED.axes
    assert outgoing["A3"].buttons == PRESSED.buttons


def test_t40_malformed_joy_does_not_engage():
    """T-40: 壊れた入力では効かせない (REQ-26)。

    要素数の違う joy はどの車両も操縦できない (REQ-18)。ボタン配列の違う機器の
    index 1 が偶然立って、意図しないブレーキが入るのを防ぐ。
    """
    short = JoyValue(axes=(0.0,) * 4, buttons=(0, 1))

    assert brake_test_engaged(short, "A3", VEHICLES, RATIO) is False


# ==========================================================================
# 配られ方
# ==========================================================================


def test_t38b_only_the_selected_vehicle_gets_the_brake():
    """T-38: 差し替えた joy を配っても、非選択車には無操作値が行く (REQ-17)。"""
    engaged = with_brake_test(PRESSED, RATIO)

    outgoing = transform(engaged, "A3", VEHICLES)

    assert driver_ratio(outgoing["A3"].axes[AXIS_BRAKE]) == pytest.approx(RATIO)
    for vehicle_id in ("A2", "A7"):
        assert outgoing[vehicle_id].axes == NO_INPUT_AXES
        assert driver_ratio(outgoing[vehicle_id].axes[AXIS_BRAKE]) == 0.0
