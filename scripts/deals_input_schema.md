# calculate_score.py 입력 스키마 정의

> 스킬 1~5 결과를 종합한 **LLM 전처리 결과(deals.json)가 따라야 할 스키마**. 
> calculate_score.py는 이 스키마의 데이터를 입력받아 점수를 계산한다.

## 전체 구조

```
deals.json = [ { deal object }, { deal object }, ... ]
```

각 딜 객체의 필수/선택 필드는 아래 참조.

---

## 딜 객체 스키마 (완전 예시)

```json
{
  "deal_id": "D-001",
  "deal_name": "카카오페이_26년도 전사 AI 연간 교육",
  "customer_name": "카카오페이",
  "status": "SQL",

  "_comment_금액": "기준 1 — 스킬 1에서 수집",
  "amount": 3.26,

  "_comment_단계": "기준 2 — 수주 전은 pipeline_stage, 수주 후는 education_prep 사용",
  "pipeline_stage": "최종 f-up",
  "education_prep": null,

  "_comment_마감": "기준 3 — 수주 전은 expected_close_date, 수주 후는 education_end_date",
  "expected_close_date": "2026-04-30",
  "education_end_date": null,
  "education_schedule_confirmed": false,

  "_comment_고객가치": "기준 4 — 스킬 1에서 수집",
  "past_deal_count": 0,
  "past_total_revenue": 0,
  "is_strategic_customer": false,

  "_comment_소통": "기준 5 — 스킬 1(세일즈맵 오픈일) + 스킬 4(지메일) 통합",
  "last_touch_days": 35,
  "customer_responded": null,

  "_comment_주간액션": "기준 6 — 스킬 2(캘린더) + 스킬 1(메모 파싱)",
  "has_meeting_this_week": true,
  "has_action_this_week": true,

  "_comment_의뢰경로": "기준 7 — 수주 전은 deal_origin(LLM 판단), 수주 후는 education_schedule_confirmed",
  "deal_origin": "RFP",

  "_comment_메타": "스킬 7(HTML 출력) + LLM 근거 생성에 사용",
  "memo_parsed": {
    "next_action": "오늘 16:00 체크인 → 견적 수정 확인",
    "current_status": "최종 f-up 단계, D-15 임박, RFP 기반 직접 의뢰"
  },
  "calendar_events": [
    {"date": "2026-04-14", "time": "16:00", "title": "카카오페이 체크인"}
  ],
  "slack_summary": "최근 2주 슬랙 대화 없음",
  "email_status": "우리가 마지막 발신",
  "planning_sheet_url": "https://docs.google.com/...",
  "deal_links": {
    "salesmap": "...",
    "planning_sheet": "...",
    "slack_thread": "..."
  }
}
```

---

## 필드별 상세 정의 및 출처

### 필수 필드 (calculate_score.py가 사용)

