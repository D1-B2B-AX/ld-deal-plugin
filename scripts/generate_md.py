"""
Phase 4b: MD 보고서 생성 (4/30 rewrite)

scored_deals.json + (옵션) changes.json → outputs/summary_report_YYYYMMDD.md

4/30 v1 핵심 변경 (4/29 결정 결과물 형식 정합):
- 만점 170 / T1 ≥ 87 / T2 ≥ 55
- T1 후보 뱃지 폐기
- **b + a 결합** 결과물 (분류 + 행동)
- T1 풀 상세: A 왜 T1? → B 추천 액션 → C 점수 표
- T2: 한 줄 + 분류 (어떤 시그널·왜 T2)
- T3: 한 줄 (지켜보기)
- **6번 grounding 표시**: LD 추정 vs LLM 추정 (불일치 ⚠️)
- **결손 LD 메시지 노출**: `_helpers.collect_ld_messages` (산출물 끝)
- LD 시각: 본문엔 빌더 용어(C1·번호) 제거, 자연어 라벨만

5 섹션 조립:
  1. 헤더 (target_ld·today)
  2. 📊 포트폴리오 현황
  3. 🔄 최근 변화 (detect_changes 결과, v1 그대로)
  4. 🔴🟠⚪ 티어 본문 (T1/T2/T3 — 4/29 결정 형식)
  5. 📅 이번 주 + 다음 주 미팅
  + 결손 LD 메시지 (4/30 신규)
  + 푸터

사용법:
  python scripts/generate_md.py \
    --scored runtime/phase3_scored_deals.json \
    [--changes runtime/changes.json] \
    [--settings config/settings.json] \
    [--today 2026-04-30] \
    [-o outputs/summary_report_20260430.md]
"""

from __future__ import annotations

import argparse
import glob
import io
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

# 프로젝트 루트 sys.path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts._helpers import (
    collect_ld_messages,
    get_today,
    runtime_path,
    safe_load_json,
)
from scripts._validation import load_validation_log, log_gate

# Windows cp949 인코딩 방지
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")


WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]

ACTIVE_STAGES_ORDER = [
    "Proposal 준비",
    "Proposal 송부",
    "2차 f-up",
    "보완 Proposal",
    "최종 f-up",
    "매출 집계 예정",
]


# ════════════════════════════════════════════
# 유틸
# ════════════════════════════════════════════


def format_date_kr(d) -> str:
    """2026-04-30 → 4/30(목)"""
    if isinstance(d, str):
        try:
            d = datetime.strptime(d[:10], "%Y-%m-%d").date()
        except ValueError:
            return d
    return f"{d.month}/{d.day}({WEEKDAY_KR[d.weekday()]})"


def format_amount(amt) -> str:
    """금액 포맷팅 (억 단위, 만원 단위 분기)"""
    if amt is None:
        return "-"
    try:
        amt = float(amt)
    except (ValueError, TypeError):
        return str(amt)
    if amt >= 1.0:
        return f"{amt:.2f}억"
    if amt > 0:
        return f"{int(amt * 10000):,}만원"
    return "-"


def days_between(date_str, today) -> int | None:
    if not date_str:
        return None
    try:
        d = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
        return (d - today).days
    except (ValueError, TypeError):
        return None


def format_dday(days) -> str:
    if days is None:
        return ""
    if days < 0:
        return f"⚠️ D+{abs(days)} (지남)"
    if days <= 7:
        return f"🚨 D-{days}"
    if days <= 14:
        return f"⏰ D-{days}"
    return f"D-{days}"


def format_grounding_badge(scoring: dict) -> str:
    """6번 grounding 비교 결과 뱃지 (4/30 신규)."""
    gm = scoring.get("grounding_match")
    llm = scoring.get("intent_category_llm")
    ld = scoring.get("intent_grounding_ld")

    if gm is True:
        return f"[grounding ✓ LD:{ld}=LLM:{llm}]"
    if gm is False:
        return f"[⚠️ grounding 불일치 LD:{ld}≠LLM:{llm}]"
    return ""


