"""
Phase 3a: 결정론적 스코어링 계산 (4/30 rewrite)

ld-deal-plugin 점수 산출 — 7기준 만점 170 + 액티브 게이트 + 결손 분기 + LLM grounding.

설계 원칙 (4/30 빌더 합의 — 7원칙):
- ① 검증 게이트: validate_schema·assert_in_range·log_gate
- ② JSON 흐름: safe_load/save (_helpers)
- ③ 코드 vs 프롬프트: 결정론(코드) + LLM은 6번 SKILL.md에서 호출 후 결과만 박힘
- ④ 결손 처리: 6 시나리오 분기 (`결손_매트릭스.md` 참조)
- ⑤ 재현성: get_today() 환경변수 mock 가능
- ⑥ 부분 실패: 1개 딜 실패 시 0점 폴백 + 로그
- ⑦ LD 친화 메시지: format_user_message (`_helpers`)

핵심 정보원 (4/30 (target_ld) 실데이터 검증):
- 1번 딜 금액: `예상 체결액` (deal 컬럼) + `Net(%)` 보조 (`금액` 폐기)
- 3번 마감 임박도: 3단계 트리 (제안서 마감일 → 수주 예정일 → LLM 추출). deadline은 merge_deals/phase에서 결정 후 deal["deadline"]에 박힘
- 6번 거래 의지: LLM 카테고리 신호 (C1·C2·C3) + `성사 가능성` grounding 비교
- 액티브 게이트: 단계 + non-LOST (merge_deals에서 처리)

자연어 피드백 layer: ⏸ v2 영역 (4/30 MVP 제외)

사용법:
  python scripts/calculate_score.py deals.json [--settings config/settings.json]
  cat deals.json | python scripts/calculate_score.py - --settings config/settings.json
"""

from __future__ import annotations

import argparse
import io
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

# 프로젝트 루트를 sys.path에 추가 (scripts/ 안에서 다른 모듈 import)
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts._validation import (
    ValidationError,
    assert_in_range,
    log_gate,
    reset_validation_log,
    summarize_partial_failures,
    validate_schema,
)
from scripts._helpers import (
    get_today,
    safe_load_json,
    safe_save_json,
    runtime_path,
)

# Windows cp949 인코딩 방지
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")


# ════════════════════════════════════════════
# 기본값 (settings.json 미제공 시 사용 — 4/30 갱신)
# ════════════════════════════════════════════

DEFAULT_SETTINGS: dict[str, Any] = {
    "scoring": {
        "max_score": 170,
        "t1_threshold": 87,
        "t2_threshold": 55,
        "active_gate_stage_min": "Proposal 준비",
        "active_gate_exclude_lost": True,
        "criteria_weights": {
            "1_deal_amount":    30,
            "2_pipeline_stage": 18,
            "3_deadline":       22,
            "4_customer_value": 23,
            "5_communication":  27,
            "6_deal_intent":    30,
            "7_deal_origin":    20,
        },
        "strategic_keywords": [],
        "new_deal_days": 7,
    },
    "data_sources": {
        "salesmap_extract_fields": {
            "amount_field": "예상 체결액",
            "amount_secondary": "Net(%)",
            "deadline_priority": ["제안서 마감일", "수주 예정일"],
            "intent_grounding_field": "성사 가능성",
            "intent_grounding_enum": ["확정", "높음", "낮음", "LOST"],
        },
    },
}


def load_settings(path: str | None) -> dict:
    """settings.json 로드 — 미존재 시 DEFAULT_SETTINGS."""
    if not path or not os.path.exists(path):
        return DEFAULT_SETTINGS
    try:
        loaded = safe_load_json(path)
        if not isinstance(loaded, dict):
            return DEFAULT_SETTINGS
        # 핵심 키만 병합 (사용자 settings에 없으면 default)
        merged = {**DEFAULT_SETTINGS}
        if "scoring" in loaded:
            merged["scoring"] = {**DEFAULT_SETTINGS["scoring"], **loaded["scoring"]}
        if "data_sources" in loaded:
            merged["data_sources"] = {**DEFAULT_SETTINGS["data_sources"], **loaded["data_sources"]}
        return merged
    except Exception as e:
        print(f"settings 로드 실패, 기본값 사용: {e}", file=sys.stderr)
        return DEFAULT_SETTINGS


