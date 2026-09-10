#!/bin/bash
set -eo pipefail

if [ ! -f /opt/ros/humble/setup.bash ]; then
    echo "Error: ROS 2 Humble が見つかりません (/opt/ros/humble)。" >&2
    exit 1
fi

# GUIや端末から直接起動しても ros2 を解決できるようにする。
# shellcheck disable=SC1091
source /opt/ros/humble/setup.bash

exec ros2 run joy joy_node --ros-args -r __ns:=/racing_kart
