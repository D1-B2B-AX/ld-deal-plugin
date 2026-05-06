-- 스킬 1: 세일즈맵 조회 (SQL 단계 전용)
-- 담당자의 SQL 단계 활성 딜 + 고객사 + 고객사 담당자 결합
-- settings.target_ld.name을 LIKE 파라미터로 치환하여 다중 LD 지원

-- ============================================
-- 메인 쿼리: SQL 단계 딜 + 고객사 + 담당자
-- ============================================
SELECT
  d.id AS deal_id,
  d."이름" AS deal_name,
  d."파이프라인 단계" AS stage_raw,         -- JSON 문자열, 후처리로 name 추출
  d."성사 가능성" AS win_probability_raw,   -- '["낮음"]' 형태
  d."예상 체결액" AS expected_amount,
  d."금액" AS amount,
  d."수주 예정일" AS expected_close_date,
  d."제안서 마감일" AS proposal_deadline,
  d."마감일" AS deadline,
  d."상태" AS status,
  d."과정포맷" AS course_format,
  d."교육 주제" AS course_topic,
  d."예상 교육 인원" AS expected_learners,
  d."예상 교육 일정" AS expected_schedule,
  d."기업 니즈" AS customer_needs,
  d."상담 문의 내용" AS inquiry,
  d."기획시트 링크" AS planning_sheet_link,
  d."최근 노트 작성일" AS last_note_date,
  d."최근 파이프라인 단계 수정 날짜" AS last_stage_change,
  d.organizationId,
  o."이름" AS organization_name,
  o."업종" AS industry,
  o."기업 규모" AS company_size,
  o."성사된 딜 개수" AS past_won_deals,
  o."총 매출" AS total_revenue,
  o."최근 딜 성사 날짜" AS last_won_date,
  d.peopleId,
  p."이름" AS contact_name,
  p."이메일" AS contact_email,
  p."직급/직책" AS contact_title,
  p."담당 업무" AS contact_role
FROM deal d
LEFT JOIN organization o ON d.organizationId = o.id
LEFT JOIN people p ON d.peopleId = p.id
WHERE d."담당자" LIKE '%{owner_name}%'   -- settings.target_ld.name 치환 (다중 LD 지원)
  AND d."상태" = 'SQL'                    -- SQL 단계만 (Won/Lost/Convert 전부 제외)
ORDER BY d."최근 파이프라인 단계 수정 날짜" DESC;


-- ============================================
-- 보조 쿼리: 각 딜의 최근 메모 N건
-- ============================================
-- :deal_ids 는 메인 쿼리 결과의 deal_id 목록으로 치환
SELECT
  m.dealId,
  m.createdAt,
  m."유형" AS memo_type,
  substr(m.text, 1, 500) AS text_preview
FROM memo m
WHERE m.dealId IN (:deal_ids)
ORDER BY m.dealId, m.createdAt DESC;
-- 애플리케이션 레벨에서 dealId별 최근 3건으로 slice
