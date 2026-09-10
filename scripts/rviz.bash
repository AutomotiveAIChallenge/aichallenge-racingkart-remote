#!/bin/bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
MAKE_DIR="${REPO_ROOT}"
# COMPOSE_FILE is defined in .env (read automatically by docker compose)

usage() {
    cat <<'USAGE'
Usage:
  rviz.bash [VEHICLE]          # start RViz stack via make rviz
  rviz.bash down               # stop and remove rviz2 service
  rviz.bash restart [VEHICLE]  # restart rviz2 service
USAGE
}

if [ ! -d "${MAKE_DIR}" ]; then
    echo "Error: repository root directory not found at '${MAKE_DIR}'." >&2
    exit 1
fi

if [ ! -f "${MAKE_DIR}/Makefile" ]; then
    echo "Error: Makefile not found in '${MAKE_DIR}'." >&2
    exit 1
fi

if [ ! -f "${REPO_ROOT}/docker-compose.yml" ]; then
    echo "Error: docker-compose.yml not found at '${REPO_ROOT}/docker-compose.yml'." >&2
    exit 1
fi

mode="start"
if [ $# -gt 0 ]; then
    case "$1" in
    down)
        mode="down"
        shift
        ;;
    restart)
        mode="restart"
        shift
        ;;
    -h | --help)
        usage
        exit 0
        ;;
    esac
fi

if [ $# -gt 1 ]; then
    echo "Error: too many arguments." >&2
    usage
    exit 1
fi
vehicle_id="${1-}"

if [ -n "${vehicle_id}" ]; then
    # shellcheck source-path=SCRIPTDIR source=../shared/vehicle_ports.sh
    source "${REPO_ROOT}/shared/vehicle_ports.sh"
    if ! zenoh_port_for_vehicle_id "${vehicle_id}" >/dev/null; then
        echo "Error: invalid VEHICLE '${vehicle_id}' (valid: ${VEHICLE_ID_VALID_LIST})." >&2
        exit 1
    fi
fi

case "${mode}" in
start)
    echo "Running 'make rviz VEHICLE=${vehicle_id}' inside '${MAKE_DIR}'."
    cd "${MAKE_DIR}"
    make rviz VEHICLE="${vehicle_id}"
    ;;
down)
    echo "Stopping and removing 'rviz2' service."
    cd "${MAKE_DIR}"
    docker compose rm -f -s rviz2
    ;;
restart)
    echo "Restarting 'rviz2' service."
    cd "${MAKE_DIR}"
    docker compose rm -f -s rviz2
    echo "Running 'make rviz VEHICLE=${vehicle_id}' inside '${MAKE_DIR}' after restart."
    cd "${MAKE_DIR}"
    make rviz VEHICLE="${vehicle_id}"
    ;;
*)
    echo "Error: unsupported mode '${mode}'." >&2
    exit 1
    ;;
esac
