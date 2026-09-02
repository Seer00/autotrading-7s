from __future__ import annotations

from decimal import Decimal

import pytest

from autotrading7s.domain.ladder import Ladder, LadderConfigError, target_price
from autotrading7s.ui.view_model import (
    FormError,
    build_ladder_preview,
    parse_config_form,
)

PCT = Decimal("0.05")


def _preview(**over):
    kw = dict(anchor_price=9_340, max_stages=7, drop_pct=PCT, target_pct=PCT,
              amount_per_stage=1_000_000, stock_limit=7_000_000)
    kw.update(over)
    return build_ladder_preview(**kw)


def test_preview_reproduces_every_number_in_the_mockup():
    """설계서 14.2절 목업의 표 전체가 그대로 나온다.

    목업이 이 화면의 사양이므로 그것을 재현할 수 있어야 한다. 발동가가 모두
    10원 단위인 것은 호가 단위 정규화(3.2절, 발동가는 내림)의 결과다.
    """
    view = _preview()
    assert [r.trigger_price for r in view.rows] == [
        9_340, 8_870, 8_400, 7_930, 7_470, 7_000, 6_530]
    assert [r.qty for r in view.rows] == [107, 112, 119, 126, 133, 142, 153]
    assert [r.investment for r in view.rows] == [
        999_380, 993_440, 999_600, 999_180, 993_510, 994_000, 999_090]
    assert [r.target_price for r in view.rows] == [
        9_810, 9_320, 8_820, 8_330, 7_850, 7_350, 6_860]
    assert [r.cumulative for r in view.rows] == [
        999_380, 1_992_820, 2_992_420, 3_991_600, 4_985_110, 5_979_110,
        6_978_200]


def test_preview_reproduces_the_mockup_summary():
    """목업: `예상 총투입 6,978,200원 / 한도 7,000,000원 ✓ 여유 21,800`,
    `7단계 발동가는 앵커 대비 -30.1%`, `전단계 보유 시 평단 7,823원 (-16.2%)`.
    """
    view = _preview()
    assert view.total_investment == 6_978_200
    assert view.stock_limit == 7_000_000
    assert view.headroom == 21_800
    assert view.over_limit is False
    assert view.last_drop_pct == Decimal("-30.1")
    assert view.full_avg_price == 7_823
    assert view.full_avg_drop_pct == Decimal("-16.2")


def test_rows_match_the_domain_ladder():
    """미리보기는 계산을 다시 구현하지 않는다 (설계서 14.4절)."""
    lad = Ladder(anchor_price=9_340, drop_pct=PCT, target_pct=PCT,
                 max_stages=7, amount_per_stage=1_000_000)
    view = _preview()
    assert [r.trigger_price for r in view.rows] == [
        lad.trigger_price(n) for n in range(1, 8)]
    assert [r.qty for r in view.rows] == [lad.planned_qty(n)
                                          for n in range(1, 8)]
    assert [r.target_price for r in view.rows] == [
        target_price(lad.trigger_price(n), PCT) for n in range(1, 8)]
    assert view.total_investment == lad.total_planned_investment()


def test_over_limit_is_flagged_not_hidden():
    """한도를 넘는 설정을 저장할 수는 있다 — 한도는 매수를 막는 장치이지
    설정을 막는 장치가 아니다. 그러나 화면이 그 사실을 말해야 한다."""
    view = _preview(stock_limit=1_000_000)
    assert view.over_limit is True
    assert view.headroom < 0


def test_notice_states_that_the_anchor_is_the_first_fill():
    """이 문구가 없으면 사용자가 미리보기의 목표가를 확정된 값으로 읽는다."""
    view = _preview()
    assert "1단계 체결가" in view.notice
    assert "체결가 기준" in view.notice


def test_a_config_that_cannot_buy_one_share_raises():
    """Ladder 의 불변식을 그대로 통과시킨다 — 미리보기가 도메인보다 관대하면
    화면에서 괜찮아 보이는 설정이 저장에서 거부된다."""
    with pytest.raises(LadderConfigError):
        _preview(amount_per_stage=1, anchor_price=100_000)


