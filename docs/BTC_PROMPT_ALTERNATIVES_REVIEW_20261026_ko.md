# 미배포 후보 #1126의 프롬프트 검토

파일: `prism-btc/live/scenario_calculated_review.py`, 상수: `DRAFT_PROMPT`.
기준 486a4e38, 후보 cad393b8. `SELECT_PROMPT`·계산·주문·위험·보유 규칙은 동일합니다.
실험에서 두 사례 모두 후보 한 개만 생성해 HOLD입니다. 아래 문구는 운영 적용 상태가 아닙니다.

## 실제 추가한 영어 원문

```text
Only for a fresh flat OPEN (no active scenario, confirmed positions or pending entries),
use the existing two-candidate cap to compare meaningfully different executable plans
when both have market support. Start with the strongest supported entry plan. A second
may use a different structurally grounded entry location OR a different supported TP /
intentional protected-remainder allocation. State the changed choice and its market
evidence in the existing rationale; do not add fields or calculate payoff yourself.
Different confidence, smaller allocation, cosmetic price changes or a copied plan are
not meaningful alternatives. One grounded candidate remains valid; do not fabricate a
second direction, price level, distant target or runner just to fill the second slot.
Preserve the thesis's structural invalidation and bounded goal when comparing its
alternatives; never tighten SL or stretch targets to manufacture a better ratio.
If a strong directional move supports an executable entry now, preserve that option
rather than replacing every candidate with a pullback/retest or future-close condition.
An alternative waiting entry needs its own supplied price structure and executable
trigger semantics. Do not impose a universal waiting rule or fixed TP/runner percentage.
The host sizes and prices each independent proposal; the unchanged SELECT compares
its supplied calculations. No extra call, candidate or risk permission is created.
```

## 대응하는 한국어 설명

1. 활성 시나리오·실제 보유·미체결 진입이 모두 없는 새 진입에만 적용합니다.
2. 기존 최대 두 후보 안에서 시장 근거가 있는 서로 다른 실행 계획을 비교합니다.
   가장 근거 있는 진입안을 먼저 만들고, 두 번째는 다른 진입 위치 또는 다른 익절·보호 잔량 배분일 수 있습니다.
3. 무엇을 바꿨고 어떤 시장 근거가 있는지는 기존 rationale에 씁니다. 새 필드를 추가하거나 모델이 손익을 직접 계산하지 않습니다.
4. 자신감·위험 배분·가격을 조금만 바꾼 복사본은 의미 있는 대안이 아닙니다.
   근거가 하나뿐이면 한 후보도 허용합니다. 두 칸을 채우려고 반대 방향·가격·먼 목표·러너를 만들지 않습니다.
5. 같은 진입 전제를 비교할 때 구조적 무효화와 제한된 목표를 보존합니다.
   손익비 숫자를 좋게 만들려고 손절을 좁히거나 목표를 늘리지 않습니다.
6. 강한 방향성 움직임에서 지금 실행 가능한 진입 근거가 있다면 그 선택지를 남깁니다.
   모든 후보를 눌림·재시험·추가 종가 대기로 바꾸지 않습니다.
7. 기다리는 진입안에는 제공된 가격 구조와 실제로 실행할 수 있는 발동 조건이 필요합니다.
   모든 상황에 대기 규칙이나 고정 익절·러너 비율을 강제하지 않습니다.
8. 기존 프로그램이 각 독립 후보의 수량·가격을 계산하고, 변경하지 않은 SELECT가 이를 비교합니다.
   호출 수·후보 수·위험 권한을 늘리지 않습니다.

한국어 설명은 검토용이며 모델에 이중으로 넣지 않았습니다. 문구가 들어갔다는 사실과
실제로 대안이 생성됐다는 사실, 수익이 개선됐다는 사실은 별도 판정합니다.
