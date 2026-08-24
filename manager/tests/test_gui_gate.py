"""gui_gate のテスト (G-01 〜 G-06)。

仕様: docs/spec/joy-routing.md §9

GUI 側に唯一許したロジック。manager が落ちても GUI には最後の status が
残り続けるが、これは manager 自身からは送れないので GUI が検出する。

塞ぐのは選択の表示だけで、ボタンは押せるままにする (REQ-21)。古い選択を
今のものとして見せるのが最も危険なので、疑わしければどのボタンも赤くしない。
"""

from __future__ import annotations

import pytest

from racing_kart_manager_core import (
    SCHEMA_VERSION,
    STATUS_TIMEOUT_S,
    gui_gate,
)


def test_g01_fresh_and_matching_version_is_usable():
    """G-01: 新鮮でバージョンも一致していれば選択を表示してよい。"""
    gate = gui_gate(status_age_s=0.1, schema_version=SCHEMA_VERSION)

    assert gate.usable is True
    assert gate.reason is None


def test_g02_stale_status_disables_everything():
    """G-02: status が途絶したら選択の表示をやめる。

    manager が落ちている可能性があり、画面の内容が現実と一致しない。
    """
    gate = gui_gate(status_age_s=STATUS_TIMEOUT_S + 0.1, schema_version=SCHEMA_VERSION)

    assert gate.usable is False
    assert gate.reason and "通信" in gate.reason


def test_g03_never_received_disables_everything():
    """G-03: 一度も status を受け取っていない状態（GUI 起動直後）も同じ。

    対象車両すら分からないので、赤くするボタンを決めようがない。
    """
    gate = gui_gate(status_age_s=None, schema_version=None)

    assert gate.usable is False
    assert gate.reason


def test_g04_version_mismatch_disables_everything():
    """G-04: `schema_version` が違えば表示をやめる。

    片方だけ更新して黙って誤動作するのを防ぐ。
    """
    gate = gui_gate(status_age_s=0.1, schema_version=SCHEMA_VERSION + 1)

    assert gate.usable is False
    assert gate.reason and "バージョン" in gate.reason


@pytest.mark.parametrize(
    "age, usable",
    [
        (STATUS_TIMEOUT_S - 0.01, True),
        (STATUS_TIMEOUT_S + 0.01, False),
    ],
)
def test_g05_timeout_boundary(age, usable):
    """G-05: 途絶判定の境界。"""
    assert gui_gate(age, SCHEMA_VERSION).usable is usable


def test_g06_staleness_wins_over_version():
    """G-06: 両方おかしい場合も必ず塞ぐ。判定の順序で漏れない。"""
    gate = gui_gate(status_age_s=99.0, schema_version=999)

    assert gate.usable is False
    assert gate.reason