def build_badges(deal: dict) -> str:
    """뱃지 조립 — T1 후보 폐기, grounding 추가"""
    badges = []
    if deal.get("is_strategic_customer"):
        badges.append("[핵심기업]")
    if (deal.get("past_deal_count", 0) or 0) >= 1:
        badges.append("[재거래]")
    if deal.get("_llm_fallback"):
        badges.append("[LLM 폴백]")
    grounding = format_grounding_badge(deal.get("scoring", {}))
    if grounding:
        badges.append(grounding)
    return " ".join(badges)


def ascii_bar(value: float, max_value: float, width: int = 10) -> str:
    if max_value <= 0:
        return "░" * width
    filled = round(width * value / max_value)
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)


# ════════════════════════════════════════════
# 섹션 1: 헤더 (4/30 갱신)
# ════════════════════════════════════════════


def render_header(target_ld_name: str, today: date) -> str:
    return f"# 딜 판단 — {target_ld_name} / {today.isoformat()} ({WEEKDAY_KR[today.weekday()]})\n"


# ════════════════════════════════════════════
# 섹션 2: 📊 포트폴리오 현황 (4/30 갱신)
# ════════════════════════════════════════════


def render_portfolio(deals: list[dict], today: date) -> str:
    total_count = len(deals)
    total_amount = sum((d.get("deal_amount") or 0) for d in deals)

    # 마감 임박 분포
    close_30d = 0
    close_7d = 0
    no_deadline = 0
    for d in deals:
        dd = days_between(d.get("deadline"), today)
        if dd is None:
            no_deadline += 1
        elif 0 <= dd <= 7:
            close_7d += 1
            close_30d += 1
        elif 0 <= dd <= 30:
            close_30d += 1

    # 단계별 분포
    stage_amounts: dict = defaultdict(lambda: {"count": 0, "amount": 0.0})
    for d in deals:
        stage = d.get("pipeline_stage_name") or "단계 미입력"
        stage_amounts[stage]["count"] += 1
        stage_amounts[stage]["amount"] += (d.get("deal_amount") or 0)

    # 액티브 6 단계 우선 정렬
    def stage_sort_key(item):
        stage_name, _ = item
        for i, pat in enumerate(ACTIVE_STAGES_ORDER):
            if pat in stage_name:
                return (0, i)
        return (1, stage_name)

    sorted_stages = sorted(stage_amounts.items(), key=stage_sort_key)
    max_amt = max((s[1]["amount"] for s in sorted_stages), default=1)

    # grounding 분포 (4/30 신규)
    g_match = sum(1 for d in deals if d.get("scoring", {}).get("grounding_match") is True)
    g_mis = sum(1 for d in deals if d.get("scoring", {}).get("grounding_match") is False)
    g_na = total_count - g_match - g_mis

    lines = []
    lines.append("## 📊 포트폴리오 현황")
    lines.append("")
    lines.append(f"**전체 액티브:** {total_count}건 / {format_amount(total_amount)}")
    lines.append(
        f"**마감 임박:** D-7 이내 {close_7d}건 / D-30 이내 {close_30d}건 / 마감 미입력 {no_deadline}건"
    )
    lines.append(
        f"**거래 의지 검증 (LD 추정 vs LLM):** 일치 {g_match}건 / 불일치 {g_mis}건 / 비교 불가 {g_na}건"
    )
    lines.append("")
    lines.append("**단계별 분포 (금액 기준):**")

    max_stage_len = max((len(s[0]) for s in sorted_stages), default=10)
    stage_col = max(max_stage_len, 12)
    for stage, info in sorted_stages:
        cnt = info["count"]
        amt = info["amount"]
        pct = round(amt / total_amount * 100) if total_amount > 0 else 0
        bar = ascii_bar(amt, max_amt)
        stage_padded = stage.ljust(stage_col)
        lines.append(f"- {stage_padded} {bar} {cnt}건 · {format_amount(amt)} ({pct}%)")

    lines.append("")
    return "\n".join(lines)


# ════════════════════════════════════════════
# 섹션 3: 🔄 최근 변화 (v1 — detect_changes 결과 그대로)
# ════════════════════════════════════════════


