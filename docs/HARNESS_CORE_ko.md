# PRISM 하네스 헌장 (Claude·Codex 공통)

사용자와 에이전트가 같은 방향으로 개발하기 위한 최상위 약속이다. 세부 절차는 아래 표의 원문 문서에
있고, 이 헌장은 그 문서들을 언제 읽어야 하는지와 어겨서는 안 되는 선만 담는다. 원문과 충돌하면 더
보수적인 쪽을 따른다. 이 블록은 `AGENTS.md`에 그대로 복사되며, `tests/test_harness_layers.py`가
두 사본이 같은지 검사한다(고칠 때는 이 파일을 고치고 `python tools/harness/sync_agents_core.py` 실행).

## 북극성 (사용자 정의, 2026-10-04)

- 오닐식 추세추종이다. 손익비를 관리하면서 **크게 가는 소수 종목**으로 계단식 성장을 한다.
- 초분할·재진입은 낙폭을 관리하면서 가는 종목을 놓치지 않고 올라타기 위한 수단이다.
- 핵심은 스크리닝과 좋은 종목을 사는 눈의 정확도다. 손절만 반복되는 빈도는 계좌를 녹인다.
- 모든 변경·검증·보고는 큰 수익 종목 포착과 비중 확대, 손실·낙폭, 손절 비용, 스크리닝 정확도로 판단한다.

## 함께 일하는 방식

- 방향이 갈리는 결정(가설 정의·대상 종목·시간축·성공 기준·적용 방식, 완화 vs 강화)은 실행 전에 사용자에게 묻는다.
- "비교해봐"는 지금 몇 건으로 하는 일회성 비교다. SHADOW·배포로 넓히려면 먼저 한 줄로 확인받는다.
- 매매 규칙은 KR·US를 같은 방향으로 바꾼다. 한쪽에서 정한 결정을 다른 쪽에서 다시 묻지 않는다.
- 사용자 보고는 한국어, 쉬운 투자자 용어로 짧게(결론·핵심 숫자·다음 행동) 한다. 내부 약어는 풀어 쓴다.

## 넘지 않는 선

1. SHADOW·LIVE를 자동으로 켜지 않는다. LIVE는 사용자의 명시적 승인이 필요하다. BTC는 Bybit 데모이며 실자금은 별도 승인이다.
2. `MISSING`(모름)을 0·실패·통과로 채우지 않는다. 판단 당시 없던 값으로 과거를 복원해 일치했다고 말하지 않는다.
3. 한 거래·한 트리거의 결과를 반례 검증 없이 전역 차단 규칙이나 프롬프트 벌점으로 올리지 않는다.
4. 한 번에 한 결정 계층만 바꾼다. 같은 근거를 스크리닝·게이트·프롬프트에 중복으로 넣지 않는다.
5. 운영에서 LLM이 승인하는 규칙을 결정론 재생만으로 채택·기각하지 않는다.
6. 뉴스·테마는 참고 자료다. 매수·보유·매도 규칙의 근거로 쓰지 않는다.
7. 매매 프롬프트를 바꾸면 형제 프롬프트(BUY·SELL·재진입 × KR·US × ko·en)와 실제 코드를 대조한다. 방향이 갈리는 모순은 사용자와 정한다.
8. 테스트 통과는 운영 검증이 아니다. 배포 확인과 첫 정기 실행 확인을 따로 보고한다.

## 상황별로 먼저 읽을 문서

| 상황 | 먼저 읽기 | 스킬·도구 |
|---|---|---|
| KR·US 스크리닝·국면·진입·청산·비중 변경, 새 전략·트리거 | `docs/TRADING_CHANGE_REVIEW_HARNESS.md`(전략 도입 검토·최소 검토 기록 6항목), `docs/RESEARCH_LESSONS_ko.md` | `prism-trading-change-review` |
| BUY·SELL·보유·재진입 프롬프트 변경 | 위 하네스의 "Prompt framing and logical-consistency review" 절 | `tests/test_sell_prompt_consistency.py` 방식 |
| 매수품질·트리거 비교·재생·승격 검토 | `docs/ENTRY_QUALITY_EVOLUTION_ko.md`, `docs/ENTRY_QUALITY_DATA_ANALYSIS_HARNESS.md` | `prism-entry-quality-analysis` |
| BTC 전략·실행·백테스트·위험·배포 | `docs/BTC_ROADMAP_ko.md`(마일스톤 ID), `docs/BTC_PROOF_GATES_20261010_ko.md` | `prism-btc-change-review` |
| 뉴스·테마 작업 | `docs/THEME_NEWS_ROADMAP_ko.md` | |
| 배포·장애 수리 | `docs/SERVER_GIT_OPERATIONS_ko.md`의 변경 범위별 검증 게이트 | |
| 2주 정기 점검 | `docs/TWO_WEEK_REVIEW_ko.md`, 하네스의 프롬프트 주기 점검 | `tools/two_week_review.py` |

## 이 헌장이 들어오는 길

`CLAUDE.md`는 `@`로 가져오고 `AGENTS.md`는 사본을 둔다. 매매 관련 질문이나 매매 파일 수정 때는 훅이 요약을
다시 넣는다(`tools/harness/hook.py`). 경로별 룰은 `.claude/rules/`, 스킬은 `skills/`에 있다. 전체 구조는
`docs/HARNESS_STRUCTURE_ko.md`를 본다.
