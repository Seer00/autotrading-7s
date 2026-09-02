from __future__ import annotations

import pytest

from autotrading7s.ports.repository import RepositoryPort, RowNotFound


def test_stage_row_id_returns_the_row_id(repo_two_stocks):
    """order_log.stage_state_id 를 채우려면 이 id 가 필요하다.

    없으면 재시작 복구가 미체결 주문을 어느 단계의 것인지 알 수 없고, 설계서
    10.1절 2단계('체결됨 → HOLDING 으로 정정')를 수행할 방법이 사라진다.
    """
    cyc = repo_two_stocks.load_active_cycles()[0]
    ids = {n: repo_two_stocks.stage_row_id(cyc.cycle_id, n) for n in range(1, 8)}
    assert len(set(ids.values())) == 7
    assert all(isinstance(v, int) for v in ids.values())


def test_stage_row_id_raises_for_a_missing_stage(repo_two_stocks):
    cyc = repo_two_stocks.load_active_cycles()[0]
    with pytest.raises(RowNotFound, match="stage_state"):
        repo_two_stocks.stage_row_id(cyc.cycle_id, 99)


def test_stage_row_id_is_scoped_to_the_cycle(repo_two_stocks):
    """다른 사이클의 같은 단계 번호를 반환하면 주문이 엉뚱한 단계에 붙는다."""
    a, b = repo_two_stocks.load_active_cycles()[:2]
    assert (repo_two_stocks.stage_row_id(a.cycle_id, 1)
            != repo_two_stocks.stage_row_id(b.cycle_id, 1))


def test_port_declares_stage_row_id():
    assert "stage_row_id" in RepositoryPort.__protocol_attrs__
