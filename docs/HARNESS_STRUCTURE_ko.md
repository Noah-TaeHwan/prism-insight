# PRISM 하네스 구조

하네스는 사용자와 AI 에이전트(Claude Code·Codex)가 같은 방향으로 매매 시스템을 개발하기 위한 장치다.
매매 실행 경로에는 아무것도 추가하지 않는다. 원문 규칙은 문서 하나씩만 두고, 그 문서를 **언제 꺼내 볼지**를
아래 층들이 자동으로 맡는다. (2026-10-11 구성)

## 층

| 층 | 무엇 | Claude Code | Codex | 언제 작동 |
|---|---|---|---|---|
| 0. 원문 | 하네스·로드맵·교훈 문서 | `docs/` | `docs/` | 층 1~4가 가리킬 때 |
| 1. 헌장 | 북극성·함께 일하는 방식·넘지 않는 선 8개·상황별 문서표 | `CLAUDE.md`가 `@docs/HARNESS_CORE_ko.md`로 가져옴 | `AGENTS.md` 사본(`tools/harness/sync_agents_core.py`) | 모든 대화 시작 |
| 2. 훅 | 질문·파일 수정 감지 → 요약 주입 | `.claude/settings.json` | `.codex/hooks.json`(`/hooks`에서 신뢰 등록) | 매매·BTC·분석 단어, 매매·BTC 파일 수정 |
| 3. 스킬 | 검토 절차 실행 | `.claude/skills/` → `skills/` 링크 | `.agents/skills/` → `skills/` 링크 | 요청이 설명과 맞을 때, 또는 직접 호출 |
| 4. 경로 룰 | 파일별 짧은 체크리스트 | `.claude/rules/prism-*.md`(`paths`) | `prism-btc/AGENTS.md` + 훅 | 해당 파일을 읽거나 고칠 때 |
| 5. 테스트 | 어긋남 방지 | `tests/test_harness_layers.py`, 프롬프트 일치·해시 테스트 | 같음 | CI |

## 원문 문서

| 영역 | 문서 |
|---|---|
| KR·US 매매 변경 | `TRADING_CHANGE_REVIEW_HARNESS.md`(전략 도입 검토·프롬프트 모순 점검·필수 7단계), `RESEARCH_LESSONS_ko.md` |
| 데이터 분석 | `ENTRY_QUALITY_EVOLUTION_ko.md`, `ENTRY_QUALITY_DATA_ANALYSIS_HARNESS.md` |
| BTC | `BTC_ROADMAP_ko.md`, `BTC_PROOF_GATES_20261010_ko.md`, `BTC_SYSTEM_BLUEPRINT_20261005_ko.md` |
| 배포 | `SERVER_GIT_OPERATIONS_ko.md` |
| 뉴스·테마 | `THEME_NEWS_ROADMAP_ko.md` |
| 정기 점검 | `TWO_WEEK_REVIEW_ko.md` |

## 스킬

| 스킬 | 대상 |
|---|---|
| `prism-trading-change-review` | KR·US 스크리닝·진입·청산·비중·재진입·프롬프트 변경 |
| `prism-btc-change-review` | BTC 전략·실행·보호·백테스트·수익 주장·배포 |
| `prism-entry-quality-analysis` | 매수품질·트리거 비교·재생·승격 검토 |

## 고치는 법

- 규칙 내용: 원문 문서를 고친다. 헌장에 들어갈 만큼 중요하면 `docs/HARNESS_CORE_ko.md`도 고치고 sync 스크립트를 돌린다.
- 매매 파일 목록: `tools/harness/hook.py`의 경로 목록과 `.claude/rules/prism-*.md`의 `paths`를 함께 고친다(테스트가 비교).
- 훅이 너무 자주·드물게 걸리면 `hook.py`의 키워드를 조정한다. 훅은 막지 않고 알려만 준다.
