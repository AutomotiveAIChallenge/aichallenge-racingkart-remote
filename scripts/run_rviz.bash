#!/bin/bash
# 遠隔監視 RViz の起動。コンテナ内で動かす想定。
#
#   run_rviz.bash        地図だけ表示
#   run_rviz.bash A3     A3 の車両トピックを中継して表示
#
# 本体リポジトリの aichallenge/utils/run_rviz.bash から遠隔 (remote) モードだけを
# 取り出したもの。launch と rviz 設定はこのリポジトリの /rviz 配下を使う。
set -eo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
RVIZ_DIR="${SCRIPT_DIR}/../rviz"

vehicle_id="${1-}"

# shellcheck disable=SC1091
{
    [ -f /opt/ros/humble/setup.bash ] && source /opt/ros/humble/setup.bash
    [ -f /autoware/install/setup.bash ] && source /autoware/install/setup.bash
    [ -f /ws/install/setup.bash ] && source /ws/install/setup.bash
} >/dev/null 2>&1 || true

# 車両から届くのは /<VEHICLE_ID>/... の prefix 付きだが、rviz 設定が購読するのは prefix
# なしの名前なので中継して噛み合わせる。
#
# 中継先を列挙しているのは意図的。/<VEHICLE_ID>/* を丸ごと中継すると、車両から返ってくる
# /<VEHICLE_ID>/racing_kart/sd/joy がローカルの /racing_kart/joy に流れ込み、manager が
# 自分の出したジョイスティック入力のエコーを掴む。
#
# /tf_static は入れない。base_link 配下は remote.launch.xml の robot_state_publisher が、
# map->viewer は同 launch の map_tf_generator がローカルで出している。加えて static TF は
# transient_local なので topic_tools relay では中継できない。
RELAY_TOPICS=(
    /tf
    /localization/kinematic_state
    /planning/scenario_planning/trajectory
    /vehicle/status/velocity_status
    /sensing/gnss/pose_with_covariance
    /v2x/vehicle_positions/markers
)

start_relays() {
    local id="${1}"
    local topic

    # 車両IDの一覧は shared/vehicle_ports.sh が唯一の出どころ。ここで複製しない。
    # shellcheck source-path=SCRIPTDIR source=../shared/vehicle_ports.sh
    source "${SCRIPT_DIR}/../shared/vehicle_ports.sh"

    # 綴り違いは黙って「何も映らない」になるだけなので、起動前に弾く。
    if ! zenoh_port_for_vehicle_id "${id}" >/dev/null; then
        echo "invalid VEHICLE: ${id} (valid: ${VEHICLE_ID_VALID_LIST})" >&2
        exit 1
    fi

    for topic in "${RELAY_TOPICS[@]}"; do
        echo "[run_rviz] relay /${id}${topic} -> ${topic}"
        ros2 run topic_tools relay "/${id}${topic}" "${topic}" &
    done
}

ros2 launch "${RVIZ_DIR}/launch/remote.launch.xml" "remote_dir:=${RVIZ_DIR}" &

if [ -n "${vehicle_id}" ]; then
    start_relays "${vehicle_id}"
else
    echo "[run_rviz] VEHICLE 未指定のため地図のみ表示します。車両を映すには make rviz VEHICLE=A3" >&2
fi

exec rviz2 -d "${RVIZ_DIR}/config/remote.rviz" -s "${RVIZ_DIR}/config/fast.png"
