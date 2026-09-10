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
    accel_released,
    advance_command,
    apply_command,
    latest_request,
    transform,
)


def broadcast(joy: JoyValue, selection: str, command: str) -> dict[str, JoyValue]:
    """ノードが1フレームで行うのと同じ順序。transform してから指令を重ねる。"""
    return apply_command(transform(joy, selection, VEHICLES), command)


def run(requests: list) -> list:
    """指令の要求列を流したときに、各 joy へ重なる指令と通知の有無を並べる。

    ここではアクセルは常に離れている (accel_released=True) ものとして進める。
    スロットルカットの持続 (REQ-37) を見たいテストは run_with_cut を使う。
    """
    return [(overlay, notify) for overlay, notify, _ in run_with_cut(requests)]


def run_with_cut(requests: list, accel_released_flags: "list | None" = None) -> list:
    """指令の要求列を流したときに、指令・通知・スロットルカットの持続を並べる (REQ-37)。

    accel_released_flags を渡さなければ、常にアクセルが離れているものとして進める。
    """
    if accel_released_flags is None:
        accel_released_flags = [True] * len(requests)
    state = CommandState()
    result = []
    for requested, released in zip(requests, accel_released_flags):
        step = advance_command(state, requested, released)
        state = step.state
        result.append((step.overlay, step.notify, step.cut_throttle))
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
# キューの間引き (T-44b)
# --------------------------------------------------------------------------


def test_t44b_latest_request_keeps_only_the_newest():
    """T-44b: 次の joy を迎える前に複数回押されたら、最新の指令だけを残す (REQ-34)。

    開始→終了と連打したのに古い方 (開始) を採用すると、終了が1フレーム遅れて出る
    うえ、まだ一度も重ねていない開始が通知されてしまう。
    """
    latest, dropped = latest_request([COMMAND_RACE_START, COMMAND_RACE_FINISH])

    assert latest == COMMAND_RACE_FINISH
    assert dropped == (COMMAND_RACE_START,)


def test_t44c_latest_request_of_a_single_pending_command():
    """1件しか溜まっていなければそのまま返し、捨てるものは無い。"""
    assert latest_request([COMMAND_RACE_START]) == (COMMAND_RACE_START, ())


def test_t44d_latest_request_of_nothing_pending():
    """何も溜まっていなければ None で、捨てるものも無い。"""
    assert latest_request([]) == (None, ())


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


def test_t47b_does_not_notify_without_a_gui_command():
    """T-47: GUI の一斉指令が無い joy フレームでは通知しない (RN-16)。"""
    assert [notify for _, notify in run([None] * 5)] == [False] * 5


def test_t48_each_command_maps_to_its_race_event():
    """T-48: 指令とレース通知のイベントが対応する (RN-16)。"""
    assert COMMAND_EVENTS[COMMAND_RACE_START] == RACE_START
    assert COMMAND_EVENTS[COMMAND_RACE_FINISH] == RACE_FINISH


# --------------------------------------------------------------------------
# アクセルが離れる判定 (T-49 の前提)
# --------------------------------------------------------------------------


def test_accel_released_is_false_while_the_trigger_is_pressed():
    """トリガーを踏んでいる間 (軸が -1.0 寄り) は離れていない。"""
    assert accel_released(JOY_FULL) is False


def test_accel_released_is_true_at_no_input():
    """無操作値 (+1.0) では離れている。"""
    assert accel_released(JOY_NO_INPUT) is True


def test_accel_released_tolerates_a_small_offset_from_no_input():
    """無操作値ぴったりでなくても、わずかな遊びは離れた扱いにする。"""
    almost_released = JoyValue(
        axes=(0.0, 0.0, +1.0, 0.0, 0.0, +0.97, 0.0, 0.0), buttons=JOY_NO_INPUT.buttons
    )
    assert accel_released(almost_released) is True


def test_accel_released_is_false_on_a_malformed_frame():
    """T-49c: 要素数が足りない joy では判定できないので「離れていない」扱いにする。

    REQ-18 / REQ-36 と同じ、壊れた入力では安全側 (カットを続ける側) に倒す考え方。
    """
    malformed = JoyValue(axes=(0.0, 0.0, 0.0), buttons=(0, 0))
    assert accel_released(malformed) is False


