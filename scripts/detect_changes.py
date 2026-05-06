"""
Phase 4a: 변화 추적 (ld-deal-plugin SQL 전용)
이전 스냅샷 vs 현재 스냅샷을 비교하여 변화를 감지한다.

기존 `deal-priority-plugin`과의 차이:
- `schedule_changed` (교육일정 변경) 제거 — education_* 필드 자체 없음
- `removed` 딜의 현재 상태(Won/Lost/Convert)를 세일즈맵 DB에서 조회 → "수주 성공" 별도 표시

감지 항목:
  1. removed — SQL에서 사라진 딜 (Won/Lost/Convert 구분)
  2. added — 신규 SQL 딜 등장
  3. stage_changed — 파이프라인 단계 변경

사용법:
  python detect_changes.py archive/prev.json archive/curr.json [--db <db_path>]
  python detect_changes.py --latest archive/ [--db <db_path>]
  python detect_changes.py --week archive [days] [--db <db_path>]
"""

import json
import sys
import io
import os
import glob
import sqlite3
from datetime import datetime, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def find_latest_two(archive_dir):
    """archive 폴더에서 가장 최근 2개 스냅샷 찾기"""
    files = sorted(glob.glob(os.path.join(archive_dir, "*.json")))
    if len(files) < 2:
        return None, files[-1] if files else None
    return files[-2], files[-1]


def build_deal_map(deals):
    """deal_id → deal 딕셔너리"""
    return {d["deal_id"]: d for d in deals}


def resolve_removed_status(removed_ids, db_path):
    """removed 딜 ID들의 현재 상태(Won/Lost/Convert)를 DB에서 조회"""
    if not db_path or not os.path.exists(db_path) or not removed_ids:
        return {}
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        placeholders = ",".join("?" for _ in removed_ids)
        cursor.execute(
            f'SELECT id, "상태" FROM deal WHERE id IN ({placeholders})',
            list(removed_ids)
        )
        result = {row[0]: row[1] for row in cursor.fetchall()}
        conn.close()
        return result
    except Exception as e:
        print(f"DB 상태 조회 실패: {e}", file=sys.stderr)
        return {}


def detect(prev_deals, curr_deals, db_path=None):
    """두 스냅샷 비교 → 변화 감지 (SQL 전용)"""
    prev_map = build_deal_map(prev_deals)
    curr_map = build_deal_map(curr_deals)

    prev_ids = set(prev_map.keys())
    curr_ids = set(curr_map.keys())

    changes = {
        "removed": [],       # 1. SQL에서 사라진 딜 (Won/Lost/Convert 구분)
        "added": [],         # 2. 신규 SQL 딜
        "stage_changed": [], # 3. 파이프라인 단계 변경
    }

    # 1. 사라진 딜 — 현재 상태 조회로 Won/Lost 구분
    removed_ids = prev_ids - curr_ids
    current_statuses = resolve_removed_status(removed_ids, db_path)

    for did in removed_ids:
        d = prev_map[did]
        current_status = current_statuses.get(did, "unknown")

        if current_status == "Won":
            reason = "🎉 수주 성공 (SQL → Won)"
            category = "won"
        elif current_status == "Lost":
            reason = "⚠️ Lost (수주 실패)"
            category = "lost"
        elif current_status == "Convert":
            reason = "↪️ Convert (다른 딜로 전환)"
            category = "convert"
        else:
            reason = "❓ SQL에서 사라짐 (상태 불명)"
            category = "unknown"

        changes["removed"].append({
            "deal_id": did,
            "deal_name": d.get("deal_name", ""),
            "customer_name": d.get("customer_name", ""),
            "prev_status": "SQL",
            "current_status": current_status,
            "category": category,
            "amount": d.get("amount"),
            "reason": reason
        })

    # 2. 신규 등장
    for did in curr_ids - prev_ids:
        d = curr_map[did]
        changes["added"].append({
            "deal_id": did,
            "deal_name": d.get("deal_name", ""),
            "customer_name": d.get("customer_name", ""),
            "amount": d.get("amount"),
            "pipeline_stage": d.get("pipeline_stage", ""),
            "reason": "➕ 신규 SQL 딜"
        })

    # 3. 파이프라인 단계 변경 (공통 딜 내 비교)
    for did in prev_ids & curr_ids:
        prev = prev_map[did]
        curr = curr_map[did]

        pp = prev.get("pipeline_stage", "")
        cp = curr.get("pipeline_stage", "")
        if pp and cp and pp != cp:
            changes["stage_changed"].append({
                "deal_id": did,
                "deal_name": curr.get("deal_name", ""),
                "customer_name": curr.get("customer_name", ""),
                "from_stage": pp,
                "to_stage": cp,
                "reason": f"📈 단계 변경: {pp} → {cp}"
            })

    # 요약
    won_count = sum(1 for r in changes["removed"] if r["category"] == "won")
    lost_count = sum(1 for r in changes["removed"] if r["category"] == "lost")
    unknown_count = sum(1 for r in changes["removed"] if r["category"] in ("convert", "unknown"))

    summary = {
        "total_changes": sum(len(v) for v in changes.values()),
        "added_count": len(changes["added"]),
        "removed_count": len(changes["removed"]),
        "won_count": won_count,
        "lost_count": lost_count,
        "other_removed_count": unknown_count,
        "stage_changed_count": len(changes["stage_changed"]),
    }

    return {"summary": summary, "changes": changes}


