"""
enrich_external.py — Phase 2.5 자동 머지 (5/6 신규)

db 모드 phase2 결과 + 스킬 2~5 결과를 deal_id로 자동 join.
4/30 검증 시 수동 박았던 영역의 자동화 — 결정론 코드 영역만 처리.
LLM 보완(deal_origin·소통 상태 등)은 PHASE 2.5의 *후속 단계*에서 별도 처리.

사용법:
  python scripts/enrich_external.py \
    --phase2 runtime/phase2_active_deals.json \
    [--skill2 runtime/skill2_calendar.json] \
    [--skill3 runtime/skill3_slack.json] \
    [--skill4 runtime/skill4_gmail.json] \
    [--skill5 runtime/skill5_drive.json] \
    -o runtime/phase2_enriched.json

각 스킬 결과 schema:
  스킬 2: [{deal_id, calendar_results: [...], ...}]
  스킬 3: [{deal_id, slack_results, slack_summary, activity_flag,
           slack_thread_count_14d, slack_attachments: [...]}]   # 5/6 신규
  스킬 4: [{deal_id, email_results, email_summary, customer_responded, last_email_days}]
  스킬 5: [{deal_id, drive_results, last_quote_sheet_updated_days}]

조인 룰:
  - phase2의 `id`(또는 `deal_id`) 기준 left join
  - 스킬 결과 없는 딜은 phase2 원본 필드만 유지
  - 스킬 결과에 phase2에 없는 deal_id는 무시 (warn 로그)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# 스킬별 머지 대상 필드
SKILL_FIELDS = {
    "skill2": [
        "calendar_results",
    ],
    "skill3": [
        "slack_results",
        "slack_summary",
        "activity_flag",
        "slack_thread_count_14d",
        "slack_attachments",                # 5/6 신규 — 견적서 첨부 사실 시그널 (본문 X)
        "slack_results_lead_history",       # 5/6 신규 — 2026-01-01부터 lead 배분 thread (6번 거래 의지 LLM raw용)
        "slack_lead_history_summary",       # 5/6 신규 — lead history 요약 (영업 사이클 흐름)
    ],
    "skill4": [
        "email_results",
        "email_summary",
        "customer_responded",
        "last_email_days",
    ],
    "skill5": [
        "drive_results",
        "last_quote_sheet_updated_days",
    ],
}


def load_skill_json(path: str | None, label: str) -> list[dict]:
    """스킬 결과 JSON 로드. 파일 없거나 인자 없으면 빈 리스트."""
    if not path:
        print(f"[skip] {label}: 인자 없음", file=sys.stderr)
        return []
    p = Path(path)
    if not p.exists():
        print(f"[warn] {label}: 파일 없음 ({path}) — 빈 리스트로 진행", file=sys.stderr)
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            print(f"[err]  {label}: 최상위 list여야 함, got {type(data).__name__}", file=sys.stderr)
            return []
        print(f"[ok]   {label}: {len(data)}건 로드", file=sys.stderr)
        return data
    except json.JSONDecodeError as e:
        print(f"[err]  {label}: JSON 파싱 실패 — {e}", file=sys.stderr)
        return []


def index_by_deal_id(items: list[dict], label: str) -> dict[str, dict]:
    """deal_id 키로 dict 변환. 중복 시 마지막 항목 채택."""
    idx: dict[str, dict] = {}
    for item in items:
        did = item.get("deal_id") or item.get("id")
        if not did:
            print(f"[warn] {label}: deal_id 없는 항목 skip", file=sys.stderr)
            continue
        if did in idx:
            print(f"[warn] {label}: deal_id 중복 ({did}) — 마지막 항목 채택", file=sys.stderr)
        idx[did] = item
    return idx


def enrich_deal(deal: dict, skill_indices: dict[str, dict[str, dict]]) -> dict:
    """단일 딜에 스킬 결과 left join."""
    enriched = dict(deal)
    did = deal.get("id") or deal.get("deal_id")
    if not did:
        return enriched

    for skill_label, idx in skill_indices.items():
        item = idx.get(did)
        if not item:
            continue
        for field in SKILL_FIELDS.get(skill_label, []):
            if field in item:
                enriched[field] = item[field]
    return enriched


def main() -> int:
    parser = argparse.ArgumentParser(description="enrich_external.py — Phase 2.5 자동 머지 (5/6 신규)")
    parser.add_argument("--phase2", required=True, help="phase2 결과 (db/skills 모드)")
    parser.add_argument("--skill2", help="캘린더 스킬 결과 JSON")
    parser.add_argument("--skill3", help="슬랙 스킬 결과 JSON")
    parser.add_argument("--skill4", help="지메일 스킬 결과 JSON")
    parser.add_argument("--skill5", help="드라이브 스킬 결과 JSON")
    parser.add_argument("-o", "--output", required=True)
    args = parser.parse_args()

    # phase2 로드 (필수)
    phase2_path = Path(args.phase2)
    if not phase2_path.exists():
        print(f"[fatal] phase2 파일 없음: {phase2_path}", file=sys.stderr)
        return 1
    phase2 = json.loads(phase2_path.read_text(encoding="utf-8"))
    print(f"[ok]   phase2: {len(phase2)}건 로드", file=sys.stderr)

    # 스킬 결과 로드
    skill_data = {
        "skill2": load_skill_json(args.skill2, "skill2 (캘린더)"),
        "skill3": load_skill_json(args.skill3, "skill3 (슬랙)"),
        "skill4": load_skill_json(args.skill4, "skill4 (지메일)"),
        "skill5": load_skill_json(args.skill5, "skill5 (드라이브)"),
    }
    skill_indices = {label: index_by_deal_id(items, label) for label, items in skill_data.items()}

    # left join
    enriched = [enrich_deal(deal, skill_indices) for deal in phase2]

    # 매칭 요약
    phase2_ids = {(d.get("id") or d.get("deal_id")) for d in phase2}
    print("\n[summary] 매칭 카운트:", file=sys.stderr)
    for skill_label, idx in skill_indices.items():
        if not idx:
            continue
        matched = len(phase2_ids & set(idx.keys()))
        orphan = len(set(idx.keys()) - phase2_ids)
        print(f"  {skill_label}: {matched}/{len(phase2)}건 매칭" + (f" (스킬엔 있으나 phase2엔 없는 deal_id {orphan}건)" if orphan else ""), file=sys.stderr)

    # 출력
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(enriched, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nenrich 완료: {len(enriched)}건 → {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
