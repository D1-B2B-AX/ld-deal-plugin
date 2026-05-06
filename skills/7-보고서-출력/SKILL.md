# 스킬 7: MD 보고서 출력

> 스킬 6(우선순위 판단)의 결과 + detect_changes.py의 변화 감지 결과를 받아 **LD용 Markdown 리포트**를 생성하는 최종 출력 스킬.

## 역할

이 플러그인의 **얼굴**. 스코어링·reason·next_action + 최근 변화를 LD가 3분 안에 파악할 수 있는 Markdown으로 조립.

## 인풋

| 파일 | 설명 |
|------|------|
| `scored_deals.json` | 스킬 6 결과 — 딜별 score/tier/reason/next_action/t1_candidate |
| `changes.json` | detect_changes.py --week 결과 — 7일 누적 + 날짜별 변화 |
| `settings.json` | owner, scoring threshold, retention_days 등 |

## 아웃풋

```
outputs/summary_report_YYYYMMDD.md
```

14일 초과 파일은 자동 삭제 (`settings.report.retention_days`).

## MD 리포트 구조 (5개 섹션)

### 섹션 1: 헤더
```markdown
# 딜 요약 — {owner.name} / {YYYY-MM-DD}
```

### 섹션 2: 📊 포트폴리오 현황
```markdown
## 📊 포트폴리오 현황

**전체:** {N}건 / {N.N}억
**이번 주:** 미팅 {N}건 | 팔로업 필요 {N}건 | 30일 내 결정 {N}건

**단계별 분포 (금액 기준):**
- 최종 f-up      ██████████ 3건 · 5.8억 (35%)
- 2차 f-up       ██████░░░░ 2건 · 3.5억 (21%)
- Proposal 송부  █████░░░░░ 2건 · 3.0억 (18%)
- Proposal 준비  ████░░░░░░ 2건 · 2.5억 (15%)
- 1차 f-up       ███░░░░░░░ 2건 · 2.0억 (11%)
```

**계산 규칙:**
- 팔로업 필요: `last_touch_days > 14`
- 30일 내 결정: `expected_close_date - today ≤ 30`
- 단계별 분포: 딜 금액 합계 기준. ASCII 바는 10칸, 최대 금액 = 10칸

### 섹션 3: 🔄 최근 변화 (최근 7일)

```markdown
## 🔄 최근 변화 (최근 7일)

**7일 누적:** 신규 {N}건 | 수주 성공 {N}건 | Lost {N}건 | 단계 진전 {N}건

**{YYYY-MM-DD}(요일, 오늘):**
- 📈 단계 진전: {고객사} ({from} → {to})

**{YYYY-MM-DD}(요일):**
- 🎉 수주 성공: {고객사} (SQL → Won, {N.N}억)
- ➕ 신규 SQL: {고객사}
- ⚠️ Lost: {고객사}

(변화 없는 날 생략)
```

**표시 규칙:**
- `cumulative` 요약을 최상단에 한 줄로
- `daily` 배열을 최신 → 오래된 순서로 출력
- 각 날짜 블록 내: added → removed (won/lost) → stage_changed 순
- 변화 0인 날은 생략

### 섹션 4: 🔴🟠⚪ 티어 본문

**T1 (3~4줄):**
```markdown
## 🔴 T1 집중 ({N}건) — 오늘 즉시 대응

### 1. {deal_name} — {N.N}억 | {pipeline_stage} {badges}
- **핵심 판단:** {reason 3줄까지}
- **다음 액션:** {next_action}
- {D-day 강조} | 최근 소통 {N}일 전
```

**T2 (2~3줄):**
```markdown
## 🟠 T2 관리 ({N}건) — 이번 주 관계 육성

### 1. {deal_name} — {N.N}억 | {pipeline_stage} {badges}
- {reason 2줄까지}
- {next_action}
```

**T3 (1줄):**
```markdown
## ⚪ T3 지켜보기 ({N}건) — 주기적 모니터링

- {deal_name} ({N.N}억, {pipeline_stage}) — {reason 1줄}
```

**뱃지 규칙 (텍스트 `[...]`):**
- `[핵심기업]` — `is_strategic_customer: true`
- `[신규딜]` — `is_new: true`
- `[재거래]` — `past_deal_count >= 1`
- `⭐T1 후보` — `t1_candidate: true` (T2에만 표시)

**D-day 시각 강조:**
- `🚨 D-{N}` — D-7 이내 긴급
- `⏰ D-{N}` — D-8~D-14 임박
- `D-{N}` — D-15 이상 일반

### 섹션 5: 📅 이번 주 + 다음 주 미팅

```markdown
## 📅 이번 주 + 다음 주 미팅

| 날짜 | 딜 | 내용 |
|---|---|---|
| 4/22(수) | 한화솔루션 | 최종 제안 리뷰 |
| 4/23(목) | 엘지전자 | 제안서 초안 공유 |
```

**수집 방식:**
- 딜별 `calendar_events` 배열에서 이번 주·다음 주 이벤트 추출
- 날짜 오름차순 정렬
- 이벤트 없으면 섹션 자체 생략 또는 "없음" 한 줄

### 하단 메타 (푸터)

```markdown
---
_생성: {YYYY-MM-DD HH:MM} | 실행 시간: {Xm Ys} | 데이터 소스: 세일즈맵·캘린더·슬랙·지메일·드라이브_
```

## 생성 절차 (`generate_md.py`)

```
STEP 1: scored_deals를 tier별로 분리 + total 내림차순 정렬
STEP 2: 단계별 분포 계산 (금액 합계 → ASCII 바)
STEP 3: 뱃지 조립 (is_strategic, is_new, past_deal_count ≥ 1, t1_candidate)
STEP 4: D-day 계산 + 강조 아이콘 매핑
STEP 5: 섹션 1~5 문자열 조립
STEP 6: 파일 저장 outputs/summary_report_YYYYMMDD.md
STEP 7: 오래된 파일 자동 삭제 (retention_days 초과)
STEP 8: 최종 완료 메시지 + 파일 경로 출력
```

## 에러 처리

| 상황 | 대응 |
|------|------|
| scored_deals 빈 배열 | "분석할 SQL 딜이 없습니다." 메시지 MD |
| changes.json 없음 | 최근 변화 섹션 생략 또는 "첫 실행입니다" |
| calendar_events 없음 | 미팅 섹션 생략 |
| 파일 저장 실패 | 터미널 텍스트 출력 (fallback) |

## 실행 완료 메시지

```
✅ 딜 요약 리포트 생성 완료
📄 outputs/summary_report_20260420.md
T1 집중 2건 · T2 관리 3건 · T3 지켜보기 6건 (전체 11건)
실행 시간: 6분 12초
```

- **금지:** 터미널에 딜별 이름·점수·판단 추가 출력 금지. 상세는 MD 리포트에서 확인.

## 기존 플러그인과의 차이

| 항목 | `deal-priority-plugin` | **`ld-deal-plugin`** |
|---|---|---|
| 출력 | HTML (CSS·JS·3컬럼 레이아웃) | **Markdown** |
| 템플릿 | priority_report_template_v5_1.html | summary_report_template.md |
| 렌더러 | generate_html.py (복잡) | **generate_md.py** (단순 문자열 조립) |
| 시각 요소 | 배터리 바·카드·아이콘 | **ASCII 바·텍스트 뱃지** |
| 복사·공유 | 스크린샷 | **Slack/Notion 붙여넣기 바로** |
| 실행 시간 | 수 초 | **1초 미만** (CSS·템플릿 처리 X) |