# --------------------------------------------------------------------------
# スロットルカットの持続 (T-49)
# --------------------------------------------------------------------------


def test_t49_throttle_cut_persists_after_the_repeat_window_while_accel_is_held():
    """T-49: レース終了を受け付けたら、繰り返しの10フレームを過ぎてもアクセルを
    離すまでスロットルカットを保つ (REQ-37)。

    10フレームだけで切ると、トリガーを踏んだまま終了ボタンを押した場合に、
    繰り返しが終わった直後からアクセルが車両へ再び届いてしまう。
    """
    requests = [COMMAND_RACE_FINISH] + [None] * (COMMAND_REPEAT + 5)
    steps = run_with_cut(requests, accel_released_flags=[False] * len(requests))

    assert [cut for _, _, cut in steps] == [True] * len(requests)
    # 重ねるボタン自体は決められた10フレームで止まる (T-43 と同じ)。
    overlays = [overlay for overlay, _, _ in steps]
    assert overlays[COMMAND_REPEAT:] == [None] * (len(requests) - COMMAND_REPEAT)


def test_t49_throttle_cut_clears_once_accel_is_released():
    """T-49: アクセルが無操作値まで戻ったらスロットルカットを解く。"""
    requests = [COMMAND_RACE_FINISH, None, None]
    released_flags = [False, False, True]
    steps = run_with_cut(requests, accel_released_flags=released_flags)

    assert [cut for _, _, cut in steps] == [True, True, False]


def test_t49b_throttle_cut_survives_a_race_start_pressed_while_accel_is_held():
    """T-49b: レース終了のあとにレース開始を押しても、アクセルを踏んだままなら
    カットは解けない (REQ-37)。物理的に離すまでは解除操作にならない。
    """
    requests = [COMMAND_RACE_FINISH] + [None] * 3 + [COMMAND_RACE_START] + [None] * 3
    steps = run_with_cut(requests, accel_released_flags=[False] * len(requests))

    assert [cut for _, _, cut in steps] == [True] * len(requests)
    overlays = [overlay for overlay, _, _ in steps]
    assert overlays[4] == COMMAND_RACE_START  # 開始が上書きしても Y は重なる


def test_t49c_advance_command_does_not_crash_on_a_malformed_frame():
    """T-49c: 壊れた joy から作った accel_released=False でも例外にならない。"""
    step = advance_command(CommandState(), COMMAND_RACE_FINISH, accel_released=False)

    assert step.cut_throttle is True


def test_t49d_apply_command_cuts_the_accelerator_on_every_vehicle_while_latched():
    """T-49: カットが持続している間は、重ねる指令が無いフレームでも全車のアクセルを
    無操作値にする (REQ-30, REQ-37)。ブレーキなど他の軸には触れない。
    """
    braking = JoyValue(
        axes=(0.0, 0.0, -1.0, 0.0, 0.0, -1.0, 0.0, 0.0), buttons=JOY_NO_INPUT.buttons
    )
    outgoing = apply_command(
        transform(braking, SELECTION_ALL, ("A2", "A3", "A7")),
        command=None,
        cut_throttle=True,
    )

    assert set(outgoing) == {"A2", "A3", "A7"}
    for joy in outgoing.values():
        assert joy.axes[AXIS_ACCEL] == NO_INPUT_AXES[AXIS_ACCEL]
        assert joy.axes[2] == -1.0  # ブレーキは触らない


def test_t49e_apply_command_does_not_cut_when_not_latched():
    """カットが立っていなければ従来どおりアクセルを素通しする。"""
    outgoing = apply_command(
        transform(JOY_FULL, SELECTION_ALL, ("A2", "A3", "A7")),
        command=None,
        cut_throttle=False,
    )

    for joy in outgoing.values():
        assert joy.axes[AXIS_ACCEL] == JOY_FULL.axes[AXIS_ACCEL]
