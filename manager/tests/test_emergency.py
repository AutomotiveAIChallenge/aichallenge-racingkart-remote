"""緊急停止と解除のテスト (T-16 〜 T-24)。

仕様: docs/spec/joy-routing.md §6

緊急停止は選択を無視して対象車両の全部へ飛ぶ。解除 (LSB+RSB) は他のボタンと
同じく選択に従う。この非対称が仕様の中心である。

車両側は4ボタンのいずれかが 1 の joy を1つ受け取った時点で緊急停止を保持し、
LSB+RSB を受け取るまで解除しない。だから manager 側で保持や再送はしない。
"""

from __future__ import annotations

import pytest

from conftest import JOY_FULL, NO_BUTTONS, VEHICLES, joy_with_buttons
from racing_kart_manager_core import (
    BUTTON_LSB,
    BUTTON_RSB,
    EMERGENCY_BUTTONS,
    NO_INPUT_AXES,
    SELECTION_ALL,
    SELECTION_NONE,
    JoyValue,
    emergency_pressed,
    transform,
)


def emergency_bits(value: JoyValue) -> tuple[int, ...]:
    return tuple(value.buttons[index] for index in EMERGENCY_BUTTONS)


ALL_PRESSED = (1, 1, 1, 1)


# ==========================================================================
# 全台へ飛ぶ
# ==========================================================================


def test_t16_single_selection_stops_every_vehicle():
    """T-16: 単車選択中に緊急停止を押すと、全車の4ボタンが 1 になる (REQ-16)。"""
    joy = joy_with_buttons(EMERGENCY_BUTTONS[0], base=JOY_FULL)

    outgoing = transform(joy, "A3", VEHICLES)

    for vehicle_id in VEHICLES:
        assert emergency_bits(outgoing[vehicle_id]) == ALL_PRESSED


def test_t17_unselected_state_stops_every_vehicle():
    """T-17: 未選択でも全車の4ボタンが 1 になる (REQ-16)。

    未選択は「どこにも送らない」ではない。緊急停止だけは常に全車へ届く。
    """
    joy = joy_with_buttons(EMERGENCY_BUTTONS[0])

    outgoing = transform(joy, SELECTION_NONE, VEHICLES)

    for vehicle_id in VEHICLES:
        assert emergency_bits(outgoing[vehicle_id]) == ALL_PRESSED


def test_t18_all_selection_stops_every_vehicle():
    """T-18: 全台選択中も同じ (REQ-16)。"""
    joy = joy_with_buttons(EMERGENCY_BUTTONS[0], base=JOY_FULL)

    outgoing = transform(joy, SELECTION_ALL, VEHICLES)

    for vehicle_id in VEHICLES:
        assert emergency_bits(outgoing[vehicle_id]) == ALL_PRESSED


@pytest.mark.parametrize("button", EMERGENCY_BUTTONS)
def test_t19_each_emergency_button_works_on_its_own(button):
    """T-19: LB / RB / START / BACK のどれ1つでも成立する (REQ-16)。

    driver は4つを OR で見る。どれが押されたかは区別しない。
    """
    outgoing = transform(joy_with_buttons(button), "A3", VEHICLES)

    for vehicle_id in VEHICLES:
        assert emergency_bits(outgoing[vehicle_id]) == ALL_PRESSED


def test_t20_not_held_means_not_sent():
    """T-20: 押されていない joy を受けたら 1 は立たない (REQ-17)。

    manager は緊急停止を保持しない。保持は車両側が行う。joy_node は押下中も
    20Hz で送り続けるので、押している限り繰り返し届く。
    """
    pressed = transform(joy_with_buttons(EMERGENCY_BUTTONS[0]), "A3", VEHICLES)
    released = transform(joy_with_buttons(), "A3", VEHICLES)

    assert emergency_bits(pressed["A2"]) == ALL_PRESSED
    assert emergency_bits(released["A2"]) == (0, 0, 0, 0)


