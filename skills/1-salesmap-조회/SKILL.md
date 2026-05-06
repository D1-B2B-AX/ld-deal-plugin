# 스킬 1: 세일즈맵 조회 (SQL 단계 전용)

> 담당자(LD) 기준으로 **SQL 단계 활성 딜 + 고객사 정보 + 고객사 담당자 + 최근 메모(파싱 포함)** 를 하나의 구조화된 JSON으로 반환하는 스킬.

## 역할

우선순위 판단 파이프라인의 **출발점**. 이 스킬의 출력이 스킬 2~4(캘린더/슬랙/메일 조회)의 인풋이 되고, 최종적으로 스킬 5(우선순위 판단)의 기반 컨텍스트가 됨.

## 딜 정의

| 구분 | 필터 |
|------|------|
| **포함** | `상태 = 'SQL'` (수주 전 활성 딜) |
| **제외** | `상태 IN ('Won', 'Lost', 'Convert')` (수주·실패·전환 전부 제외) |

> 본 플러그인은 **운영(Won) 제외, 세일즈 우선순위에만 집중.** 운영 관리가 필요하면 `deal-priority-plugin`을 사용.

## 인풋

| 파라미터 | 예시 | 설명 |
|---------|------|------|
| `담당자명` | `settings.target_ld.name` | 세일즈맵 담당자 이름 (JSON 컬럼 LIKE 검색, 다중 LD 지원) |

**주의:** 담당자명은 **settings.json의 owner.name을 치환**. 하드코딩 금지. `query.sql`의 `{owner_name}` 플레이스홀더를 LD별 값으로 대체.

## 처리 흐름

```
STEP 1: SQL 쿼리 (딜 + 고객사 + 담당자)
  ↓
STEP 2: SQL 쿼리 (각 딜의 최근 메모 3건)
  ↓
STEP 3: LLM 메모 파싱 (다음 액션+기한 / 현재 상황 1줄 요약)
  ↓
STEP 4: JSON 구조화 + 검색 키워드 정리
```

## STEP 1~2: SQL 쿼리

전체 쿼리: `query.sql` 참조

**핵심 쿼리 요약:**
```sql
SELECT ... FROM deal d
LEFT JOIN organization o ON d.organizationId = o.id
LEFT JOIN people p ON d.peopleId = p.id
WHERE d."담당자" LIKE '%{owner_name}%'
  AND d."상태" = 'SQL'
ORDER BY d."최근 파이프라인 단계 수정 날짜" DESC;
```

**보조 쿼리 (메모):**
```sql
SELECT dealId, createdAt, text FROM memo
WHERE dealId IN (:deal_ids)
ORDER BY dealId, createdAt DESC;
-- 애플리케이션 레벨에서 dealId별 최근 3건으로 slice
```

## STEP 3: LLM 메모 파싱

각 딜의 메모 원문에서 **2가지만** 추출:

| 파싱 항목 | 설명 | 예시 |
|---------|------|------|
| **다음 액션 + 기한** | 메모에서 CTA/TODO/다음 할 일 추출 | "3/13(금) 판교 대면 미팅 가능 여부 확인 필요" |
| **현재 상황 1줄 요약** | 이 딜이 지금 어떤 상태인지 한 줄 압축 | "RFP 기반 연간 교육 제안 준비 중, 대면 미팅 일정 조율 단계" |

**파싱 규칙:**
- 다음 액션이 메모에 없으면 `null` (억지로 만들지 않음)
- 기한이 상대적이면 메모 작성일 기준으로 절대 날짜 변환 (예: "차주 목" + 메모일 3/4 → "3/11")
- 메모 원문 텍스트도 그대로 보존하여 스킬 5에 전달

### LLM 프롬프트 (구체)

