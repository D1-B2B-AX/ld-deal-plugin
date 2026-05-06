# 스킬 3: 슬랙 조회

> 스킬 1 메모의 **최종 작성일 이후** 슬랙에서 발생한 딜 관련 최신 대화를 보충하고, 대화가 전혀 없는 딜에 대해 **"대화 부재" 플래그**를 생성하는 보조 스킬.

## 역할

스킬 1(세일즈맵)의 **보조 스킬**. 메모에 아직 기록되지 않은 최신 내부 대화를 보충:
- 메모 이후 팀원 간 딜 관련 논의
- OM → LD DM으로 온 딜 진행 업데이트
- 긴급 상황 공유 ("일정 앞당겨졌다" 등)

## 인풋

| 파라미터 | 출처 | 설명 |
|---------|------|------|
| `organization_name` | 스킬 1 아웃풋 | 고객사명 (검색 키워드) |
| `last_note_date` | 스킬 1 아웃풋 | 마지막 메모 작성일 (대화 부재 판단 기준) |
| `owner_name` | `settings.target_ld.name` | LD 본인 이름 (작성자/멘션 필터) |
| `channels` | `settings.data_sources.slack_common_channels` + `slack_team_channels[{owner.team}]` | 공통 + 소속 팀 채널 자동 병합 |

## 채널 설정 (팀 기반 자동 매핑 + 고객사 조건부)

### 채널 구성 3개 축

1. **공통 채널 (`slack_common_channels`)** — 항상 검색
2. **팀 채널 (`slack_team_channels[owner.team]`)** — LD 소속 팀에 따라 자동 추가
3. **조건부 채널 (`slack_conditional_channels`)** — **고객사명에 키워드 포함된 딜이 있을 때만** 추가

```json
"owner": {
  "team": "2팀"
},
"data_sources": {
  "slack_common_channels": ["b2b_all", "b2b_lead", "b2b_skillmatch"],
  "slack_team_channels": {
    "1팀": ["b2b_1팀_견적제안", "b2b_1팀_all"],
    "2팀": ["b2b_2팀_견적제안", "b2b_2팀_all", "b2b_2팀_skillmatch", "b2b_finaldeals", "b2b_2팀_제안노트"]
  },
  "slack_conditional_channels": {
    "삼성": ["b2b_삼성전자"]
  }
}
```

### 조건부 채널 로직

```
for keyword, channels in slack_conditional_channels.items():
    if any(keyword in deal.customer_name for deal in deals):
        채널 목록에 channels 추가
```

**예시:**
- target_ld 딜 중 "삼성전자"·"삼성생명" 등 있으면 → `b2b_삼성전자` 채널 추가 조회
- "삼성" 포함 딜 없으면 → `b2b_삼성전자` 채널 조회 안 함 (시간·토큰 절감)

### 현재 구성 계산 예시

**2팀 LD** 기본: 공통 3 + 2팀 5 = **8개 채널**
- 만약 이번 주 삼성 관련 딜 있으면 → 9개 채널
- 삼성 관련 딜 없으면 → 8개 채널 유지

**1팀 LD** 기본: 공통 3 + 1팀 2 = **5개 채널**
- 삼성 관련 딜 있으면 → 6개 채널

### 채널 추가·삭제

- 팀 변경·채널 추가 시 `settings.json`만 수정
- 고객사 전용 채널 신설 시 `slack_conditional_channels`에 키워드 매핑 추가
- 예: `"LG": ["b2b_LG전자"]`, `"SK": ["b2b_SK하이닉스"]`

### 제외 대상 (본 플러그인 범위 외)

- 운영 관련 채널(`b2b_2팀_운영논의`, `b2b_2팀_운영요청` 등) — SQL 전용이라 불필요

## 처리 흐름

```
STEP 1: 고객사명 중복 제거 + OR 검색으로 통합 (호출 횟수 최소화)
  ↓
STEP 2: 검색 결과를 딜별로 분류
  ↓
STEP 3: 딜별 최신 메시지 3~5건 정리
  ↓
STEP 4: 결과 없는 딜 → 대화 부재 판별
  ↓
STEP 5: 딜별 JSON 구조화
```

## ⚠️ STEP 1 ~ STEP 4의 두 영역 분리 (5/6 파트장 제안 옵션 A)

본 스킬의 검색은 *두 영역*에 사용:

| 영역 | 윈도우 | settings 키 | 출력 필드 | 활용 |
|---|---|---|---|---|
| **5번 소통 점수** | 14일 (최근 활동 평가) | `slack_recent_window_days` | `slack_results`·`slack_thread_count_14d` | 점수 룩룩 |
| **6번 거래 의지 LLM raw** | 2026-01-01부터 (영업 사이클 인지) | `slack_intent_search_after` | `slack_results_lead_history` (5/6 신규) | LLM 의지 분석 raw |

