"""status / command のシリアライズのテスト (T-25 〜 T-27)。

仕様: docs/spec/joy-routing.md §8

manager と GUI は別プロセスなので、この JSON が両者の唯一の契約になる。
形を変えたら SCHEMA_VERSION を上げること。
"""

from __future__ import annotations

import json

import pytest

from conftest import VEHICLES
from racing_kart_manager_core import (
    SCHEMA_VERSION,
    SELECTION_ALL,
    SELECTION_NONE,
    command_to_json,
    parse_command,
    status_to_json,
)


# ==========================================================================
# status
# ==========================================================================


def test_t25_status_has_the_documented_shape():
    """T-25: status が §8.3 の形になる。"""
    payload = json.loads(
        status_to_json(
            selection="A3",
            vehicles=VEHICLES,
            joy_age_s=0.05,
            emergency=False,
            stamp_ns=1234567890,
        )
    )

    assert payload == {
        "schema_version": SCHEMA_VERSION,
        "stamp_ns": 1234567890,
        "selection": "A3",
        "vehicles": ["A2", "A3", "A7"],
        "joy_age_s": 0.05,
        "emergency_pressed": False,
    }


def test_t25b_vehicles_keep_the_startup_order():
    """T-25: 対象車両は起動引数の順のまま出す。GUI のボタン並びに効く。"""
    payload = json.loads(
        status_to_json(SELECTION_NONE, ("A7", "A2"), None, False, 0)
    )

    assert payload["vehicles"] == ["A7", "A2"]


def test_t25c_never_received_joy_is_null_not_zero():
    """T-25: 一度も joy を受けていなければ joy_age_s は null。

    0 に潰すと「たった今受け取った」と読めてしまう。
    """
    payload = json.loads(status_to_json(SELECTION_NONE, VEHICLES, None, False, 0))

    assert payload["joy_age_s"] is None


@pytest.mark.parametrize("selection", [SELECTION_NONE, SELECTION_ALL, "A3"])
def test_t25d_selection_is_written_as_is(selection):
    """T-25: selection は車両ID / "all" / "none" のいずれかをそのまま出す。"""
    payload = json.loads(status_to_json(selection, VEHICLES, 0.1, False, 0))

    assert payload["selection"] == selection


# ==========================================================================
# command
# ==========================================================================


@pytest.mark.parametrize("target", [SELECTION_NONE, SELECTION_ALL, "A3"])
def test_t26_command_round_trips(target):
    """T-26: GUI が書いた command を manager が同じ意味で読む (§8.2)。

    両者が別プロセスで動く以上、片方だけ直しても気づけない。往復で固定する。
    """
    assert parse_command(command_to_json(target)) == target


def test_t26b_command_has_the_documented_shape():
    """T-26: command が §8.2 の形になる。"""
    payload = json.loads(command_to_json("A3"))

    assert payload == {
        "schema_version": SCHEMA_VERSION,
        "command": "select",
        "target": "A3",
    }


def test_t27_command_with_another_schema_version_is_ignored():
    """T-27: schema_version が違うコマンドは無視する (REQ-08)。

    片方だけ更新して黙って誤動作するのを防ぐ。
    """
    payload = json.dumps(
        {
            "schema_version": SCHEMA_VERSION + 1,
            "command": "select",
            "target": "A3",
        }
    )

    assert parse_command(payload) is None


def test_t27b_command_without_schema_version_is_ignored():
    """T-27: schema_version が無いものも無視する。"""
    assert parse_command(json.dumps({"command": "select", "target": "A3"})) is None