# ════════════════════════════════════════════
# 날짜 헬퍼
# ════════════════════════════════════════════


def days_between(date_str: str | None, today: date) -> int | None:
    """ISO 날짜 문자열 → today와의 차이(일). 음수=과거, 양수=미래. 미입력 시 None."""
    if not date_str:
        return None
    try:
        target = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
        return (target - today).days
    except (ValueError, TypeError):
        return None


# ════════════════════════════════════════════
# 1번 딜 금액 (만점 30) — `예상 체결액` 1차, `Net(%)` 보조
# ════════════════════════════════════════════


def score_deal_amount(deal: dict) -> int:
    """
    1차: `deal_amount` (예상 체결액 환산, 억 단위)
    보조: `net_pct` (있을 때 +2 가산)
    """
    amount = deal.get("deal_amount")
    if amount is None or amount <= 0:
        return 0

    if amount >= 3.0:
        base = 30
    elif amount >= 1.0:
        base = 23
    elif amount >= 0.5:
        base = 17
    elif amount >= 0.3:
        base = 12
    else:
        base = 6

    # Net(%) 보조 가산 (마진율 30%+ 일 때만)
    net_pct = deal.get("net_pct")
    if isinstance(net_pct, (int, float)) and net_pct >= 30:
        base = min(30, base + 2)

    return base


# ════════════════════════════════════════════
# 2번 파이프라인 단계 (만점 18) — 4/29 노을님 결정 정합
# ════════════════════════════════════════════

PIPELINE_STAGE_SCORES = {
    "Proposal 준비":        5,
    "Proposal 송부":       10,
    "2차 f-up":            13,
    "보완 Proposal":       14,
    "최종 f-up":           16,
    "매출 집계 예정":      18,
    # 비액티브 (액티브 게이트에서 걸러지지만 안전망)
    "1차 f-up":             3,
    "SQL":                  2,
}


def score_pipeline_stage(deal: dict) -> int:
    stage = deal.get("pipeline_stage_name", "") or ""
    # 부분 매칭 (단계명에 "(교육 제안)" 등 suffix 있을 수 있음)
    for key, score in PIPELINE_STAGE_SCORES.items():
        if key in stage:
            return score
    return 0


# ════════════════════════════════════════════
# 3번 마감 임박도 (만점 22) — 3단계 트리 *결과* 받음
# ════════════════════════════════════════════


def score_deadline(deal: dict, today: date) -> int:
    """
    deadline은 merge_deals/phase에서 3단계 트리로 *이미 결정됨*:
    1차 제안서 마감일 → 2차 수주 예정일 → 3차 슬랙·메일 LLM 추출
    여기선 결정된 deadline 받아 점수만 산출. 결손 시 0점 (매트릭스 ③).
    """
    deadline = deal.get("deadline")

    if deadline is None:
        log_gate("phase3", "warn", {
            "kind": "deadline_missing",
            "deal_id": deal.get("id"),
            "tried": ["제안서 마감일", "수주 예정일", "llm_extract"],
        })
        return 0

    d = days_between(deadline, today)
    if d is None:
        return 0

    # 4/30 결정: 이미 지난 deadline도 0점 (페널티 X — "없는 데이터 만들기 X" 정신)
    if d < 0:
        return 0
    if d <= 7:
        return 22
    if d <= 14:
        return 18
    if d <= 30:
        return 12
    if d <= 60:
        return 7
    return 3


# ════════════════════════════════════════════
# 4번 고객 가치 (만점 23) — 4차원
#  거래 15 + 규모·지명도 2 + 레퍼런스 3 + 확장 3
# ════════════════════════════════════════════


