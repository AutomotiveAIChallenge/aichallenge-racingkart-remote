"""ランチャの純ロジック。

Tk にも subprocess にも ROS にも依存しない。判断はここが行い、remote_launcher.py は
「押されたら呼ぶ、返ったら描く」だけの薄い層に徹する (LN-32)。manager 側の
racing_kart_manager_core.py と同じ作りで、テストは ROS も画面も起こさずに走る。

仕様: docs/spec/launcher.md
"""

from __future__ import annotations

#: ランチャが並べる車両 (LN-07)。実機が存在するのはこの4台で、shared/vehicle_ports.sh の
#: vehicle_id_for_hostname が挙げる ECU と一致する。VEHICLE_ID_VALID_LIST は A1 / A5 / A8 も
#: 含むが、選べても繋がる先が無い。
FLEET: "tuple[str, ...]" = ("A2", "A3", "A6", "A7")

#: 管理する構成要素 (LN-01)。RViz は含めない。コンテナで動き lifecycle が違う。
COMPONENTS: "tuple[str, ...]" = ("zenoh", "joy", "manager")

#: 対象車両が要る構成要素。joy は宛先を持たないので要らない。
NEEDS_VEHICLES: "tuple[str, ...]" = ("zenoh", "manager")

# 状態 (LN-21〜23)
STOPPED = "stopped"
STARTING = "starting"
RUNNING = "running"
STOPPING = "stopping"
FAILED = "failed"

#: 起動していない状態。FAILED も「動いていない」ので対象車両を変えてよい。
IDLE_STATES: "tuple[str, ...]" = (STOPPED, FAILED)

#: 動いている状態。停止できるのはこの2つだけ。
LIVE_STATES: "tuple[str, ...]" = (STARTING, RUNNING)

STATE_LABELS = {
    STOPPED: "停止",
    STARTING: "起動中",
    RUNNING: "稼働中",
    STOPPING: "停止処理中",
    FAILED: "異常終了",
}

#: 停止・再起動の前に出す確認 (LN-27)。どれを止めても joy が5秒途切れ、車両側が
#: 緊急停止をラッチする。解除には両スティックの押し込みが要る。
STOP_WARNING = (
    "{component} を止めると joy の配信が途切れ、5秒で車両が緊急停止をラッチします。\n"
    "解除には左右スティックの同時押し込みが要ります。\n\n"
    "車両は停車していますか？"
)


def missing_vehicles(ports: "dict[str, int | None]") -> "tuple[str, ...]":
    """ポート表から引けなかった車両を返す (LN-08)。

    ポート表の唯一の出どころは shared/vehicle_ports.sh で、ランチャは複製を持たない。
    表から消えた車両を黙って並べると、繋がらない相手を選べてしまう。
    """
    return tuple(vehicle for vehicle in FLEET if not ports.get(vehicle))


def ordered(vehicles: "list[str] | tuple[str, ...] | set[str]") -> "tuple[str, ...]":
    """FLEET の並びに揃える。

    選んだ順で引数が変わると、同じ選択でもコマンド行が一致しない。
    """
    chosen = set(vehicles)
    return tuple(vehicle for vehicle in FLEET if vehicle in chosen)


def parse_brake_test(text: "str | None") -> "float | None":
    """ブレーキ試験の入力を読む。空なら機能そのものが無い (§11)。"""
    text = (text or "").strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        raise ValueError("ブレーキ試験は数値で指定してください。") from None
    if not 0.0 <= value <= 100.0:
        raise ValueError(f"ブレーキ試験は 0 から 100 の間で指定してください: {text}")
    return value


def component_args(
    component: str,
    vehicles: "list[str] | tuple[str, ...] | set[str]",
    brake_test: "float | None" = None,
) -> "list[str]":
    """remote_component.bash に渡す引数 (LOG_DIR より後ろ) を組み立てる。"""
    chosen = ordered(vehicles)
    if component == "zenoh":
        # run_zenoh.bash は車両を1つの文字列で受ける。
        return [" ".join(chosen)]
    if component == "joy":
        return []
    if component == "manager":
        args = list(chosen)
        if brake_test is not None:
            args += ["--brake-test", f"{brake_test:g}"]
        return args
    raise ValueError(f"unknown component: {component}")


def start_blocked_reason(
    component: str,
    state: str,
    vehicles: "list[str] | tuple[str, ...] | set[str]",
    remote_pid_alive: bool,
) -> "str | None":
    """起動できない理由。起動してよければ None。"""
    if remote_pid_alive:
        # make remote とランチャを同時に使うと joy が二重に流れる (LN-28)。
        return (
            "make remote が動いています (output/remote.pid)。\n"
            "joy が二重に流れるので、先に make remote-stop で止めてください。"
        )
    if state in LIVE_STATES:
        return f"{component} はすでに起動しています。"
    if state == STOPPING:
        return f"{component} は停止処理の途中です。"
    if component in NEEDS_VEHICLES and not ordered(vehicles):
        # 対象車両は1台以上 (LN-09)。
        return "対象車両を1台以上選んでください。"
    return None


def can_stop(state: str) -> bool:
    return state in LIVE_STATES


def can_change_vehicles(states: "dict[str, str] | list[str]") -> bool:
    """対象車両を変えてよいか (LN-10)。

    manager は対象車両を起動引数で確定し、選択ボタンを1回作るだけ。zenoh は車両ごとに
    1プロセスを持つ。走行中に集合を変えると両者がズレる。
    """
    values = states.values() if isinstance(states, dict) else states
    return all(state in IDLE_STATES for state in values)


def transition(state: str, event: str) -> str:
    """状態遷移 (LN-21〜23)。知らない組み合わせでは状態を変えない。"""
    if event == "start":
        return STARTING if state in IDLE_STATES else state
    if event == "alive":
        return RUNNING if state in (STARTING, RUNNING) else state
    if event == "stop":
        return STOPPING if state in LIVE_STATES else state
    if event == "exited":
        # 停止要求で終わった子を異常終了として扱わない (LN-20)。run_zenoh.bash は
        # kill されると exit 1 で終わるので、区別しないと正常な停止が毎回赤くなる。
        if state == STOPPING:
            return STOPPED
        if state in LIVE_STATES:
            return FAILED
        return state
    raise ValueError(f"unknown event: {event}")


def log_tabs(vehicles: "list[str] | tuple[str, ...] | set[str]") -> "list[tuple[str, str]]":
    """画面に出すログのタブ (LN-26)。

    zenoh は2種類ある。zenoh.log は前段と監視プロセスの出力で、前提が足りずに
    起動できなかった理由もここに出る。車両ごとの実際のブリッジ出力は
    zenoh-<ID>.log にある。前者が無いと、起動しなかったときに何も分からない。
    """
    tabs = [("zenoh", "zenoh.log")]
    tabs += [(f"zenoh-{v}", f"zenoh-{v}.log") for v in ordered(vehicles)]
    tabs += [("joy", "joy.log"), ("manager", "manager.log")]
    return tabs
