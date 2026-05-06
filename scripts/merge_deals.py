"""
merge_deals.py — Phase 1·2 raw → deals.json 병합 (4/30 rewrite)

두 모드:
- skills 모드: 스킬 1~5 결과 통합 (운영 시 — Claude가 호출 후 통합)
- db 모드: 본부장 대시보드 DB 직접 쿼리 (검증·테스트 시)

4/30 변경 (큰 폭):
- 액티브 게이트: 단계 6종 + `성사 가능성 ≠ LOST` (LOST 79% 차단)
- 1번 amount: `예상 체결액` (`금액` 폐기)
- 3번 deadline: 3단계 트리 1·2차 (`제안서 마감일` → `수주 예정일`) — LLM 추출은 6번 SKILL
- 5번 두 축: `slack_thread_count_14d` + `last_quote_sheet_updated_days`
- 6번 grounding: `성사 가능성` 4 enum 추출 (확정·높음·낮음·LOST)
- memo 테이블 join (dealId 매칭, text 결합)
- DB 신선도 체크 (db 모드 — 24h 넘으면 경고)
- _validation 통합

사용법:
  # 검증·테스트 시
  python scripts/merge_deals.py --mode db --settings config/settings.json
  # 운영 시 (Claude가 스킬 1~5 호출 후)
  python scripts/merge_deals.py --mode skills --skill1 ... --settings config/settings.json
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# KST 시간대 (UTC+9) — DB는 UTC ISO 형식으로 저장, parse_date에서 KST로 변환 후 date 추출 (4/30 EOD fix).
KST = timezone(timedelta(hours=9))

# 프로젝트 루트 sys.path 추가
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts._validation import (
    ValidationError,
    log_gate,
    summarize_partial_failures,
    validate_schema,
)
from scripts._helpers import (
    get_today,
    runtime_path,
    safe_load_json,
    safe_save_json,
)

# Windows cp949 인코딩 방지
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")


# ════════════════════════════════════════════
# 액티브 게이트 단계 패턴 (4/29 노을님 결정)
# ════════════════════════════════════════════

ACTIVE_STAGE_PATTERNS = [
    "Proposal 준비",
    "Proposal 송부",
    "2차 f-up",
    "보완 Proposal",
    "최종 f-up",
    "매출 집계 예정",
]

# DB 신선도 임계값
DB_STALE_HOURS = 24


# ════════════════════════════════════════════
# 공용 헬퍼
# ════════════════════════════════════════════


def parse_date(s) -> date | None:
    """다양한 날짜 포맷 → KST 기준 date 객체.

    DB는 UTC ISO 형식으로 저장 ('2026-05-28T15:00:00.000Z' 등). KST(+9h) 변환 후 date 추출.
    4/30 EOD fix — 이전 UTC date 추출로 모든 7건 답지 대비 -1일 시프트 발견.
    """
    if not s:
        return None
    s = str(s).strip()
    # ISO with time → KST 변환 후 date
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            # naive datetime은 UTC로 가정 (DB 저장 형식 기준)
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(KST).date()
    except (ValueError, TypeError):
        pass
    # date-only 포맷 — timezone 무관 (이미 KST 기준 date 표기로 가정)
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def extract_json_name(json_str) -> str | None:
    """JSON 객체 문자열에서 name 필드 추출 (담당자·파이프라인 단계 등)."""
    if not json_str:
        return None
    if isinstance(json_str, dict):
        return json_str.get("name")
    try:
        obj = json.loads(json_str)
        if isinstance(obj, dict):
            return obj.get("name")
    except (json.JSONDecodeError, TypeError):
        pass
    # JSON 아니면 raw string 반환
    return json_str if isinstance(json_str, str) else None


def extract_json_array_first(json_str) -> str | None:
    """JSON array 첫 element 추출 (성사 가능성 등)."""
    if not json_str:
        return None
    if isinstance(json_str, list):
        return json_str[0] if json_str else None
    try:
        obj = json.loads(json_str) if isinstance(json_str, str) else json_str
        if isinstance(obj, list) and obj:
            return obj[0]
    except (json.JSONDecodeError, TypeError):
        pass
    # 단순 string이면 strip 후 반환
    if isinstance(json_str, str):
        cleaned = json_str.strip().strip("[]").strip('"').strip("'")
        return cleaned or None
    return None


def amount_won_to_eok(won) -> float | None:
    """원 → 억 단위 (소수 4자리)."""
    if won is None or won == "":
        return None
    try:
        v = float(won)
        if v <= 0:
            return None
        return round(v / 1_0000_0000, 4) if v > 1000 else v
    except (ValueError, TypeError):
        return None


def is_strategic(name: str, keywords: list[str]) -> bool:
    if not name or not keywords:
        return False
    return any(kw in name for kw in keywords)


# ════════════════════════════════════════════
# DB 신선도 체크 (db 모드)
# ════════════════════════════════════════════


def check_db_freshness(db_path: str) -> tuple[float, str | None]:
    """
    DB 파일 modified time + manifest run_info 체크.
    24h 넘으면 경고 + 다운로드 명령 안내. 강제 중단 X (사용자 결정).
    """
    if not os.path.exists(db_path):
        raise ValidationError("phase1", f"DB 파일 없음: {db_path}", {"db_path": db_path})

    db_mtime = datetime.fromtimestamp(os.path.getmtime(db_path))
    age_hours = (datetime.now() - db_mtime).total_seconds() / 3600

    captured_at: str | None = None
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT captured_at_utc FROM run_info ORDER BY run_tag DESC LIMIT 1")
        row = cur.fetchone()
        if row:
            captured_at = row[0]
        conn.close()
    except sqlite3.Error:
        pass

    is_stale = age_hours > DB_STALE_HOURS
    log_gate("phase1", "warn" if is_stale else "pass", {
        "kind": "db_freshness",
        "age_hours": round(age_hours, 1),
        "captured_at": captured_at,
        "stale": is_stale,
    })

    if is_stale:
        print(f"⚠️ DB가 {age_hours:.1f}시간 전 export ({captured_at}). 새로 받기 권장:", file=sys.stderr)
        print("   curl -L -o ~/salesmap/salesmap_latest.db \\", file=sys.stderr)
        print("     https://github.com/sabinanfranz/data_analysis_ai/releases/download/salesmap-db-latest/salesmap_latest.db", file=sys.stderr)
    else:
        print(f"✓ DB 신선도 OK — {age_hours:.1f}h 전 export ({captured_at})", file=sys.stderr)

    return age_hours, captured_at


# ════════════════════════════════════════════
# DB 모드 — 직접 쿼리
# ════════════════════════════════════════════


def query_active_deals_from_db(settings: dict, today: date) -> list[dict]:
    """본부장 DB에서 액티브 target_ld 딜 추출 + memo join."""
    db_path = os.path.expanduser(settings["data_sources"]["salesmap_db_path"])

    # DB 신선도 체크
    check_db_freshness(db_path)

    target_email = settings["target_ld"]["email"]
    extract_fields = settings["data_sources"]["salesmap_extract_fields"]
    grounding_field = extract_fields["intent_grounding_field"]
    # 액티브 게이트 차단 enum (4/30 EOD: LOST + 확정/Won)
    grounding_exclude = settings["scoring"].get("intent_grounding_exclude", ["LOST"])

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        # 1. user 테이블에서 target_ld id 조회
        cur.execute("SELECT id, name FROM user WHERE email = ?", (target_email,))
        user_row = cur.fetchone()
        if not user_row:
            raise ValidationError("phase1", f"target_ld user not found: {target_email}",
                                  {"email": target_email})
        target_id = user_row["id"]
        target_name = user_row["name"]

        # 2. 액티브 게이트 SQL — 단계 6종 + grounding ∉ exclude (4/30 EOD: LOST·확정 둘 다 차단)
        or_clauses = " OR ".join(['"파이프라인 단계" LIKE ?'] * len(ACTIVE_STAGE_PATTERNS))
        not_like_clauses = " AND ".join(
            [f'"{grounding_field}" NOT LIKE ?' for _ in grounding_exclude]
        )
        sql = f'''
            SELECT * FROM deal
            WHERE "담당자" LIKE ?
              AND ({or_clauses})
              AND (({not_like_clauses}) OR "{grounding_field}" IS NULL)
            ORDER BY "최근 파이프라인 수정 날짜" DESC
        '''
        params = (
            [f'%"id": "{target_id}"%']
            + [f"%{p}%" for p in ACTIVE_STAGE_PATTERNS]
            + [f"%{ex}%" for ex in grounding_exclude]
        )
        cur.execute(sql, params)
        raw_deals = [dict(r) for r in cur.fetchall()]

        log_gate("phase1", "pass", {
            "kind": "active_gate_query",
            "active_count": len(raw_deals),
            "target_ld": target_name,
            "target_id": target_id,
        })

        # 3. memo 테이블 join — dealId 매칭 모든 메모 결합
        memo_by_deal: dict[str, list[dict]] = {}
        deal_ids = [d.get("id") for d in raw_deals if d.get("id")]
        if deal_ids:
            placeholders = ",".join(["?"] * len(deal_ids))
            cur.execute(
                f'SELECT "dealId", "text", "유형", "createdAt" FROM memo '
                f'WHERE "dealId" IN ({placeholders}) ORDER BY "createdAt" DESC',
                deal_ids,
            )
            for memo_row in cur.fetchall():
                d_id = memo_row["dealId"]
                memo_by_deal.setdefault(d_id, []).append({
                    "text": memo_row["text"],
                    "type": memo_row["유형"],
                    "created_at": memo_row["createdAt"],
                })

        log_gate("phase1", "pass", {
            "kind": "memo_join",
            "deals_with_memo": len(memo_by_deal),
            "total_memos": sum(len(m) for m in memo_by_deal.values()),
        })

    finally:
        conn.close()

    # 4. 각 raw deal normalize
    normalized = []
    failed = []
    for raw in raw_deals:
        try:
            normalized.append(
                normalize_deal_from_db(raw, memo_by_deal.get(raw.get("id"), []), settings, today)
            )
        except Exception as e:
            failed.append({"deal_id": raw.get("id", "unknown"), "reason": str(e)})

    if failed:
        summarize_partial_failures(len(normalized), failed, "phase1")

    return normalized


def normalize_deal_from_db(raw: dict, memos: list[dict], settings: dict, today: date) -> dict:
    """DB 한 row → 코드 친화 deal schema."""
    extract_fields = settings["data_sources"]["salesmap_extract_fields"]
    keywords = settings["scoring"].get("strategic_keywords", []) or []

    # 1번 amount — 예상 체결액 (원 → 억)
    amount = amount_won_to_eok(raw.get(extract_fields["amount_field"]))
    # Net(%) 보조
    net_raw = raw.get(extract_fields.get("amount_secondary", "Net(%)"))
    try:
        net_pct = float(net_raw) if net_raw else None
    except (ValueError, TypeError):
        net_pct = None

    # 2번 단계 — JSON name 추출
    stage_name = extract_json_name(raw.get("파이프라인 단계"))

    # 3번 deadline — 3단계 트리 1·2차 (LLM 추출은 6번 SKILL)
    deadline = None
    deadline_source = None
    for col in extract_fields.get("deadline_priority", ["제안서 마감일", "수주 예정일"]):
        val = raw.get(col)
        if val:
            d = parse_date(val)
            if d:
                deadline = d.isoformat()
                deadline_source = f"from_field:{col}"
                break

    # 4번 — 고객사·strategic
    # db 모드: organization 테이블 join 안 함 (v2 영역). 딜 이름의 underscore 앞부분을 customer_name으로 추정
    deal_name_raw = raw.get("이름") or ""
    if "_" in deal_name_raw:
        customer_name = deal_name_raw.split("_", 1)[0]
    else:
        customer_name = deal_name_raw
    is_strat = is_strategic(customer_name, keywords)

    # 6번 grounding — 성사 가능성 (JSON array first)
    intent_grounding = extract_json_array_first(raw.get(extract_fields["intent_grounding_field"]))

    # memo 본문 결합
    memo_texts = [m["text"] for m in memos if m.get("text")]
    memo_text = "\n\n---\n\n".join(memo_texts)

    return {
        "id": raw.get("id"),
        "deal_name": raw.get("이름"),
        "customer_name": customer_name,
        "deal_amount": amount,
        "net_pct": net_pct,
        "pipeline_stage_name": stage_name,
        "deadline": deadline,
        "deadline_source": deadline_source,
        "raw_proposal_deadline": raw.get("제안서 마감일"),
        "raw_expected_close_date": raw.get("수주 예정일"),
        "is_strategic_customer": is_strat,
        # 4번 placeholder (organization 테이블 join은 v2)
        "past_deal_count": 0,
        "reference_signal": False,
        "extension_signal": False,
        # 5번 placeholder — db 모드는 슬랙·견적서 없음 (스킬 3·5에서 채움)
        "slack_thread_count_14d": 0,
        "last_quote_sheet_updated_days": None,
        # 6번 LLM 영역 — placeholder
        "intent_signals": [],
        "intent_category": None,
        "intent_grounding_ld": intent_grounding,
        # 7번 — 잠정 (LLM이 정밀화)
        "deal_origin": None,
        # raw text
        "memo_text": memo_text,
        "memo_count": len(memos),
        "slack_raw": "",
        # 메타
        "_source": "db",
    }


# ════════════════════════════════════════════
# Skills 모드 — 스킬 1~5 결과 통합 (운영 시)
# ════════════════════════════════════════════


def merge_from_skills(
    skill1: dict,
    skill2: dict,
    skill3: dict,
    skill4: dict,
    skill5: dict,
    today: date,
    settings: dict,
) -> list[dict]:
    """스킬 1~5 결과 통합 → 액티브 게이트 적용 → normalize."""
    extract_fields = settings["data_sources"]["salesmap_extract_fields"]
    # 액티브 게이트 차단 enum (4/30 EOD: LOST + 확정/Won)
    grounding_exclude = settings["scoring"].get("intent_grounding_exclude", ["LOST"])
    grounding_field = extract_fields["intent_grounding_field"]

    deals = skill1.get("deals", skill1 if isinstance(skill1, list) else [])

    results = []
    failed = []
    for d in deals:
        try:
            stage = d.get("pipeline_stage_name") or extract_json_name(d.get("파이프라인 단계")) or ""

            # 액티브 게이트 — 단계
            if not any(p in stage for p in ACTIVE_STAGE_PATTERNS):
                continue

            # 액티브 게이트 — grounding ∉ exclude
            if grounding_exclude:
                grounding = extract_json_array_first(d.get(grounding_field) or d.get("성사 가능성"))
                if grounding in grounding_exclude:
                    continue

            normalized = normalize_deal_from_skills(d, skill2, skill3, skill4, skill5, today, settings)
            results.append(normalized)
        except Exception as e:
            failed.append({"deal_id": d.get("id", "unknown"), "reason": str(e)})

    if failed:
        summarize_partial_failures(len(results), failed, "phase1")

    log_gate("phase1", "pass", {
        "kind": "merge_from_skills",
        "active_count": len(results),
        "failed": len(failed),
    })
    return results


def normalize_deal_from_skills(
    d: dict, skill2: dict, skill3: dict, skill4: dict, skill5: dict,
    today: date, settings: dict,
) -> dict:
    """스킬 결과 통합 normalize."""
    extract_fields = settings["data_sources"]["salesmap_extract_fields"]
    keywords = settings["scoring"].get("strategic_keywords", []) or []

    customer_name = d.get("customer_name") or d.get("org_name") or d.get("이름") or ""

    # 1번
    amount = amount_won_to_eok(
        d.get(extract_fields["amount_field"]) or d.get("예상 체결액") or d.get("amount")
    )
    net_raw = d.get(extract_fields.get("amount_secondary")) or d.get("Net(%)")
    try:
        net_pct = float(net_raw) if net_raw else None
    except (ValueError, TypeError):
        net_pct = None

    # 2번
    stage_name = (
        d.get("pipeline_stage_name")
        or extract_json_name(d.get("파이프라인 단계"))
        or d.get("pipeline_stage")
    )

    # 3번 deadline 1·2차
    deadline = None
    deadline_source = None
    for col in extract_fields.get("deadline_priority", ["제안서 마감일", "수주 예정일"]):
        val = d.get(col)
        if val:
            dt = parse_date(val)
            if dt:
                deadline = dt.isoformat()
                deadline_source = f"from_field:{col}"
                break

    # 5번 communication 두 축
    slack_info = (skill3 or {}).get(customer_name, {}) if isinstance(skill3, dict) else {}
    slack_count = slack_info.get("thread_count_14d", 0) if isinstance(slack_info, dict) else 0
    slack_raw = slack_info.get("raw", "") if isinstance(slack_info, dict) else ""

    drive_info = (skill5 or {}).get(customer_name, {}) if isinstance(skill5, dict) else {}
    quote_recent = drive_info.get("last_quote_sheet_updated_days") if isinstance(drive_info, dict) else None

    # 6번 grounding
    intent_grounding = extract_json_array_first(
        d.get(extract_fields["intent_grounding_field"]) or d.get("성사 가능성")
    )

    return {
        "id": d.get("id") or d.get("deal_id"),
        "deal_name": d.get("deal_name") or d.get("이름"),
        "customer_name": customer_name,
        "deal_amount": amount,
        "net_pct": net_pct,
        "pipeline_stage_name": stage_name,
        "deadline": deadline,
        "deadline_source": deadline_source,
        "is_strategic_customer": is_strategic(customer_name, keywords),
        "past_deal_count": d.get("past_deal_count", 0) or 0,
        "reference_signal": d.get("reference_signal", False),
        "extension_signal": d.get("extension_signal", False),
        "slack_thread_count_14d": slack_count,
        "last_quote_sheet_updated_days": quote_recent,
        "intent_signals": d.get("intent_signals", []),
        "intent_category": d.get("intent_category"),
        "intent_grounding_ld": intent_grounding,
        "deal_origin": d.get("deal_origin"),
        "memo_text": d.get("memo_text", ""),
        "slack_raw": slack_raw,
        "_source": "skills",
    }


# ════════════════════════════════════════════
# 진입점
# ════════════════════════════════════════════


def main() -> int:
    parser = argparse.ArgumentParser(description="merge_deals (4/30 v1) — 두 모드 지원")
    parser.add_argument("--mode", choices=["skills", "db"], default="skills",
                        help="skills(운영) | db(검증)")
    parser.add_argument("--skill1", default=None)
    parser.add_argument("--skill2", default=None)
    parser.add_argument("--skill3", default=None)
    parser.add_argument("--skill4", default=None)
    parser.add_argument("--skill5", default=None)
    parser.add_argument("--today", default=None, help="기준 날짜 (YYYY-MM-DD)")
    parser.add_argument("--settings", default="config/settings.json")
    parser.add_argument("-o", "--output", default=None)
    args = parser.parse_args()

    today = parse_date(args.today) if args.today else get_today()
    settings = safe_load_json(args.settings, default={})

    if not settings:
        print(f"settings 로드 실패: {args.settings}", file=sys.stderr)
        return 1

    # phase 시작 anchor
    log_gate("phase1", "pass", {
        "kind": "phase1_start",
        "mode": args.mode,
        "today": str(today),
    })

    if args.mode == "db":
        try:
            deals = query_active_deals_from_db(settings, today)
        except ValidationError as e:
            print(f"DB 모드 실패: {e}", file=sys.stderr)
            return 1
    else:
        skill1 = safe_load_json(args.skill1, default={"deals": []}) if args.skill1 else {"deals": []}
        skill2 = safe_load_json(args.skill2, default={}) if args.skill2 else {}
        skill3 = safe_load_json(args.skill3, default={}) if args.skill3 else {}
        skill4 = safe_load_json(args.skill4, default={}) if args.skill4 else {}
        skill5 = safe_load_json(args.skill5, default={}) if args.skill5 else {}
        deals = merge_from_skills(skill1, skill2, skill3, skill4, skill5, today, settings)

    # 결과 저장
    output_path = args.output or str(runtime_path("phase2_active_deals.json"))
    safe_save_json(output_path, deals)
    print(f"\n병합 완료: {len(deals)}건 → {output_path}", file=sys.stderr)

    # 요약
    if deals:
        print(f"  단계 분포:", file=sys.stderr)
        from collections import Counter
        stages = Counter(d.get("pipeline_stage_name", "?") for d in deals)
        for s, c in stages.most_common():
            print(f"    {s}: {c}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