def score_customer_value(deal: dict, settings: dict) -> int:
    score = 0

    # ① 거래 (15) — 기고객 + 재거래
    past_deals = deal.get("past_deal_count") or 0
    if past_deals >= 20:
        score += 15
    elif past_deals >= 10:
        score += 12
    elif past_deals >= 5:
        score += 8
    elif past_deals >= 1:
        score += 5

    # ② 규모·지명도 (2) — 핵심기업 키워드 (4/29 약화)
    # 결손 매트릭스 ⑤: 키워드 0건 → 0점 (자연)
    customer_name = deal.get("customer_name", "") or ""
    keywords = settings["scoring"].get("strategic_keywords", []) or []
    if any(kw in customer_name for kw in keywords):
        score += 2

    # ③ 레퍼런스 (3) — RFP·소개/추천 영역 (4/29 4차원)
    if deal.get("reference_signal"):
        score += 3

    # ④ 확장 (3) — 재의뢰·확장 시그널 (4/29 W4 역산)
    if deal.get("extension_signal"):
        score += 3

    return min(score, 23)


# ════════════════════════════════════════════
# 5번 소통 활성도 (만점 27 / -27 페널티 가능) — 두 축 결합
#  슬랙 thread + 견적서 갱신 (4/30 결손 매트릭스 ① 적용)
# ════════════════════════════════════════════

QUOTE_SILENT_THRESHOLD_DAYS = 30


def _slack_score(slack_count: int) -> int:
    """슬랙 thread 활동량 점수 (max 15)."""
    if slack_count >= 30:
        return 15
    if slack_count >= 15:
        return 12
    if slack_count >= 5:
        return 8
    if slack_count >= 1:
        return 4
    return 0


def _quote_score(quote_recent_days: int | None) -> int:
    """견적서 갱신 활성도 점수 (max 12)."""
    if quote_recent_days is None or quote_recent_days > QUOTE_SILENT_THRESHOLD_DAYS:
        return 0
    if quote_recent_days <= 7:
        return 12
    if quote_recent_days <= 14:
        return 9
    if quote_recent_days <= 30:
        return 6
    return 3


def score_communication(deal: dict) -> int:
    """5번 소통 활성도 — 4 축 종합 (5/6 보강).

    축 4종:
      메인: slack_thread_count_14d / last_quote_sheet_updated_days
      보조: customer_responded(메일 양방향) / memo_count(메모 풍부도)

    룰:
      - 메인 두 축 모두 침묵 → 보조 축 활동으로 *보조 점수* 부여 (max 13, 메인 활발 대비 절반)
      - 메인 한 축 침묵 → 기존 -5 페널티 + 메일 활동 시 +5 상쇄 (메일이 한 축 영역 흡수)
      - 메인 두 축 모두 활발 → 기존 룰 그대로
    """
    slack_count = deal.get("slack_thread_count_14d") or 0
    quote_recent = deal.get("last_quote_sheet_updated_days")
    email_active = bool(deal.get("customer_responded"))
    memo_count = deal.get("memo_count", 0) or 0

    slack_silent = slack_count == 0
    quote_silent = quote_recent is None or quote_recent > QUOTE_SILENT_THRESHOLD_DAYS

    # 메인 두 축 모두 침묵 — 보조 축(메일·메모)으로 보조 점수 (max 13)
    if slack_silent and quote_silent:
        aux = 0
        if email_active:
            aux += 8  # 고객 응답 있음 (메일 양방향)
        if memo_count >= 3:
            aux += 5  # 메모 풍부
        elif memo_count >= 1:
            aux += 2  # 메모 있음
        return min(13, aux)

    s_score = _slack_score(slack_count)
    q_score = _quote_score(quote_recent)
    total = s_score + q_score

    # 메인 한 축만 침묵 — 약한 페널티 -5, 단 메일 양방향 활발 시 +5 상쇄
    if slack_silent or quote_silent:
        base = max(0, total - 5)
        if email_active:
            base += 5  # 메일이 한 축 영역 흡수
        return min(27, base)

    return min(27, total)