| 필드 | 타입 | 설명 | 출처 | 없을 때 기본값 |
|------|------|------|------|-------------|
| `deal_id` | string | 딜 고유 식별자 | 스킬 1 | 없으면 에러 |
| `deal_name` | string | 딜명 | 스킬 1 | 없으면 에러 |
| `customer_name` | string | 고객사명 | 스킬 1 | 없으면 에러 |
| `status` | `"SQL"` / `"Won"` | 수주 전/후 구분 | 스킬 1 | 없으면 에러 |
| `amount` | float (억 단위) | 딜 금액 | 스킬 1 | `null` → 0점 |
| `pipeline_stage` | string | 파이프라인 단계 (수주 전만) | 스킬 1 | `""` → 5점 |
| `education_prep` | string | 교육 준비 상태 (수주 후만) | 스킬 1 또는 LLM 판단 | `"미확정"` |
| `expected_close_date` | `"YYYY-MM-DD"` | 수주 예정일 | 스킬 1 | `null` → 5점 |
| `education_end_date` | `"YYYY-MM-DD"` | 교육 종료일 | 스킬 1 | `null` → 5점 |
| `education_schedule_confirmed` | boolean | 교육일정 확정 여부 | 스킬 1 (`edu_start` NULL 여부로 계산) | `false` |
| `past_deal_count` | int | 기고객 누적 거래 건수 | 스킬 1 | 0 |
| `past_total_revenue` | float (억) | 기고객 누적 매출 | 스킬 1 | 0 |
| `is_strategic_customer` | boolean | 전략적 핵심 고객 여부 | **LLM 판단** (핵심기업 리스트 대조) | `false` |
| `last_touch_days` | int \| null | 마지막 연락 경과일 | **LLM 전처리**: 스킬 4(response_status) 우선 → 없으면 스킬 1(세일즈맵 이메일 오픈일) | `null` → 0점 |
| `customer_responded` | `true`/`false`/`null` | 고객 회신 여부 | 스킬 4 (`response_status` 기반) | `null` |
| `has_meeting_this_week` | boolean | 이번 주 미팅 존재 | 스킬 2 (`matched_events` 기반) | `false` |
| `has_action_this_week` | boolean | 이번 주 액션 예정 | 스킬 1 메모 파싱 (`memo_parsed.next_action` 기반) | `false` |
| `deal_origin` | string | 딜 출발점 (수주 전만) | **LLM 판단** (메모+메일 파싱) | `""` → 5점 |

### `deal_origin` 허용 값

LLM은 아래 중 하나를 선택해야 함:
- `"RFP"` → +20 (사전 미팅 후 RFP 유사, 구체적 조율 중)
- `"소개/추천"` → +20 (내부 소개)
- `"기고객 재의뢰"` → +20 (활성 기고객 재연락)
- `"인바운드"` → +10 (일반 문의)
- `"아웃바운드"` → +5 (우리가 먼저 접근)
- `"다수 업체 비교"` → +5 (경쟁 컨택)
- `"가격 비교만"` → +0 (Price Only)

### 선택 필드 (LLM 근거 생성 + HTML 출력에 사용, 스코어링 자체에는 미사용)

| 필드 | 타입 | 설명 | 출처 |
|------|------|------|------|
| `memo_parsed` | object | 메모 파싱 결과 | 스킬 1 LLM 파싱 |
| `memo_parsed.next_action` | string \| null | 다음 액션 + 기한 | 스킬 1 LLM 파싱 |
| `memo_parsed.current_status` | string \| null | 현황 1줄 요약 | 스킬 1 LLM 파싱 |
| `calendar_events` | array | 이번 주+다음 주 일정 | 스킬 2 |
| `slack_summary` | string \| null | 슬랙 대화 요약 | 스킬 3 |
| `email_status` | string \| null | 메일 응답 상태 4단계 | 스킬 4 |
| `planning_sheet_url` | string \| null | 기획시트 링크 | 스킬 5 |
| `deal_links` | object | 세일즈맵/기획시트/슬랙 링크 | 스킬 1~5 통합 |

---

## 에러 처리 정책 (Phase 3a)

### 필수 필드 누락 시
- `deal_id`, `deal_name`, `customer_name`, `status` 중 하나라도 없으면:
  - 해당 딜만 스킵 + stderr에 경고 로그
  - 나머지 딜은 계속 처리
- 전체 딜이 하나도 남지 않으면: Phase 3 전체 중단 → Phase 1 데이터 재확인 요청

### 타입 오류 시
- `amount`가 숫자가 아니면 → 0 처리 + 경고
- 날짜 필드가 `YYYY-MM-DD` 형식 아니면 → `null` 처리 + 경고

### 범위 초과 시
- calculate_score.py는 범위 초과를 그냥 계산 (verify_scores.py가 Phase 3.5에서 감지)
- Phase 3.5 실패 → Phase 3 재실행 (최대 2회)

---

## 스킬 2~5 실패 시 기본값

오케스트레이터가 스킬 2~5 실패 시 아래 기본값으로 채움:

| 스킬 | 관련 필드 | 실패 시 기본값 |
|------|---------|-------------|
| 스킬 2 실패 | `calendar_events`, `has_meeting_this_week` | `[]`, `false` |
| 스킬 3 실패 | `slack_summary` | `null` |
| 스킬 4 실패 | `email_status`, `customer_responded` | `null`, `null` |
| 스킬 5 실패 | `planning_sheet_url` | `null` |

→ 기본값 처리 후에도 스킬 1 데이터(amount, status, pipeline_stage 등)만 있으면 메인 4개 기준으로 스코어링 가능.

---

---

## 스킬 1 출력 → deals_input_schema 변환 매핑 (필수)

스킬 1은 세일즈맵 DB 원본 구조를 반영한 자체 포맷으로 출력한다. LLM 전처리 단계에서 아래 매핑표를 따라 변환한다.

### 기본 필드 매핑

| 스킬 1 출력 (원본) | deals_input_schema (스코어링용) | 변환 로직 |
|-------------------|-------------------------------|---------|
| `deal_id` | `deal_id` | 그대로 |
| `deal_name` | `deal_name` | 그대로 |
| `deal_type: "수주 전"` | `status: "SQL"` | "수주 전" → "SQL", "수주 후" → "Won" |
| `stage` | `pipeline_stage` | 그대로 (수주 전 딜만) |
| `expected_amount` (원 단위) | `amount` (억 단위) | `expected_amount / 100_000_000` |
| `expected_close_date` | `expected_close_date` | 그대로 |
| `edu_start` | (여기서 직접 없음) | `education_schedule_confirmed = (edu_start is not null)` |
| `edu_end` | `education_end_date` | 그대로 |
| `organization.name` | `customer_name` | 그대로 |
| `organization.past_won_deals` | `past_deal_count` | 그대로 |
| `organization.total_revenue` (원) | `past_total_revenue` (억) | `total_revenue / 100_000_000` |
| `recent_activity.memo_parsed.next_action` | `memo_parsed.next_action` | 그대로 |
| `recent_activity.memo_parsed.situation_summary` | `memo_parsed.current_status` | 필드명 변경만 |
| `recent_activity.days_since_last_note` | `last_touch_days` | **단, 스킬 4 `response_status` 있으면 스킬 4 기반으로 재계산** |

### 스킬 1에 없어서 LLM/다른 스킬이 채워야 하는 필드

| deals_input_schema 필드 | 출처 | LLM 판단 로직 |
|----------------------|------|------------|
| `education_prep` | LLM 판단 (수주 후만) | `edu_start`, `edu_end`, 오늘 날짜로 판단. "교육 진행 중"/"임박"/"준비 중"/"일정 미확정" |
| `education_schedule_confirmed` | 자동 계산 | `edu_start is not null` |
| `customer_responded` | 스킬 4 (response_status) | `"고객이 마지막"` or `"회신 중"` → true, `"우리가 마지막"` or `"교환 없음"` → false/null |
| `has_meeting_this_week` | 스킬 2 (matched_events) | 이번 주 이벤트 존재 여부 |
| `has_action_this_week` | 스킬 1 memo_parsed + 스킬 2 | next_action에 이번 주 날짜 있거나, has_meeting_this_week true |
| `deal_origin` | **LLM 판단** | 메모 + 메일 원문에서 판단. 허용 값 7개 중 선택 (RFP/소개/추천/기고객 재의뢰/인바운드/아웃바운드/다수 업체 비교/가격 비교만) |
| `is_strategic_customer` | **LLM 판단** | `organization.name`이 핵심 기업 리스트(삼성/현대/KT/SK/LG 및 계열사)에 포함되는지 |

### 전처리 의사 코드

