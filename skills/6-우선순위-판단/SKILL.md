# 스킬 6: 우선순위 판단 (4/30 v1)

> 스킬 1~5에서 수집한 데이터를 *7기준 스코어링 + 거래 의지 LLM 판정 + 결과 reason/next_action 생성*으로 통합하는 핵심 스킬.

## 역할

이 플러그인의 **두뇌**. 데이터 수집(1~5) → 우선순위 결정 → 결과 출력(7) 사이의 처리 핵심.

### 4/30 v1 핵심 변경 (baseline 대비)
- **만점 170** (155 → 170)
- **T1 ≥ 87 / T2 ≥ 55** (80/50 → 87/55)
- **7기준 weights 갱신** (4/29 결정): 1번 30·2번 18·3번 22·4번 23·5번 27·6번 30·7번 20
- **6번 = 거래 의지** (이전 "이번 주 액션" 폐기 → LLM 카테고리 신호 C1·C2·C3)
- **3번 마감 = 3단계 트리** (제안서 마감일 → 수주 예정일 → LLM 추출)
- **LLM grounding** = `성사 가능성` 컬럼 비교 (4/30 신규)
- **액티브 게이트** = 단계 + `성사 가능성 ≠ LOST` (4/30 LOST 79% 차단)
- T1 후보 뱃지 ⏸ **폐기** (이중 가산 위험)
- 자연어 피드백 layer ⏸ **v2 영역** (4/30 MVP 제외)

## 인풋 (스킬 1~5 통합 결과)

| 출처 | 데이터 | 역할 |
|---|---|---|
| 스킬 1 (세일즈맵) | 딜 기본 정보 (`예상 체결액`·`수주 예정일`·`성사 가능성` 등) + memo 테이블 | **출발점** |
| 스킬 2 (캘린더) | 딜 관련 일정 | 보조 |
| **스킬 3 (슬랙) 14일** | `slack_results` — 최근 thread + 활동 빈도 | 5번 소통 점수 |
| **스킬 3 (슬랙) 2026-01-01부터** ⭐ 5/6 신규 | `slack_results_lead_history` — lead 배분 thread root 포함 영업 사이클 raw | **6번 거래 의지 LLM raw 메인** |
| 스킬 4 (지메일) | 2팀 공용 라벨 메일 | 보조 |
| 스킬 5 (드라이브) | 견적서 (최종 탭 net·총액·갱신일) + 기획문서 | 보조 (견적서=숫자 시그널) |

**5/6 신규 — 슬랙 두 영역 분리** (파트장 제안 옵션 A):
- 5번 소통 점수 영역과 6번 거래 의지 LLM raw 영역의 *윈도우 다름*
- 신세계 4/1 lead thread (5/6 기준 36일 전) 같은 영역이 14일 윈도우엔 누락 → 6번 LLM이 *최근 정체*만 보고 mid 판정 → 옵션 C로 흡수되지만 *진짜 raw 보고 정밀 판단* 가치 있음
- `slack_results_lead_history`는 **STEP 1 LLM raw 입력에 필수 포함** (아래 STEP 1 설명 참조)

## 처리 흐름 (4 STEP)

```
STEP 1 [LLM 1번]: 거래 의지 카테고리 판정 + 마감 추출 (per deal)
        입력: slack_raw + memo_text + 일부 deal 메타
        출력: intent_signals·intent_category·extracted_deadline
  ↓
STEP 2 [스크립트]: calculate_score.py 실행
        7기준 스코어 + 메인/서브 + 티어 + grounding 비교
  ↓
STEP 3 [스크립트]: verify_scores.py 실행
        6항목 검증 (4/30 갱신)
  ↓
STEP 4 [LLM 2번]: reason + next_action 일괄 생성 (티어별 톤)
```

---

## STEP 1: LLM 1번 — 거래 의지 + 마감 추출 (per deal)