→ 두 영역 모두 STEP 1·2·2.5·2.6의 검색·확장·메타 추출 룰 동일 적용. **윈도우만 다름**.

**왜 분리?** 영업 사이클은 보통 3~6개월. 14일 윈도우만으론 *lead 배분 thread root*가 누락 (예: 신세계 4/1 thread는 5/6 시점 36일 전). 6번 거래 의지 LLM이 *진짜 raw* 보고 정확한 카테고리 판단 가능하게.

**점수 룩룩 영향 X** — 5번 점수는 14일 그대로. 2026년부터 raw는 *6번 LLM 입력에만* 사용.

## STEP 1: 검색 쿼리 (통합 최적화 — 2026-04-20 강화 + 2026-05-06 lead-root 보강 + 2026-05-06 두 영역 분리)

### 속도 최적화: from + mention 통합 1회 호출

딜별 개별 검색은 비효율. from과 mention도 별도 호출할 필요 없음 → **1회 통합 쿼리**로 축소.

```
[base] "(from:@{owner_name} OR @{owner_name}) ({고객사1} OR {고객사2} OR ...)"
  → LD가 쓰거나 멘션된 메시지 1회 쿼리로 통합
```

- 검색 범위: 최근 2주
- 검색 대상: `slack_common_channels` + `slack_team_channels[owner.team]` + DM
- **길이 제한 대응:** OR 쿼리가 길면 고객사를 5~6개씩 분할 (최대 2~3회)

### Lead-root 보강 검색 (2026-05-06 신규)

**문제**: lead 배분 thread는 *팀장·파트장이 root 메시지 작성*이라 `from:@LD`도 `@LD` 멘션도 hit 안 함. base 쿼리만으론 root 누락 → STEP 2.5 thread expansion이 시작점을 잃음.

**보강 쿼리 2개** (base와 합치면 총 3회 호출):

```
[lead-root-1] "({고객사1} OR {고객사2} OR ...) (Lead OR lead OR @{owner_name})"
              → in:#b2b_lead, in:#b2b_2팀_견적제안 등 lead 배분 채널
[lead-root-2] "from:@{partner_or_team_lead} ({고객사1} OR ...)"
              → 팀장·파트장이 직접 쓴 lead 배분 메시지 추적 (settings에 명시 시)
```

- **목적**: hit한 root 메시지의 `thread_ts`만 수집 → STEP 2.5에서 thread 전체 read
- **중복 처리**: base 결과와 thread_ts 기준 dedupe

### ⚠️ 쿼리 길이·문법 검증 (Fallback 설계)

Slack 검색 API의 쿼리 길이·중첩 괄호 지원 여부가 미검증 상태:

```
1순위 시도: "(from:@X OR @X) (고객사OR...)" 통합 쿼리
  → 검색 성공 시 결과 사용

2순위 fallback: 통합 쿼리 실패·결과 이상 시
  → 기존 방식으로 복귀: "고객사OR... from:@X" + "고객사OR... @X" 2회
  → 경고 로그: "통합 쿼리 실패, 개별 쿼리 fallback"

3순위 fallback: 개별 쿼리도 실패 시
  → 딜별 개별 검색 (N회) — 가장 느리지만 가장 안전
```

- 중복 제거: 동일 메시지 1건으로 처리
- 결과 분류: 메시지 텍스트에 포함된 고객사명 기준

### STEP 2: 딜별 분류 (2026-05-06 보강 — thread root 기준 확대)

통합 검색 결과에서 각 메시지의 텍스트에 포함된 고객사명을 기준으로 해당 딜에 배정.
동일 고객사 복수 딜은 양쪽 모두에 중복 배정.

**보강 룰 (lead 배분 패턴 차단)**:
- thread root에 회사명 hit이면 → 해당 thread의 모든 reply도 동일 딜로 분류
- reply 본문에 회사명·@LD 멘션 *없어도* 분류 통과 (LD가 reply만 단 케이스 대응)
- 분류 우선순위: ① reply 본문 회사명 → ② thread root 회사명 → ③ 둘 다 없으면 미분류 drop

### STEP 2.6: 첨부 파일 메타 추출 (2026-05-06 신규 — 견적서 슬랙 전달 사실 시그널)

**목적**: 슬랙에 첨부된 견적서·자료의 *전달 사실*만 시그널로 박음. 본문 데이터(net·총액·시트 내용)는 드라이브 견적시트에서만 추출 (스킬 5).