def render_changes(changes_data) -> str:
    if not changes_data:
        return ""

    lines = []
    lines.append("## 🔄 최근 변화 (최근 7일)")
    lines.append("")

    summary = changes_data.get("summary") or changes_data.get("cumulative") or {}
    if not summary:
        lines.append("_변화 데이터 없음_")
        lines.append("")
        return "\n".join(lines)

    if summary.get("first_run"):
        lines.append("_첫 실행 — 비교할 이전 스냅샷 없음_")
    else:
        parts = []
        if summary.get("added"):
            parts.append(f"신규 {summary['added']}건")
        if summary.get("won"):
            parts.append(f"🎉 수주 {summary['won']}건")
        if summary.get("lost"):
            parts.append(f"⚠️ Lost {summary['lost']}건")
        if summary.get("stage_changed"):
            parts.append(f"단계 진전 {summary['stage_changed']}건")
        if parts:
            lines.append("**누적:** " + " | ".join(parts))
        else:
            lines.append("**변화 없음**")
    lines.append("")
    return "\n".join(lines)


# ════════════════════════════════════════════
# 섹션 2.5: 🔄 이전 회차 대비 변화 (5/7 신규 — 김민선 시연 피드백)
# ════════════════════════════════════════════


def compute_diff_vs_previous(scored_today: list, archive_dir: str, today_str: str) -> dict | None:
    """직전 회차 archive vs 오늘 phase3 비교 — 핵심 변화 영역만 (5/7 EOD 정정)

    5/7 EOD 결함 정정: PHASE 4a가 archive 덮어쓰기 후 비교 시점에 *직전 영역 사라짐* → "첫 실행" 잘못 박힘.
    해결: `_previous_run.json` 영역 우선 사용 (generate_md 시작 시점에 *현재 archive → _previous_run* 복사 박힘).
    """
    prev_run_path = os.path.join(archive_dir, "_previous_run.json")
    prev_path = None

    if os.path.exists(prev_run_path):
        prev_path = prev_run_path
    else:
        # _previous_run.json 없으면 기존 흐름 fallback — files에서 오늘 제외 가장 최근
        files = sorted(glob.glob(os.path.join(archive_dir, "*.json")))
        today_filename = f"{today_str}.json"
        prev_files = [f for f in files
                      if not f.endswith(today_filename)
                      and "_previous_run" not in os.path.basename(f)]
        if not prev_files:
            return None  # 첫 실행 — 비교 baseline X
        prev_path = prev_files[-1]
    try:
        with open(prev_path, "r", encoding="utf-8") as f:
            prev_deals = json.load(f)
    except Exception:
        return None

    # 리스트 직접 박힌 영역 또는 dict 안 deals 박힌 영역 처리
    if isinstance(prev_deals, dict):
        prev_deals = prev_deals.get("deals", [])

    prev_map = {(d.get("deal_id") or d.get("id")): d for d in prev_deals if (d.get("deal_id") or d.get("id"))}
    curr_map = {(d.get("deal_id") or d.get("id")): d for d in scored_today if (d.get("deal_id") or d.get("id"))}

    prev_ids = set(prev_map.keys())
    curr_ids = set(curr_map.keys())

    diff = {
        "previous_date": Path(prev_path).stem,
        "added": [],
        "removed": [],
        "tier_changed": [],
        "score_changed_big": [],
    }

    # 신규 진입
    for did in curr_ids - prev_ids:
        d = curr_map[did]
        diff["added"].append({
            "name": d.get("customer_name") or d.get("deal_name", ""),
            "tier": d.get("scoring", {}).get("tier"),
        })

    # 이탈
    for did in prev_ids - curr_ids:
        d = prev_map[did]
        diff["removed"].append({
            "name": d.get("customer_name") or d.get("deal_name", ""),
        })

    # 양쪽 모두 박힌 딜 — 티어 변경·점수 큰 변동
    for did in curr_ids & prev_ids:
        prev_d = prev_map[did]
        curr_d = curr_map[did]
        prev_tier = prev_d.get("scoring", {}).get("tier")
        curr_tier = curr_d.get("scoring", {}).get("tier")
        prev_score = prev_d.get("scoring", {}).get("total", 0)
        curr_score = curr_d.get("scoring", {}).get("total", 0)
        delta = curr_score - prev_score

        if prev_tier != curr_tier and prev_tier is not None and curr_tier is not None:
            diff["tier_changed"].append({
                "name": curr_d.get("customer_name") or curr_d.get("deal_name", ""),
                "from": prev_tier,
                "to": curr_tier,
                "score_delta": delta,
            })
        elif abs(delta) >= 10:
            diff["score_changed_big"].append({
                "name": curr_d.get("customer_name") or curr_d.get("deal_name", ""),
                "from": prev_score,
                "to": curr_score,
                "delta": delta,
            })

    return diff


