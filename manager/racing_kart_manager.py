#!/usr/bin/env python3
"""racing_kart_manager の ROS ノード。

判断はすべて racing_kart_manager_core の純関数が行い、このファイルは
「購読して、呼んで、publish する」だけの薄い層に徹する。

依存するメッセージパッケージは sensor_msgs と std_msgs だけ。車両テレメトリは
購読しない。

仕様: docs/spec/joy-routing.md
"""

from __future__ import annotations

import rclpy
from rclpy.node import Node
from rclpy.qos import (
    QoSDurabilityPolicy,
    QoSHistoryPolicy,
    QoSProfile,
    QoSReliabilityPolicy,
)
from sensor_msgs.msg import Joy
from std_msgs.msg import String

from racing_kart_manager_core import (
    INITIAL_SELECTION,
    JoyValue,
    emergency_pressed,
    parse_command,
    parse_vehicles,
    select,
    status_to_json,
    transform,
)

STATUS_PUBLISH_RATE_HZ = 5.0

#: GUI を後から起動しても最新の選択が出るように transient_local にする
STATUS_QOS = QoSProfile(
    depth=1,
    history=QoSHistoryPolicy.KEEP_LAST,
    reliability=QoSReliabilityPolicy.RELIABLE,
    durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
)

#: 選択の切り替えを取りこぼさない
COMMAND_QOS = QoSProfile(
    depth=10,
    history=QoSHistoryPolicy.KEEP_LAST,
    reliability=QoSReliabilityPolicy.RELIABLE,
)


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
    def __init__(self, vehicles: tuple[str, ...]) -> None:
        super().__init__("racing_kart_manager")

        self._vehicles = vehicles
        self._selection = INITIAL_SELECTION

        self._joy: JoyValue | None = None
        self._joy_at: float | None = None

        self._joy_publishers = {
            vehicle_id: self.create_publisher(
                Joy, f"/{vehicle_id}/racing_kart/joy", 1
            )
            for vehicle_id in vehicles
        }

        self.create_subscription(Joy, "/racing_kart/joy", self._on_joy, 1)

        self._status_publisher = self.create_publisher(
            String, "/racing_kart_manager/status", STATUS_QOS
        )
        self.create_subscription(
            String, "/racing_kart_manager/command", self._on_command, COMMAND_QOS
        )
        self.create_timer(1.0 / STATUS_PUBLISH_RATE_HZ, self._publish_status)

        self.get_logger().info(f"racing_kart_manager started. vehicles={vehicles}")

    def _now(self) -> float:
        return self.get_clock().now().nanoseconds / 1e9

    def _on_joy(self, msg: Joy) -> None:
        """joy 受信が唯一の publish 契機 (REQ-09)。

        タイマーで publish すると、ジョイスティックが死んでも車両には新鮮な joy が
        届き続け、車両側の5秒の生存チェーンが成立しなくなる。
        """
        value = to_core_joy(msg)
        self._joy = value
        self._joy_at = self._now()

        for vehicle_id, outgoing in transform(
            value, self._selection, self._vehicles
        ).items():
            self._joy_publishers[vehicle_id].publish(to_ros_joy(outgoing))

    def _on_command(self, msg: String) -> None:
        target = parse_command(msg.data)
        if target is None:
            self.get_logger().warn(f"invalid command dropped: {msg.data!r}")
            return

        before = self._selection
        self._selection = select(before, target, self._vehicles)
        if self._selection != before:
            self.get_logger().info(f"selection {before} -> {self._selection}")

    def _publish_status(self) -> None:
        """status だけはタイマーで出す。joy の送出経路ではないので生存チェーンに影響しない。"""
        joy_age_s = None if self._joy_at is None else self._now() - self._joy_at
        emergency = self._joy is not None and emergency_pressed(self._joy)

        message = String()
        message.data = status_to_json(
            self._selection,
            self._vehicles,
            joy_age_s,
            emergency,
            self.get_clock().now().nanoseconds,
        )
        self._status_publisher.publish(message)


def main() -> None:
    import sys

    vehicles = parse_vehicles(sys.argv[1:])
    if vehicles is None:
        print(
            "usage: racing_kart_manager.py <VEHICLE_ID> [VEHICLE_ID ...]\n"
            "  例: racing_kart_manager.py A2 A3 A7\n"
            "  対象車両を1台以上、重複なしで指定してください。",
            file=sys.stderr,
        )
        raise SystemExit(1)

    rclpy.init()
    node = RacingKartManagerNode(vehicles)
    try:
        rclpy.spin(node)  # 既定の SingleThreadedExecutor。到着順の処理を保証する
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