```python
def preprocess(skill1_output, skill2_output, skill3_output, skill4_output, skill5_output):
    result = []
    for d in skill1_output:
        # 기본 매핑
        deal = {
            "deal_id": d["deal_id"],
            "deal_name": d["deal_name"],
            "customer_name": d["organization"]["name"],
            "status": "SQL" if d["deal_type"] == "수주 전" else "Won",
            "amount": d["expected_amount"] / 100_000_000 if d.get("expected_amount") else None,
            "pipeline_stage": d.get("stage", ""),
            "expected_close_date": d.get("expected_close_date"),
            "education_end_date": d.get("edu_end"),
            "education_schedule_confirmed": d.get("edu_start") is not None,
            "past_deal_count": d["organization"].get("past_won_deals", 0),
            "past_total_revenue": (d["organization"].get("total_revenue", 0) or 0) / 100_000_000,
            "memo_parsed": {
                "next_action": d["recent_activity"]["memo_parsed"].get("next_action"),
                "current_status": d["recent_activity"]["memo_parsed"].get("situation_summary")
            }
        }

        # 스킬 4 기반 last_touch_days (우선)
        s4 = skill4_output.get(d["deal_id"])
        if s4 and s4.get("last_touch_date"):
            deal["last_touch_days"] = days_between(s4["last_touch_date"])
            deal["customer_responded"] = s4["response_status"] in ["회신 중", "고객이 마지막"]
        else:
            deal["last_touch_days"] = d["recent_activity"].get("days_since_last_note")
            deal["customer_responded"] = None

        # 스킬 2 기반 주간 액션
        s2 = skill2_output.get(d["deal_id"], {})
        deal["has_meeting_this_week"] = len(s2.get("matched_events", [])) > 0
        deal["has_action_this_week"] = deal["has_meeting_this_week"] or has_this_week_date(deal["memo_parsed"]["next_action"])

        # LLM 판단 (별도 호출)
        deal["deal_origin"] = llm_judge_origin(d, s4) if deal["status"] == "SQL" else None
        deal["is_strategic_customer"] = is_key_customer(deal["customer_name"])
        deal["education_prep"] = llm_judge_edu_prep(d) if deal["status"] == "Won" else None

        # 메타 필드 (HTML 출력용)
        deal["calendar_events"] = s2.get("matched_events", [])
        deal["slack_summary"] = skill3_output.get(d["deal_id"], {}).get("slack_summary")
        deal["email_status"] = s4.get("response_status") if s4 else None
        deal["planning_sheet_url"] = skill5_output.get(d["deal_id"], {}).get("url")

        result.append(deal)
    return result
```

---

---

## LLM 판단 프롬프트 (핵심 필드)

전처리 단계에서 LLM이 판단해야 하는 3개 필드의 구체적 프롬프트.

### 프롬프트 1: `deal_origin` 판단 (수주 전 딜만)

스킬 1 메모 + 스킬 4 메일을 종합해 허용 값 7개 중 하나로 분류. calculate_score.py는 이 문자열을 ORIGIN_SCORES 딕셔너리로 매핑해 점수화하므로, **반드시 허용 값 그대로 반환**할 것.

**⚠️ 필수**: 모든 수주 전(SQL) 딜에 대해 반드시 판단해야 합니다. 빈 값("")이나 null 반환 금지. 판단 근거가 부족하면 "인바운드"(기본값)를 사용하세요. 메모를 꼼꼼히 읽으면 대부분 판단 가능합니다.

