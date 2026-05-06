# 딜 판단 플러그인 (ld-deal-plugin)

> LD(영업 리더)가 매일 **SQL 단계 딜 우선순위를 3분 안에 파악**할 수 있는 Claude Code 플러그인.
> 4개 데이터 소스 + 기획시트를 통합 수집하고, 7개 기준 정량 스코어링 + LLM 판단으로 단일 MD 리포트를 생성합니다.

## 목적

**운영(Won) 제외, 세일즈 우선순위 판단에 집중**한 경량 버전.
- 수주 전 SQL 단계 딜만 대상 → 빠른 실행 (5~8분)
- MD 리포트 → Slack/Notion 붙여넣기 즉시 활용
- LD 다수 지원 → settings.json으로 본인 값 세팅

**기존 `deal-priority-plugin`(풀버전)과의 관계:**
- 풀버전 = 단일 LD 개인 실무 (SQL + Won 운영, HTML 리포트)
- 본 플러그인 = LD 경량 요약 (SQL 전용, MD 리포트)
- 두 플러그인 병행 가능, 용도에 따라 선택 사용

## 설계 원칙

**"스코어링은 코드, 맥락 판단은 LLM, 최종 의사결정은 사람"**
- 점수 산출·티어 분류·검증 = Python 스크립트 (결정론적, 재현 가능)
- 딜 출처·전략 고객 판별·reason 생성 = LLM
- LLM이 점수를 바꾸지 못하도록 분리 — 환각 리스크 차단

## 실행 파이프라인

```
Phase 0: 환경 확인 + DB 자동 다운로드 (urllib, gh CLI 불필요)
Phase 1: 세일즈맵 SQL 딜 조회 (스킬 1)
Phase 2: 캘린더·슬랙·지메일·드라이브 병렬 수집 (스킬 2~5)
Phase 2.5: LLM 보조 데이터 보완 (deal_origin, 일정 추정 등)
Phase 3: 스코어링 (merge_deals → calculate_score → verify)
Phase 3.5: LLM reason + next_action 일괄 생성
Phase 4: 변화 감지 + MD 리포트 생성
```

## 폴더 구조

```
ld-deal-plugin/
├── README.md                          # 이 문서
├── 딜판단.md                          # 슬래시 커맨드 (/딜판단)
├── IMPLEMENTATION_CHECKLIST.md        # 구현 체크리스트
├── config/
│   ├── settings.example.json          # 초기 설정 템플릿
│   └── settings.json                  # LD 본인 설정 (gitignored)
├── skills/
│   ├── 오케스트레이터.md              # 메인 지휘자
│   ├── 1-salesmap-조회/SKILL.md
│   ├── 2-캘린더-조회/SKILL.md
│   ├── 3-슬랙-조회/SKILL.md
│   ├── 4-지메일-조회/SKILL.md
│   ├── 5-드라이브-기획시트-조회/SKILL.md
│   ├── 6-우선순위-판단/SKILL.md
│   └── 7-보고서-출력/SKILL.md
├── scripts/
│   ├── check_env.py                   # Phase 0: DB 자동 갱신 (urllib)
│   ├── merge_deals.py                 # Phase 3a: 5개 소스 → deals.json
│   ├── calculate_score.py             # Phase 3b: 7기준 스코어링
│   ├── verify_scores.py               # Phase 3c: 5항목 검증
│   ├── detect_changes.py              # Phase 4b: 변화 감지 (Won 구분)
│   ├── generate_md.py                 # Phase 4c: MD 렌더러
│   └── deals_input_schema.md          # 스키마 정의
├── outputs/                           # MD 리포트 (14일 자동 삭제)
├── archive/                           # 일별 스냅샷 (변화 추적)
├── test/sample_outputs/               # 테스트·샘플
└── docs/
    ├── information_criteria.md  # 7기준 정보화 매트릭스 (DIKW 3조건)
    └── 스코어링_가이드.md         # 점수 룩룩·티어 분류
```

## 필요한 MCP

