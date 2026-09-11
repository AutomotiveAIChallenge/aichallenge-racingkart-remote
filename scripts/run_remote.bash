#!/bin/bash
# 遠隔操作PC一式をホストで起動する。
#
#   run_remote.bash "A2 A3 A7" [LOG_DIR]
#
# 起動するのは zenoh ブリッジ・joy・manager の3つ。manager は joy の中継・選択の GUI・
# レース通知を1つのプロセスで行う。RViz だけは Autoware の RViz プラグインと
# map_loader が要るのでコンテナのまま (make rviz)。
#
# 起動の前段 (.env / ROS 環境 / ROS_DOMAIN_ID / ログ先) は remote_component.bash が持つ。
# ここはそれを3回呼ぶだけで、前段を持たない (LN-13)。ランチャ GUI も同じものを1回ずつ
# 呼ぶので、CLI と GUI で起動のしかたが割れない。
#
# make remote が setsid で起動するので、このスクリプトがセッションリーダーになり、
# 子も孫も同じプロセスグループに入る。make remote-stop は `kill -TERM -<PID>` で
# グループごと畳む。`ros2 run` は joy_node を subprocess で起こすため、親だけを kill
# すると joy_node が孤児として残る。グループで畳めばそれが起きない。
#
# 子が落ちても上げ直さない。黙って復活すると、不安定なまま運用を続けてしまう。
# 何が生きているかは make ps で見る。zenoh ブリッジの再接続だけは run_zenoh.bash が
# 自前で面倒をみる (通信断からの復帰は当然のため)。
#
# 仕様: docs/spec/launcher.md
set -eo pipefail

if [ "$#" -lt 1 ] || [ -z "${1}" ]; then
    echo 'usage: run_remote.bash "A2 A3 A7" [LOG_DIR]' >&2
    exit 1
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
component="${script_dir}/remote_component.bash"

vehicles="${1}"
read -r -a vehicle_list <<<"${vehicles}"

# ログの置き場所は3つで共有する。remote_component.bash も同じ規則で <LOG_DIR>/remote を使う。
log_dir="${2-}"
log_dir="${log_dir:-$(cd "${script_dir}/.." && pwd)/output/$(date +%Y%m%d-%H%M%S)}"

# ブレーキ試験 (実験用)。環境変数ではなく引数で渡すのは、make ps に出て、走行前に
# 何%が仕込まれているか目で確認できるため。
manager_args=("${vehicle_list[@]}")
if [ -n "${BRAKE_TEST-}" ]; then
    manager_args+=(--brake-test "${BRAKE_TEST}")
fi

start() {
    local name="${1}"
    shift
    "${component}" "${name}" "${log_dir}" "$@" &
    echo "[run_remote] ${name}: PID $! -> ${log_dir}/remote/${name}.log"
}

# zenoh を先に上げる。ブリッジが繋がる前に manager が joy を出しても、届く先が
# 無いだけで害はない。
start zenoh "${vehicles}"
start joy
start manager "${manager_args[@]}"

echo "[run_remote] up: ${vehicles}"

# 子が全部消えるまで居座る。ここが生きている限りプロセスグループが残り、
# make remote-stop の `kill -TERM -<PID>` が全員に届く。
wait
echo "[run_remote] all children exited"
