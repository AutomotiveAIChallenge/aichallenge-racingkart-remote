"""本体側GUIとの差分として追加したManager定義を検証する。"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
from unittest import mock

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import gui_tools as launcher


def test_manager_has_start_stop_and_restart_commands():
    assert [spec.label for spec in launcher.COMMANDS if spec.log_key == "manager"] == [
        "Start Manager",
        "Stop Manager",
        "Restart Manager",
    ]


def test_manager_uses_the_common_component_preamble():
    command = launcher.SPEC_MAP["Start Manager"].render(["A2", "A3", "A6", "A7"])
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
    command = launcher.SPEC_MAP["Start Zenoh"].render(["A2", "A6", "A7"])
    assert command == (
        "REMOTE_COMPONENT_STDIO=1 ./remote_component.bash zenoh "
        '../output/gui-launcher "A2 A6 A7"'
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
        command = launcher.SPEC_MAP[label].render(["A2"])
        assert launcher.command_touches_remote_component(command) is True


# --- RC13: 孤児 joy_node の掃除 ---


@pytest.mark.parametrize(
    ("executable", "matches"),
    [
        ("/opt/ros/humble/lib/joy/joy_node", True),
        ("/tmp/overlay/install/joy/lib/joy/joy_node", True),
        ("/opt/ros/humble/lib/joy/joy_node_other", False),
        ("/opt/ros/humble/lib/other/joy_node", False),
        ("ros2 run joy joy_node", False),
    ],
)
def test_orphan_pattern_matches_only_the_joy_executable(executable, matches):
    # ROS を起動せず、孤児と同じ argv[0] を持つ子を作る。pgrep は pkill と同じ
    # 正規表現を使い、-P でこのテストの子だけに限定する。
    proc = subprocess.Popen([executable, "30"], executable="/bin/sleep")
    try:
        result = subprocess.run(
            ["pgrep", "-f", "-P", str(os.getpid()), launcher.ORPHAN_KILL_PATTERNS["joy"]],
            capture_output=True,
            text=True,
            timeout=3.0,
        )
        assert (str(proc.pid) in result.stdout.splitlines()) is matches
    finally:
        proc.terminate()
        proc.wait(timeout=3.0)


@pytest.mark.parametrize("phase", ["idle", "running", "pending", "stopping"])
def test_stop_joy_is_available_for_orphans_except_while_busy(phase):
    app = object.__new__(launcher.RemoteGui)
    app.buttons = {
        label: mock.Mock() for label in ("Stop Joy", "Stop Zenoh", "Stop Manager")
    }
    app._button_state_cache = {}
    app._process_phase = lambda key: phase
    app._refresh_status_indicators = mock.Mock()

    app._refresh_button_states()

    joy_state = launcher.tk.NORMAL if phase in ("idle", "running") else launcher.tk.DISABLED
    tracked_state = launcher.tk.NORMAL if phase == "running" else launcher.tk.DISABLED
    app.buttons["Stop Joy"].configure.assert_called_once_with(state=joy_state)
    for label in ("Stop Zenoh", "Stop Manager"):
        app.buttons[label].configure.assert_called_once_with(state=tracked_state)


def test_unregistered_log_key_does_not_call_pkill():
    # zenoh/manager は明示登録が無い。無関係なプロセスを巻き込まないよう pkill 自体を
    # 呼ばない。
    with mock.patch("gui_tools.subprocess.run") as run:
        assert launcher.kill_orphan_pattern("manager") is False
    run.assert_not_called()


def test_joy_is_registered_and_uses_pkill_dash_f():
    assert "joy" in launcher.ORPHAN_KILL_PATTERNS
    completed = mock.Mock(returncode=0)
    with mock.patch("gui_tools.subprocess.run", return_value=completed) as run:
        assert launcher.kill_orphan_pattern("joy") is True
    run.assert_called_once()
    args, kwargs = run.call_args
    assert args[0] == ["pkill", "-f", launcher.ORPHAN_KILL_PATTERNS["joy"]]
    assert kwargs.get("timeout") == 3.0


def test_wait_for_orphan_gone_returns_true_once_pgrep_finds_nothing():
    # pkill はシグナルを送るだけ。消える前に新プロセスを起こすと publisher が二重になる。
    results = [mock.Mock(returncode=0), mock.Mock(returncode=1)]
    with mock.patch("gui_tools.subprocess.run", side_effect=results) as run:
        with mock.patch("gui_tools.time.sleep"):
            assert launcher.wait_for_orphan_gone("joy", timeout=1.0, interval=0.0) is True
    assert run.call_count == 2
    assert run.call_args[0][0] == ["pgrep", "-f", launcher.ORPHAN_KILL_PATTERNS["joy"]]


def test_wait_for_orphan_gone_returns_false_when_the_orphan_survives():
    # 居座り続けるなら timeout で諦めて False。GUI 側はそれをログに出す。
    with mock.patch("gui_tools.subprocess.run", return_value=mock.Mock(returncode=0)):
        with mock.patch("gui_tools.time.sleep"):
            assert launcher.wait_for_orphan_gone("joy", timeout=0.0, interval=0.0) is False


def test_wait_for_orphan_gone_is_a_noop_for_unregistered_log_key():
    with mock.patch("gui_tools.subprocess.run") as run:
        assert launcher.wait_for_orphan_gone("manager") is True
    run.assert_not_called()


def test_no_match_returns_false():
    # pkill は該当プロセスが無いと非0を返す。誤って「掃除した」とログしないための境界。
    completed = mock.Mock(returncode=1)
    with mock.patch("gui_tools.subprocess.run", return_value=completed):
        assert launcher.kill_orphan_pattern("joy") is False
