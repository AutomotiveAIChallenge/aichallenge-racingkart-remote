#!/bin/bash
# racing_kart_manager の起動。
#
#   manager.bash A2 A3 A7   # 対象車両を指定
#
# joy の中継・選択の GUI・レース通知を1つのプロセスで行う。ホストで動かす
# (make remote が run_remote.bash 経由で起動する)。
set -eo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

# ROS 環境。docker-entrypoint.sh を経由しない起動でも動くように自分で読む。
# setup.bash は未定義変数を触るので set -u は使わない。
# shellcheck disable=SC1091
{
    [ -f /opt/ros/humble/setup.bash ] && source /opt/ros/humble/setup.bash
} >/dev/null 2>&1 || true

if ! python3 -c "import rclpy" 2>/dev/null; then
    echo "Error: rclpy が見つかりません。ROS 2 Humble を入れてください。" >&2
    echo "       sudo apt install ros-humble-ros-base python3-tk" >&2
    exit 1
fi

if [ "$#" -eq 0 ]; then
    echo "Usage: $0 <VEHICLE_ID> [VEHICLE_ID ...]" >&2
    exit 1
fi

cd "${SCRIPT_DIR}/../manager"
exec python3 racing_kart_manager.py "$@"
