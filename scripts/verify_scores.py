"""
Phase 3.5: 스코어링 검증 (4/30 rewrite)

"LLM이 만든 걸 LLM이 검증하지 않는다" — 스크립트가 객관적으로 검증.

검증 항목 6가지:
  1. 누락 체크: 모든 딜에 점수가 매겨졌는가
  2. 합산 정합성: 개별 기준 점수 합 == total인가
  3. 범위 검증: 각 기준 점수가 허용 범위 내인가 (4/30 새 weights)
  4. 만점 일치: settings.max_score == 7기준 합인가
  5. 티어 일치: total 기준과 tier 분류가 맞는가 (4/30 새 임계값 87/55)
  6. 중복 체크: 동일 딜이 2번 이상 포함되지 않았는가

출력: 검증 결과 JSON
  { "passed": true, "checks": [...], "errors": [], "warnings": [] }

사용법:
  python scripts/verify_scores.py scored_deals.json [--settings config/settings.json]
"""

from __future__ import annotations

import argparse
import io
import os
import sys
from pathlib import Path
from typing import Any

# 프로젝트 루트를 sys.path에 추가
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts._validation import log_gate
from scripts._helpers import safe_load_json

# Windows cp949 인코딩 방지
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")


# ════════════════════════════════════════════
# 검증 룰 — 4/30 갱신 (calculate_score.py와 동기화)
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

EXPECTED_MAX_SCORE = 30 + 18 + 22 + 23 + 27 + 30 + 20  # 170

DEFAULT_T1 = 87
DEFAULT_T2 = 55


def get_thresholds(settings: dict | None) -> tuple[int, int]:
    if not settings or "scoring" not in settings:
        return DEFAULT_T1, DEFAULT_T2
    s = settings["scoring"]
    return s.get("t1_threshold", DEFAULT_T1), s.get("t2_threshold", DEFAULT_T2)


def expected_tier(total: int, t1: int, t2: int) -> int:
    if total >= t1:
        return 1
    if total >= t2:
        return 2
    return 3


# ════════════════════════════════════════════
# 검증 본체
# ════════════════════════════════════════════