# ════════════════════════════════════════════
# 6번 거래 의지 (만점 30) — LLM 카테고리 신호 합산
#  C1 결정 시그널 +12 / C2 권한·예산 +10 / C3 활동·관심 +8 (4/29 §2-11)
#  + `성사 가능성` grounding 비교 (4/30 신규)
# ════════════════════════════════════════════

CATEGORY_SCORES = {"C1": 12, "C2": 10, "C3": 8}


def score_deal_intent(deal: dict) -> int:
    """
    LLM이 6번 SKILL.md에서 판정 후 deal에 박힘:
      deal["intent_signals"]: ["C1", "C2", ...] — 발견된 카테고리 리스트
      deal["intent_category"]: "high"|"mid"|"low" — 종합 판정 (grounding 비교용)

    결손 매트릭스 ④ — raw 부족 mid 폴백은 6번 SKILL에서 처리, 여기엔 결과만 옴.
    """
    signals = deal.get("intent_signals") or []
    if not isinstance(signals, list):
        log_gate("phase3.5", "warn", {
            "kind": "llm_enum_invalid",
            "deal_id": deal.get("id"),
            "got": signals,
            "fallback": [],
        })
        signals = []

    score = 0
    for cat in signals:
        score += CATEGORY_SCORES.get(cat, 0)

    return min(30, score)


def check_grounding_match(intent_category: str | None, ld_grounding: str | None) -> bool | None:
    """
    6번 LLM 결과(high/mid/low) vs LD 입력 `성사 가능성`(확정·높음·낮음·LOST) 비교.
    LOST는 액티브 게이트에서 차단되므로 여기엔 안 옴.

    enum 매핑 (5/6 옵션 C — 비대칭 1단계 흡수):
      high ↔ 확정·높음 (한 단계 차이까지 흡수)
      mid  ↔ 확정·높음·낮음 (애매한 영역 → 모든 LD enum 흡수)
      low  ↔ 낮음 (한 단계 차이까지 흡수)
    검토 권장(mismatch) 케이스:
      - LD 확정·높음 vs LLM low → 두 단계 차이, LLM이 너무 보수적
      - LD 낮음 vs LLM high → 두 단계 차이, LLM이 너무 적극
    LD 진실 우선 — *한 단계 차이는 노이즈로 흡수, 두 단계 차이만 시그널*.
    """
    if not intent_category or not ld_grounding:
        return None
    # ld_grounding은 JSON array 문자열일 수 있음 — strip
    if isinstance(ld_grounding, str):
        cleaned = ld_grounding.strip().strip("[]").strip('"').strip("'")
    else:
        cleaned = str(ld_grounding)

    mapping = {
        "high": ["확정", "높음"],
        "mid":  ["확정", "높음", "낮음"],   # 5/6 변경 — 양쪽 영역 흡수
        "low":  ["낮음"],
    }
    allowed = mapping.get(intent_category, [])
    if not allowed:
        return None
    return cleaned in allowed


# ════════════════════════════════════════════
# 7번 딜 출발점 (만점 20) — single 결정론
#  win_probability boost는 6번 grounding 영역으로 분리 (4/30)
# ════════════════════════════════════════════

ORIGIN_SCORES = {
    "RFP":              20,
    "소개/추천":        20,
    "기고객 재의뢰":    20,
    "인바운드":         10,
    "아웃바운드":        5,
    "다수 업체 비교":    5,
    "가격 비교만":       0,
}


def score_deal_origin(deal: dict) -> int:
    origin = deal.get("deal_origin")
    # 결손 매트릭스 ⑥ — enum 빈칸 0점 (default 추정 X)
    if origin is None or origin == "":
        log_gate("phase3", "warn", {
            "kind": "deal_origin_missing",
            "deal_id": deal.get("id"),
        })
        return 0
    return ORIGIN_SCORES.get(origin, 0)


# ════════════════════════════════════════════
# 종합 계산
# ════════════════════════════════════════════

SCORE_RANGES = {
    "deal_amount":     (0, 30),
    "pipeline_stage":  (0, 18),
    "deadline":        (0, 22),
    "customer_value":  (0, 23),
    "communication":   (-27, 27),
    "deal_intent":     (0, 30),
    "deal_origin":     (0, 20),
}