### 입력 텍스트 결합 (deal별, 5/6 갱신 — lead_history 추가)

```
combined_raw = (
    slack_raw                                       # 14일 최근 활동
    + "\n---slack_lead_history---\n"
    + slack_lead_history_raw                        # ⭐ 5/6 신규: 2026-01-01부터 lead 배분 thread (영업 사이클 raw)
    + "\n---memo---\n"
    + memo_text                                     # memo 테이블 dealId 매칭 텍스트 결합
)
```

**왜 lead_history 별도 영역?** — 5번 소통 점수와 6번 거래 의지 LLM 영역 분리 (스킬 3 STEP 1 영역 분리 참조). 신세계 4/1 lead thread 같은 *영업 사이클 시작 시점* raw가 14일 윈도우엔 누락. 6번 LLM이 *진짜 적극도 판단* 가능하게 lead_history를 명시 입력으로 박음.

### 결손 매트릭스 ④ — raw 부족 시 LLM skip

```python
if count_lines(combined_raw) <= 2:
    # 결과를 직접 박음 (LLM 호출 X)
    output = {
        "intent_signals": [],
        "intent_category": "mid",     # 폴백
        "extracted_deadline": None,
        "_llm_fallback": True,
        "_fallback_reason": "raw_too_short",
    }
    log_gate("phase3.5", "warn", {
        "kind": "intent_raw_insufficient",
        "deal_id": deal["id"],
    })
```

### LLM 호출 룰

- **temperature = 0** (재현성 — 같은 raw에 같은 결과)
- **좁은 출력 schema 강제** (JSON only — 자유 텍스트 X)
- **시스템 프롬프트**:

```
당신은 B2B 영업 딜의 거래 의지를 판정하는 분석가입니다.
주어진 슬랙·memo raw에서 *3 카테고리의 시그널*을 식별하고, 종합 거래 의지를 판정하며, 추출 가능한 마감일이 있다면 추출하세요.

## 시그널 카테고리 (3종)

### C1 결정 시그널 (거래 임박)
- 키워드: "최종 확정", "계약서 작성", "발주서", "수주 결정", "입금 일자", "견적 확정"
- 의미: 거래 *직전 단계* 도달 — 실제 결정/실행이 임박

### C2 권한·예산 시그널 (의사결정 진척)
- 키워드: "재무 부서 협의", "임원 보고", "예산 확보", "결재 진행", "내부 보고용 자료", "전사 TF"
- 의미: 의사결정 권한자가 *움직이기 시작*

### C3 활동·관심 시그널 (대화 활성)
- 키워드: "급하게", "ASAP", "운영매니저 배정", "추가 자료 요청", "유사 기업 제안서", "확장안", "후속 교육"
- 의미: 고객이 *적극 관여* 중

## 종합 판정 (intent_category)

- **high**: C1 발견 또는 C2 다수 + C3 활성 → 거래 의지 강함
- **mid**: C2 또는 C3 일부 → 거래 의지 보통
- **low**: 시그널 부재 또는 불확실 톤 → 거래 의지 약함

## 마감 추출 (extracted_deadline)

raw에서 마감 관련 키워드 발견 시 ISO 날짜로 추출:
- "제출 마감", "전달 마감", "전달 예정", "납기", "마감일", "deadline" 등
- 발견 못 하면 null

## 출력 형식 (JSON 단일 객체 — 자유 텍스트 X)

{
  "intent_signals": ["C1", "C2"],          // 발견된 카테고리 리스트 (없으면 [])
  "intent_category": "high",                // "high"|"mid"|"low"
  "extracted_deadline": "2026-05-15",      // ISO 날짜 또는 null
  "evidence": {                             // 근거 발췌 (간결 — 30자 이내)
    "C1": "최종 확정 5/15",
    "C2": "재무 부서 협의 중"
  }
}
```

### 출력 검증 (assert_llm_enum)

