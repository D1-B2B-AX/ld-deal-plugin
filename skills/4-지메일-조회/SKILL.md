# 스킬 4: 지메일 조회

> 고객사 담당자와의 **메일 교환 현황**을 확인하여 고객 반응 온도, 요구사항, 필요 액션을 보충하는 보조 스킬.

## 역할

스킬 1(세일즈맵)의 **보조 스킬**. 내부 대화(슬랙)가 아닌 **외부 고객과의 직접 커뮤니케이션** 상태를 파악:
- 고객이 응답하고 있는가? (반응 온도)
- 고객이 뭘 요구하고 있는가? (조건/요청)
- 우리가 뭘 해야 하는가? (액션)
- 고객이 어떻게 평가하고 있는가? (태도/온도)

## 인풋

| 파라미터 | 출처 | 설명 |
|---------|------|------|
| `contact.email` | 스킬 1 아웃풋 | 고객사 담당자 이메일 (정확 매칭 검색) |
| `contact.name` | 스킬 1 아웃풋 | 참조용 |
| `owner_email` | `settings.target_ld.email` | LD 본인 이메일 |
| `gmail_label` | `settings.data_sources.gmail_label` | B2B 관련 라벨 (예: `B2b_2팀메일`) — LD별 커스터마이징 |

## 슬랙(스킬 3)과의 차이

| | 슬랙 (스킬 3) | 지메일 (스킬 4) |
|--|------------|------------|
| 검색 키워드 | 고객사명 | **고객 담당자 이메일 주소** (정확 매칭) |
| 대화 상대 | 내부 팀원 | **외부 고객** |
| 핵심 시그널 | 내부 논의 현황 | **고객 반응 온도 + 요구사항** |
| 파싱 수준 | 1줄 요약 | **요구/액션/평가 + 1줄 요약** (고객 메일은 내용이 중요) |

## 처리 흐름

```
STEP 1: 고객 이메일 주소로 양방향 검색 (from + to)
  ↓
STEP 2: 최근 2주 이내, 딜당 최근 3건 필터
  ↓
STEP 3: 응답 상태 판별 (주고받는 중 / 미응답 / 안 보내고 있음)
  ↓
STEP 4: LLM 파싱 (요구 / 액션 / 평가 / 1줄 요약)
  ↓
STEP 5: 딜별 JSON 구조화
```

## STEP 1: 검색 쿼리 (통합 최적화 — 2026-04-20 강화)

### 속도 최적화: 딜별 개별 호출 → OR 통합

기존: 딜 N건 × 2회(from + to) = **N×2회 호출** (딜 10건 → 20회)
개선: **고객 이메일 주소를 OR로 묶어 통합 쿼리**

```
개선 방식: (from:A@... OR from:B@... OR from:C@... OR ... OR
            to:A@...   OR to:B@...   OR to:C@...   OR ...)
           label:{gmail_label}
범위: 최근 2주
```

- 양방향(from + to)을 같이 봐야 "주고받는 중" vs "미응답" 구분 가능 → OR로 전부 묶기
- 결과: 모든 딜 이메일이 섞여서 나옴 → **후처리에서 고객 이메일 주소로 딜별 분류**

### ⚠️ 쿼리 길이 제한 대응 (Fallback 설계)

Gmail 검색 쿼리에도 길이 상한 존재:

```
1순위 시도: 전체 딜 이메일 OR 통합 1회 쿼리
  → 성공 시 1회 호출로 완료

2순위 fallback: 쿼리 길이 초과 또는 실패 시
  → 5~6건씩 분할 → 2~3회 호출
  → 결과 통합 후 동일하게 딜별 분류

3순위 fallback: 분할 쿼리도 실패 시
  → 딜별 개별 호출 (N×2회) — 가장 느리지만 가장 안전
```

### 속도 예상
- 최적화 후: 딜 10~12건 기준 **1~3회 호출** (기존 20회 → 대폭 감소)
- LD → 고객, 고객 → LD 양방향 커버 동일

## STEP 3: 응답 상태 판별

| 조건 | 판별 | `response_status` |
|------|------|-------------------|
| 2주 내 양방향 메일 있음 | 정상 | `"주고받는 중"` |
| LD가 보냈는데 고객 회신 없음 | 리마인드 필요 | `"⚠️ 내가 보냈는데 고객 미응답"` |
| LD도 안 보내고 있음 | 방치 | `"⚠️ 메일 교환 없음"` |
| 고객이 보냈는데 LD 회신 없음 | 긴급 | `"🔴 고객이 보냈는데 내가 미회신"` |

