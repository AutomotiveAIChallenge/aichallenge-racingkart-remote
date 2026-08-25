"""remote_launcher_core の検証。docs/spec/launcher.md §11 の観点に対応する。

remote_launcher_core は Tk にも subprocess にも ROS にも依存しないので、画面も ROS も
起こさずに走る。実行例:

    uv run --with pytest pytest scripts/tests -q

sys.path をここで足すのは、conftest.py を置くと manager/tests/conftest.py と
モジュール名がぶつかり、リポジトリルートから pytest を走らせたときに壊れるため。
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from remote_launcher_core import (
    FAILED,
    FLEET,
    RUNNING,
    STARTING,
    STOPPED,
    STOPPING,
    can_change_vehicles,
    can_stop,
    component_args,
    log_tabs,
    missing_vehicles,
    ordered,
    parse_brake_test,
    start_blocked_reason,
    transition,
)

ALL_PORTS = {"A2": 7448, "A3": 7449, "A6": 7450, "A7": 7451}


# --- 対象車両 -----------------------------------------------------------------


def test_fleet_is_the_four_karts_that_exist():
    """LN-07: 実機が存在する4台だけを並べる。"""
    assert FLEET == ("A2", "A3", "A6", "A7")


def test_missing_vehicles_is_empty_when_every_vehicle_resolves():
    """LN-08: 4台とも表から引ければ起動してよい。"""
    assert missing_vehicles(ALL_PORTS) == ()


def test_missing_vehicles_reports_vehicles_dropped_from_the_port_table():
    """LN-08: 表から消えた車両は黙って並べない。"""
    ports = dict(ALL_PORTS)
    ports["A6"] = None
    assert missing_vehicles(ports) == ("A6",)
    assert missing_vehicles({}) == FLEET


def test_zero_vehicles_blocks_components_that_need_them():
    """LN-09: 対象車両は1台以上。"""
    assert start_blocked_reason("zenoh", STOPPED, [], False) is not None
    assert start_blocked_reason("manager", STOPPED, [], False) is not None


def test_zero_vehicles_does_not_block_joy():
    """joy は宛先を持たないので車両が要らない。"""
    assert start_blocked_reason("joy", STOPPED, [], False) is None


def test_vehicles_are_locked_while_anything_is_up():
    """LN-10: 何か1つでも起動していたら集合を変えられない。"""
    assert can_change_vehicles({"zenoh": STOPPED, "joy": STOPPED, "manager": STOPPED})
    assert can_change_vehicles({"zenoh": FAILED, "joy": STOPPED, "manager": STOPPED})
    assert not can_change_vehicles({"zenoh": RUNNING, "joy": STOPPED, "manager": STOPPED})
    assert not can_change_vehicles({"zenoh": STARTING, "joy": STOPPED, "manager": STOPPED})
    assert not can_change_vehicles({"zenoh": STOPPING, "joy": STOPPED, "manager": STOPPED})


# --- 引数の組み立て -----------------------------------------------------------


def test_zenoh_gets_one_string_in_a_stable_order():
    """LN-12: 選んだ順ではなく FLEET の並びで揃える。"""
    assert component_args("zenoh", ["A7", "A2", "A3"]) == ["A2 A3 A7"]
    assert component_args("zenoh", {"A3", "A7"}) == component_args("zenoh", {"A7", "A3"})


def test_manager_gets_the_vehicle_ids_as_separate_arguments():
    assert component_args("manager", ["A7", "A2"]) == ["A2", "A7"]


def test_joy_takes_no_vehicles():
    assert component_args("joy", ["A2", "A3"]) == []


def test_brake_test_is_appended_only_when_enabled():
    """LN-30: 渡した値は manager のタイトルに出る。"""
    assert component_args("manager", ["A3"]) == ["A3"]
    assert component_args("manager", ["A3"], 20.0) == ["A3", "--brake-test", "20"]
    assert component_args("manager", ["A3"], 12.5) == ["A3", "--brake-test", "12.5"]


def test_brake_test_input_is_validated():
    assert parse_brake_test("") is None
    assert parse_brake_test("   ") is None
    assert parse_brake_test("20") == 20.0
    assert parse_brake_test("0") == 0.0
    assert parse_brake_test("100") == 100.0
    with pytest.raises(ValueError):
        parse_brake_test("-1")
    with pytest.raises(ValueError):
        parse_brake_test("101")
    with pytest.raises(ValueError):
        parse_brake_test("つよめ")


def test_ordered_keeps_the_fleet_order():
    assert ordered({"A7", "A2"}) == ("A2", "A7")
    assert ordered([]) == ()


def test_unknown_component_is_rejected():
    with pytest.raises(ValueError):
        component_args("rviz", ["A2"])


# --- 状態遷移 -----------------------------------------------------------------


def test_start_goes_through_starting_to_running():
    """LN-22: 起動要求のあと、生存を確認して初めて稼働中になる。"""
    state = transition(STOPPED, "start")
    assert state == STARTING
    assert transition(state, "alive") == RUNNING


def test_a_requested_stop_does_not_look_like_a_failure():
    """LN-20: run_zenoh.bash は kill されると exit 1 で終わる。"""
    state = transition(RUNNING, "stop")
    assert state == STOPPING
    assert transition(state, "exited") == STOPPED


def test_an_unrequested_exit_is_a_failure():
    """LN-19, LN-23: 落ちた子は上げ直さず、赤いまま残す。"""
    assert transition(RUNNING, "exited") == FAILED
    assert transition(STARTING, "exited") == FAILED


def test_failed_can_be_started_again():
    assert transition(FAILED, "start") == STARTING


def test_restart_waits_for_the_stop_to_finish():
    """LN-18: 停止しきる前に起こさない。"""
    assert transition(STOPPING, "start") == STOPPING
    assert start_blocked_reason("joy", STOPPING, [], False) is not None


def test_unknown_event_is_rejected():
    with pytest.raises(ValueError):
        transition(STOPPED, "explode")


# --- 操作の可否 ---------------------------------------------------------------


def test_starting_something_already_up_is_refused():
    assert start_blocked_reason("joy", RUNNING, [], False) is not None
    assert start_blocked_reason("joy", STARTING, [], False) is not None


def test_stopping_something_already_down_is_refused():
    assert not can_stop(STOPPED)
    assert not can_stop(FAILED)
    assert not can_stop(STOPPING)
    assert can_stop(RUNNING)
    assert can_stop(STARTING)


def test_make_remote_blocks_every_start():
    """LN-28: 同時に使うと joy が二重に流れる。"""
    for component in ("zenoh", "joy", "manager"):
        reason = start_blocked_reason(component, STOPPED, FLEET, True)
        assert reason is not None
        assert "make remote" in reason


# --- ログのタブ ---------------------------------------------------------------


def test_log_tabs_cover_the_supervisor_and_every_selected_vehicle():
    """LN-26: 前提が足りずに起動できなかった理由は zenoh.log にしか出ない。"""
    keys = [key for key, _ in log_tabs(["A3", "A2"])]
    assert keys == ["zenoh", "zenoh-A2", "zenoh-A3", "joy", "manager"]
    assert dict(log_tabs(["A2"]))["zenoh-A2"] == "zenoh-A2.log"