def render_diff_vs_previous(diff: dict | None) -> str:
    """이전 회차 대비 변화 — 핵심 4~5줄 (5/7 김민선 시연 피드백 — 상단 노출)"""
    lines = ["## 🔄 이전 회차 대비 변화", ""]

    if not diff:
        lines.append("_첫 실행 — 비교할 이전 데이터 없음_")
        lines.append("")
        return "\n".join(lines)

    parts = []

    # 티어 변경 (가장 시그널 강함)
    for tc in diff.get("tier_changed", []):
        # T1=1, T3=3 — 작을수록 좋음
        arrow = "📈" if tc["to"] < tc["from"] else "🔻"
        delta_str = f" ({tc['score_delta']:+d}점)" if tc["score_delta"] else ""
        parts.append(f"{arrow} **티어 변경**: {tc['name']} T{tc['from']} → T{tc['to']}{delta_str}")

    # 점수 큰 변동 (±10점+)
    for sc in diff.get("score_changed_big", []):
        arrow = "📈" if sc["delta"] > 0 else "📉"
        parts.append(f"{arrow} **점수 변동**: {sc['name']} {sc['from']} → {sc['to']} ({sc['delta']:+d}점)")

    # 신규 진입
    if diff.get("added"):
        names = ", ".join(a["name"] for a in diff["added"][:3])
        more = f" 외 {len(diff['added'])-3}건" if len(diff["added"]) > 3 else ""
        parts.append(f"🆕 **신규 진입**: {len(diff['added'])}건 ({names}{more})")

    # 이탈
    if diff.get("removed"):
        names = ", ".join(r["name"] for r in diff["removed"][:3])
        more = f" 외 {len(diff['removed'])-3}건" if len(diff["removed"]) > 3 else ""
        parts.append(f"🚪 **이탈**: {len(diff['removed'])}건 ({names}{more})")

    prev_date = diff.get("previous_date", "미상")
    if not parts:
        lines.append(f"**변화 없음** _(이전 회차: {prev_date})_")
    else:
        lines.append(f"_이전 회차: {prev_date}_")
        lines.append("")
        for p in parts:
            lines.append(f"- {p}")

    lines.append("")
    return "\n".join(lines)


# ════════════════════════════════════════════
# 섹션 4: 🔴🟠⚪ 티어 본문 (4/30 갱신 — b+a 결합)
# ════════════════════════════════════════════


CRITERIA_LABELS_KO = {
    "deal_amount":    ("딜 금액",        30),
    "pipeline_stage": ("파이프라인 단계", 18),
    "deadline":       ("마감 임박도",    22),
    "customer_value": ("고객 가치",      23),
    "communication":  ("소통 활성도",    27),
    "deal_intent":    ("거래 의지",      30),
    "deal_origin":    ("딜 출발점",      20),
}

# 메인 vs 서브 시각 분리
MAIN_KEYS = {"deal_amount", "deal_intent"}