def test_t21_axes_masking_is_unchanged_while_stopping():
    """T-21: 緊急停止中も選択車の軸は素通し、非選択車の軸は無操作値 (REQ-18)。

    車両側は緊急停止がラッチしている間、軸を見ずに停止指令を出す。
    ここで軸を触らないのは、緊急停止の有無でマスクの規則を変えないため。
    """
    joy = joy_with_buttons(EMERGENCY_BUTTONS[0], base=JOY_FULL)

    outgoing = transform(joy, "A3", VEHICLES)

    assert outgoing["A3"].axes == JOY_FULL.axes
    assert outgoing["A2"].axes == NO_INPUT_AXES


# ==========================================================================
# 壊れた入力
# ==========================================================================


def test_t22_malformed_joy_masks_everyone_but_still_carries_the_stop():
    """T-22: 要素数が規定と異なる joy では全車が非選択車扱いになり、
    読み取れた緊急停止ボタンは反映される (REQ-14)。

    壊れた入力で操縦させない。ただし壊れていても緊急停止だけは通す。
    正規化せずに素通しすると、driver が joy ごと捨てるので緊急停止も届かない。
    """
    short = JoyValue(axes=(0.0,) * 4, buttons=(0, 0, 0, 0, 1, 0, 0, 0))

    assert emergency_pressed(short) is True

    outgoing = transform(short, SELECTION_ALL, VEHICLES)

    for vehicle_id in VEHICLES:
        assert outgoing[vehicle_id].axes == NO_INPUT_AXES
        assert emergency_bits(outgoing[vehicle_id]) == ALL_PRESSED


def test_t22b_malformed_joy_without_a_stop_is_just_idle():
    """T-22: 緊急停止が読み取れない壊れた入力では、全車が無操作 joy を受ける。"""
    short = JoyValue(axes=(), buttons=(0, 0))

    outgoing = transform(short, "A3", VEHICLES)

    for vehicle_id in VEHICLES:
        assert outgoing[vehicle_id].axes == NO_INPUT_AXES
        assert outgoing[vehicle_id].buttons == NO_BUTTONS


# ==========================================================================
# 解除
# ==========================================================================


def test_t23_clear_reaches_the_selected_vehicle_only():
    """T-23: LSB+RSB は選択車にだけ届く (REQ-19)。

    停止は全台・解除は選択、という非対称。1台ずつ戻すときは選択を切り替えながら
    解除する。
    """
    joy = joy_with_buttons(BUTTON_LSB, BUTTON_RSB)

    outgoing = transform(joy, "A3", VEHICLES)

    assert outgoing["A3"].buttons[BUTTON_LSB] == 1
    assert outgoing["A3"].buttons[BUTTON_RSB] == 1
    for vehicle_id in ("A2", "A7"):
        assert outgoing[vehicle_id].buttons[BUTTON_LSB] == 0
        assert outgoing[vehicle_id].buttons[BUTTON_RSB] == 0


def test_t24_clear_reaches_everyone_when_all_are_selected():
    """T-24: 全台選択中は全車に届く (REQ-19)。

    全台に配った緊急停止を一度に戻すときの標準手順。
    """
    joy = joy_with_buttons(BUTTON_LSB, BUTTON_RSB)

    outgoing = transform(joy, SELECTION_ALL, VEHICLES)

    for vehicle_id in VEHICLES:
        assert outgoing[vehicle_id].buttons[BUTTON_LSB] == 1
        assert outgoing[vehicle_id].buttons[BUTTON_RSB] == 1


def test_t24b_clear_does_not_reach_anyone_while_unselected():
    """T-24: 未選択のときは解除がどこにも届かない (REQ-19)。"""
    joy = joy_with_buttons(BUTTON_LSB, BUTTON_RSB)

    outgoing = transform(joy, SELECTION_NONE, VEHICLES)

    for vehicle_id in VEHICLES:
        assert outgoing[vehicle_id].buttons == NO_BUTTONS