```python
from scripts._validation import assert_llm_enum, log_gate

# intent_category 검증
category, was_fallback = assert_llm_enum(
    output.get("intent_category"),
    ["high", "mid", "low"],
    phase="phase3.5",
    fallback_value="mid",
    deal_id=deal["id"],
)

# intent_signals 검증 (각 항목 enum)
valid_signals = ["C1", "C2", "C3"]
signals = [s for s in (output.get("intent_signals") or []) if s in valid_signals]

# extracted_deadline 검증 (ISO 날짜 또는 null)
deadline = output.get("extracted_deadline")
if deadline and not is_iso_date(deadline):
    deadline = None
```

### deal에 결과 박기

```python
deal["intent_signals"] = signals
deal["intent_category"] = category
deal["llm_extracted_deadline"] = deadline
deal["intent_evidence"] = output.get("evidence", {})
deal["_llm_fallback"] = was_fallback or output.get("_llm_fallback", False)
```

### 부분 실패 처리

1개 딜 LLM 실패해도 나머지는 정상 진행:
- 호출 자체 실패 (네트워크·타임아웃) → mid 폴백 + warn 로그
- 출력 schema 위반 → mid 폴백 + warn 로그
- 모든 deals 처리 후 `summarize_partial_failures()` 호출

---

## STEP 2: 3번 마감 deadline 결정 (3단계 트리)

calculate_score.py 호출 *직전* deal["deadline"] 결정:

```python
def get_deadline(deal):
    # 1차: 제안서 마감일 (현재 0% 채움이지만 향후 LD 입력 시 자동 활용)
    if deal.get("제안서 마감일"):
        return deal["제안서 마감일"], "from_proposal_field"

    # 2차: 수주 예정일 (백업, 100% 채움)
    base = deal.get("수주 예정일")

    # 3차: STEP 1 LLM 추출 결과 (실시간 보강)
    extracted = deal.get("llm_extracted_deadline")

    # 결합: 둘 다 있으면 더 임박한 쪽 (보수적)
    if extracted and base:
        return min(extracted, base), "merged_extract_and_base"
    return (extracted or base), ("from_llm" if extracted else ("from_field" if base else None))
```

deal["deadline"] + deal["deadline_source"] 박은 후 calculate_score.py 호출.

---

## STEP 3: calculate_score.py + verify_scores.py 실행

```bash
python scripts/calculate_score.py runtime/phase2_active_deals.json --settings config/settings.json
python scripts/verify_scores.py runtime/phase3_scored_deals.json --settings config/settings.json
```

각 스크립트 내부 처리:
- **calculate_score.py** — 7기준 스코어 + 메인(1·6번)/서브 분리 + 티어 + **`성사 가능성` grounding 비교**
- **verify_scores.py** — 6항목 검증 (누락·합산·범위·**만점 170 일치**·**티어 87/55**·중복)

### grounding 비교 매트릭스 (5/6 옵션 C — 비대칭 1단계 흡수)

LLM 결과(high/mid/low)와 LD 입력 `성사 가능성`(확정·높음·낮음·LOST) 비교 룰:

| LD 입력 | LLM high | LLM mid | LLM low |
|---|---|---|---|
| **확정** | ✅ match | ✅ match | ⚠️ mismatch (검토 권장) |
| **높음** | ✅ match | ✅ match | ⚠️ mismatch (검토 권장) |
| **낮음** | ⚠️ mismatch (검토 권장) | ✅ match | ✅ match |
| LOST | (액티브 게이트 차단 — 여기엔 안 옴) | | |

**룰의 본질** (LD 진실 우선):
- 한 단계 차이(예: LD 높음 vs LLM mid)는 **match로 흡수** — LD가 직접 매긴 값을 의심 시그널로 띄우지 않음
- 두 단계 차이(LD 적극 vs LLM 보수, 또는 LD 보수 vs LLM 적극)만 **검토 권장** — 진짜 갭 케이스만 시그널화
- mid는 *애매한 영역*이라 LD enum 3종(확정·높음·낮음) 모두 흡수

