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


def test_operational_fleet_is_selected_in_stable_order(tmp_path):
    # VEHICLE_IDS はもうハードコードではなく shared/vehicle_ports.sh から読む (LN-16)。
    # ここではソートし直したりせず、ファイルに書かれた順のまま保つことだけを見る。
    vehicle_ports = tmp_path / "vehicle_ports.sh"
    vehicle_ports.write_text('VEHICLE_ID_VALID_LIST="A7, A2, A6, A3"\n')
    assert launcher.load_vehicle_ids(vehicle_ports) == ["A7", "A2", "A6", "A3"]


def test_operational_fleet_is_loaded_from_the_shared_vehicle_ports_file_at_import():
    assert launcher.VEHICLE_IDS == launcher.load_vehicle_ids(launcher.VEHICLE_PORTS_PATH)


# --- LN-16: 車両リストは shared/vehicle_ports.sh、既定選択は .env から ---


def test_load_vehicle_ids_reads_the_shared_fleet_list(tmp_path):
    vehicle_ports = tmp_path / "vehicle_ports.sh"
    vehicle_ports.write_text('VEHICLE_ID_VALID_LIST="A1, A2, A3, A5, A6, A7, A8"\n')
    assert launcher.load_vehicle_ids(vehicle_ports) == [
        "A1",
        "A2",
        "A3",
        "A5",
        "A6",
        "A7",
        "A8",
    ]


def test_load_vehicle_ids_picks_up_a_newly_added_vehicle_with_no_code_change(tmp_path):
    vehicle_ports = tmp_path / "vehicle_ports.sh"
    vehicle_ports.write_text('VEHICLE_ID_VALID_LIST="A1, A2, A3, A4, A5, A6, A7, A8"\n')
    assert "A4" in launcher.load_vehicle_ids(vehicle_ports)


def test_load_vehicle_ids_falls_back_when_the_file_is_missing(tmp_path):
    missing = tmp_path / "does-not-exist.sh"
    assert launcher.load_vehicle_ids(missing) == ["A2", "A3", "A6", "A7"]


def test_load_vehicle_ids_falls_back_when_the_variable_is_empty(tmp_path):
    vehicle_ports = tmp_path / "vehicle_ports.sh"
    vehicle_ports.write_text('VEHICLE_ID_VALID_LIST=""\n')
    assert launcher.load_vehicle_ids(vehicle_ports) == ["A2", "A3", "A6", "A7"]


def test_default_selected_vehicles_reads_remote_vehicles_from_env(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text('REMOTE_VEHICLES="A2 A3 A6 A7"\n')
    vehicle_ids = ["A1", "A2", "A3", "A5", "A6", "A7", "A8"]
    assert launcher.default_selected_vehicles(env_file, vehicle_ids) == [
        "A2",
        "A3",
        "A6",
        "A7",
    ]


def test_default_selected_vehicles_accepts_commas_too(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("REMOTE_VEHICLES=A2,A3, A6,A7\n")
    vehicle_ids = ["A1", "A2", "A3", "A5", "A6", "A7", "A8"]
    assert launcher.default_selected_vehicles(env_file, vehicle_ids) == [
        "A2",
        "A3",
        "A6",
        "A7",
    ]


def test_default_selected_vehicles_ignores_unknown_ids(tmp_path, capsys):
    env_file = tmp_path / ".env"
    env_file.write_text('REMOTE_VEHICLES="A2 A99"\n')
    vehicle_ids = ["A1", "A2", "A3"]
    assert launcher.default_selected_vehicles(env_file, vehicle_ids) == ["A2"]
    assert "A99" in capsys.readouterr().err


def test_default_selected_vehicles_ignores_comments_and_blank_lines(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# 統括SD PC 用\n\nMQTT_HOST=example.com\nREMOTE_VEHICLES=\"A2 A3\"\n"
    )
    vehicle_ids = ["A2", "A3"]
    assert launcher.default_selected_vehicles(env_file, vehicle_ids) == ["A2", "A3"]


def test_default_selected_vehicles_selects_nothing_when_env_is_missing(tmp_path):
    missing = tmp_path / ".env"
    assert launcher.default_selected_vehicles(missing, ["A2", "A3"]) == []


def test_default_selected_vehicles_selects_nothing_when_unset(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("MQTT_HOST=example.com\n")
    assert launcher.default_selected_vehicles(env_file, ["A2", "A3"]) == []


def test_default_selected_vehicles_selects_nothing_when_value_is_empty(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text('REMOTE_VEHICLES=""\n')
    assert launcher.default_selected_vehicles(env_file, ["A2", "A3"]) == []


def test_default_rviz_vehicle_prefers_the_first_selected_vehicle():
    assert launcher.default_rviz_vehicle(["A3", "A6"], ["A2", "A3", "A6"]) == "A3"


def test_default_rviz_vehicle_falls_back_to_the_first_known_vehicle():
    assert launcher.default_rviz_vehicle([], ["A2", "A3", "A6"]) == "A2"


def test_default_rviz_vehicle_is_empty_when_there_are_no_vehicles():
    assert launcher.default_rviz_vehicle([], []) == ""


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