**처리 흐름**:
```
for 각 hit 메시지 (thread expansion 후 최종 raw):
    if 메시지에 Files 메타 있음:
        slack_attachments에 추가:
          - filename
          - mime_type (예: image/png, application/vnd.ms-excel, application/pdf)
          - upload_ts (KST)
          - author (메시지 작성자)
          - file_id (슬랙 file ID)
        ※ 본문 다운로드·파싱 X (v2 영역)
```

**아웃풋 추가 필드**: `slack_attachments: [{filename, mime_type, upload_ts, author, file_id}, ...]`

**왜 본문 추출은 드라이브에서만 하나** (5/6 사용자 결정):
- 슬랙 첨부 본문 추출(엑셀 파싱·PDF 텍스트)은 v1엔 부담 큼 → v2 영역
- 드라이브 견적시트는 *공식 보관 영역*이라 schema 안정적, 본문 추출 비용 낮음
- 슬랙 첨부는 *전달 시그널만* 충분 — "견적서 전달됐다" 사실이 6번 거래 의지 raw에 보탬

**활용** (6번 거래 의지 LLM):
- raw에 "슬랙에 견적서 첨부 N건 발견 (yyyy-mm-dd, 작성자: LD)" 시그널 노출
- 견적시트 드라이브에 없을 때 *백업 시그널*로 활용 (5번 두 축 보조)

**검증 사례** (지멘스 thread 4/9 lead 배분 reply 4):
- `Files: image.png (ID: F0AU16LDA9H, image/png, 129.9 KB)` 박힘 — 슬랙 read API에서 자동 회수
- 즉 별도 도구 없이 메타데이터 즉시 잡힘

### STEP 2.5: Thread Expansion (2026-05-06 신규 — 핵심)

**목적**: 검색 hit 메시지가 thread의 일부분이면, thread 전체를 가져와 LD reply까지 raw에 포함.

**처리 흐름**:
```
for 각 hit 메시지:
    if hit.thread_ts is None or hit.thread_ts == hit.ts:
        # (a) hit이 thread parent (혹은 thread 없는 단독 메시지)
        if hit.reply_count > 0:
            slack_read_thread(channel_id, hit.ts) → reply 전부 수집
    else:
        # (b) hit이 thread reply
        slack_read_thread(channel_id, hit.thread_ts) → root + 전체 reply 수집

  중복 제거: 동일 message_ts 1건으로 처리
```

**왜 필요한가** (4/30 지멘스 사고 — 5/6 발견·차단):
- 4/9 권노을(파트장)이 `#b2b_2팀_견적제안`에서 thread root로 lead 배분 → "지멘스" 키워드 + @(target_ld) 둘 다 박힘
- 4/21 (target_ld)(LD)이 reply 2개로 *커리큘럼 작업·강사 확인 활동* — reply 본문엔 "지멘스" 키워드 없음
- → base 쿼리만으론 reply 누락 → 6번 거래 의지 LLM이 "lead 배분 후 우선 대기" raw만 봄 → **딜 진척도 잘못 판단**
- **STEP 2.5 적용 후**: thread 전체 read → reply 4·5번까지 raw 포함 → "4/21 커리큘럼·강사 작업 진행 중" 정확 반영

**아웃풋 추가 필드**: 각 메시지에 `is_thread_root`(bool)·`thread_root_ts`·`from_lead_thread`(bool) 부착 → 6번 LLM이 thread 맥락 인지 가능

## STEP 3-2: 슬랙 요약 (LLM) — 딜별 개별 처리 유지

딜별로 수집된 슬랙 메시지(3~5건)를 **한 줄로 압축**:
- 메시지별 파싱은 하지 않음 (슬랙 메시지는 짧으므로 과함)
- 딜 단위로 "최근 2주 슬랙 대화 흐름"을 1줄 요약
- 원문 메시지도 그대로 보존 (스킬 5에서 필요 시 참조)

```
예시: "4/10 견적 수정 완료, 4/12 고객 회신 대기 중"
```

### ⚠️ 왜 LLM 일괄 처리하지 않는가

딜별 슬랙 메시지 내용은 **완전히 다른 맥락** (카카오페이 대화 ≠ 한화솔루션 대화).
일괄 LLM에서 딜 간 맥락 혼동 시 중요 정보 누락·오분류 위험 → **딜별 개별 LLM 호출 유지**.

**검색 통합(API 호출 감소)은 데이터가 섞이지 않아 안전. LLM 파싱은 딜별 분리.**

## STEP 4: 대화 부재 판별