def render_tier_section(deals: list[dict], today: date) -> str:
    t1 = [d for d in deals if d.get("scoring", {}).get("tier") == 1]
    t2 = [d for d in deals if d.get("scoring", {}).get("tier") == 2]
    t3 = [d for d in deals if d.get("scoring", {}).get("tier") == 3]

    for t in (t1, t2, t3):
        t.sort(key=lambda d: d.get("scoring", {}).get("total", 0), reverse=True)

    lines = []

    # T1 — 풀 상세
    lines.append(f"## 🔴 T1 집중 ({len(t1)}건) — 오늘 즉시 대응")
    lines.append("")
    if not t1:
        lines.append("_해당 없음_")
        lines.append("")
    else:
        for idx, d in enumerate(t1, 1):
            lines.extend(render_t1_full(d, idx, today))
            lines.append("")

    # T2 — 한 줄 + 분류
    lines.append(f"## 🟠 T2 관리 ({len(t2)}건) — 이번 주 관계 육성")
    lines.append("")
    if not t2:
        lines.append("_해당 없음_")
        lines.append("")
    else:
        for idx, d in enumerate(t2, 1):
            lines.append(render_t2_compact(d, idx, today))
        lines.append("")

    # T3 — 한 줄 (지켜보기)
    lines.append(f"## ⚪ T3 지켜보기 ({len(t3)}건) — 주기적 모니터링")
    lines.append("")
    if not t3:
        lines.append("_해당 없음_")
        lines.append("")
    else:
        for d in t3:
            lines.append(render_t3_line(d, today))
        lines.append("")

    return "\n".join(lines)


def render_t1_full(deal: dict, rank: int, today: date) -> list[str]:
    """T1 풀 상세 (옵션 b+a) — A 왜 T1? → B 추천 액션 → C 점수 표"""
    name = deal.get("deal_name", "")
    customer = deal.get("customer_name", "")
    amt = format_amount(deal.get("deal_amount"))
    stage = deal.get("pipeline_stage_name") or "단계 미입력"
    badges = build_badges(deal)
    reason = deal.get("reason") or "_LLM reason 미생성 (5/7 검증 시점)_"
    next_action = deal.get("next_action") or "_LLM next_action 미생성 (5/7 검증 시점)_"

    dday = format_dday(days_between(deal.get("deadline"), today))
    deadline_source = deal.get("deadline_source") or "—"

    scoring = deal.get("scoring", {})
    total = scoring.get("total", 0)
    max_p = scoring.get("max_possible", 170)
    pct = scoring.get("total_pct", 0)

    lines = []
    header = f"### {rank}. {name} ({customer}) — {amt} · {stage}"
    if badges:
        header += f"  {badges}"
    lines.append(header)
    lines.append("")

    # A. 왜 T1? — 헤드라인(통찰) + 메타 + 데이터 근거 (5/6 갱신)
    lines.append("**A. 왜 T1?**")
    # 헤드라인 (한 줄 통찰) — 5/6 신규
    headline = deal.get("reason_headline")
    if headline:
        lines.append(f"- 🎯 **{headline.strip()}**")
    # 메타 한 줄 (D-day·마감 출처·시그널·grounding)
    why_parts = []
    if dday:
        why_parts.append(dday)
    if deadline_source:
        why_parts.append(f"마감 출처: {format_deadline_source(deadline_source)}")
    intent_signals = deal.get("intent_signals") or []
    if intent_signals:
        why_parts.append(f"거래 의지 시그널: {format_intent_signals(intent_signals)}")
    grounding_match = scoring.get("grounding_match")
    if grounding_match is True:
        why_parts.append("LD 추정과 LLM 분석 일치")
    elif grounding_match is False:
        why_parts.append("⚠️ LD 추정과 LLM 분석 불일치 — 검토 권장")
    if why_parts:
        lines.append("- " + " · ".join(why_parts))
    # 데이터 근거 (LLM 결과)
    reason_lines = [l for l in reason.split("\n") if l.strip()][:3]
    for rl in reason_lines:
        lines.append(f"- {rl}")
    lines.append("")

    # B. 추천 액션 (5/6 — ⚡ 이모지로 시각 강조)
    lines.append("**B. ⚡ 추천 액션:**")
    next_lines = [l for l in next_action.split("\n") if l.strip()][:3]
    for nl in next_lines:
        lines.append(f"- ⚡ {nl}")
    lines.append("")

    # C. 점수 표 (메인/서브 시각 분리)
    lines.append(f"**C. 점수 표** ({total}/{max_p}점, {pct}%)")
    lines.append("")
    lines.append("| 기준 | 점수 | 만점 | 그룹 |")
    lines.append("|---|---|---|---|")
    scores = scoring.get("scores", {})
    for key in ["deal_amount", "pipeline_stage", "deadline", "customer_value",
                "communication", "deal_intent", "deal_origin"]:
        label, max_val = CRITERIA_LABELS_KO[key]
        val = scores.get(key, 0)
        group = "**메인**" if key in MAIN_KEYS else "서브"
        lines.append(f"| {label} | {val} | {max_val} | {group} |")

    return lines