**검토 권장 케이스 활용** (STEP 4 reason):
- LD 확정·높음 vs LLM low → "LLM이 보수적 판단 — 슬랙·memo raw에 진척 부재 시그널 박혀있나? 재검토 권장."
- LD 낮음 vs LLM high → "LLM이 적극적 판단 — raw에 LD가 모르는 진척 시그널 박혀있나? 재검토 권장."

→ 매핑 dict는 `scripts/calculate_score.py:check_grounding_match()`에 박힘.

---

## STEP 4: LLM 2번 — reason + next_action 일괄 생성

### 길이·톤 원칙

| 티어 | 길이 | 구성 |
|---|---|---|
| **T1** (≥87) | 최대 3줄 | 핵심 팩트 2~3개 + 비즈니스 임팩트 판단 |
| **T2** (≥55) | 최대 2줄 | 현재 상태 + 병목/집중 포인트 |
| **T3** (<55) | 1줄 | 간결 상태 (지켜보기 카테고리) |

### LLM 프롬프트 (4/30 갱신)

```
당신은 B2B 영업 딜 우선순위 리포트를 작성하는 어시스턴트입니다.
scored_deals.json 전체를 읽고 N건의 reason + next_action을 한 응답으로 일괄 반환하세요.

## reason 작성 원칙

1. **LD 관점의 비즈니스 판단** — 데이터 시그널(슬랙·memo·견적서·메일) 반영. 개인 배경 X.

2. **뱃지·태그 정보 반복 금지** — 금액·D-day·거래건수·단계명은 카드 메타에 이미 있음.

3. **6번 grounding 활용 (4/30 신규)**:
   - `scoring.grounding_match == False` 케이스: "LD 본인 추정과 LLM 분석 결과 불일치 — 검토 권장" 톤 추가
   - 이런 딜은 *우선순위 검토 대상*으로 자연스럽게 강조

4. **티어별 톤:**

   **T1 reason (집중)** — "왜 지금 즉시 대응해야 하는가":
   - 길이: 최대 3줄
   - 구조: [핵심 팩트 2~3개] + [비즈니스 임팩트·모멘텀 판단]
   - 예:
     > 어제 LG측과 메일 교환으로 소통 활성, 5월 내 확장안 결정 예정.
     > 3개 사업장 확장 가능성 — 만족도 기반 후속 교육까지 이어지면 레퍼런스 가치 큰 딜.
     > 이번 주 내 제안 마무리 시 성사·확장 양쪽 모멘텀 확보.

   **T2 reason (관리)** — "무엇이 걸려 있고 왜 이번 주 중요한가":
   - 길이: 최대 2줄
   - 예: "Proposal 송부 후 고객 반응 대기. 강사 조건이 현재 병목. 이번 주 내 대안 제시 없으면 진전 어려움."

   **T3 reason (지켜보기)** — "현재 상태 한 줄":
   - 길이: 1줄
   - 예: "초기 단계, 2주 후 재확인" / "3주째 무응답, 경쟁 탈락 가능성"

5. **금지 표현:** 대화체("~죠"·"~네요") / 추측성("아마"·"~일 수도") / 뱃지 반복 / 주관 형용사("좋은"·"훌륭한")

## next_action 작성 원칙

- 구체 행동 + 기한/대상 포함
- 좋은 예: "5/2(목) 제안서 최종본 전달 + 확장안 사전 논의"
- 나쁜 예: "팔로업 필요" (추상)

## 출력 형식 (JSON 배열)

[
  {
    "deal_id": "019baba5-...",
    "reason": "...",
    "next_action": "..."
  },
  ...
]
```

---

## 결손 매트릭스 적용 (LD 산출물 노출)