## STEP 4: LLM 파싱 — 딜별 개별 처리 유지

메일 본문에서 **4가지** 추출:

| 파싱 항목 | 설명 | 예시 |
|---------|------|------|
| **고객 요구** (`customer_request`) | 고객이 원하는 것 / 조건 변경 | "견적 10% 할인 가능 여부 확인 요청" |
| **다음 액션** (`next_action`) | 우리 쪽에서 해야 할 행동 + 기한 | "수정 견적서 금요일까지 회신" |
| **고객 평가** (`customer_sentiment`) | 고객의 현재 태도 / 온도 | "제안 방향 긍정적, 예산 초과 우려" |
| **상황 요약** (`situation_summary`) | 메일 흐름 1줄 압축 | "교육 일정 5월 확정, 견적 조건 조율 중" |

**파싱 규칙:**
- 해당 항목이 메일에 없으면 `null` (억지로 만들지 않음)
- 기한이 상대적이면 메일 날짜 기준 절대 날짜 변환

### ⚠️ 왜 LLM 일괄 처리하지 않는가

메일 본문 내용은 **딜별 완전히 다른 맥락**:
- `customer_request`는 딜마다 완전히 다른 요구사항 (할인율·일정·커리큘럼 등)
- `next_action`도 딜 고유 정보 (다른 딜에 섞이면 치명적)

일괄 LLM 파싱 시 딜 간 맥락 혼동·오분류 위험 → **딜별 개별 LLM 호출 유지**.

**검색 통합(API 호출 감소)은 데이터가 섞이지 않아 안전. LLM 파싱은 딜별 분리.**

### Verify 필요 항목

- `deal_id` 매핑 정합성 (결과 4필드가 정확히 해당 딜 것인지)
- 필수 필드 존재 여부 (null은 정상, 키 자체 누락은 오류)
- 실패 시: 해당 딜만 default_response로 대체, 나머지 딜은 정상 진행

## 아웃풋

```json
{
  "deal_id": "019baba5-418d-733f-aab9-4d330145b8f6",
  "deal_name": "카카오페이_26년도 전사 AI 연간 교육",
  "response_status": "주고받는 중",
  "last_sent": "2026-04-08",
  "last_received": "2026-04-11",
  "email_parsed": {
    "customer_request": "커리어레벨별 맞춤 큐레이션 가능 여부 확인",
    "next_action": "스킬매치 레퍼런스 자료 송부 필요",
    "customer_sentiment": "온라인 LMS + 오프라인 연간 플랜 모두 관심 높음",
    "situation_summary": "RFP 기반 제안 준비 중, 고객이 적극적으로 자료 요청"
  },
  "email_results_raw": [
    { "date": "2026-04-11", "from": "contact@example.com", "subject": "Re: 교육 제안 관련", "body_preview": "..." },
    { "date": "2026-04-08", "from": "{owner.email}", "subject": "교육 제안 관련", "body_preview": "..." }
  ]
}
```

### 필드 의미

| 필드 | 용도 |
|------|------|
| `response_status` | 스킬 5 — 고객 반응 온도 시그널 (가장 직접적) |
| `last_sent` / `last_received` | 스킬 5 — 메일 기반 라스트 터치 |
| `email_parsed.customer_request` | 스킬 5 — 고객이 뭘 원하는지 |
| `email_parsed.next_action` | 스킬 5 — 우리가 뭘 해야 하는지 |
| `email_parsed.customer_sentiment` | 스킬 5 — 고객 태도/온도 |
| `email_parsed.situation_summary` | 스킬 5, 6 — 보고서 요약용 |
| `email_results_raw` | 스킬 5 — 필요 시 원문 참조 |

## 실패 시 처리

| 실패 유형 | 반환 필드 | 기본값 |
|---------|---------|-------|
| workspace-mcp 인증 실패 | 전체 | default_response |
| 검색 성공, 결과 0건 | `email_results_raw` | `[]` |
| LLM 파싱 실패 | `email_parsed` | `null` |
| 일부 딜만 실패 | 해당 딜만 default_response | 경고 로그만 |

**default_response:**
```json
{
  "response_status": null,
  "last_sent": null,
  "last_received": null,
  "email_parsed": null,
  "email_results_raw": []
}
```