def format_intent_signals(signals: list[str]) -> str:
    """C1·C2·C3 → LD 친화 라벨 (빌더 용어 제거)"""
    label_map = {
        "C1": "결정 단계 임박",
        "C2": "권한·예산 진척",
        "C3": "고객 활동·관심 활발",
    }
    return ", ".join(label_map.get(s, s) for s in signals)


def format_deadline_source(source: str) -> str:
    """deadline_source → LD 친화 표현"""
    if "proposal_field" in source:
        return "제안서 마감일"
    if "수주 예정일" in source or "from_field" in source:
        return "수주 예정일"
    if "llm" in source.lower() or "extract" in source.lower():
        return "슬랙·메일에서 추출"
    if "merged" in source:
        return "필드 + 추출 결합"
    return source


def render_t2_compact(deal: dict, rank: int, today: date) -> str:
    """T2 — 메타·분류·grounding + 왜 T2 reason + ⚡ 추천 액션 (5/7 갱신, 점수 표는 T1만)

    5/7 김민선 시연 피드백 — T2부터 사유·액션 부재로 결과 빈약함 발생. T1 0건·T2 다수 케이스 대응.
    """
    name = deal.get("deal_name", "")
    customer = deal.get("customer_name", "")
    amt = format_amount(deal.get("deal_amount"))
    stage = deal.get("pipeline_stage_name") or "단계 미입력"
    dday = format_dday(days_between(deal.get("deadline"), today))
    badges = build_badges(deal)
    reason = deal.get("reason") or ""
    next_action = deal.get("next_action") or ""

    intent_signals = deal.get("intent_signals") or []
    signal_str = format_intent_signals(intent_signals) if intent_signals else "거래 의지 시그널 부재"

    out = []
    header = f"- **{rank}. {name}** ({customer}) — {amt} · {stage}"
    if dday:
        header += f" · {dday}"
    out.append(header)

    # 메타·분류 한 줄 (grounding은 badges에 이미 박힘 — 5/7 중복 정정)
    meta_line = f"  - 분류: {signal_str}"
    if badges:
        meta_line += f"  {badges}"
    out.append(meta_line)

    # 왜 T2 reason (5/7 신규 — 김민선 시연 피드백)
    if reason and not reason.startswith("_LLM"):
        reason_clean = reason.replace("\n", " ").strip()
        if len(reason_clean) > 220:
            reason_clean = reason_clean[:220] + "..."
        out.append(f"  - 왜 T2: {reason_clean}")

    # ⚡ 추천 액션 (5/7 신규 — 김민선 시연 피드백)
    if next_action and not next_action.startswith("_LLM"):
        action_clean = next_action.replace("\n", " ").strip()
        if len(action_clean) > 220:
            action_clean = action_clean[:220] + "..."
        out.append(f"  - ⚡ 추천 액션: {action_clean}")

    return "\n".join(out)


def render_t3_line(deal: dict, today: date) -> str:
    """T3 한 줄 (지켜보기)"""
    name = deal.get("deal_name", "")
    customer = deal.get("customer_name", "")
    amt = format_amount(deal.get("deal_amount"))
    stage = deal.get("pipeline_stage_name") or "단계 미입력"
    return f"- {name} ({customer}, {amt}, {stage})"


# ════════════════════════════════════════════
# 섹션 5: 📅 미팅
# ════════════════════════════════════════════