```
당신은 B2B 영업 딜 메모를 분석하는 어시스턴트입니다.
아래 메모 원문을 읽고 정확히 2가지 항목만 JSON으로 반환하세요.

[메모 원문]
{memo_text}

[메모 작성일]
{memo_date}

[딜 기본 정보 (참고용)]
딜명: {deal_name}
고객사: {customer_name}
파이프라인 단계: {pipeline_stage}

[추출 규칙]
1. next_action (다음 액션 + 기한)
   - 메모에서 "해야 할 것", "팔로업", "다음 단계", "약속", "TODO" 성격의 내용 찾기
   - 형식: "{행동} → {대상/목적} (기한: {절대 날짜})"
   - 상대 날짜("내일", "차주 목", "다음주")는 메모 작성일 기준으로 절대 날짜(YYYY-MM-DD)로 변환
   - 기한이 명시되지 않은 액션이면 날짜 부분 생략
   - 다음 액션이 메모에 전혀 없으면 null (억지로 만들지 말 것)

2. current_status (현재 상황 1줄 요약)
   - 이 딜이 "지금" 어떤 상태인지를 한 문장(40~60자)으로 압축
   - 포함해야 할 것: 진행 단계 + 핵심 맥락 1~2개 + (해당 시) 위험 시그널
   - 감정 표현("ㅠㅠ", "좋았음") 제거, 팩트만
   - 메모가 비어있으면 "메모 없음"으로 반환

[아웃풋 형식 — JSON만]
{
  "next_action": "3/11(화) 견적서 v2 발송 → 신대리 검토 요청" | null,
  "current_status": "RFP 기반 연간 교육 제안 준비 중, 대면 미팅 일정 조율 단계"
}

[주의]
- JSON 외의 다른 텍스트(설명, 인사말 등) 출력 금지
- 메모에 없는 내용을 추론하거나 상상해서 넣지 말 것
- 상대 날짜 변환이 모호하면 원문 표현 그대로 유지 (예: "차주 중")
```

## STEP 4: 아웃풋 JSON 구조

```json
{
  "deal_id": "019baba5-418d-733f-aab9-4d330145b8f6",
  "deal_name": "카카오페이_26년도 전사 AI 연간 교육",
  "stage": "최종 f-up",
  "win_probability": "높음",
  "expected_amount": 326480000,
  "course_format": "출강",
  "expected_close_date": "2026-04-29",
  "days_to_close": 15,
  "organization": {
    "name": "카카오페이",
    "industry": "금융/보험업",
    "past_won_deals": 0,
    "is_existing_customer": false,
    "total_revenue": 0
  },
  "contact": {
    "name": "담당자",
    "email": "contact@example.com",
    "title": "실무자"
  },
  "recent_activity": {
    "last_note_date": "2026-03-10",
    "days_since_last_note": 35,
    "memo_parsed": {
      "next_action": "3/13(금) 판교 대면 미팅 가능 여부 확인",
      "current_status": "RFP 기반 연간 교육 제안 준비 중, 대면 미팅 일정 조율 단계"
    },
    "recent_memos_raw": [
      { "date": "2026-03-10", "text": "(원문 보존)" },
      { "date": "2026-01-14", "text": "(원문 보존)" }
    ]
  },
  "search_keywords": {
    "deal_name_tokens": ["카카오페이", "26년도", "전사", "AI", "연간", "교육"],
    "organization_name": "카카오페이",
    "contact_name": "담당자"
  }
}
```

### 필드 의미

| 필드 | 용도 |
|------|------|
| `deal_id`, `deal_name`, `stage` | 스킬 5, 6 (표시·랭킹) |
| `expected_amount`, `expected_close_date`, `days_to_close` | 스킬 5 (수주 임박도·금액 스코어링) |
| `organization.past_won_deals`, `is_existing_customer` | 스킬 5 (기고객 여부 시그널) |
| `contact.name`, `contact.email` | 스킬 2, 3 (캘린더·슬랙 검색 키워드) |
| `memo_parsed.next_action` | 스킬 5 (가장 직접적인 우선순위 시그널) |
| `memo_parsed.current_status` | 스킬 5, 6 (보고서 요약용) |
| `recent_memos_raw` | 스킬 5 (LLM이 추가 맥락 필요 시 참조) |
| `search_keywords` | 스킬 2, 3, 4 (검색 쿼리용) |

## 알려진 제약·주의사항

| 항목 | 내용 |
|------|------|
| JSON 컬럼 | `담당자`, `파이프라인 단계`, `성사 가능성` 등은 JSON 문자열 — 후처리 파싱 필요 |
| NULL 필드 다수 | `최근 연락일`, `기획시트 링크`, `기업 니즈` 등 대부분 비어 있음 |
| 라스트 터치 | `최근 연락일` NULL → `최근 노트 작성일`로 대체 사용 |
| 메모 형태 편차 | 콜로그 / 한 줄 요약 / 웹폼 / 고객 DM 복붙 등 — 비정형이므로 "다음 액션" 파싱 불가 시 null |

## 기존 플러그인과의 차이

| 항목 | `deal-priority-plugin` (풀버전) | **`ld-deal-plugin` (본 플러그인)** |
|---|---|---|
| 딜 범위 | SQL + Won (운영 포함) | **SQL만** |
| JOIN 필드 | 수강시작일·수강종료일·계약체결일 포함 | **제외** |
| 담당자 | LD 이름 하드코딩 | **settings.target_ld.name** 치환 |
| 아웃풋 | `deal_type`("수주 전"/"수주 후"), `edu_start`, `edu_end` 포함 | **제거** (SQL만이라 불필요) |