def calculate_deal_score(deal: dict, settings: dict, today: date) -> dict:
    """단일 딜의 7기준 점수 + 메인/서브 + 티어 + grounding 비교."""
    scores = {
        "deal_amount":    score_deal_amount(deal),
        "pipeline_stage": score_pipeline_stage(deal),
        "deadline":       score_deadline(deal, today),
        "customer_value": score_customer_value(deal, settings),
        "communication":  score_communication(deal),
        "deal_intent":    score_deal_intent(deal),
        "deal_origin":    score_deal_origin(deal),
    }

    # 범위 검증 — 위반 시 0 폴백 (부분 실패 처리)
    for key, val in list(scores.items()):
        lo, hi = SCORE_RANGES[key]
        try:
            assert_in_range(val, lo, hi, key, phase="phase3")
        except ValidationError as e:
            log_gate("phase3", "fail", {
                "kind": "score_out_of_range",
                "deal_id": deal.get("id"),
                "criterion": key,
                "value": val,
                "details": e.details,
            })
            scores[key] = 0  # 폴백

    # 메인 (1번·6번) / 서브 분리
    main_score = scores["deal_amount"] + scores["deal_intent"]
    sub_score = sum(v for k, v in scores.items() if k not in ("deal_amount", "deal_intent"))
    total = main_score + sub_score

    # 티어 분류
    t1_th = settings["scoring"]["t1_threshold"]
    t2_th = settings["scoring"]["t2_threshold"]
    if total >= t1_th:
        tier = 1
    elif total >= t2_th:
        tier = 2
    else:
        tier = 3

    # 6번 grounding 비교
    grounding_match = check_grounding_match(
        deal.get("intent_category"),
        deal.get("intent_grounding_ld"),
    )

    # 각 기준 % (시각화용)
    max_values = {k: SCORE_RANGES[k][1] for k in scores}
    percentages = {}
    for key, val in scores.items():
        mv = max_values[key]
        pct = max(0, min(100, round((val / mv) * 100))) if mv > 0 and val > 0 else 0
        percentages[key] = pct

    return {
        "scores": scores,
        "main_score": main_score,
        "sub_score": sub_score,
        "total": total,
        "max_possible": settings["scoring"]["max_score"],
        "total_pct": round((total / settings["scoring"]["max_score"]) * 100) if total > 0 else 0,
        "tier": tier,
        "grounding_match": grounding_match,
        "intent_category_llm": deal.get("intent_category"),
        "intent_grounding_ld": deal.get("intent_grounding_ld"),
        "percentages": percentages,
    }


def calculate_all(deals: list[dict], settings: dict, today: date) -> tuple[list[dict], list[dict]]:
    """
    전체 딜 스코어링 + 정렬. 부분 실패 처리.

    Returns:
        (scored_deals, failed_items): 성공 딜 리스트 + 실패 딜 리스트
    """
    results = []
    failed = []
    for deal in deals:
        try:
            # 입력 schema 검증 (id 필수)
            validate_schema(deal, ["id"], phase="phase3")
            scoring = calculate_deal_score(deal, settings, today)
            deal["scoring"] = scoring
            results.append(deal)
        except ValidationError as e:
            failed.append({
                "deal_id": deal.get("id", "unknown"),
                "reason": e.reason,
                "details": e.details,
            })
            log_gate("phase3", "fail", {
                "kind": "missing_required_keys",
                "deal_id": deal.get("id", "unknown"),
                "details": e.details,
            })

    # 부분 실패 요약
    if failed:
        summarize_partial_failures(len(results), failed, phase="phase3")

    # 총점 내림차순 정렬
    results.sort(key=lambda d: d["scoring"]["total"], reverse=True)
    return results, failed


# ════════════════════════════════════════════
# 기준 라벨 (한국어, 리포트 출력용)
# ════════════════════════════════════════════