| MCP | 용도 | 필수 여부 |
|-----|------|---------|
| salesmap (SQLite 직접 조회) | 딜·메모·고객사 조회 | 필수 |
| workspace-mcp | Gmail / Calendar / Drive | 필수 |
| Slack (claude.ai 커넥터) | 딜 관련 메시지 조회 | 권장 |

## 스코어링 기준 (만점 170점, 4/30 갱신)

| 구분 | 기준 | 배점 | 비고 |
|---|---|---|---|
| **메인** | 딜 금액 | +30 | `예상 체결액` + `Net(%)` 보조 |
| 서브 | 파이프라인 단계 | +18 | (5/10/13/14/16/18) |
| 서브 | 마감 임박도 | +22 | 3단계 트리 (제안서 마감일 → 수주 예정일 → LLM 추출) |
| 서브 | 고객 가치 | +23 | 4차원 (거래·규모·레퍼·확장) |
| 서브 | 소통 활성도 | +27 / -27 | 두 축 (슬랙 thread + 견적서 갱신) |
| **메인** | 거래 의지 | +30 | LLM 카테고리 (C1·C2·C3) + `성사 가능성` grounding |
| 서브 | 딜 출발점 | +20 | |

**티어 분류 (4/30 갱신):**
- 🔴 T1 집중: 87점+ (오늘 즉시 대응)
- 🟠 T2 관리: 55~86점 (이번 주 관계 육성)
- ⚪ T3 지켜보기: 55점 미만 (주기적 모니터링)

**액티브 게이트:** 단계 6종 (Proposal 준비 ~ 매출 집계 예정) + `성사 가능성 ≠ LOST`

## 설치·사용

### 1. 설치
```bash
git clone https://github.com/D1-B2B-AX/ld-deal-plugin.git \
  ~/.claude/commands/ld-deal-plugin
```

### 2. 설정 파일 복사 + 본인 값 세팅
```bash
cp ~/.claude/commands/ld-deal-plugin/config/settings.example.json \
   ~/.claude/commands/ld-deal-plugin/config/settings.json
```

`config/settings.json` 편집:
- `target_ld.name`: 분석 대상 LD 이름 (예: "홍길동")
- `target_ld.email`: 분석 대상 LD 회사 메일
- `target_ld.team`: "1팀" 또는 "2팀"
- `target_ld.calendar_id`: 분석 대상 LD 구글 캘린더 ID

### 3. 실행
Claude Code에서:
```
/딜판단
```

### 4. 결과 확인
`outputs/summary_report_YYYYMMDD.md` 파일을 에디터·Slack·Notion에서 확인.

## 5/6 갱신 (v0.1)

5/6에 핵심 룰 다수 정밀화 — 5/7 deploy 직전 상태:

- **슬랙 thread expansion** (lead 배분 패턴 차단) — root만 hit, LD reply 누락하던 사고 차단
- **6번 grounding 매트릭스 옵션 C** — LLM(high/mid/low) ↔ LD(`성사 가능성`) 비대칭 1단계 흡수. *한 단계 차이는 노이즈로 흡수, 두 단계 차이만 검토 권장*
- **strategic_keywords 그룹 prefix + 한글 변종** — 36 → 48 키워드. "삼성"으로 박으면 모든 계열사 자동 hit, "케이티"·"엘지" 한글 변종도 박힘
- **답지 LLM 격리 (DM 차단)** — `channel_types`로 슬랙 DM 자동 차단, 답지·내부 메모 누출 차단
- **외부 raw 자동 머지** — `enrich_external.py` 신규 (PHASE 2.5a). deal_id 기준 left join, 4/30엔 수동 박았던 영역 자동화
- **메일 검색 시점 한정 룰** — `email_search_window_days`(14일) + `email_after_date`(test 모드 한정). 가입 전 메일 자동 제외
- **5번 소통 활성도 4 축 확장** — 슬랙·견적시트만 보던 룰을 메일·메모까지 4 축. 메모 풍부 + 메일 양방향 딜이 정합 점수 받음

## 담당·일정

- **담당:** 전정현 (B2B AI Transformation팀)
- **시작:** 2026-04-20
- **시연 대상:** target_ld (settings.target_ld로 분기)
- **버전:** v0.1 (5/6 정밀화 후)
