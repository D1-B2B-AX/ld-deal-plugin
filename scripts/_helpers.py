"""
ld-deal-plugin 헬퍼 모듈 (4/30 신설)

재현성·LD 친화 메시지·JSON I/O 유틸.

설계 원칙 (4/30 빌더 합의 — 7원칙 중 ②·⑤·⑦ 담당):
- ② JSON 흐름: safe_load_json·safe_save_json (인코딩 안전, 부모폴더 자동 생성)
- ⑤ 재현성: get_today() 환경변수 mock 가능 → 테스트·디버깅 시 날짜 고정
- ⑦ LD 친화 메시지: format_user_message()로 dev kind를 LD가 이해 가능한 문장으로 변환

관련 문서: docs/_builder/_validation_design.md
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# KST = 한국 표준시 (메모리 feedback_timezone 정합)
KST = timezone(timedelta(hours=9))

# 환경변수 이름 — 테스트·재현성용 today override
ENV_TODAY_OVERRIDE = "LD_DEAL_TODAY_OVERRIDE"


# ============================================================================
# 1. 재현성 — 날짜 헬퍼 (⑤)
# ============================================================================


def get_today(override: str | None = None) -> date:
    """
    오늘 날짜 반환 (KST 기준).

    우선순위:
    1. 인자 override (예: "2026-04-30")
    2. 환경변수 LD_DEAL_TODAY_OVERRIDE
    3. 시스템 현재 시각 (KST)

    예시:
        # 운영
        today = get_today()
        # 테스트·디버깅
        os.environ["LD_DEAL_TODAY_OVERRIDE"] = "2026-04-15"
        today = get_today()  # date(2026, 4, 15)

    Why: 마감 임박도 등 today 의존 점수가 코드 변경 없이도 매일 달라지면 디버깅 X.
         override로 고정하면 재현 가능 + 테스트 가능 + 산출물 상단에 "기준일자: ..." 명시 가능.
    """
    raw = override if override is not None else os.environ.get(ENV_TODAY_OVERRIDE)
    if raw and raw.strip():
        return date.fromisoformat(raw.strip())
    return datetime.now(KST).date()


def get_today_iso(override: str | None = None) -> str:
    """get_today()를 ISO 문자열로. 산출물 헤더 표기·로그용."""
    return get_today(override).isoformat()


# ============================================================================
# 2. LD 친화 메시지 (⑦)
# ============================================================================

# 개발 로그의 'kind' 키 → LD가 이해 가능한 한국어 문장
# 추가 kind는 _validation.py log_gate() 호출 시 사용한 kind 그대로 키로 등록
_LD_MESSAGES: dict[str, str] = {
    "llm_enum_invalid": (
        "이 딜의 거래 의지 판단에 어려움이 있어 중립값으로 처리했습니다. "
        "슬랙 raw가 짧거나 모호한 표현이 많을 때 발생합니다."
    ),
    "missing_required_keys": (
        "이 딜의 필수 데이터가 일부 누락되어 점수 산정에서 제외했습니다."
    ),
    "score_out_of_range": (
        "이 딜의 점수 계산 중 비정상 값이 감지되어 결과에서 제외했습니다. "
        "원인 데이터 확인이 필요합니다."
    ),
    "data_source_unavailable": (
        "데이터 소스 일부에 접근할 수 없어 부분 결과로 진행했습니다."
    ),
    "partial_failure_summary": (
        "전체 딜 중 일부가 정상 산정되지 못해 폴백값으로 처리되었습니다. "
        "해당 딜은 결과 표 끝에 별도 표기됩니다."
    ),
    # 4/30 결손 매트릭스 신규 4건
    "slack_and_quote_silent": (
        "이 딜은 슬랙·견적시트 활동이 없어 5번 소통 점수가 중립값으로 처리되었습니다."
    ),
    "deadline_missing": (
        "이 딜은 마감일 정보가 없어 3번 임박도 점수에 반영되지 않았습니다. "
        "세일즈맵의 '제안서 마감일' 또는 '수주 예정일'에 입력하면 자동 반영됩니다."
    ),
    "intent_raw_insufficient": (
        "이 딜의 거래 의지 판단 근거가 부족해 중립값으로 처리했습니다. "
        "슬랙 대화 raw 보강이 도움됩니다."
    ),
    "deal_origin_missing": (
        "이 딜은 출발점 정보가 없어 7번 점수에 반영되지 않았습니다. "
        "세일즈맵의 '소스' 필드에 입력하면 자동 반영됩니다."
    ),
}

# 미정의 kind 발생 시 안전한 기본 메시지 (LD 노출 가능)
_LD_DEFAULT_MESSAGE = (
    "처리 중 일부 항목에서 예상치 못한 상태가 감지되었습니다. "
    "상세 내역은 빌더가 검토할 예정입니다."
)


def format_user_message(dev_kind: str) -> str:
    """
    개발 로그의 'kind' 키를 LD 친화 메시지로 변환.

    예시:
        format_user_message("llm_enum_invalid")
        -> "이 딜의 거래 의지 판단에 어려움이 있어 중립값으로 처리했습니다. ..."

    미정의 kind는 안전한 기본 메시지 반환 (LD 노출 OK, 빌더가 추후 _LD_MESSAGES에 추가).
    """
    return _LD_MESSAGES.get(dev_kind, _LD_DEFAULT_MESSAGE)


def collect_ld_messages(log_entries: list[dict]) -> list[str]:
    """
    _validation_log.json의 entries 중 status="warn"|"fail" 항목을 LD 메시지 목록으로 변환.

    중복 제거 (같은 kind 여러 번 발생해도 메시지 1번만 노출).

    예시:
        entries = load_validation_log()
        messages = collect_ld_messages(entries)
        # 산출물 MD 끝에 messages 노출
    """
    seen: set[str] = set()
    messages: list[str] = []
    for entry in log_entries:
        if entry.get("status") not in ("warn", "fail"):
            continue
        kind = entry.get("details", {}).get("kind")
        if not kind or kind in seen:
            continue
        seen.add(kind)
        messages.append(format_user_message(kind))
    return messages


# ============================================================================
# 3. JSON I/O 안전 유틸 (②)
# ============================================================================


def safe_load_json(path: str | Path, default: Any = None) -> Any:
    """
    JSON 파일 읽기. 미존재·파싱 실패 시 default 반환.
    UTF-8 BOM 자동 처리 (utf-8-sig 인코딩).

    예시:
        deals_data = safe_load_json("runtime/phase1_merged_deals.json", default={"deals": []})
    """
    p = Path(path)
    if not p.exists():
        return default
    try:
        text = p.read_text(encoding="utf-8-sig")  # BOM 자동 제거
        return json.loads(text)
    except (json.JSONDecodeError, OSError):
        return default


def safe_save_json(path: str | Path, data: Any) -> None:
    """
    JSON 파일 쓰기. 부모 폴더 자동 생성. UTF-8 (BOM 없음).

    한글 보존: ensure_ascii=False
    가독성: indent=2
    date·datetime 등 직렬화 불가 객체: default=str (str() 변환)

    예시:
        safe_save_json("runtime/phase2_active_deals.json", {"deals": filtered, "today": today})
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


# ============================================================================
# 4. 경로 유틸 (runtime/ 폴더 진입점)
# ============================================================================


def runtime_path(*parts: str) -> Path:
    """
    runtime/ 하위 경로 생성. 부모 폴더 자동 생성하지 않음 (호출 측 결정).

    예시:
        runtime_path("phase1_merged_deals.json")  # Path("runtime/phase1_merged_deals.json")
        runtime_path("state", "deal_overrides.json")
    """
    return Path("runtime").joinpath(*parts)
