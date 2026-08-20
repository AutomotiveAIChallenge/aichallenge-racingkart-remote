#!/bin/bash
# 遠隔操作PC一式をホストで起動する。
#
#   run_remote.bash "A2 A3 A7" [LOG_DIR]
#
# 起動するのは zenoh ブリッジ・joy・manager の3つ。manager は joy の中継・選択の GUI・
# レース通知を1つのプロセスで行う。RViz だけは Autoware の RViz プラグインと
# map_loader が要るのでコンテナのまま (make rviz)。
#
# make remote が setsid で起動するので、このスクリプトがセッションリーダーになり、
# 子も孫も同じプロセスグループに入る。make remote-stop は `kill -TERM -<PID>` で
# グループごと畳む。`ros2 run` は joy_node を subprocess で起こすため、親だけを kill
# すると joy_node が孤児として残る。グループで畳めばそれが起きない。
#
# 子が落ちても上げ直さない。黙って復活すると、不安定なまま運用を続けてしまう。
# 何が生きているかは make ps で見る。zenoh ブリッジの再接続だけは run_zenoh.bash が
# 自前で面倒をみる (通信断からの復帰は当然のため)。
set -eo pipefail

if [ "$#" -lt 1 ] || [ -z "${1}" ]; then
    echo 'usage: run_remote.bash "A2 A3 A7" [LOG_DIR]' >&2
    exit 1
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

vehicles="${1}"
read -r -a vehicle_list <<<"${vehicles}"

# ログの置き場所は zenoh と共有する。run_zenoh.bash も同じ規則で <LOG_DIR>/remote を使う。
log_dir="${2-}"
log_dir="${log_dir:-$(cd "${script_dir}/.." && pwd)/output/$(date +%Y%m%d-%H%M%S)}"
out_dir="${log_dir}/remote"

# .env を読む。MQTT の接続情報 (レース通知) と TLS_ROOT がここに入る。docker compose は
# 自動で読むが、ホストで動く子には誰も渡さないのでここで読む。認証情報は .env にだけ置く。
if [ -f "${script_dir}/../.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "${script_dir}/../.env"
    set +a
fi

# ROS 環境。make から setsid で起動されるとログインシェルを通らないので自分で読む。
# setup.bash は未定義変数を触るので set -u は使わない。
# shellcheck disable=SC1091
{
    [ -f /opt/ros/humble/setup.bash ] && source /opt/ros/humble/setup.bash
} >/dev/null 2>&1 || true

if ! command -v ros2 >/dev/null 2>&1; then
    echo "Error: ros2 が見つかりません。ROS 2 Humble を入れてください。" >&2
    exit 1
fi

# 遠隔側は常に domain 0。車両側の domain とは無関係で、車両IDで区別する。
# 子はここから継承する。
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"

mkdir -p "${out_dir}"

start() {
    local name="${1}"
    shift
    "$@" >>"${out_dir}/${name}.log" 2>&1 &
    echo "[run_remote] ${name}: PID $! -> ${out_dir}/${name}.log"
}

# zenoh を先に上げる。ブリッジが繋がる前に manager が joy を出しても、届く先が
# 無いだけで害はない。
start zenoh "${script_dir}/run_zenoh.bash" "${vehicles}" "${log_dir}"
start joy "${script_dir}/joy.bash"
start manager "${script_dir}/manager.bash" "${vehicle_list[@]}"

echo "[run_remote] up on ROS_DOMAIN_ID ${ROS_DOMAIN_ID}: ${vehicles}"

# 子が全部消えるまで居座る。ここが生きている限りプロセスグループが残り、
# make remote-stop の `kill -TERM -<PID>` が全員に届く。
wait
echo "[run_remote] all children exited"
