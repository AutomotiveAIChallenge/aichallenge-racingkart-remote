"""本体側GUIとの差分として追加したManager定義を検証する。"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import remote_launcher as launcher


def test_manager_has_start_stop_and_restart_commands():
    assert [spec.label for spec in launcher.COMMANDS if spec.log_key == "manager"] == [
        "Start Manager",
        "Stop Manager",
        "Restart Manager",
    ]


def test_manager_uses_the_common_component_preamble():
    command = launcher.SPEC_MAP["Start Manager"].render("A3")
    assert command == (
        "REMOTE_COMPONENT_STDIO=1 ./remote_component.bash manager "
        "../output/gui-launcher A3"
    )


def test_manager_is_added_to_the_controls_and_logs():
    assert ("Manager", ["Start Manager", "Stop Manager", "Restart Manager"]) in (
        launcher.COLUMN_LAYOUT
    )
    assert launcher.LOG_AREAS["manager"] == "Manager Log"
