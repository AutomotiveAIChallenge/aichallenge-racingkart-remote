#!/usr/bin/env python3
"""racing_kart_manager のエントリ。

joy の中継・選択の GUI・レース通知を1つのプロセスで行う (REQ-01)。判断はすべて
racing_kart_manager_core の純関数が行い、このファイルは「購読して、呼んで、publish
する」だけの薄い層に徹する。

スレッドは3つ。

    メイン        Tk の mainloop、ボタン、100ms の再描画
    ROS 実行      joy の受信・変換・publish・レース通知の立ち上がり判定
    race_notifier mosquitto_pub の起動と再試行

守る約束は3つ。

    1. Tk のウィジェットに触るのはメインスレッドだけ (Tkinter はスレッドセーフでない)
    2. node.selection を書くのはメインスレッドだけ。ROS スレッドは読むだけ
    3. joy のコールバックは外部 I/O を待たない。通知はキューに積んで即座に戻る

仕様: docs/spec/joy-routing.md, docs/spec/race-notification.md
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Joy

from race_notifier import RaceNotifier, config_from_env
from racing_kart_manager_core import (
    INITIAL_SELECTION,
    JoyValue,
    brake_test_engaged,
    parse_vehicles,
    race_events,
    race_triggers,
    transform,
    with_brake_test,
)
from racing_kart_manager_gui import ManagerWindow

#: ROS スレッドが抜けるのを待つ時間 (REQ-05)
SHUTDOWN_TIMEOUT_S = 2.0


def to_core_joy(msg: Joy) -> JoyValue:
    stamp = msg.header.stamp
    return JoyValue(
        axes=tuple(float(a) for a in msg.axes),
        buttons=tuple(int(b) for b in msg.buttons),
        stamp_ns=int(stamp.sec) * 1_000_000_000 + int(stamp.nanosec),
    )


def to_ros_joy(value: JoyValue) -> Joy:
    msg = Joy()
    msg.header.stamp.sec = value.stamp_ns // 1_000_000_000
    msg.header.stamp.nanosec = value.stamp_ns % 1_000_000_000
    msg.axes = [float(a) for a in value.axes]
    msg.buttons = [int(b) for b in value.buttons]
    return msg


class RacingKartManagerNode(Node):
    def __init__(
        self,
        vehicles: tuple[str, ...],
        notifier: RaceNotifier,
        brake_test: float | None = None,
    ) -> None:
        super().__init__("racing_kart_manager")

        self.vehicles = vehicles

        #: ブレーキ試験の比率 (0.0-1.0)。None なら機能そのものが無い (§11)
        self.brake_test = brake_test
        self._brake_engaged = False

        #: GUI (メインスレッド) が書き、joy のコールバック (ROS スレッド) が読む。
        #: 書き手が1つで、読み書きとも文字列1つの代入なのでロックは要らない。
        self.selection = INITIAL_SELECTION

        self._notifier = notifier

        #: レース通知の立ち上がり判定用。_on_joy の中だけで読み書きする。
        #: 単一スレッドの executor で直列に走ることが前提 (REQ-04)。
        self._triggers = None

        self._joy_publishers = {
            vehicle_id: self.create_publisher(Joy, f"/{vehicle_id}/racing_kart/sd/joy", 1)
            for vehicle_id in vehicles
        }

        self.create_subscription(Joy, "/racing_kart/joy", self._on_joy, 1)

        self.get_logger().info(f"racing_kart_manager started. vehicles={vehicles}")

    def _on_joy(self, msg: Joy) -> None:
        """joy 受信が唯一の publish 契機 (REQ-13)。

        タイマーで publish すると、ジョイスティックが死んでも車両には新鮮な joy が
        届き続け、車両側の5秒の生存チェーンが成立しなくなる。
        """
        value = to_core_joy(msg)

        # 冒頭で1回だけ読む。この1件を処理している間に選択が変わっても、
        # 宛先とレース通知が食い違わない。
        selection = self.selection

        engaged = brake_test_engaged(value, selection, self.vehicles, self.brake_test)
        if engaged:
            value = with_brake_test(value, self.brake_test)
        if engaged != self._brake_engaged:
            self.get_logger().info(
                f"brake test {'engaged' if engaged else 'released'}"
                f" ({self.brake_test * 100:g}%) on {selection}"
            )
            self._brake_engaged = engaged

        for vehicle_id, outgoing in transform(value, selection, self.vehicles).items():
            self._joy_publishers[vehicle_id].publish(to_ros_joy(outgoing))

        triggers = race_triggers(value, selection)
        for event in race_events(self._triggers, triggers):
            self._notifier.publish(event, value.stamp_ns)
        self._triggers = triggers


def parse_arguments(argv: "list[str]") -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="遠隔操作PCの joy 中継。対象車両を1台以上、重複なしで指定する。"
    )
    parser.add_argument("vehicles", nargs="*", metavar="VEHICLE_ID")
    parser.add_argument(
        "--brake-test",
        type=float,
        default=None,
        metavar="PERCENT",
        help="実験用。B ボタンを押している間、ステアだけ自動にして一定ブレーキを入れる",
    )
    return parser.parse_args(argv)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s")

    arguments = parse_arguments(sys.argv[1:])

    vehicles = parse_vehicles(arguments.vehicles)
    if vehicles is None:
        print(
            "usage: racing_kart_manager.py <VEHICLE_ID> [VEHICLE_ID ...]\n"
            "  例: racing_kart_manager.py A2 A3 A7\n"
            "  対象車両を1台以上、重複なしで指定してください。",
            file=sys.stderr,
        )
        raise SystemExit(1)

    brake_test = arguments.brake_test
    if brake_test is not None:
        if not 0.0 <= brake_test <= 100.0:
            print(
                f"--brake-test は 0 から 100 の間で指定してください: {brake_test}",
                file=sys.stderr,
            )
            raise SystemExit(1)
        brake_test /= 100.0

    notifier = RaceNotifier(config_from_env())

    # 起動のどこで失敗しても後始末が走るように、ここから finally の中に入れる。
    # 途中で落ちたときに部品が半端に残ると、プロセスグループに死なないプロセスが
    # 居座る。
    node = None
    spinner = None
    try:
        rclpy.init()
        node = RacingKartManagerNode(vehicles, notifier, brake_test)

        # ROS は別スレッドで回す。GUI の描画や操作が joy の中継を止めないため (REQ-03)。
        # rclpy.spin は単一スレッドの executor を使う。joy のコールバックが到着順に
        # 直列で走ることに、レース通知の立ち上がり判定が依存している (REQ-04)。
        spinner = threading.Thread(target=rclpy.spin, args=(node,), daemon=True)
        spinner.start()

        # DISPLAY が無ければここで落ちる。画面のない運用は想定しない (REQ-02)。
        window = ManagerWindow(node)

        # rclpy.init() が SIGTERM を横取りするので、その後で上書きする。そのままだと
        # make remote-stop の TERM で Tk の mainloop が抜けない。ハンドラは
        # _refresh の after() で Python に戻る隙 (100ms ごと) に走る。
        def on_terminate(signum, frame) -> None:  # noqa: ARG001
            window.root.quit()

        signal.signal(signal.SIGTERM, on_terminate)
        signal.signal(signal.SIGINT, on_terminate)

        window.run()
    finally:
        # shutdown → join → destroy の順 (REQ-05)。executor が保持しているノードを
        # 別スレッドから壊さない。
        rclpy.try_shutdown()
        if spinner is not None:
            spinner.join(timeout=SHUTDOWN_TIMEOUT_S)
        if node is not None:
            node.destroy_node()
        notifier.close()


if __name__ == "__main__":
    main()