**스키마 연동:**
- `customer_responded` → `response_status`가 `"주고받는 중"`이면 `true`
- `last_touch_days` → `last_received` 날짜 기준. 스킬 4 실패 시 스킬 1의 `last_note_date`로 대체

## MCP 호출 (5/6 — 검색 시점 한정 룰 박음)

```
도구: mcp__workspace-mcp__search_gmail_messages
파라미터:
  user_google_email: settings.target_ld.email
  query: "(from:{고객이메일} OR to:{고객이메일}) label:{gmail_label} after:{경계일}"
```

### 검색 시점 한정 룰 (5/6 신규 — 라벨 검색 사고 차단)

**경계일 결정**:
```python
window_days   = settings.data_sources.email_search_window_days  # default 14, 모든 모드 적용
after_date    = settings.data_sources.email_after_date           # test 모드 한정 (옵션)
default_after = today - timedelta(days=window_days)

if after_date:  # test 모드 — 라벨 검색
    boundary_date = max(default_after, after_date)
else:           # deploy 모드 — target_ld 본인 메일함
    boundary_date = default_after
# query에 박힘: after:{boundary_date.strftime('%Y/%m/%d')}
```

**모드별 작동**:

| 모드 | 메일 출처 | `email_after_date` | 효과 |
|---|---|---|---|
| **test** (현재 — 사용자 ≠ target_ld) | 2팀 공용계정 + `B2b_2팀메일` 라벨 | 사용자 라벨 가입일 (예: `2026-04-16`) | 가입 전 메일 차단 (라벨에 박혀있는 OM·다른 LD 메일 자동 제외) |
| **deploy** (5/7 이후 — 사용자 = target_ld) | target_ld 본인 받은편지함·보낸편지함 | `null` | 본인 메일함이라 가입일 개념 없음 → 14일 window만 적용 |

**왜 필요한가** (5/6 라벨 검색 사고):
- 4/30 검색에서 특정 회사 메일 10건+ hit. 답지엔 "메일 내역 없음"으로 박혀있어 답지 누락 후보 #2로 분류됐음
- 5/6 사용자 직접 확인: "2팀 메일 그룹 가입 이후 해당 회사 딜 관련 메일 없음. 검색 결과 10건+은 가입 전 메일 또는 동일 회사 다른 딜 섞임"
- 사용자 짚음 (5/6): "이건 테스트를 위한 세팅. 실제 (target_ld)이 사용할 때는 이 기간 무관" — *test 모드 한정* 룰임을 정확히 짚음
- → 라벨 검색 시점 한정만 박고, deploy 시엔 자동 무시되는 구조

**효과**:
- test 모드: 가입 전 메일 자동 제외 (라벨에 박힌 OM·다른 LD 메일)
- deploy 모드: 14일 window만 적용 (본인 메일함이라 시점 한정 불필요)
- 캘린더·슬랙과 검색 범위 통일 (14일)
- 기존 `(최근 2주 필터 추가)` 추상 표현을 `after:{경계일}` 명시 쿼리로 교체

## 알려진 제약·주의사항

| 항목 | 내용 |
|------|------|
| 검색 범위 (window) | `email_search_window_days`(default 14일) — 모든 모드 적용 |
| 검색 횟수 | 딜 10~12건 = 최대 12회 (이메일 주소 기반이라 통합 어려움) |
| 본문 읽기 | 메일 본문 포함 조회 → 파싱 시 LLM 호출 추가 |
| CC/BCC | 고객 CC 메일도 잡힘 (긍정적 — 놓치는 것보다 나음) |
| 동일 고객사 복수 딜 | 담당자 이메일이 다르면 자연스럽게 분리됨 |
| **가입 전 메일 자동 제외 — test 모드 한정 (5/6)** | `email_after_date` 박혀있을 때만 적용 (라벨 검색). `null`이면 deploy 모드로 간주, 자동 무시. 4/30 라벨 검색 사고는 *test 모드 영역* 차단. |

## LD 체크 포인트

1. 고객과 메일 외 다른 채널(카톡 등)로 소통하는 경우 — 메일만으로 "미응답" 판단이 부정확해질 수 있음
2. 팀 공용 메일 계정 발송 시 개인 메일 검색에서 누락 가능

## 기존 플러그인과의 차이

- `owner_email` 하드코딩 → settings 외부화
- `gmail_label` 명시적 settings 참조 (기존엔 암묵적)
