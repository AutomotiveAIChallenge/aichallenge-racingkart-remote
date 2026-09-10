"""本体側GUIとの差分として追加したManager定義を検証する。"""

from __future__ import annotations

import os
import pathlib
import subprocess
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


# --- LN-15: ランチャ GUI と make remote の多重起動防止 ---


def test_acquire_launcher_lock_writes_own_pid(tmp_path):
    lock = tmp_path / "gui-launcher.pid"
    assert launcher.acquire_launcher_lock(lock) is True
    assert lock.read_text().strip() == str(os.getpid())


def test_acquire_launcher_lock_creates_missing_output_dir(tmp_path):
    lock = tmp_path / "nested" / "output" / "gui-launcher.pid"
    assert launcher.acquire_launcher_lock(lock) is True
    assert lock.exists()


def test_acquire_launcher_lock_refuses_when_pid_is_alive(tmp_path):
    lock = tmp_path / "gui-launcher.pid"
    lock.write_text(f"{os.getpid()}\n")
    assert launcher.acquire_launcher_lock(lock) is False
    # 生きているPIDを書いたファイルは上書きされない。
    assert lock.read_text().strip() == str(os.getpid())


def test_acquire_launcher_lock_overwrites_a_stale_pid(tmp_path):
    lock = tmp_path / "gui-launcher.pid"
    lock.write_text("999999999\n")  # 存在しないであろう PID
    assert launcher.acquire_launcher_lock(lock) is True
    assert lock.read_text().strip() == str(os.getpid())


def test_acquire_launcher_lock_treats_garbage_content_as_stale(tmp_path):
    lock = tmp_path / "gui-launcher.pid"
    lock.write_text("not-a-pid\n")
    assert launcher.acquire_launcher_lock(lock) is True


def test_acquire_launcher_lock_treats_missing_file_as_stale(tmp_path):
    lock = tmp_path / "gui-launcher.pid"
    assert launcher.acquire_launcher_lock(lock) is True


def test_release_launcher_lock_removes_the_file(tmp_path):
    lock = tmp_path / "gui-launcher.pid"
    lock.write_text("123\n")
    launcher.release_launcher_lock(lock)
    assert not lock.exists()


def test_release_launcher_lock_is_a_noop_when_missing(tmp_path):
    lock = tmp_path / "gui-launcher.pid"
    launcher.release_launcher_lock(lock)  # 例外を投げないこと


def test_remote_stack_pid_is_none_when_the_file_is_missing(tmp_path):
    assert launcher.remote_stack_pid(tmp_path / "remote.pid") is None


def test_remote_stack_pid_is_none_when_the_group_is_stale(tmp_path):
    pid_file = tmp_path / "remote.pid"
    pid_file.write_text("999999999\n")
    assert launcher.remote_stack_pid(pid_file) is None


def test_remote_stack_pid_returns_the_pid_while_the_group_is_alive(tmp_path):
    # setsid 相当の「PID == プロセスグループID」を再現するため、実際に
    # start_new_session=True の子プロセスを立てて確認する。
    proc = subprocess.Popen(["sleep", "5"], start_new_session=True)
    try:
        pid_file = tmp_path / "remote.pid"
        pid_file.write_text(f"{proc.pid}\n")
        assert launcher.remote_stack_pid(pid_file) == proc.pid
    finally:
        proc.terminate()
        proc.wait()


def test_remote_stack_pid_is_none_once_the_group_has_exited(tmp_path):
    proc = subprocess.Popen(["sleep", "5"], start_new_session=True)
    proc.terminate()
    proc.wait()
    pid_file = tmp_path / "remote.pid"
    pid_file.write_text(f"{proc.pid}\n")
    assert launcher.remote_stack_pid(pid_file) is None


def test_command_touches_remote_component_for_zenoh_joy_manager():
    for label in ("Start Zenoh", "Restart Joy", "Start Manager"):
        command = launcher.SPEC_MAP[label].render(["A2"], "A2")
        assert launcher.command_touches_remote_component(command) is True


def test_command_touches_remote_component_is_false_for_rviz_alone():
    command = launcher.SPEC_MAP["Start RViz"].render(["A2"], "A2")
    assert launcher.command_touches_remote_component(command) is False


def test_command_touches_remote_component_is_true_for_combined_restart():
    command = launcher.SPEC_MAP["Restart Zenoh and RViz"].render(["A2"], "A2")
    assert launcher.command_touches_remote_component(command) is True
