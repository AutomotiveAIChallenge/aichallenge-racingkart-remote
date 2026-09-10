"""本体側GUIとの差分として追加したManager定義を検証する。"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import gui_tools as launcher


def test_manager_has_start_stop_and_restart_commands():
    assert [spec.label for spec in launcher.COMMANDS if spec.log_key == "manager"] == [
        "Start Manager",
        "Stop Manager",
        "Restart Manager",
    ]


def test_manager_uses_the_common_component_preamble():
    command = launcher.SPEC_MAP["Start Manager"].render(
        ["A2", "A3", "A6", "A7"], "A3"
    )
    assert command == (
        "REMOTE_COMPONENT_STDIO=1 ./remote_component.bash manager "
        "../output/gui-launcher A2 A3 A6 A7"
    )


def test_manager_is_added_to_the_controls_and_logs():
    assert ("Manager", ["Start Manager", "Stop Manager", "Restart Manager"]) in (
        launcher.COLUMN_LAYOUT
    )
    assert launcher.LOG_AREAS["manager"] == "Manager Log"


def test_zenoh_receives_all_selected_vehicles():
    command = launcher.SPEC_MAP["Start Zenoh"].render(["A2", "A6", "A7"], "A3")
    assert command == (
        "REMOTE_COMPONENT_STDIO=1 ./remote_component.bash zenoh "
        '../output/gui-launcher "A2 A6 A7"'
    )


def test_rviz_uses_its_separate_vehicle():
    command = launcher.SPEC_MAP["Start RViz"].render(["A2", "A6"], "A7")
    assert command == "./rviz.bash A7"


def test_combined_restart_uses_both_vehicle_settings():
    command = launcher.SPEC_MAP["Restart Zenoh and RViz"].render(
        ["A2", "A3", "A6", "A7"], "A6"
    )
    assert command == (
        "./rviz.bash restart A6 && "
        "REMOTE_COMPONENT_STDIO=1 ./remote_component.bash zenoh "
        '../output/gui-launcher "A2 A3 A6 A7"'
    )


def test_operational_fleet_is_selected_in_stable_order():
    assert launcher.VEHICLE_IDS == ["A2", "A3", "A6", "A7"]
