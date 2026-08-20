"""joy の送出とマスクのテスト (T-09 〜 T-15)。

仕様: docs/spec/joy-routing.md §4

| 宛先     | axes       | LB/RB/START/BACK | LSB/RSB | A/B/X/Y |
| -------- | ---------- | ---------------- | ------- | ------- |
| 選択車   | 素通し     | 素通し           | 素通し  | 素通し  |
| 非選択車 | 無操作値   | 素通し           | 0       | 0       |

緊急停止の列は test_emergency.py で検証する。
"""

from __future__ import annotations

import pytest

from conftest import JOY_FULL, JOY_NO_INPUT, NO_BUTTONS, VEHICLES, joy_with_buttons
from racing_kart_manager_core import (
    BUTTON_A,
    BUTTON_B,
    BUTTON_LSB,
    BUTTON_RSB,
    BUTTON_X,
    BUTTON_Y,
    NO_INPUT_AXES,
    NUM_AXES,
    NUM_BUTTONS,
    SELECTION_ALL,
    SELECTION_NONE,
    JoyValue,
    transform,
)


# ==========================================================================
# 送出の契機と宛先
# ==========================================================================


@pytest.mark.parametrize("selection", [SELECTION_NONE, SELECTION_ALL, "A3"])
def test_t09_publishes_to_every_target_vehicle(selection):
    """T-09: joy 1つにつき、対象車両全部へ1つずつ送る (REQ-09)。

    宛先は選択で絞らない。絞ると、送らなくなった車両が5秒後に緊急停止をラッチし、
    選択し直しても解除操作なしには動かせなくなる。
    """
    outgoing = transform(JOY_FULL, selection, VEHICLES)

    assert set(outgoing) == set(VEHICLES)


def test_t10_unselected_state_still_publishes_idle_joy_to_all():
    """T-10: 未選択でも全車へ送り、軸は無操作値になる (REQ-10, REQ-13)。"""
    outgoing = transform(JOY_FULL, SELECTION_NONE, VEHICLES)

    assert set(outgoing) == set(VEHICLES)
    for value in outgoing.values():
        assert value.axes == NO_INPUT_AXES


def test_t09b_no_target_vehicles_sends_nothing():
    """T-09: 対象車両が空なら送らない。起動時に弾いているので通常は起きない。"""
    assert transform(JOY_FULL, SELECTION_ALL, ()) == {}


# ==========================================================================
# 選択車 = 素通し
# ==========================================================================


def test_t11_selected_vehicle_receives_the_joy_unchanged():
    """T-11: 選択車には軸もボタンも素通しする (REQ-12)。"""
    joy = joy_with_buttons(BUTTON_A, BUTTON_Y, base=JOY_FULL)

    outgoing = transform(joy, "A3", VEHICLES)

    assert outgoing["A3"].axes == joy.axes
    assert outgoing["A3"].buttons == joy.buttons


def test_t11b_all_selection_passes_through_to_every_vehicle():
    """T-11: 全台選択中は全車が選択車。A/X/Y も含めて等しく届く (REQ-12)。

    Y を押せば全車が同時に自動運転へ入る。仕様として受け入れている
    (§10「保証しないこと」)。
    """
    joy = joy_with_buttons(BUTTON_Y, base=JOY_FULL)

    outgoing = transform(joy, SELECTION_ALL, VEHICLES)

    for vehicle_id in VEHICLES:
        assert outgoing[vehicle_id].axes == joy.axes
        assert outgoing[vehicle_id].buttons == joy.buttons


# ==========================================================================
# 非選択車 = マスク
# ==========================================================================


def test_t12_unselected_vehicle_receives_the_idle_axes():
    """T-12: 非選択車の軸は無操作値になる (REQ-13)。

    アクセルとブレーキは 0.0 ではなく +1.0 が無操作。0 で埋めると driver は
    アクセル50%・ブレーキ50%を踏んだ扱いにする。
    """
    outgoing = transform(JOY_FULL, "A3", VEHICLES)

    for vehicle_id in ("A2", "A7"):
        assert outgoing[vehicle_id].axes == NO_INPUT_AXES


@pytest.mark.parametrize(
    "button", [BUTTON_A, BUTTON_B, BUTTON_X, BUTTON_Y, BUTTON_LSB, BUTTON_RSB]
)
def test_t13_unselected_vehicle_receives_no_buttons(button):
    """T-13: 非選択車の A/B/X/Y と LSB/RSB は 0 になる (REQ-13)。

    素通しにすると control_mode の切り替えと緊急停止解除が、選択していない車両にも
    飛ぶ。緊急停止だけが例外である (test_emergency.py)。
    """
    joy = joy_with_buttons(button, base=JOY_FULL)

    outgoing = transform(joy, "A3", VEHICLES)

    for vehicle_id in ("A2", "A7"):
        assert outgoing[vehicle_id].buttons == NO_BUTTONS


def test_t13b_gear_dpad_does_not_reach_unselected_vehicles():
    """T-13: ギア操作 (Dpad) も非選択車には届かない。無操作値の一部として 0 になる。"""
    outgoing = transform(JOY_FULL, "A3", VEHICLES)

    assert JOY_FULL.axes[7] == +1.0  # ギアD を入れている
    assert outgoing["A2"].axes[6] == 0.0
    assert outgoing["A2"].axes[7] == 0.0


# ==========================================================================
# 形の保証
# ==========================================================================


@pytest.mark.parametrize("selection", [SELECTION_NONE, SELECTION_ALL, "A3"])
def test_t14_outgoing_joy_always_has_the_required_size(selection):
    """T-14: 送出する joy は常に axes 8 / buttons 11 (REQ-11)。

    driver は要素数が一致しない joy を使わず停止指令に落とす。
    """
    for joy in (JOY_NO_INPUT, JOY_FULL, JoyValue(axes=(), buttons=())):
        for value in transform(joy, selection, VEHICLES).values():
            assert len(value.axes) == NUM_AXES
            assert len(value.buttons) == NUM_BUTTONS


@pytest.mark.parametrize("selection", [SELECTION_NONE, SELECTION_ALL, "A3"])
def test_t15_stamp_is_carried_over(selection):
    """T-15: header.stamp は入力の joy から引き継ぐ (REQ-15)。"""
    joy = JoyValue(axes=JOY_FULL.axes, buttons=JOY_FULL.buttons, stamp_ns=1234567890)

    for value in transform(joy, selection, VEHICLES).values():
        assert value.stamp_ns == 1234567890
