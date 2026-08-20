#!/bin/bash
# 本体リポジトリ (aichallenge-racingkart) の docker_build.sh と同じ作法で使う。
#
#   ./docker_build.sh remote   遠隔操作イメージ
#   ./docker_build.sh rviz     遠隔監視イメージ

set -euo pipefail

target="${1-}"
shift || true

if [ -z "${target}" ]; then
    echo "Usage: ./docker_build.sh <remote|rviz>" >&2
    exit 2
fi

while [ $# -gt 0 ]; do
    case "$1" in
    --no-cache)
        NO_CACHE="--no-cache"
        shift
        ;;
    --)
        shift
        break
        ;;
    *)
        echo "invalid argument: '$1'" >&2
        echo "Usage: ./docker_build.sh <remote|rviz>" >&2
        exit 2
        ;;
    esac
done

case "${target}" in
"remote" | "rviz") ;;
*)
    echo "invalid argument (use 'remote' or 'rviz')" >&2
    exit 1
    ;;
esac

opts="${NO_CACHE-}"

# remote -> aichallenge-remote / rviz -> aichallenge-remote-rviz
if [ "${target}" = "remote" ]; then
    IMAGE_SUFFIX=""
else
    IMAGE_SUFFIX="-${target}"
fi

ts="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="output/docker/${ts}-docker_build-$$.log"
mkdir -p output/docker output/latest
ln -sfn "${PWD}/${LOG_FILE}" output/latest/docker_build.log

# shellcheck disable=SC2086
docker build ${opts} --progress=plain --target "${target}" \
    -t "aichallenge-remote${IMAGE_SUFFIX}" . 2>&1 | tee "$LOG_FILE"
echo "========================================================"
echo "This log is in : ${LOG_FILE}"
echo "========================================================"
