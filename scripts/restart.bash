#!/bin/bash
# RViz を上げ直してから、1台だけ zenoh に繋ぎ直す。
#
#   restart.bash {A1|A2|A3|A5|A6|A7|A8|test-*}
#
# 単車を手で扱うための道具で、ランチャ (remote_launcher.py) からは呼ばれない。
# 複数台をまとめて扱うときは make remote を使う。
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
RVIZ_SCRIPT="${SCRIPT_DIR}/rviz.bash"
CONNECT_SCRIPT="${SCRIPT_DIR}/connect_zenoh.bash"

usage() {
    echo "Usage: $0 {A1|A2|A3|A5|A6|A7|A8|test-*}" >&2
}

if [ ! -x "${CONNECT_SCRIPT}" ]; then
    echo "Error: connect script not found or not executable: ${CONNECT_SCRIPT}" >&2
    exit 1
fi

# RViz を上げる前に引数を見る。先に上げてから usage で落ちると、半端に立ち上がった
# ままになる。
if [ $# -ne 1 ]; then
    usage
    exit 1
fi

TARGET="$1"

# SCRIPT_DIR 基準で呼ぶ。カレントディレクトリ基準だと、リポジトリルートから叩いたときに
# 落ちる。以前は gui_tools.py が scripts/ を作業ディレクトリにして起こしていたので
# 表面化していなかった。
"${RVIZ_SCRIPT}" down

"${RVIZ_SCRIPT}" &

echo "5秒待機しzenohに接続します..."
sleep 5

echo "Stopping existing 'zenoh-bridge-ros2dds' processes..."
pkill -f 'zenoh-bridge-ro' >/dev/null 2>&1 || true

echo "Waiting for processes to terminate..."
while pgrep -f 'zenoh-bridge-ro' >/dev/null 2>&1; do
    sleep 0.5
done

echo "Restarting zenoh bridge for target '${TARGET}'"
exec "${CONNECT_SCRIPT}" "${TARGET}"
