"""レース開始・終了通知のテスト (T-23 〜 T-34)。

仕様: docs/spec/race-notification.md

開始も終了も**立ち上がり**で判定する。joy_node は押下中も 20Hz で送り続けるので、
押されているかどうかだけで判定すると1回の押下で連続送信になる。
"""

from __future__ import annotations

import json

import pytest

from conftest import JOY_FULL, VEHICLES, joy_with_buttons
from race_notifier import BrokerConfig, config_from_env, publish_command
from racing_kart_manager_core import (
    BUTTON_X,
    BUTTON_Y,
    EMERGENCY_BUTTONS,
    RACE_FINISH,
    RACE_START,
    SELECTION_ALL,
    SELECTION_NONE,
    JoyValue,
    race_events,
    race_payload,
    race_topic,
    race_triggers,
    to_jst_iso8601,
)


def feed(*frames: "tuple[JoyValue, str]") -> "list[tuple[str, ...]]":
    """joy を順に流し、各フレームで発火したイベントを返す。

    最初のフレームは前回値の初期化に使われるので必ず空になる (RN-08)。
    """
    previous = None
    result = []
    for joy, selection in frames:
        current = race_triggers(joy, selection)
        result.append(race_events(previous, current))
        previous = current
    return result


IDLE = joy_with_buttons()
AUTONOMOUS = joy_with_buttons(BUTTON_Y)
STOP = joy_with_buttons(EMERGENCY_BUTTONS[0])


# ==========================================================================
# 開始
# ==========================================================================


def test_t23_start_fires_once_on_the_press():
    """T-23: 全台選択で Y を押した瞬間に開始が1回だけ発火する (RN-05)。"""
    events = feed(
        (IDLE, SELECTION_ALL),
        (AUTONOMOUS, SELECTION_ALL),
    )

    assert events == [(), (RACE_START,)]


def test_t24_holding_does_not_fire_again():
    """T-24: Y を押し続けても2回目は発火しない (RN-05)。

    joy_node は autorepeat_rate=20.0 で押下中も送り続ける。
    """
    events = feed(
        (IDLE, SELECTION_ALL),
        (AUTONOMOUS, SELECTION_ALL),
        (AUTONOMOUS, SELECTION_ALL),
        (AUTONOMOUS, SELECTION_ALL),
    )

    assert events == [(), (RACE_START,), (), ()]


@pytest.mark.parametrize("selection", [SELECTION_NONE, "A3"])
def test_t25_start_needs_all_vehicles_selected(selection):
    """T-25: 単車選択・未選択で Y を押しても発火しない。"""
    events = feed((IDLE, selection), (AUTONOMOUS, selection))

    assert events == [(), ()]


def test_t26_steer_only_autonomous_does_not_start():
    """T-26: X (AUTONOMOUS_STEER_ONLY) を押しても発火しない (RN-04)。"""
    events = feed(
        (IDLE, SELECTION_ALL),
        (joy_with_buttons(BUTTON_X), SELECTION_ALL),
    )

    assert events == [(), ()]


def test_t25b_selection_change_while_holding_starts():
    """T-25: Y を押したまま全台選択にしても、条件の立ち上がりとして発火する。

    条件は「全台選択かつ Y 押下」の合成なので、どちらが後でも成立した瞬間に出る。
    """
    events = feed(
        (AUTONOMOUS, "A3"),
        (AUTONOMOUS, SELECTION_ALL),
    )

    assert events == [(), (RACE_START,)]


# ==========================================================================
# 終了
# ==========================================================================


@pytest.mark.parametrize("button", EMERGENCY_BUTTONS)
def test_t27_each_emergency_button_finishes(button):
    """T-27: 緊急停止4種それぞれの押下で終了が1回だけ発火する (RN-05)。"""
    events = feed(
        (IDLE, SELECTION_NONE),
        (joy_with_buttons(button), SELECTION_NONE),
    )

    assert events == [(), (RACE_FINISH,)]


def test_t28_holding_the_stop_does_not_fire_again():
    """T-28: 緊急停止を押し続けても2回目は発火しない (RN-05)。"""
    events = feed(
        (IDLE, SELECTION_NONE),
        (STOP, SELECTION_NONE),
        (STOP, SELECTION_NONE),
    )

    assert events == [(), (RACE_FINISH,), ()]


def test_t29_releasing_and_pressing_again_fires_again():
    """T-29: 離してもう一度押せば再び発火する (RN-06)。

    レースの進行状態を持たないので、条件が成立するたびに送る。
    """
    events = feed(
        (IDLE, SELECTION_NONE),
        (STOP, SELECTION_NONE),
        (IDLE, SELECTION_NONE),
        (STOP, SELECTION_NONE),
    )

    assert events == [(), (RACE_FINISH,), (), (RACE_FINISH,)]


def test_t29b_start_can_fire_again_after_a_finish():
    """T-29: 終了のあとでも開始を出せる (RN-06)。同じ卓で2回目のレースができる。"""
    events = feed(
        (IDLE, SELECTION_ALL),
        (STOP, SELECTION_ALL),
        (IDLE, SELECTION_ALL),
        (AUTONOMOUS, SELECTION_ALL),
    )

    assert events == [(), (RACE_FINISH,), (), (RACE_START,)]


def test_t30_both_conditions_fire_together():
    """T-30: 開始条件と終了条件が同時に成立したら両方発火する (RN-07)。"""
    both = joy_with_buttons(BUTTON_Y, EMERGENCY_BUTTONS[0])

    events = feed((IDLE, SELECTION_ALL), (both, SELECTION_ALL))

    assert events == [(), (RACE_START, RACE_FINISH)]