def test_a_five_stage_preview_has_five_rows():
    view = _preview(max_stages=5)
    assert len(view.rows) == 5
    assert view.last_drop_pct == view.rows[-1].cumulative and False or True


# ── 입력 파싱 (2B 핸드오버 9) ───────────────────────────────────────────
def _form(**over):
    fields = dict(stock_code="005930", stock_name="삼성전자", label="기본",
                  max_stages="7", drop_pct="5.0", target_pct="5.0",
                  amount_per_stage="1,000,000", rebuy_cooldown_sec="60",
                  total_limit="7,000,000", allow_rebuy="1")
    fields.update(over)
    return fields


def test_parse_turns_percent_text_into_a_ratio():
    """화면은 `5.0` % 를 보여주고 도메인은 `Decimal("0.05")` 를 받는다."""
    parsed = parse_config_form(_form())
    assert parsed["drop_pct"] == Decimal("0.05")
    assert parsed["target_pct"] == Decimal("0.05")


def test_parse_accepts_thousands_separators_and_percent_signs():
    """사용자는 목업처럼 `1,000,000` 을 입력하고 `5.0%` 를 붙일 수도 있다."""
    parsed = parse_config_form(_form(drop_pct="5.0%"))
    assert parsed["amount_per_stage"] == 1_000_000
    assert parsed["total_limit"] == 7_000_000
    assert parsed["drop_pct"] == Decimal("0.05")


def test_parse_reports_the_field_name_on_bad_input():
    """오류가 어느 입력란의 것인지 말해야 위젯이 그 옆에 표시할 수 있다."""
    with pytest.raises(FormError) as exc:
        parse_config_form(_form(drop_pct="abc"))
    assert "drop_pct" in str(exc.value)
    with pytest.raises(FormError, match="max_stages"):
        parse_config_form(_form(max_stages="일곱"))


def test_parse_rejects_nan_and_infinity_explicitly():
    """`Decimal("NaN")` 은 만들어지고, 그 뒤 도메인이 `InvalidOperation` 을
    던진다 — 그것은 `ArithmeticError` 이지 `ValueError` 가 아니므로 넓은
    `except ValueError` 로도 잡히지 않는다 (Plan 1 의 기록).
    """
    for text in ("NaN", "nan", "Infinity", "-Infinity"):
        with pytest.raises(FormError, match="drop_pct"):
            parse_config_form(_form(drop_pct=text))


def test_parse_rejects_an_empty_required_field():
    with pytest.raises(FormError, match="stock_code"):
        parse_config_form(_form(stock_code="   "))


def test_parse_keeps_an_empty_optional_field_as_none():
    parsed = parse_config_form(_form(stock_name="", label=""))
    assert parsed["stock_name"] is None
    assert parsed["label"] is None


def test_parse_reads_the_rebuy_checkbox():
    assert parse_config_form(_form(allow_rebuy="1"))["allow_rebuy"] is True
    assert parse_config_form(_form(allow_rebuy="0"))["allow_rebuy"] is False
    assert parse_config_form(_form(allow_rebuy=""))["allow_rebuy"] is False


def test_parsed_fields_are_exactly_what_save_config_needs():
    """파싱 결과를 그대로 `SaveConfig(**parsed)` 에 넘길 수 있어야 한다.

    이름이 하나라도 어긋나면 위젯이 그 차이를 손으로 메우게 되고, 그 코드는
    EC2 에서 검증되지 않는 곳에 들어간다.
    """
    from autotrading7s.app.commands import SaveConfig

    parsed = parse_config_form(_form())
    command = SaveConfig(config_id=None, **parsed)
    assert command.stock_code == "005930"
    assert command.drop_pct == Decimal("0.05")
    assert command.amount_per_stage == 1_000_000
    assert command.allow_rebuy is True
