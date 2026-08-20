"""racing_kart_manager の純粋ロジック。

ROS にも Tk にも依存しない。sensor_msgs/Joy との相互変換はノード側
(racing_kart_manager.py) が行う。これによりテストは ROS を起動せず pytest だけで完走できる。

manager は joy の中継器である。判断に使うのは GUI で選んだ選択と、受信した joy の中身
だけで、車両から届くテレメトリは購読しない。

仕様: docs/spec/joy-routing.md, docs/spec/race-notification.md
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

# --------------------------------------------------------------------------
# 対象車両
#
# 台数も車両IDも固定しない。起動時に引数で渡す (REQ-06)。
# --------------------------------------------------------------------------

#: 運用に存在する車両ID。起動引数の検証にだけ使う。
#: 車両IDの正本は shared/vehicle_ports.sh (本体リポジトリからの複製)。車両を増やすときは
#: そちらと揃えること。ここから source できないので複製している。
KNOWN_VEHICLE_IDS: tuple[str, ...] = ("A1", "A2", "A3", "A5", "A6", "A7", "A8")


def parse_vehicles(args) -> Optional[tuple[str, ...]]:
    """起動引数から対象車両を決める。解釈できなければ None (REQ-07)。

    指定した順を保つ (GUI のボタン並びに効く)。空・未知のID・重複は拒否する。
    重複を許すと同じ車両へ2回 publish することになり、宛先の数が状態と食い違う。
    """
    vehicles = tuple(args)
    if not vehicles:
        return None
    if any(vehicle_id not in KNOWN_VEHICLE_IDS for vehicle_id in vehicles):
        return None
    if len(set(vehicles)) != len(vehicles):
        return None
    return vehicles


# --------------------------------------------------------------------------
# キーバインド
#
# racing_kart_interface/src/racing_kart_driver/src/keybind/joystick.hpp の複製。
# 別リポジトリのため import できない。変更するときは両方を直すこと。
# --------------------------------------------------------------------------

NUM_AXES = 8
NUM_BUTTONS = 11

AXIS_ACCEL = 5  # RightTrigger
AXIS_DPAD_V = 7  # DpadVertical (ギア)

BUTTON_A = 0  # control_mode を MANUAL へ
BUTTON_B = 1  # 未使用
BUTTON_X = 2  # control_mode を AUTONOMOUS_STEER_ONLY へ
BUTTON_Y = 3  # control_mode を AUTONOMOUS へ
BUTTON_LB = 4
BUTTON_RB = 5
BUTTON_START = 6
BUTTON_BACK = 7
BUTTON_LSB = 9  # RSB との同時押しで緊急停止解除
BUTTON_RSB = 10

#: driver はこの4つを OR で見て is_emergency_ を立てる
#: (racing_kart_driver_node.cpp:227-238)
EMERGENCY_BUTTONS: tuple[int, ...] = (BUTTON_LB, BUTTON_RB, BUTTON_START, BUTTON_BACK)

#: 無操作の実値。driver はアクセル・ブレーキを clamp((1.0 - axes[i]) / 2.0, 0, 1) で
#: 解釈するため (racing_kart_driver_node.cpp:345,367)、無操作は 0.0 ではなく +1.0。
#: ゼロ埋めするとアクセル50%・ブレーキ50%を踏んだ扱いになる。
NO_INPUT_AXES: tuple[float, ...] = (
    0.0,  # 0 Steer
    0.0,  # 1 LeftStickVertical (未使用)
    +1.0,  # 2 Brake
    0.0,  # 3 RightStickHorizontal (未使用)
    0.0,  # 4 RightStickVertical (未使用)
    +1.0,  # 5 Accel
    0.0,  # 6 DpadHorizontal (ギア)
    0.0,  # 7 DpadVertical (ギア)
)

#: 非選択車へ送るボタン。緊急停止だけは別途立てる (REQ-17, REQ-20)
NO_INPUT_BUTTONS: tuple[int, ...] = (0,) * NUM_BUTTONS


# --------------------------------------------------------------------------
# 選択
#
# 選択を変えるのは GUI のボタンだけで、そのボタンは対象車両から作る (REQ-11)。
# だから選択が対象車両の外を指すことはない。
# --------------------------------------------------------------------------

#: どの車両も選択していない。全車に無操作 joy を送る (REQ-14)
SELECTION_NONE = "none"

#: 対象車両すべてを選択している
SELECTION_ALL = "all"

#: 起動直後は未選択 (REQ-10)
INITIAL_SELECTION = SELECTION_NONE


def selected_vehicles(selection: str, vehicles: tuple[str, ...]) -> frozenset[str]:
    """選択車の集合。ここに入らない車両が非選択車になる。"""
    if selection == SELECTION_ALL:
        return frozenset(vehicles)
    if selection in vehicles:
        return frozenset({selection})
    return frozenset()


# --------------------------------------------------------------------------
# joy
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class JoyValue:
    """sensor_msgs/Joy の中身。ROS 型に依存しないための入れ物。"""

    axes: tuple[float, ...]
    buttons: tuple[int, ...]
    stamp_ns: int = 0


def joy_is_well_formed(joy: JoyValue) -> bool:
    """要素数が規定どおりか (REQ-15)。

    driver は要素数が一致しない joy を使わず停止指令に落とすため
    (racing_kart_driver_node.cpp:186-190)、そのまま流しても操縦はできない。
    """
    return len(joy.axes) == NUM_AXES and len(joy.buttons) == NUM_BUTTONS


def _pressed(joy: JoyValue, index: int) -> bool:
    """要素数が足りない joy でも読める範囲で判定する。"""
    return index < len(joy.buttons) and bool(joy.buttons[index])


def emergency_pressed(joy: JoyValue) -> bool:
    """緊急停止ボタン4種のいずれかが押されているか。

    壊れた入力でも緊急停止だけは通す (REQ-18)。
    """
    return any(_pressed(joy, index) for index in EMERGENCY_BUTTONS)


def autonomous_pressed(joy: JoyValue) -> bool:
    """自動運転 (Y) が押されているか。X は含めない (RN-04)。"""
    return _pressed(joy, BUTTON_Y)


def _with_emergency(joy: JoyValue) -> JoyValue:
    """緊急停止ボタン4つすべてを立てた joy。"""
    buttons = list(joy.buttons)
    for index in EMERGENCY_BUTTONS:
        buttons[index] = 1
    return JoyValue(axes=joy.axes, buttons=tuple(buttons), stamp_ns=joy.stamp_ns)


def transform(
    joy: JoyValue, selection: str, vehicles: tuple[str, ...]
) -> dict[str, JoyValue]:
    """受信した joy を、対象車両ごとにマスクして配る (REQ-13, §4.2)。

    宛先は絞らない。選択が未選択でも全車へ送る。送出を止めた車両は5秒後に
    緊急停止がラッチし、選択し直しても解除操作なしには動かせなくなる (REQ-14)。
    """
    if not vehicles:
        return {}

    if joy_is_well_formed(joy):
        targets = selected_vehicles(selection, vehicles)
        chosen = JoyValue(
            axes=tuple(joy.axes), buttons=tuple(joy.buttons), stamp_ns=joy.stamp_ns
        )
    else:
        # 壊れた入力では操縦させない。全車を非選択車として扱う (REQ-18)
        targets = frozenset()
        chosen = None

    idle = JoyValue(axes=NO_INPUT_AXES, buttons=NO_INPUT_BUTTONS, stamp_ns=joy.stamp_ns)

    if emergency_pressed(joy):
        idle = _with_emergency(idle)
        if chosen is not None:
            chosen = _with_emergency(chosen)

    return {
        vehicle_id: (chosen if vehicle_id in targets else idle)
        for vehicle_id in vehicles
    }


# --------------------------------------------------------------------------
# レース通知
#
# 仕様: docs/spec/race-notification.md
# --------------------------------------------------------------------------

RACE_START = "start"
RACE_FINISH = "finish"

TOPIC_RACE_START = "kart_race_start"
TOPIC_RACE_FINISH = "kart_race_finish"

JST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class RaceTriggers:
    """ある1つの joy から見た、2つの発火条件の成否。"""

    start: bool
    finish: bool


def race_triggers(joy: JoyValue, selection: str) -> RaceTriggers:
    """発火条件を評価する。条件は互いに独立に見る (RN-07)。

    開始は transform が操縦を許す joy のときだけ見る (RN-10)。要素数が規定と違う入力は
    どの車両も操縦できない (REQ-18) のに、ボタン配列の違う機器の index 3 が偶然立って
    レース開始が飛び、retain された started_at を上書きする、というのを防ぐ。
    終了は要素数を問わない。壊れていても止めるほうは通す。
    """
    return RaceTriggers(
        start=(
            joy_is_well_formed(joy)
            and selection == SELECTION_ALL
            and autonomous_pressed(joy)
        ),
        finish=emergency_pressed(joy),
    )


def race_events(
    previous: Optional[RaceTriggers], current: RaceTriggers
) -> tuple[str, ...]:
    """立ち上がったものだけを返す (RN-05)。

    joy_node は押下中も 20Hz で送り続けるため、押されているかどうかだけで判定すると
    1回の押下で連続送信になる。

    previous が None のときは何も返さない (RN-08)。ボタンを押したまま起動したときに、
    押した覚えのない通知が飛ぶのを防ぐ。
    """
    if previous is None:
        return ()

    events = []
    if current.start and not previous.start:
        events.append(RACE_START)
    if current.finish and not previous.finish:
        events.append(RACE_FINISH)
    return tuple(events)


def race_topic(event: str) -> str:
    return TOPIC_RACE_START if event == RACE_START else TOPIC_RACE_FINISH


def to_jst_iso8601(stamp_ns: int) -> str:
    """ナノ秒を JST の ISO 8601 にする。ミリ秒3桁、オフセットは +09:00 (RN-02, RN-03)。

    秒とナノ秒に分けてから組み立てる。stamp_ns / 1e9 と割ってしまうと float の桁が
    足りず、ミリ秒がずれることがある。
    """
    seconds, remainder = divmod(stamp_ns, 1_000_000_000)
    moment = datetime.fromtimestamp(seconds, tz=JST)
    return f"{moment.strftime('%Y-%m-%dT%H:%M:%S')}.{remainder // 1_000_000:03d}+09:00"


def race_payload(event: str, stamp_ns: int) -> str:
    """通知のペイロード。時刻は joy の header.stamp から作る (RN-09)。"""
    field = "started_at" if event == RACE_START else "finished_at"
    return json.dumps({field: to_jst_iso8601(stamp_ns)})
