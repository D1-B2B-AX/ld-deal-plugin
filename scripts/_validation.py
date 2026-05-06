"""
ld-deal-plugin Phase 검증 모듈 (4/30 신설)

각 Phase 경계(0→1→2→3→3.5→4)에서 입력·출력 검증 + 게이트 로깅.

설계 원칙 (4/30 빌더 합의 — 7원칙 중 ①·⑥ 담당):
- ① 검증 게이트: validate_schema·assert_in_range로 Phase 입출력 사전 차단
- ⑥ 부분 실패: assert_llm_enum이 LLM enum 위반 시 폴백 반환 (전체 파이프라인 X 멈춤)
- 모든 게이트 결과는 runtime/_validation_log.json에 append-only로 누적
- ValidationError = 빌더 디버깅용 풀 디테일 / LD 친화 메시지는 _helpers.format_user_message()로 변환

관련 문서: docs/_builder/_validation_design.md
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# KST = 한국 표준시. 모든 timestamp는 KST 기준 (메모리 feedback_timezone 정합)
KST = timezone(timedelta(hours=9))

# 게이트 로그 기본 경로 (호출 측에서 override 가능)
DEFAULT_LOG_PATH = Path("runtime") / "_validation_log.json"


class ValidationError(Exception):
    """
    Phase 경계 검증 실패. 빌더 디버깅용 풀 디테일 포함.

    LD 산출물에는 이 예외를 직접 노출하지 말고 _helpers.format_user_message()로 변환할 것.
    """

    def __init__(self, phase: str, reason: str, details: dict | None = None):
        self.phase = phase
        self.reason = reason
        self.details = details or {}
        super().__init__(f"[{phase}] {reason}")


@dataclass
class GateResult:
    """검증 게이트 결과 (성공·실패 무관 — 누적 로깅용)."""

    phase: str
    status: str  # "pass" | "fail" | "warn"
    details: dict


# ============================================================================
# 1. 스키마 검증 (입력·출력 dict의 필수 키 확인)
# ============================================================================


def validate_schema(
    data: Any,
    required_keys: list[str],
    phase: str,
    optional_keys: list[str] | None = None,
) -> None:
    """
    딕셔너리에 필수 키가 모두 있는지 검증.

    Raises:
        ValidationError: data가 dict가 아니거나, required_keys 중 누락 있을 때.

    예시:
        validate_schema(deal, ["deal_id", "amount", "stage"], phase="phase2")
    """
    if not isinstance(data, dict):
        raise ValidationError(
            phase,
            f"expected dict, got {type(data).__name__}",
            {"data_type": type(data).__name__},
        )
    missing = [k for k in required_keys if k not in data]
    if missing:
        raise ValidationError(
            phase,
            f"missing required keys: {missing}",
            {"missing": missing, "present": list(data.keys())},
        )


# ============================================================================
# 2. 범위 검증 (점수·비율 등 수치)
# ============================================================================


def assert_in_range(
    value: Any,
    lo: float,
    hi: float,
    label: str,
    phase: str,
) -> None:
    """
    수치가 [lo, hi] 범위 안인지 검증. 점수·비율 등에 사용.

    예시:
        assert_in_range(deal_score, 0, 170, "deal_score", phase="phase3")
        assert_in_range(active_count, 0, 100, "active_deals", phase="phase2")
    """
    if not isinstance(value, (int, float)):
        raise ValidationError(
            phase,
            f"{label}: expected number, got {type(value).__name__}",
            {"label": label, "value_type": type(value).__name__},
        )
    if value < lo or value > hi:
        raise ValidationError(
            phase,
            f"{label}={value} out of range [{lo}, {hi}]",
            {"label": label, "value": value, "lo": lo, "hi": hi},
        )


# ============================================================================
# 3. LLM 출력 enum 검증 + 폴백 처리 (⑥ 부분 실패 핵심)
# ============================================================================


def assert_llm_enum(
    output_value: Any,
    allowed_values: list[Any],
    phase: str,
    fallback_value: Any,
    label: str = "llm_output",
    deal_id: str | None = None,
    log_path: str | Path | None = None,
) -> tuple[Any, bool]:
    """
    LLM 출력이 enum 안에 있는지 검증.

    실패 시: 폴백 값 반환 + warn 로그 (전체 파이프라인 X 멈춤).
    8개 딜 중 1개 LLM 실패해도 나머지 7개 결과는 정상 산출.

    Returns:
        (final_value, was_fallback): 최종 값 + 폴백 여부.

    예시:
        category, was_fallback = assert_llm_enum(
            llm_response.get("category"),
            ["high", "mid", "low"],
            phase="phase3.5",
            fallback_value="mid",
            deal_id="DEAL-007",
        )
        if was_fallback:
            deal["_llm_fallback"] = True  # 산출물에 표시할 플래그
    """
    if output_value in allowed_values:
        return output_value, False

    # 폴백 처리 — 부분 실패 로그
    log_gate(
        phase,
        "warn",
        {
            "kind": "llm_enum_invalid",
            "label": label,
            "got": output_value,
            "allowed": allowed_values,
            "fallback": fallback_value,
            "deal_id": deal_id,
        },
        log_path=log_path,
    )
    return fallback_value, True


# ============================================================================
# 4. 게이트 로그 (모든 검증 결과 append-only 누적)
# ============================================================================


def log_gate(
    phase: str,
    status: str,
    details: dict,
    log_path: str | Path | None = None,
) -> None:
    """
    게이트 통과/실패를 runtime/_validation_log.json에 append.

    log_path 미지정 시 기본 경로 (DEFAULT_LOG_PATH).
    파일이 손상돼 있으면 새로 시작 (백업 안 만듦 — runtime/은 매 실행 reset 권장).
    """
    target = Path(log_path) if log_path is not None else DEFAULT_LOG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)

    entries: list[dict] = []
    if target.exists():
        try:
            loaded = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                entries = loaded
        except (json.JSONDecodeError, OSError):
            entries = []

    entries.append(
        {
            "ts": datetime.now(KST).isoformat(),
            "phase": phase,
            "status": status,
            "details": details,
        }
    )

    target.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def reset_validation_log(log_path: str | Path | None = None) -> None:
    """
    실행 시작 시 이전 _validation_log.json 비우기.
    각 run의 게이트만 추적하도록 (Phase 0 시작 시 호출).
    """
    target = Path(log_path) if log_path is not None else DEFAULT_LOG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("[]", encoding="utf-8")


# ============================================================================
# 5. 부분 실패 요약 (산출물 노출용)
# ============================================================================


def summarize_partial_failures(
    success_count: int,
    failed_items: list[dict],
    phase: str,
    log_path: str | Path | None = None,
) -> dict:
    """
    부분 실패 요약 생성. failed_items = [{"deal_id": ..., "reason": ...}, ...] 형태.

    호출 측: 산출물(MD)에 이 요약을 노출 → LD에게 어떤 딜이 폴백 처리됐는지 알림.

    Returns:
        {"success": int, "failed": int, "fail_reasons": [...], "phase": str}
    """
    summary = {
        "success": success_count,
        "failed": len(failed_items),
        "fail_reasons": failed_items,
        "phase": phase,
    }
    log_gate(
        phase,
        "warn" if failed_items else "pass",
        {"kind": "partial_failure_summary", **summary},
        log_path=log_path,
    )
    return summary


# ============================================================================
# 6. 게이트 로그 조회 (산출물 생성 시 LD 친화 메시지 변환에 사용)
# ============================================================================


def load_validation_log(log_path: str | Path | None = None) -> list[dict]:
    """
    누적된 게이트 로그 조회. status="warn"·"fail" 항목을 LD 메시지로 변환할 때 사용.
    """
    target = Path(log_path) if log_path is not None else DEFAULT_LOG_PATH
    if not target.exists():
        return []
    try:
        loaded = json.loads(target.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, list) else []
    except (json.JSONDecodeError, OSError):
        return []