```
당신은 B2B 영업 딜의 유입 경로와 고객 행동 패턴을 분석하는 어시스턴트입니다.
아래 딜 정보를 보고 7개 카테고리 중 정확히 하나를 선택해 반환하세요.

[딜 정보]
고객사: {customer_name}
딜명: {deal_name}

[세일즈맵 메모 (최근 2~3건)]
{memo_text_combined}

[지메일 최근 메일 (있으면)]
{email_text_combined}

[판단 카테고리 7개]
1. "RFP" — 고객사가 RFP를 발송했거나, 사전 미팅 후 RFP가 유사하게 나옴. 담당자가 자주 연락하고 구체적 일정/내용 조율 중
2. "소개/추천" — 내부 소개나 외부 추천으로 연결된 딜
3. "기고객 재의뢰" — 이전 거래 경험이 있는 고객이 재연락해온 경우
4. "인바운드" — 일반 웹폼, 일반 문의 등 고객이 먼저 접근했으나 경쟁 상황·관계는 불명
5. "아웃바운드" — 우리가 먼저 고객에게 접근한 경우
6. "다수 업체 비교" — 여러 업체에 동시에 컨택 중 + 견적/커리큘럼을 빨리 달라고 요청 (노을님 행동지표: "사실상 낮음")
7. "가격 비교만" — 가격 중심 비교 문의(Price Only), 담당자 연락 빈도 낮음 (노을님 행동지표: "비교 쇼핑")

[판단 우선순위 규칙]
- 여러 카테고리에 해당할 수 있으면, 성사 가능성이 높은 쪽을 선택 (RFP > 소개/추천 > 기고객 재의뢰 > 인바운드 > 아웃바운드 > 다수 업체 비교 > 가격 비교만)
- 단, "가격만 물어봄" 패턴이 명확하면 "가격 비교만" 우선
- "여러 업체 병렬 컨택" 패턴이 명확하면 "다수 업체 비교" 우선
- 정보가 부족해서 판단 불가하면 "인바운드" (기본값)

[아웃풋 형식]
{"deal_origin": "RFP"}

[주의]
- JSON 외 다른 텍스트 출력 금지
- 카테고리 7개 외의 값 반환 금지 (오타/변형 금지)
- 추측하거나 과장하지 말 것 — 메모/메일에 명시된 정보만 사용
```

**예시 I/O**

Input:
```
customer_name: 카카오페이
memo_text_combined: "RFP 기반 연간 교육 제안 요청 받음. 담당자 신대리와 3차례 미팅 후 구체적 커리큘럼 조율 중. 견적서 v2 요청 상태."
email_text_combined: "신대리: 견적서 v2 검토 완료. 내부 임원 컨펌 후 다시 연락드리겠습니다."
```

Output:
```json
{"deal_origin": "RFP"}
```

---

### 프롬프트 2: `is_strategic_customer` 판단

```
당신은 B2B 영업에서 전략적 핵심 고객을 식별하는 어시스턴트입니다.
아래 고객사명이 핵심 기업 리스트에 포함되는지 판단하세요.

[고객사명]
{customer_name}

[핵심 기업 리스트 (계열사 포함)]
- 삼성: 삼성전자, 삼성SDS, 에스원, 삼성디스플레이 등
- 현대: 현대자동차, 현대카드, 현대건설, 현대모비스 등
- KT: KT, KT&G, KT하이텔 등
- SK: SK하이닉스, SK텔레콤, SK이노베이션 등
- LG: LG전자, LG화학, LG디스플레이 등

[판단 규칙]
- 고객사명이 위 5대 그룹의 직접 계열사이면 true
- 모회사나 계열사명이 부분 일치해도 true (예: "한화솔루션 케미칼부문" → 한화 계열이지만 핵심 리스트 아님 → false)
- 불확실하면 false

[아웃풋]
{"is_strategic_customer": true | false}
```

---

### 프롬프트 3: `education_prep` 판단 (수주 후 딜만)

```
당신은 수주 완료된 교육 딜의 준비 상태를 분류하는 어시스턴트입니다.

[딜 정보]
교육 시작일 (edu_start): {edu_start}
교육 종료일 (edu_end): {edu_end}
오늘 날짜: {today}

[분류 카테고리 4개]
- "교육 진행 중": edu_start가 오늘보다 과거이고 edu_end가 오늘보다 미래
- "교육 임박": edu_start가 오늘 기준 14일 이내 미래
- "준비 중": edu_start가 14일 초과 미래이며 확정 상태
- "일정 미확정": edu_start 또는 edu_end가 null

[아웃풋]
{"education_prep": "교육 진행 중"}
```

→ 이 판단은 LLM 없이 스크립트로도 가능. 전처리 함수에서 날짜 계산으로 처리 권장.

---

## 버전
- v1.0: 2026-04-15 초안
- v1.1: 2026-04-15 저녁 — 스킬 1 → schema 변환 매핑 추가
- v1.2: 2026-04-15 저녁 — LLM 판단 프롬프트 3개 추가 (deal_origin, is_strategic_customer, education_prep)
