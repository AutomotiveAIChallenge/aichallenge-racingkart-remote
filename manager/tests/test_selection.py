"""選択のテスト (T-01 〜 T-08)。

仕様: docs/spec/joy-routing.md §3

選択は joy の中身も直前の選択も見ずに切り替わる。前提条件を課さないのが要点で、
「切り替えられない状態」を作らないための設計である。
"""

from __future__ import annotations

import pytest

from conftest import JOY_FULL, VEHICLES
from racing_kart_manager_core import (
    AXIS_ACCEL,
    INITIAL_SELECTION,
    KNOWN_VEHICLE_IDS,
    SELECTION_ALL,
    SELECTION_NONE,
    parse_command,
    parse_vehicles,
    select,
    selected_vehicles,
    transform,
)


# ==========================================================================
# 選択の状態
# ==========================================================================


def test_t01_starts_unselected():
    """T-01: 起動直後は未選択 (REQ-05)。

    起動の瞬間にスティックが効くと、置いたままのコントローラで車が動く。
    """
    assert INITIAL_SELECTION == SELECTION_NONE
    assert selected_vehicles(INITIAL_SELECTION, VEHICLES) == frozenset()


def test_t02_single_selection_selects_only_that_vehicle():
    """T-02: 単車を選ぶとその1台だけが選択車になる (REQ-04)。"""
    selection = select(SELECTION_NONE, "A3", VEHICLES)

    assert selection == "A3"
    assert selected_vehicles(selection, VEHICLES) == frozenset({"A3"})


def test_t03_all_selection_selects_every_target_vehicle():
    """T-03: 全台を選ぶと対象車両の全部が選択車になる (REQ-04, REQ-03)。

    「全台」は運用に存在する車両の全部ではなく、起動引数で渡した対象車両の全部。
    """
    selection = select(SELECTION_NONE, SELECTION_ALL, VEHICLES)

    assert selected_vehicles(selection, VEHICLES) == frozenset(VEHICLES)
    assert selected_vehicles(selection, ("A2",)) == frozenset({"A2"})


def test_t04_can_go_back_to_unselected():
    """T-04: 未選択に戻せる (REQ-04)。"""
    selection = select("A3", SELECTION_NONE, VEHICLES)

    assert selection == SELECTION_NONE
    assert selected_vehicles(selection, VEHICLES) == frozenset()


@pytest.mark.parametrize("target", ["A1", "a3", "", "ALL", "全台"])
def test_t05_unknown_target_keeps_the_selection(target):
    """T-05: 対象車両に無いIDを指定しても選択は変わらない (REQ-07)。

    A1 は既知の車両IDだが、この起動の対象車両ではない。zenoh ブリッジが
    立っていない車両を選べてしまうと、送ったつもりの joy がどこにも届かない。
    """
    assert "A1" in KNOWN_VEHICLE_IDS
    assert select("A3", target, VEHICLES) == "A3"


# ==========================================================================
# コマンドの解釈
# ==========================================================================


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "not json",
        "[]",
        "null",
        '{"schema_version": 2}',
        '{"schema_version": 2, "command": "select"}',
        '{"schema_version": 2, "command": "select", "target": 3}',
        '{"schema_version": 2, "command": "select", "target": ""}',
        '{"schema_version": 2, "command": "enter_all_mode"}',
    ],
)
def test_t06_undecodable_command_is_ignored_without_raising(payload):
    """T-06: 解釈できないコマンドは無視する。例外を投げてはならない (REQ-08)。

    manager が落ちると joy が止まり、5秒後に全車が緊急停止する。
    落ちないことが安全性に直結する。
    """
    assert parse_command(payload) is None


# ==========================================================================
# 切り替えに前提条件を課さない
# ==========================================================================


def test_t07_switches_even_while_the_throttle_is_held():
    """T-07: アクセルを踏んだ状態でも選択は切り替わる (REQ-06)。

    切り替え先はその踏み込み量で動き出す。これは仕様として受け入れている
    (§10「保証しないこと」)。ここで検証するのは、踏んでいることを理由に
    切り替えを止めないこと。
    """
    assert JOY_FULL.axes[AXIS_ACCEL] < 0.9  # 実際に踏んでいる

    selection = select(SELECTION_NONE, "A3", VEHICLES)
    assert selection == "A3"

    outgoing = transform(JOY_FULL, selection, VEHICLES)
    assert outgoing["A3"].axes == JOY_FULL.axes


# ==========================================================================
# 起動引数
# ==========================================================================


def test_t08a_accepts_a_valid_list_in_order():
    """T-08: 指定した順を保つ。GUI のボタン並びに効く。"""
    assert parse_vehicles(["A7", "A2", "A3"]) == ("A7", "A2", "A3")


def test_t08b_rejects_an_empty_list():
    """T-08: 1台も指定しないのは拒否する (REQ-02)。

    台数0で起動すると、何も送れないノードが黙って立ち上がる。
    """
    assert parse_vehicles([]) is None


def test_t08c_rejects_unknown_vehicle_ids():
    """T-08: 知らない車両IDは拒否する (REQ-02)。"""
    assert parse_vehicles(["A2", "A9"]) is None
    assert parse_vehicles(["a2"]) is None


def test_t08d_rejects_duplicates():
    """T-08: 重複は拒否する (REQ-02)。

    同じIDが2つあると同じ車両へ2回 publish することになり、宛先の数が状態と食い違う。
    """
    assert parse_vehicles(["A2", "A3", "A2"]) is None
