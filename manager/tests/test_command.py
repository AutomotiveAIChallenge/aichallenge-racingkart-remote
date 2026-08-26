"""一斉指令のテスト (T-41 〜 T-48)。

仕様: docs/spec/joy-routing.md §4.3, docs/spec/race-notification.md §4

「レース開始ボタンが選択を全台にする」(REQ-28) は GUI 側の1行で、Tk を起こさないと
確かめられないためここには置かない。選択を書くのはメインスレッドだけという約束
(REQ-11) の内側にある。
"""

from __future__ import annotations

import pytest
from conftest import JOY_FULL, JOY_NO_INPUT, VEHICLES, joy_with_buttons
from racing_kart_manager_core import (
    AXIS_ACCEL,
    BUTTON_LB,
    BUTTON_X,
    BUTTON_Y,
    COMMAND_EVENTS,
    COMMAND_RACE_FINISH,
    COMMAND_RACE_START,
    COMMAND_REPEAT,
    EMERGENCY_BUTTONS,
    NO_INPUT_AXES,
    NUM_AXES,
    NUM_BUTTONS,
    RACE_FINISH,
    RACE_START,
    SELECTION_ALL,
    SELECTION_NONE,
    CommandState,
    JoyValue,
    advance_command,
    apply_command,
    transform,
)


def broadcast(joy: JoyValue, selection: str, command: str) -> dict[str, JoyValue]:
    """ノードが1フレームで行うのと同じ順序。transform してから指令を重ねる。"""
    return apply_command(transform(joy, selection, VEHICLES), command)


def run(requests: list) -> list:
    """指令の要求列を流したときに、各 joy へ重なる指令と通知の有無を並べる。"""
    state = CommandState()
    result = []
    for requested in requests:
        step = advance_command(state, requested)
        state = step.state
        result.append((step.overlay, step.notify))
    return result


# --------------------------------------------------------------------------
# 中身 (T-41, T-42)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("selection", [SELECTION_NONE, "A3", SELECTION_ALL])
def test_t41_race_start_raises_y_on_every_vehicle(selection):
    """T-41: レース開始は選択を問わず全宛先の Y を立てる (REQ-30, REQ-31)。

    非選択車にも届かないと、未選択のまま押したときに1台も走り出さない。
    """
    outgoing = broadcast(JOY_NO_INPUT, selection, COMMAND_RACE_START)

    assert set(outgoing) == set(VEHICLES)
    for joy in outgoing.values():
        assert joy.buttons[BUTTON_Y] == 1
        assert joy.buttons[BUTTON_X] == 0
        assert len(joy.axes) == NUM_AXES
        assert len(joy.buttons) == NUM_BUTTONS


def test_t42_race_finish_raises_x_and_cuts_the_throttle():
    """T-42: レース終了は全宛先の X を立て、アクセルを無操作値にする (REQ-31)。

    AUTONOMOUS_STEER_ONLY ではアクセルが joy 側に移る。全台選択でトリガーを踏んだまま
    終了ボタンを押すと、スロットルカットが無ければ自動操舵のまま加速する。
    """
    assert JOY_FULL.axes[AXIS_ACCEL] < 0.9  # 実際に踏んでいる

    outgoing = broadcast(JOY_FULL, SELECTION_ALL, COMMAND_RACE_FINISH)

    for joy in outgoing.values():
        assert joy.buttons[BUTTON_X] == 1
        assert joy.buttons[BUTTON_Y] == 0
        assert joy.axes[AXIS_ACCEL] == NO_INPUT_AXES[AXIS_ACCEL]


def test_t42b_race_finish_leaves_the_brake_alone():
    """T-42: ブレーキ軸には触れない (REQ-31)。終了と同時にブレーキを踏める。"""
    braking = JoyValue(
        axes=(0.0, 0.0, -1.0, 0.0, 0.0, +1.0, 0.0, 0.0),
        buttons=(0,) * NUM_BUTTONS,
    )

    outgoing = broadcast(braking, SELECTION_ALL, COMMAND_RACE_FINISH)

    assert outgoing["A3"].axes[2] == -1.0


def test_t42c_no_command_leaves_the_joy_untouched():
    """指令が無いフレームでは transform の結果をそのまま返す。"""
    plain = transform(JOY_FULL, SELECTION_ALL, VEHICLES)

    assert apply_command(plain, None) == plain