CRITERIA_LABELS = {
    "deal_amount":    {"label": "딜 금액",        "max": 30, "group": "main"},
    "pipeline_stage": {"label": "파이프라인 단계", "max": 18, "group": "sub"},
    "deadline":       {"label": "마감 임박도",    "max": 22, "group": "sub"},
    "customer_value": {"label": "고객 가치",      "max": 23, "group": "sub"},
    "communication":  {"label": "소통 활성도",    "max": 27, "group": "sub"},
    "deal_intent":    {"label": "거래 의지",      "max": 30, "group": "main"},
    "deal_origin":    {"label": "딜 출발점",      "max": 20, "group": "sub"},
}


# ════════════════════════════════════════════
# 진입점
# ════════════════════════════════════════════


def main() -> int:
    parser = argparse.ArgumentParser(description="ld-deal-plugin 스코어링 (4/30 v1, 5/7 default 갱신)")
    # 5/7 — input 인자 optional 변환 + default를 phase2_enriched.json (PHASE 2.5a 결과) 강제
    # 이전엔 input 안 박히면 에러였고, merge_deals.py가 폐기되면서 PHASE 3 input은 *phase2_enriched.json만* 정합.
    parser.add_argument(
        "input",
        nargs="?",
        default="runtime/phase2_enriched.json",
        help="딜 JSON 경로 (default: runtime/phase2_enriched.json — PHASE 2.5a 결과). '-'면 stdin.",
    )
    parser.add_argument("--settings", default=None, help="settings.json 경로 (선택)")
    parser.add_argument("--reset-log", action="store_true", help="실행 전 _validation_log.json 비우기")
    args = parser.parse_args()

    settings = load_settings(args.settings)
    today = get_today()

    if args.reset_log:
        reset_validation_log()

    # Phase 0 — today anchor
    log_gate("phase3", "pass", {"kind": "today_anchor", "today": str(today)})

    # 입력 로드 (5/7 정정 — phase2_enriched.json 강제 fallback)
    import json as _json
    if args.input == "-":
        deals = _json.load(sys.stdin)
    else:
        # phase2_enriched.json이 없으면 phase2_active_deals.json fallback + warn (raw 누락 위험)
        if not Path(args.input).exists():
            fallback = "runtime/phase2_active_deals.json"
            if Path(fallback).exists():
                print(f"[warn] {args.input} 없음 — fallback {fallback} 사용 (raw 영역 누락 가능 — PHASE 2.5a enrich_external 미실행 의심)", file=sys.stderr)
                args.input = fallback
        deals = safe_load_json(args.input)

    if not isinstance(deals, list):
        print("입력은 딜 배열이어야 합니다.", file=sys.stderr)
        return 1

    scored, failed = calculate_all(deals, settings, today)

    # 요약 (stderr)
    tier_counts = {1: 0, 2: 0, 3: 0}
    grounding_match_count = {"match": 0, "mismatch": 0, "na": 0}
    for d in scored:
        tier_counts[d["scoring"]["tier"]] += 1
        gm = d["scoring"]["grounding_match"]
        if gm is True:
            grounding_match_count["match"] += 1
        elif gm is False:
            grounding_match_count["mismatch"] += 1
        else:
            grounding_match_count["na"] += 1

    print(f"\n스코어링 완료 — 성공 {len(scored)}건 / 실패 {len(failed)}건", file=sys.stderr)
    print(f"  Tier 1 (집중):    {tier_counts[1]}건", file=sys.stderr)
    print(f"  Tier 2 (관리):    {tier_counts[2]}건", file=sys.stderr)
    print(f"  Tier 3 (지켜보기): {tier_counts[3]}건", file=sys.stderr)
    print(f"  6번 grounding — match {grounding_match_count['match']} / mismatch {grounding_match_count['mismatch']} / N/A {grounding_match_count['na']}", file=sys.stderr)
    if failed:
        print(f"  실패 {len(failed)}건 — runtime/_validation_log.json 참조", file=sys.stderr)

    # 결과 저장 (runtime/) + stdout
    safe_save_json(runtime_path("phase3_scored_deals.json"), scored)
    print(_json.dumps(scored, ensure_ascii=False, indent=2, default=str))

    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