def detect_week(archive_dir, days=7, db_path=None):
    """최근 N일치 스냅샷을 순차 비교하여 날짜별 변화 수집"""
    files = sorted(glob.glob(os.path.join(archive_dir, "*.json")))
    if not files:
        return {"mode": "week", "total_changes": 0, "first_run": True, "daily": []}

    def file_date(f):
        base = os.path.basename(f).replace(".json", "")
        try:
            return datetime.strptime(base, "%Y%m%d").date()
        except ValueError:
            return None

    dated_files = [(f, file_date(f)) for f in files if file_date(f)]
    dated_files.sort(key=lambda x: x[1])

    today = datetime.now().date()
    cutoff = today - timedelta(days=days)

    recent = [(f, d) for f, d in dated_files if d >= cutoff]
    before_cutoff = [(f, d) for f, d in dated_files if d < cutoff]

    if before_cutoff:
        recent = [before_cutoff[-1]] + recent

    if len(recent) < 2:
        return {"mode": "week", "total_changes": 0, "daily": []}

    daily = []
    total = 0
    for i in range(len(recent) - 1):
        prev_f, prev_d = recent[i]
        curr_f, curr_d = recent[i + 1]
        prev_deals = load_json(prev_f)
        curr_deals = load_json(curr_f)
        result = detect(prev_deals, curr_deals, db_path=db_path)
        if result["summary"]["total_changes"] > 0:
            daily.append({
                "date": str(curr_d),
                "label": "오늘" if curr_d == today else str(curr_d),
                "summary": result["summary"],
                "changes": result["changes"],
            })
            total += result["summary"]["total_changes"]

    # 7일 누적 요약
    cum_added = sum(d["summary"]["added_count"] for d in daily)
    cum_won = sum(d["summary"]["won_count"] for d in daily)
    cum_lost = sum(d["summary"]["lost_count"] for d in daily)
    cum_stage = sum(d["summary"]["stage_changed_count"] for d in daily)

    return {
        "mode": "week",
        "total_changes": total,
        "days_covered": days,
        "cumulative": {
            "added": cum_added,
            "won": cum_won,
            "lost": cum_lost,
            "stage_changed": cum_stage,
        },
        "daily": daily,
    }


def main():
    import argparse

    parser = argparse.ArgumentParser(description="변화 감지 (SQL 전용)")
    parser.add_argument("input1", nargs="?", help="이전 스냅샷 또는 --latest/--week 모드 인자")
    parser.add_argument("input2", nargs="?", help="현재 스냅샷 또는 days 수")
    parser.add_argument("--latest", action="store_true", help="archive 폴더에서 가장 최근 2개 자동 비교")
    parser.add_argument("--week", action="store_true", help="최근 N일치 순차 비교")
    parser.add_argument("--db", default=None, help="세일즈맵 DB 경로 (removed 딜 상태 조회용)")
    args = parser.parse_args()

    if args.week:
        archive_dir = args.input1 or "archive"
        days = int(args.input2) if args.input2 and args.input2.isdigit() else 7
        result = detect_week(archive_dir, days=days, db_path=args.db)
        print(
            f"주간 변화 감지: {result['total_changes']}건 ({len(result.get('daily', []))}일)",
            file=sys.stderr
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        sys.exit(0)

    if args.latest:
        archive_dir = args.input1 or "archive"
        prev_path, curr_path = find_latest_two(archive_dir)
        if not prev_path:
            print("첫 실행입니다 — 비교할 이전 스냅샷이 없습니다.", file=sys.stderr)
            result = {"summary": {"total_changes": 0, "first_run": True}, "changes": {}}
            print(json.dumps(result, ensure_ascii=False, indent=2))
            sys.exit(0)
        print(f"비교: {os.path.basename(prev_path)} → {os.path.basename(curr_path)}", file=sys.stderr)
    else:
        prev_path = args.input1
        curr_path = args.input2
        if not prev_path or not curr_path:
            parser.print_help()
            sys.exit(1)

    prev_deals = load_json(prev_path)
    curr_deals = load_json(curr_path)

    result = detect(prev_deals, curr_deals, db_path=args.db)

    # 요약 출력
    s = result["summary"]
    print(f"변화 감지 완료: 총 {s['total_changes']}건", file=sys.stderr)
    if s["added_count"]:
        print(f"  신규 SQL: {s['added_count']}건", file=sys.stderr)
    if s["removed_count"]:
        print(f"  사라진 딜: {s['removed_count']}건", file=sys.stderr)
        if s["won_count"]:
            print(f"    - 🎉 수주 성공: {s['won_count']}건", file=sys.stderr)
        if s["lost_count"]:
            print(f"    - ⚠️ Lost: {s['lost_count']}건", file=sys.stderr)
        if s["other_removed_count"]:
            print(f"    - 기타/불명: {s['other_removed_count']}건", file=sys.stderr)
    if s["stage_changed_count"]:
        print(f"  단계 변경: {s['stage_changed_count']}건", file=sys.stderr)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
