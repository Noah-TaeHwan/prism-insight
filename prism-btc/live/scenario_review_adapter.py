"""Explicit, default-off callback for calculated reviews; never submits orders."""
from analysis.scenario_review_tape import record_review
from functools import partial
from live.scenario_calculated_review import research_review
from live.scenario_preview import response_contract
from live.scenario_provenance import review_policy_binding


def make_calculated_review_proposer(*, generate, clock, research_mode="legacy"):
    if research_mode not in {"legacy", "allocation-single", "allocation-calculated",
                             "action-allocation-single-v1", "action-allocation-calculated-v1",
                             "economic-reject-single-v1", "economic-reject-calculated-v1"}:
        raise ValueError("research_review_mode")
    if not callable(generate) or not callable(clock):
        raise ValueError("review_dependencies_required")

    def propose(snapshot, context, *, record):
        def no_retry_generate(**request):
            if request.get("retry_transport") is not False:
                raise ValueError("review_retry_forbidden")
            return generate(**request)

        review = research_review if research_mode == "legacy" else partial(research_review, research_mode=research_mode)
        return record_review(review, snapshot, context, response_contract(context),
            generate=no_retry_generate, record=record, clock=clock,
            bindings=review_policy_binding(research_mode=research_mode))

    return propose
