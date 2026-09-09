"""レース開始・終了通知のペイロードと送信設定のテスト。

通知の発火条件は GUI の一斉指令と同じ状態機械で扱うため、test_command.py で検証する。
仕様: docs/spec/race-notification.md
"""

from __future__ import annotations

import json

from race_notifier import BrokerConfig, config_from_env, publish_command
from racing_kart_manager_core import (
    RACE_FINISH,
    RACE_START,
    race_payload,
    race_topic,
    to_jst_iso8601,
)


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


def test_t33_time_comes_from_the_command_frame_stamp():
    """T-33: 一斉指令を最初に乗せた joy の時刻からペイロードを作る (RN-17)。"""
    stamp_ns = 1_786_767_123_480_000_000

    assert json.loads(race_payload(RACE_START, stamp_ns))["started_at"].startswith(
        "2026-08-15T13:12:03.480"
    )


def test_topics_are_fixed():
    """トピック名は仕様で固定されている (§2)。"""
    assert race_topic(RACE_START) == "kart_race_start"
    assert race_topic(RACE_FINISH) == "kart_race_finish"


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