| 조건 | 판별 | 플래그 |
|------|------|--------|
| 슬랙 결과 있음 | 정상 | 없음 |
| 슬랙 없음 + 마지막 메모 **2주 이내** | 정상 (메모에 이미 기록) | 없음 |
| 슬랙 없음 + 마지막 메모 **2주 초과** | **대화 부재** | `"⚠️ 대화 부재 — 마지막 메모 N일 전, 최근 2주 슬랙 대화 없음"` |

> 대화 부재 플래그 = 스킬 5(우선순위 판단)에서 "방치 중인 딜 → 지금 챙겨야 함" 시그널로 활용

## STEP 4.5: Lead 배분 패턴 — 검색 누락 차단 룰 (2026-05-06 신규)

### 패턴 정의

B2B 영업팀 슬랙 운영 패턴:
- **Thread root**: 팀장·파트장이 작성 (회사명 + @LD 멘션 + Lead 키워드)
- **Reply**: LD가 진행 상황을 reply로 단다 (회사명·@LD 둘 다 *없는 경우 잦음*)

→ 회사명 키워드 단독 검색은 **root만 hit, LD reply 누락**.

### 룰

1. **검색 단계에서**: STEP 1 lead-root 보강 쿼리 2개로 root 추적 (위 STEP 1 참조)
2. **분류 단계에서**: thread root 회사명 hit이면 reply 본문에 회사명 없어도 동일 딜로 분류 (STEP 2 보강 룰)
3. **확장 단계에서**: hit한 root·reply 모두 thread 전체 read 필수 (STEP 2.5)
4. **출력 단계에서**: thread reply에는 `from_lead_thread=true` 부착 → 6번 LLM이 *lead 배분 thread임을 인지하고 reply만으로도 활동량 측정*

### 검증 사례 — 지멘스 4/21 (4/30 발견 → 5/6 차단)

| 항목 | 4/30 검증 (보강 전) | 5/6 차단 (보강 후) |
|---|---|---|
| Thread root | 4/9 16:43 권노을 lead 배분 | hit ✅ |
| Reply 1·2·3 (4/9~10 권노을 후속) | hit X | thread expansion으로 잡힘 |
| **Reply 4·5 (4/21 (target_ld) 커리큘럼·강사)** | **❌ 누락** | **✅ thread expansion으로 잡힘** |
| 6번 거래 의지 raw | "lead 배분 후 우선 대기"만 | "+ 4/21 커리큘럼 작업·강사 확인 진행 중" 추가 |
| 거래 진척도 판단 | 정체 | 진행 중 |

### 메모리 cross-link

- `~/.claude/projects/C--Users-GA/memory/project_slack_search_lead_distribution_pattern.md` (2026-04-30 박은 룰)

## 아웃풋

```json
{
  "deal_id": "019baba5-418d-733f-aab9-4d330145b8f6",
  "deal_name": "카카오페이_26년도 전사 AI 연간 교육",
  "slack_results": [
    {
      "date": "2026-04-10",
      "channel": "#b2b_2팀_견적제안",
      "author": "{owner_name}",
      "message_preview": "카카오페이 견적 수정 버전 올립니다. 변경점: ...",
      "thread_link": "https://slack.com/..."
    }
  ],
  "slack_summary": "4/10 견적 수정 완료, 고객 회신 대기 중",
  "activity_flag": null
}
```

**대화 부재 딜 예시:**
```json
{
  "slack_results": [],
  "activity_flag": "⚠️ 대화 부재 — 마지막 메모 81일 전, 최근 2주 슬랙 대화 없음"
}
```

### 필드 의미

| 필드 | 용도 |
|------|------|
| `slack_results` | 스킬 5 — 메모에 없는 최신 컨텍스트 보충 (원문 보존) |
| `slack_summary` | 스킬 5 — 해당 딜의 최근 슬랙 대화 흐름 1줄 요약 (LLM) |
| `activity_flag` | 스킬 5 — 방치 딜 감지 시그널 |
| `thread_link` | 스킬 6 (MD 리포트) — 링크로 슬랙 원문 이동 |

## 실패 시 처리

| 실패 유형 | 반환 필드 | 기본값 |
|---------|---------|-------|
| Slack MCP 연결 실패 | 전체 | default_response |
| 검색 성공, 결과 0건 | `slack_results` | `[]` |
| LLM 요약 실패 | `slack_summary` | `null` |
| 일부 딜만 실패 | 해당 딜만 default_response | 경고 로그만 |

**default_response:**
```json
{
  "slack_results": [],
  "slack_summary": null,
  "activity_flag": null,
  "thread_link": null
}
```

