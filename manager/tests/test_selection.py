"""選択のテスト (T-01 〜 T-06)。

仕様: docs/spec/joy-routing.md §3

選択を変えるのは GUI のボタンだけで、そのボタンは対象車両から作る (REQ-11)。
だから選択が対象車両の外を指す経路が無く、検証も置いていない。選択は文字列そのもので、
それを宛先の集合に変えるのが selected_vehicles() である。
"""

from __future__ import annotations

from conftest import JOY_FULL, VEHICLES
from racing_kart_manager_core import (
    AXIS_ACCEL,
    INITIAL_SELECTION,
    SELECTION_ALL,
    SELECTION_NONE,
    parse_vehicles,
    selected_vehicles,
    transform,
)


def test_t01_starts_unselected():
    """T-01: 起動直後は未選択 (REQ-10)。

    起動の瞬間にスティックが効くと、置いたままのコントローラで車が動く。
    """
    assert INITIAL_SELECTION == SELECTION_NONE
    assert selected_vehicles(INITIAL_SELECTION, VEHICLES) == frozenset()


def test_t02_single_selection_selects_only_that_vehicle():
    """T-02: 単車を選ぶとその1台だけが選択車になる (REQ-09)。"""
    assert selected_vehicles("A3", VEHICLES) == frozenset({"A3"})


def test_t03_all_selection_selects_every_target_vehicle():
    """T-03: 全台を選ぶと対象車両の全部が選択車になる (REQ-08, REQ-09)。

    「全台」は運用に存在する車両の全部ではなく、起動引数で渡した対象車両の全部。
    """
    assert selected_vehicles(SELECTION_ALL, VEHICLES) == frozenset(VEHICLES)
    assert selected_vehicles(SELECTION_ALL, ("A2",)) == frozenset({"A2"})


def test_t04_can_go_back_to_unselected():
    """T-04: 未選択に戻せる (REQ-09)。"""
    assert selected_vehicles(SELECTION_NONE, VEHICLES) == frozenset()


def test_t05_switches_even_while_the_throttle_is_held():
    """T-05: アクセルを踏んだ状態でも選択は切り替わる (REQ-12)。

    切り替え先はその踏み込み量で動き出す。これは仕様として受け入れている
    (§9「保証しないこと」)。ここで検証するのは、踏んでいることを理由に
    切り替えを止める仕組みが無いこと。
    """
    assert JOY_FULL.axes[AXIS_ACCEL] < 0.9  # 実際に踏んでいる

    before = transform(JOY_FULL, SELECTION_NONE, VEHICLES)
    after = transform(JOY_FULL, "A3", VEHICLES)

    assert before["A3"].axes != JOY_FULL.axes
    assert after["A3"].axes == JOY_FULL.axes


def test_t06a_accepts_a_valid_list_in_order():
    """T-06: 指定した順を保つ。GUI のボタン並びに効く。"""
    assert parse_vehicles(["A7", "A2", "A3"]) == ("A7", "A2", "A3")


def test_t06b_rejects_an_empty_list():
    """T-06: 1台も指定しないのは拒否する (REQ-07)。

    台数0で起動すると、何も送れないノードが黙って立ち上がる。
    """
    assert parse_vehicles([]) is None


def test_t06c_rejects_unknown_vehicle_ids():
    """T-06: 知らない車両IDは拒否する (REQ-07)。"""
    assert parse_vehicles(["A2", "A9"]) is None
    assert parse_vehicles(["a2"]) is None


def test_t06d_rejects_duplicates():
    """T-06: 重複は拒否する (REQ-07)。

    同じIDが2つあると同じ車両へ2回 publish することになり、宛先の数が状態と食い違う。
    """
    assert parse_vehicles(["A2", "A3", "A2"]) is None