def verify(scored_deals: list[dict], settings: dict | None = None) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    checks: list[dict] = []

    t1, t2 = get_thresholds(settings)

    # ── 1. 누락 체크 ──
    missing = [
        d.get("deal_name", f"인덱스 {i}")
        for i, d in enumerate(scored_deals)
        if "scoring" not in d
    ]
    checks.append({
        "name": "누락 체크",
        "passed": len(missing) == 0,
        "detail": f"점수 없는 딜: {missing}" if missing else f"전체 {len(scored_deals)}건 점수 존재",
    })
    if missing:
        errors.append(f"점수 누락 {len(missing)}건: {missing}")

    # ── 2. 합산 정합성 ──
    sum_mismatches = []
    for i, d in enumerate(scored_deals):
        scoring = d.get("scoring")
        if not scoring:
            continue
        scores = scoring.get("scores", {})
        expected_total = sum(scores.values())
        actual_total = scoring.get("total", 0)
        if expected_total != actual_total:
            sum_mismatches.append({
                "deal": d.get("deal_name", f"인덱스 {i}"),
                "expected": expected_total,
                "actual": actual_total,
            })
    checks.append({
        "name": "합산 정합성",
        "passed": len(sum_mismatches) == 0,
        "detail": f"불일치: {sum_mismatches}" if sum_mismatches else "전체 합산 일치",
    })
    if sum_mismatches:
        errors.append(f"합산 불일치 {len(sum_mismatches)}건")

    # ── 3. 범위 검증 ──
    range_violations = []
    for i, d in enumerate(scored_deals):
        scoring = d.get("scoring")
        if not scoring:
            continue
        for key, val in scoring.get("scores", {}).items():
            if key not in SCORE_RANGES:
                warnings.append(f"미정의 기준: {key} (딜 {d.get('deal_name', i)})")
                continue
            lo, hi = SCORE_RANGES[key]
            if val < lo or val > hi:
                range_violations.append({
                    "deal": d.get("deal_name", f"인덱스 {i}"),
                    "criterion": key,
                    "value": val,
                    "allowed": f"{lo} ~ {hi}",
                })
    checks.append({
        "name": "범위 검증",
        "passed": len(range_violations) == 0,
        "detail": f"범위 초과 {len(range_violations)}건" if range_violations else "전체 범위 정상",
    })
    if range_violations:
        errors.append(f"범위 초과 {len(range_violations)}건: {range_violations[:3]}{'...' if len(range_violations) > 3 else ''}")

    # ── 4. 만점 일치 (4/30 신규) ──
    declared_max = settings["scoring"]["max_score"] if settings and "scoring" in settings and "max_score" in settings["scoring"] else EXPECTED_MAX_SCORE
    max_match = declared_max == EXPECTED_MAX_SCORE
    checks.append({
        "name": "만점 일치",
        "passed": max_match,
        "detail": f"settings.max_score={declared_max} / 7기준 합={EXPECTED_MAX_SCORE}",
    })
    if not max_match:
        errors.append(f"만점 불일치: settings.max_score={declared_max} ≠ 7기준 합 {EXPECTED_MAX_SCORE}")

    # ── 5. 티어 일치 ──
    tier_mismatches = []
    for i, d in enumerate(scored_deals):
        scoring = d.get("scoring")
        if not scoring:
            continue
        total = scoring.get("total", 0)
        actual_tier = scoring.get("tier", 0)
        expected = expected_tier(total, t1, t2)
        if actual_tier != expected:
            tier_mismatches.append({
                "deal": d.get("deal_name", f"인덱스 {i}"),
                "total": total,
                "actual_tier": actual_tier,
                "expected_tier": expected,
            })
    checks.append({
        "name": f"티어 일치 (T1≥{t1} / T2≥{t2})",
        "passed": len(tier_mismatches) == 0,
        "detail": f"불일치 {len(tier_mismatches)}건" if tier_mismatches else "전체 티어 일치",
    })
    if tier_mismatches:
        errors.append(f"티어 불일치 {len(tier_mismatches)}건")

    # ── 6. 중복 체크 ──
    deal_ids = [d.get("id") for d in scored_deals if d.get("id")]
    dup_ids = sorted({id_ for id_ in deal_ids if deal_ids.count(id_) > 1})
    checks.append({
        "name": "중복 체크 (id 기준)",
        "passed": len(dup_ids) == 0,
        "detail": f"중복 id: {dup_ids}" if dup_ids else "중복 없음",
    })
    if dup_ids:
        warnings.append(f"중복 딜 id {len(dup_ids)}건")

    # ── 최종 ──
    all_passed = len(errors) == 0
    log_gate("phase3.5", "pass" if all_passed else "fail", {
        "kind": "verify_scores",
        "total_deals": len(scored_deals),
        "errors": len(errors),
        "warnings": len(warnings),
    })

    return {
        "passed": all_passed,
        "total_deals": len(scored_deals),
        "checks": checks,
        "errors": errors,
        "warnings": warnings,
    }


# ════════════════════════════════════════════
# 진입점
# ════════════════════════════════════════════


def main() -> int:
    parser = argparse.ArgumentParser(description="스코어링 검증 (4/30 v1)")
    parser.add_argument("input", help="scored_deals.json 경로 (또는 '-' for stdin)")
    parser.add_argument("--settings", default=None, help="settings.json 경로 (선택)")
    args = parser.parse_args()

    # 입력 로드
    import json as _json
    if args.input == "-":
        data = _json.load(sys.stdin)
    elif os.path.exists(args.input):
        data = safe_load_json(args.input)
    else:
        print(f"입력 파일 없음: {args.input}", file=sys.stderr)
        return 1

    settings = safe_load_json(args.settings) if args.settings else None

    result = verify(data, settings)

    # 사람이 읽기 쉬운 요약 (stderr)
    print("=" * 50, file=sys.stderr)
    print("Phase 3.5: 스코어링 검증 (4/30)", file=sys.stderr)
    print("=" * 50, file=sys.stderr)
    for check in result["checks"]:
        status = "PASS" if check["passed"] else "FAIL"
        print(f"  [{status}] {check['name']}: {check['detail']}", file=sys.stderr)
    print(file=sys.stderr)

    if result["passed"]:
        print(f"  [OK] 전체 검증 통과 — {result['total_deals']}건 / Phase 4 진행 가능", file=sys.stderr)
    else:
        print(f"  [FAIL] 에러 {len(result['errors'])}건 — 수정 후 재실행 필요", file=sys.stderr)
        for err in result["errors"]:
            print(f"    - {err}", file=sys.stderr)

    if result["warnings"]:
        print(f"  [WARN] 경고 {len(result['warnings'])}건:", file=sys.stderr)
        for w in result["warnings"]:
            print(f"    - {w}", file=sys.stderr)

    # JSON 출력 (stdout)
    print(_json.dumps(result, ensure_ascii=False, indent=2))

    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