# ==========================================================================
# 起動直後
# ==========================================================================


@pytest.mark.parametrize("joy", [AUTONOMOUS, STOP])
def test_t31_first_joy_never_fires(joy):
    """T-31: 最初の joy では発火しない (RN-08)。

    ボタンを押したまま manager が起動したときに、押した覚えのない通知が飛ぶのを防ぐ。
    """
    assert race_events(None, race_triggers(joy, SELECTION_ALL)) == ()


# ==========================================================================
# ペイロード
# ==========================================================================


def test_t32_payload_is_jst_iso8601_with_milliseconds():
    """T-32: `+09:00` とミリ秒3桁の ISO 8601 になる (RN-02, RN-03)。"""
    # 2026-08-15T13:12:03.480+09:00 = 2026-08-15T04:12:03.480Z
    stamp_ns = 1_786_767_123_480_000_000

    assert to_jst_iso8601(stamp_ns) == "2026-08-15T13:12:03.480+09:00"
    assert json.loads(race_payload(RACE_START, stamp_ns)) == {
        "started_at": "2026-08-15T13:12:03.480+09:00"
    }
    assert json.loads(race_payload(RACE_FINISH, stamp_ns)) == {
        "finished_at": "2026-08-15T13:12:03.480+09:00"
    }


def test_t32b_milliseconds_are_truncated_not_rounded():
    """T-32: ミリ秒未満は切り捨てる。丸めると押下時刻より後になり得る。"""
    assert to_jst_iso8601(1_786_767_123_480_999_999).endswith(".480+09:00")


def test_t33_time_comes_from_the_joy_stamp():
    """T-33: 時刻は joy の header.stamp から作る (RN-09)。

    受信時刻を使うと、MQTT の接続や再試行にかかった時間が開始時刻に混じる。
    """
    joy = JoyValue(
        axes=JOY_FULL.axes, buttons=JOY_FULL.buttons, stamp_ns=1_786_767_123_480_000_000
    )

    assert json.loads(race_payload(RACE_START, joy.stamp_ns))["started_at"].startswith(
        "2026-08-15T13:12:03.480"
    )


def test_t34_malformed_joy_cannot_start_but_can_finish():
    """T-34: 要素数が規定と異なる joy では開始が発火しない (RN-10)。

    その joy は transform が操縦を許さない (REQ-18)。ボタン配列の違う機器の index 3 が
    偶然立ってレース開始が飛び、retain された started_at を上書きするのを防ぐ。
    止めるほうは壊れていても通す。
    """
    short_y = JoyValue(axes=(0.0,) * 4, buttons=(0, 0, 0, 1))
    short_stop = JoyValue(axes=(0.0,) * 4, buttons=(0, 0, 0, 0, 1))

    assert race_triggers(short_y, SELECTION_ALL).start is False
    assert race_triggers(short_stop, SELECTION_ALL).finish is True

    events = feed((IDLE, SELECTION_ALL), (short_y, SELECTION_ALL))
    assert events == [(), ()]

    events = feed((IDLE, SELECTION_ALL), (short_stop, SELECTION_ALL))
    assert events == [(), (RACE_FINISH,)]


def test_topics_are_fixed():
    """トピック名は仕様で固定されている (§2)。"""
    assert race_topic(RACE_START) == "kart_race_start"
    assert race_topic(RACE_FINISH) == "kart_race_finish"


# ==========================================================================
# 送信
# ==========================================================================


def test_publish_command_uses_qos1_and_retain():
    """retain なので、順位計算側が後から購読しても時刻を取りこぼさない (§2)。"""
    config = BrokerConfig(host="broker", port=1883, username="u", password="p")

    command = publish_command(config, "kart_race_start", '{"started_at":"x"}')

    assert command[:2] == ["mosquitto_pub", "-h"]
    assert "-r" in command
    assert command[command.index("-q") + 1] == "1"
    assert command[command.index("-t") + 1] == "kart_race_start"
    assert command[command.index("-u") + 1] == "u"
    assert command[command.index("-P") + 1] == "p"


def test_publish_command_without_credentials():
    """認証情報が無ければ -u / -P を付けない。"""
    command = publish_command(BrokerConfig(host="broker"), "t", "m")

    assert "-u" not in command
    assert "-P" not in command


def test_empty_broker_host_disables_the_notification():
    """MQTT_HOST が空なら通知を無効にする。manager は起動できなければならない (RN-12)。"""
    assert config_from_env({}).enabled is False
    assert config_from_env({"MQTT_HOST": "  "}).enabled is False
    assert config_from_env({"MQTT_HOST": "broker"}).enabled is True


def test_config_from_env_reads_the_documented_names():
    """§1 の変数名で読む。"""
    config = config_from_env(
        {
            "MQTT_HOST": "broker",
            "MQTT_PORT": "8883",
            "MQTT_USERNAME": "u",
            "MQTT_PASSWORD": "p",
        }
    )

    assert (config.host, config.port, config.username, config.password) == (
        "broker",
        8883,
        "u",
        "p",
    )


def test_config_from_env_falls_back_to_1883():
    """ポートが壊れていても既定値で動く。通知の設定ミスで manager を止めない。"""
    assert config_from_env({"MQTT_HOST": "broker", "MQTT_PORT": "x"}).port == 1883


def test_vehicles_fixture_is_used_by_the_selection_conditions():
    """開始条件は選択の文字列だけを見る。対象車両の並びには依存しない。"""
    assert race_triggers(AUTONOMOUS, SELECTION_ALL).start is True
    assert race_triggers(AUTONOMOUS, VEHICLES[0]).start is False