각 시나리오 결손 발생 시 reason에 자연스럽게 반영:

| 시나리오 | 영향 | LD 메시지 (reason 안) |
|---|---|---|
| ① 슬랙·견적서 두 축 침묵 | 5번 0점 | "활동 시그널 부재" |
| ③ 마감 3중 모두 실패 | 3번 0점 | "마감 정보 미입력 — 임박도 판단 보류" |
| ④ raw 부족 mid 폴백 | 6번 0점·`_llm_fallback=True` | "거래 의지 판단 근거 부족 — 슬랙 raw 보강 권장" |
| ⑥ 출발점 빈칸 | 7번 0점 | "출발점 정보 미입력" |

---

## 산출물 (스킬 7로 전달)

```json
{
  "generated_at": "2026-04-30",
  "today": "2026-04-30",
  "target_ld": "<settings.target_ld.name>",
  "deals": [
    {
      "rank": 1,
      "tier": 1,
      "deal_id": "...",
      "deal_name": "...",
      "customer_name": "...",
      "deal_amount": 3.26,
      "pipeline_stage": "최종 f-up",
      "deadline": "2026-05-05",
      "deadline_source": "from_proposal_field",
      "scoring": {
        "scores": {...},
        "main_score": 60,
        "sub_score": 65,
        "total": 125,
        "max_possible": 170,
        "tier": 1,
        "grounding_match": true,
        "intent_category_llm": "high",
        "intent_grounding_ld": "확정"
      },
      "intent_signals": ["C1", "C2"],
      "intent_evidence": {...},
      "reason": "...",
      "next_action": "...",
      "badges": []
    }
  ],
  "validation_warnings": [
    "이 딜의 거래 의지 판단 근거가 부족해 중립값으로 처리했습니다."
  ]
}
```

`validation_warnings` = `_validation_log.json`에서 `status=warn` 항목을 `_helpers.collect_ld_messages()`로 변환한 결과.

---

## 스크립트 vs LLM 역할 분리 (4/30 갱신)

| 처리 | 담당 |
|---|---|
| 7기준 점수 계산 | **스크립트** (calculate_score.py) |
| 티어 분류 (87/55) | **스크립트** |
| **`성사 가능성` grounding 비교** | **스크립트** (4/30 신규) |
| 6항목 검증 | **스크립트** (verify_scores.py) |
| **거래 의지 카테고리 판정** | **LLM** (STEP 1, temperature=0, JSON schema 강제) |
| **마감 추출** | **LLM** (STEP 1, 통합 출력) |
| reason + next_action 생성 | **LLM** (STEP 4, 티어별 톤) |
| 자연어 피드백 처리 | ⏸ **v2 영역** (MVP 제외) |

---

## 잔여 개발 영역 (5/6·5/7)

- `결손_매트릭스.md` ④ raw 부족 임계값 (현재 ≤2줄) — 5/7 검증 시 조정
- `memo` 테이블 토큰 부담 — 5/7 측정 후 섹션 추출 룰 검토 (`확인_리스트.md` §5 #1)
- LLM ↔ enum 매핑 (high·확정/높음 등) — 5/7 mismatch 분포 측정 후 정밀화
- v2 영역: 자연어 피드백 layer 진화 메커니즘 (CLAUDE.md Play 4)

---

## 관련 파일

- 위계: `../../CLAUDE.md` 1.4·1.5·1.6 (4/30 갱신)
- 결손 룰: `../../docs/_builder/결손_매트릭스.md`
- 검증 모듈: `../../scripts/_validation.py` + `../../scripts/_helpers.py`
- 점수 코드: `../../scripts/calculate_score.py` + `verify_scores.py`
- 정보원 매핑: `../../docs/information_criteria.md` §0-2

작성: 2026-04-30 (4/30 1일차 6번 작업. baseline rewrite — 7기준 새 weights·LLM grounding·결손 분기·v2 분리 모두 반영.)