**오케스트레이터 정책:**
- 스킬 3 전체 실패 시에도 Phase 2 계속 진행 (스킬 2/4 결과로 보조)
- 스킬 5(스코어링)는 `slack_summary`가 null이어도 기준 5(최근 연락)는 스킬 4 결과로 대체하여 작동

## MCP 호출 (2026-05-06 보강 — thread expansion 도구 추가)

### 1. 검색 도구 (STEP 1)

```
도구: mcp__claude_ai_Slack__slack_search_public_and_private
파라미터:
  query: "{고객사명} from:@{owner_name}" 또는 "{고객사명} @{owner_name}"
         또는 lead-root 보강 쿼리 (STEP 1 참조)
  channel_types: "public_channel,private_channel"   ← 5/6 신규 (DM 차단)
  (검색 기간은 슬랙 검색 문법 before/after로 조합)
```

**`channel_types` 차단 룰 (2026-05-06 신규)**:
- 기본값 `"public_channel,private_channel,mpim,im"` (전체) → **`"public_channel,private_channel"`로 강제 제한**
- 차단 대상: `im`(1:1 DM)·`mpim`(그룹 DM)
- **사유**: 4/30 슬랙 DM에 박힌 *(target_ld) 답지* 일부가 LLM prompt에 누출될 위험 식별 (메모리 `feedback_answer_key_llm_isolation`). 검증 답지·내부 회의 메모는 DM에 박히는 패턴 잦음 → 6번 거래 의지 LLM raw에 들어가면 prompt 오염
- **부작용**: OM → LD DM의 딜 진행 업데이트도 같이 차단됨 → 알려진 제약·주의사항 표 참조

### 2. Thread expansion 도구 (STEP 2.5 — 신규)

```
도구: mcp__claude_ai_Slack__slack_read_thread
파라미터:
  channel_id: hit 메시지의 채널 ID (예: C096A5Z7S0Y = #b2b_2팀_견적제안)
  message_ts: hit 메시지의 thread_ts (없으면 ts 자체)
              ↑ 형식: 문자열 + 소수점 (예: "1775720591.399409")
  limit: 100~200 (default 100, 긴 thread 대응)
  response_format: "detailed" (작성자·시각·reactions·files 모두 포함)
```

**호출 시점 룰**:
- 검색 hit당 1회 호출이 원칙. 단, 동일 thread_ts는 1회만 (중복 차단).
- thread reply가 1건도 없는 단독 메시지(reply_count=0)는 호출 skip → 토큰 절약.

> 비공개 채널 접근은 LD가 해당 채널 멤버인 경우에만 가능.

## 알려진 제약·주의사항

| 항목 | 내용 |
|------|------|
| 검색 기간 | 최근 2주 고정 (캘린더와 동일) |
| 노이즈 | 고객사명 + 담당자 필터로 최소화. 동일 고객사 다른 딜 구분 불가 (중복 허용) |
| DM 차단 (5/6 갱신) | **DM(`im`·`mpim`) 자동 차단** — 답지·내부 메모 누출 위험 (4/30 사고). OM → LD DM의 딜 업데이트는 추후 v2 영역에서 *별도 채널 추출 도구*로 안전 회수 검토. |
| 비공개 채널 | 멤버인 경우만 가능 |
| 검색 횟수 | base 1회 + lead-root 보강 1~2회 + thread expansion (hit thread당 1회) — 총 가변 |
| **Lead 배분 패턴** | 팀장·파트장 root + LD reply 패턴. thread 전체 read 필수 (회사명 단독 검색은 reply 누락). 지멘스 4/21 사고 5/6 차단. |
| **Thread expansion 비용** | hit thread 수에 비례한 호출. base 검색 결과가 N건이면 최대 N회 호출 (reply 없는 단독 메시지는 skip). |

## LD 체크 포인트

1. ~~DM 포함 검색 사전 확인~~ → 5/6부터 DM 자동 차단 (변경됨)
2. `settings.slack_channels` 목록이 본인 업무 채널을 잘 포괄하는지
3. 고객사 약어 사용 여부 (예: "현건" = 현대건설) → 매칭 누락 위험
4. (5/6 신규) DM에 박힌 딜 업데이트가 누락되더라도 OK인지 — 본 룰은 답지·내부 메모 누출 차단이 우선이라 DM 정보는 메모 테이블·공식 채널 raw에서 보강 가능 영역

## 기존 플러그인과의 차이

- `owner_name`, `slack_channels` 하드코딩 → settings 외부화 (다중 LD 지원)
- 1팀/2팀 고정 목록 제거 → LD가 settings에서 자율 관리