def render_meetings(deals: list[dict], today: date) -> str:
    week_start = today - timedelta(days=today.weekday())
    next_sunday = week_start + timedelta(days=13)

    meetings = []
    for d in deals:
        for ev in (d.get("calendar_events") or []):
            ev_date_str = ev.get("date")
            try:
                ev_d = datetime.strptime(str(ev_date_str)[:10], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue
            if week_start <= ev_d <= next_sunday:
                meetings.append({
                    "date": ev_d,
                    "deal_name": d.get("deal_name", ""),
                    "customer": d.get("customer_name", ""),
                    "title": ev.get("title", ""),
                    "time": ev.get("time", ""),
                })

    meetings.sort(key=lambda m: m["date"])

    lines = []
    lines.append("## 📅 이번 주 + 다음 주 미팅")
    lines.append("")
    if not meetings:
        lines.append("_예정된 미팅 없음_")
        lines.append("")
        return "\n".join(lines)

    lines.append("| 날짜 | 고객 | 내용 |")
    lines.append("|---|---|---|")
    for m in meetings:
        date_str = format_date_kr(m["date"])
        content = m["title"]
        if m["time"]:
            content += f" ({m['time']})"
        lines.append(f"| {date_str} | {m['customer']} | {content} |")
    lines.append("")
    return "\n".join(lines)


# ════════════════════════════════════════════
# 섹션 6: ⚠️ 처리 안내 (4/30 신규 — LD 친화 메시지)
# ════════════════════════════════════════════


def render_validation_warnings() -> str:
    """_validation_log에서 warn 항목을 LD 친화 메시지로 변환."""
    log_entries = load_validation_log()
    messages = collect_ld_messages(log_entries)

    if not messages:
        return ""

    lines = []
    lines.append("## ⚠️ 처리 안내")
    lines.append("")
    lines.append("이번 분석에서 다음 사항이 감지되었습니다:")
    lines.append("")
    for msg in messages:
        lines.append(f"- {msg}")
    lines.append("")
    return "\n".join(lines)


# ════════════════════════════════════════════
# 푸터
# ════════════════════════════════════════════


def render_footer(runtime_sec: float | None = None, deal_count: int = 0) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    parts = [f"생성: {now}", f"분석 딜: {deal_count}건"]
    if runtime_sec is not None:
        mins = int(runtime_sec // 60)
        secs = int(runtime_sec % 60)
        parts.append(f"실행 시간: {mins}분 {secs}초")
    parts.append("데이터 소스: 세일즈맵·슬랙·견적서·메일·캘린더 + memo 테이블")
    return "---\n_" + " | ".join(parts) + "_\n"


# ════════════════════════════════════════════
# 자동 삭제
# ════════════════════════════════════════════


def cleanup_old_reports(output_dir: str, retention_days: int) -> int:
    if not os.path.exists(output_dir):
        return 0
    cutoff = datetime.now() - timedelta(days=retention_days)
    deleted = 0
    for f in glob.glob(os.path.join(output_dir, "summary_report_*.md")):
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(f))
            if mtime < cutoff:
                os.remove(f)
                deleted += 1
        except OSError:
            continue
    return deleted


# ════════════════════════════════════════════
# 진입점
# ════════════════════════════════════════════


def main() -> int:
    parser = argparse.ArgumentParser(description="MD 리포트 생성 (4/30 v1)")
    parser.add_argument("--scored", default="runtime/phase3_scored_deals.json")
    parser.add_argument("--changes", default=None)
    parser.add_argument("--settings", default="config/settings.json")
    parser.add_argument("--today", default=None)
    parser.add_argument("--runtime", type=float, default=None, help="실행 시간(초)")
    parser.add_argument("-o", "--output", default=None)
    args = parser.parse_args()

    today = (
        datetime.strptime(args.today, "%Y-%m-%d").date()
        if args.today
        else get_today()
    )
    settings = safe_load_json(args.settings, default={}) or {}
    target_ld_name = settings.get("target_ld", {}).get("name", "LD")

    scored = safe_load_json(args.scored, default=[])
    if not isinstance(scored, list):
        print(f"scored 입력 형식 오류: {args.scored}", file=sys.stderr)
        return 1

    changes_data = safe_load_json(args.changes) if args.changes else None

    # 5/7 EOD 정정 — _previous_run.json 흐름 박기 (PHASE 4a 순서 결함 정정)
    # 기존: PHASE 4a에서 archive 덮어쓰기 → PHASE 4c 비교 시점에 *직전 영역 사라짐* → "첫 실행" 잘못 박힘
    # 정정: generate_md 시작 시점에 *기존 today archive → _previous_run.json 복사* (Day 기준 비교 baseline 보존)
    import shutil
    archive_dir = settings.get("report", {}).get("archive_dir", "archive")
    Path(archive_dir).mkdir(parents=True, exist_ok=True)
    today_archive_path = os.path.join(archive_dir, f"{today.strftime('%Y%m%d')}.json")
    prev_run_path = os.path.join(archive_dir, "_previous_run.json")

    # 1. _previous_run.json 갱신 — 기존 today_archive 영역을 _previous로 복사
    if os.path.exists(today_archive_path):
        # PHASE 4a가 박은 영역 (또는 이전 회차 박은 영역)을 _previous로 보존
        shutil.copy(today_archive_path, prev_run_path)
    elif not os.path.exists(prev_run_path):
        # today archive 없음 + _previous도 없음 — 가장 최근 archive (어제 영역) → _previous 복사
        files = sorted(glob.glob(os.path.join(archive_dir, "*.json")))
        files = [f for f in files if "_previous_run" not in os.path.basename(f)]
        if files:
            shutil.copy(files[-1], prev_run_path)

    # 2. 비교 — _previous_run vs 오늘 phase3
    diff_vs_prev = compute_diff_vs_previous(scored, archive_dir, today.strftime("%Y%m%d"))

    # 섹션 조립 (직전 비교는 포트폴리오 바로 아래에 박힘 — 김민선 시연 피드백)
    sections = [
        render_header(target_ld_name, today),
        render_portfolio(scored, today),
        render_diff_vs_previous(diff_vs_prev),
        render_changes(changes_data),
        render_tier_section(scored, today),
        render_meetings(scored, today),
        render_validation_warnings(),
        render_footer(args.runtime, deal_count=len(scored)),
    ]
    md = "\n".join(s for s in sections if s)

    # 출력
    output_path = args.output or f"outputs/summary_report_{today.strftime('%Y%m%d')}.md"
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(md, encoding="utf-8")

    # 자동 삭제
    output_dir = settings.get("report", {}).get("output_dir", "outputs")
    retention = settings.get("report", {}).get("retention_days", 14)
    deleted = cleanup_old_reports(output_dir, retention)

    log_gate("phase4", "pass", {
        "kind": "md_generated",
        "output_path": output_path,
        "deal_count": len(scored),
        "deleted_old": deleted,
    })

    print(f"\nMD 리포트 생성 완료 → {output_path}", file=sys.stderr)
    print(f"  딜 {len(scored)}건 / 길이 {len(md)} chars", file=sys.stderr)
    if deleted:
        print(f"  오래된 리포트 {deleted}건 자동 삭제", file=sys.stderr)

    # 5/7 EOD 신규 — 오늘 scored_deals → archive 박기 (덮어쓰기, 다음 회차 _previous_run baseline)
    # PHASE 4a가 박은 raw 영역 archive를 *scored_deals (점수·tier 박힌 영역)*로 덮어쓰기 → 다음 회차 비교 정합
    try:
        with open(today_archive_path, "w", encoding="utf-8") as f:
            json.dump(scored, f, ensure_ascii=False, indent=2)
        print(f"  archive 갱신 → {today_archive_path}", file=sys.stderr)
    except Exception as e:
        print(f"  (archive 갱신 실패: {e})", file=sys.stderr)

    # 5/7 신규 — 결과물 자동 열기 (Windows 기본 프로그램 = VS Code·메모장 등)
    auto_open = settings.get("report", {}).get("auto_open", True)
    if auto_open:
        try:
            abs_path = str(Path(output_path).resolve())
            if sys.platform == "win32":
                os.startfile(abs_path)
            elif sys.platform == "darwin":
                import subprocess
                subprocess.Popen(["open", abs_path])
            else:
                import subprocess
                subprocess.Popen(["xdg-open", abs_path])
            print(f"  결과물 자동 열기 → {abs_path}", file=sys.stderr)
        except Exception as e:
            # silent fallback — 환경 미지원 시 경로만 출력
            print(f"  (자동 열기 실패: {e} — 위 경로 직접 열어주세요)", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
