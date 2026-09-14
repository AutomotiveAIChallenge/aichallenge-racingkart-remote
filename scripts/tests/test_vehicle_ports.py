"""車両IDと大会サーバーのZenohポート対応を検証する。"""

from __future__ import annotations

import pathlib
import subprocess


VEHICLE_PORTS = pathlib.Path(__file__).resolve().parents[2] / "shared" / "vehicle_ports.sh"


def test_a4_maps_to_zenoh_port_7455():
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; zenoh_port_for_vehicle_id A4',
            "bash",
            str(VEHICLE_PORTS),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "7455"
