#!/bin/bash
# 遠隔操作PCの構成要素を1つだけ起動する。
#
#   remote_component.bash check
#   remote_component.bash zenoh   <LOG_DIR> "A2 A3 A7"
#   remote_component.bash joy     <LOG_DIR>
#   remote_component.bash manager <LOG_DIR> A2 A3 A7 [--brake-test PERCENT]
#
# 起動の前段はここだけが持つ (LN-12)。前段とは .env の読み込み・ROS 環境・
# ROS_DOMAIN_ID・ログ先の4つを指す。joy.bash は ROS の setup.bash を自分では読まない
# ので、前段抜きで起こすと ROS が見つからない。.env を読まなければレース通知が黙って
# 止まる。
#
# make remote (run_remote.bash) もランチャ GUI も同じこれを呼ぶ (LN-02, LN-13)。前段が
# 2箇所にあると、いずれ片方だけが直る。
#
# 出力はすべて <LOG_DIR>/remote/<name>.log に入る。check の失敗も含めて例外を作らない
# (LN-15)。端末に出るか出ないかを呼び出し側ごとに変えると、GUI から起こしたときだけ
# 失敗の理由が消える。
#
# 仕様: docs/spec/launcher.md
set -eo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"

usage() {
    cat <<'USAGE' >&2
usage:
  remote_component.bash check
  remote_component.bash zenoh   <LOG_DIR> "A2 A3 A7"
  remote_component.bash joy     <LOG_DIR>
  remote_component.bash manager <LOG_DIR> <VEHICLE_ID> [VEHICLE_ID ...] [--brake-test PERCENT]
USAGE
}

# ROS 環境。make から setsid で起動されるとログインシェルを通らないので自分で読む。
# setup.bash は未定義変数を触るので set -u は使わない。
load_ros() {
    # shellcheck disable=SC1091
    {
        [ -f /opt/ros/humble/setup.bash ] && source /opt/ros/humble/setup.bash
    } >/dev/null 2>&1 || true
}

# .env を読む。MQTT の接続情報 (レース通知) と TLS_ROOT がここに入る。docker compose は
# 自動で読むが、ホストで動く子には誰も渡さないのでここで読む。認証情報は .env にだけ置く。
load_env() {
    if [ -f "${repo_root}/.env" ]; then
        set -a
        # shellcheck disable=SC1091
        source "${repo_root}/.env"
        set +a
    fi
}

# 前提の確認 (LN-14)。Makefile・ランチャ・この下の起動が同じこれを呼ぶ。確認の内容を
# 3箇所に書かないためにサブコマンドにしてある。
run_check() {
    local failed=0

    if [ ! -f /opt/ros/humble/setup.bash ]; then
        echo 'Error: ROS 2 Humble が見つかりません (/opt/ros/humble)。' >&2
        echo '       sudo apt install ros-humble-ros-base ros-humble-joy python3-tk' >&2
        return 1
    fi

    if ! command -v ros2 >/dev/null 2>&1; then
        echo 'Error: ros2 が見つかりません。ROS 2 Humble を入れてください。' >&2
        failed=1
    fi

    # manager は rclpy と sensor_msgs だけを使う。Autoware は要らない。
    if ! python3 -c "import rclpy" >/dev/null 2>&1; then
        echo 'Error: rclpy が見つかりません。' >&2
        echo '       sudo apt install ros-humble-ros-base' >&2
        failed=1
    fi

    # GUI を開けない環境では manager もランチャも起動しない (joy-routing.md REQ-02)。
    if ! python3 -c "import tkinter" >/dev/null 2>&1; then
        echo 'Error: tkinter が見つかりません。' >&2
        echo '       sudo apt install python3-tk' >&2
        failed=1
    fi

    if [ ! -d /opt/ros/humble/share/joy ]; then
        echo 'Error: joy パッケージが見つかりません。' >&2
        echo '       sudo apt install ros-humble-joy' >&2
        failed=1
    fi

    if ! command -v zenoh-bridge-ros2dds >/dev/null 2>&1; then
        echo 'Error: zenoh-bridge-ros2dds が見つかりません。' >&2
        echo '       sudo dpkg -i vendor/zenoh-bridge-ros2dds_1.5.0_amd64.deb' >&2
        failed=1
    fi

    return "${failed}"
}

if [ "$#" -lt 1 ]; then
    usage
    exit 1
fi

component="$1"
shift

# 確認だけして戻る。ここはログに落とさない。呼び出し側の端末に出す必要がある。
if [ "${component}" = "check" ]; then
    load_ros
    run_check
    exit
fi

case "${component}" in
zenoh | joy | manager) ;;
*)
    echo "Error: 不明な構成要素です: ${component}" >&2
    usage
    exit 1
    ;;
esac

if [ "$#" -lt 1 ] || [ -z "${1}" ]; then
    echo "Error: LOG_DIR を指定してください。" >&2
    usage
    exit 1
fi
log_dir="$1"
shift

out_dir="${log_dir}/remote"
mkdir -p "${out_dir}"

# ここから先の出力はすべてログへ (LN-15)。
exec >>"${out_dir}/${component}.log" 2>&1
echo "[remote_component] $(date '+%Y-%m-%d %H:%M:%S') starting ${component}"

load_env
load_ros

if ! run_check; then
    echo "[remote_component] 前提が足りないので ${component} を起動しません。"
    exit 1
fi

# 遠隔側は常に domain 0。車両側の domain とは無関係で、車両IDで区別する。.env で
# 変えられないように、読み込みの後で固定する。
export ROS_DOMAIN_ID=0

case "${component}" in
zenoh)
    if [ "$#" -lt 1 ] || [ -z "${1}" ]; then
        echo "Error: 対象車両を指定してください。" >&2
        exit 1
    fi
    exec "${script_dir}/run_zenoh.bash" "$1" "${log_dir}"
    ;;
joy)
    exec "${script_dir}/joy.bash"
    ;;
manager)
    if [ "$#" -lt 1 ]; then
        echo "Error: 対象車両を指定してください。" >&2
        exit 1
    fi
    exec "${script_dir}/manager.bash" "$@"
    ;;
esac