# --------------------------------------------------------------------------
# 繰り返し (T-43, T-44)
# --------------------------------------------------------------------------


def test_t43_repeats_for_a_fixed_number_of_frames_then_stops():
    """T-43: 1回の押下で決まった数の joy に乗り、そのあと止まる (REQ-33)。

    joy の QoS は depth 1 で、取りこぼすと control_mode が変わらない。1フレームだけ
    では、押したのに効かないことが起こりうる。
    """
    steps = run([COMMAND_RACE_START] + [None] * (COMMAND_REPEAT + 3))

    overlays = [overlay for overlay, _ in steps]
    assert overlays[:COMMAND_REPEAT] == [COMMAND_RACE_START] * COMMAND_REPEAT
    assert overlays[COMMAND_REPEAT:] == [None] * 4


def test_t43b_nothing_is_overlaid_before_any_press():
    """T-43: 押していないうちは何も重ねない (REQ-29)。joy は一斉指令を起こさない。"""
    assert run([None] * 5) == [(None, False)] * 5


def test_t44_a_later_command_replaces_the_one_in_flight():
    """T-44: 繰り返しの途中で押したら、あとの指令で置き換える (REQ-34)。

    走行中に終了を押したのに、開始の残りが上書きし返すことがあってはならない。
    """
    steps = run(
        [COMMAND_RACE_START, None, COMMAND_RACE_FINISH] + [None] * COMMAND_REPEAT
    )

    overlays = [overlay for overlay, _ in steps]
    assert overlays[:2] == [COMMAND_RACE_START] * 2
    assert overlays[2 : 2 + COMMAND_REPEAT] == [COMMAND_RACE_FINISH] * COMMAND_REPEAT
    assert overlays[2 + COMMAND_REPEAT :] == [None]


# --------------------------------------------------------------------------
# 他の機能との重なり (T-45, T-46)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("command", [COMMAND_RACE_START, COMMAND_RACE_FINISH])
def test_t45_emergency_stop_survives_the_command(command):
    """T-45: 緊急停止と同時でも4ボタンは立ったまま (REQ-35)。

    一斉指令が緊急停止のマスクを打ち消したら、止めたつもりの車が止まらない。
    """
    outgoing = broadcast(joy_with_buttons(BUTTON_LB), SELECTION_ALL, command)

    for joy in outgoing.values():
        assert all(joy.buttons[index] == 1 for index in EMERGENCY_BUTTONS)


@pytest.mark.parametrize("command", [COMMAND_RACE_START, COMMAND_RACE_FINISH])
def test_t46_reaches_every_vehicle_even_on_a_malformed_joy(command):
    """T-46: 要素数が規定と異なる joy のフレームでも届く (REQ-36)。

    指令の出どころは GUI であり、joy がどう壊れているかとは関係しない。送り出す joy
    自体は transform が規定の要素数に直しているので、車両側も読める。
    """
    malformed = JoyValue(axes=(0.0, 0.0, 0.0), buttons=(0, 0))

    outgoing = broadcast(malformed, SELECTION_ALL, command)

    for joy in outgoing.values():
        assert len(joy.axes) == NUM_AXES
        assert len(joy.buttons) == NUM_BUTTONS
        assert joy.buttons[BUTTON_Y if command == COMMAND_RACE_START else BUTTON_X] == 1


# --------------------------------------------------------------------------
# レース通知 (T-47, T-48)
# --------------------------------------------------------------------------


def test_t47_notifies_once_per_press():
    """T-47: 通知は押下1回につき1回。繰り返しでは出さない (RN-16)。

    各フレームで出すと、1回の押下で同じ時刻の通知が COMMAND_REPEAT 回飛ぶ。
    """
    steps = run([COMMAND_RACE_START] + [None] * COMMAND_REPEAT + [COMMAND_RACE_FINISH])

    assert [notify for _, notify in steps] == (
        [True] + [False] * COMMAND_REPEAT + [True]
    )


def test_t48_each_command_maps_to_its_race_event():
    """T-48: 指令とレース通知のイベントが対応する (RN-16)。"""
    assert COMMAND_EVENTS[COMMAND_RACE_START] == RACE_START
    assert COMMAND_EVENTS[COMMAND_RACE_FINISH] == RACE_FINISH
