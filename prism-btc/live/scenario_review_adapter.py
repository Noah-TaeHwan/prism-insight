"""Explicit, default-off callback for calculated reviews; never submits orders."""
from analysis.scenario_review_tape import record_review
from live.scenario_calculated_review import research_review
from live.scenario_preview import response_contract
from live.scenario_provenance import review_policy_binding


def make_calculated_review_proposer(*, generate, clock):
    if not callable(generate) or not callable(clock):
        raise ValueError("review_dependencies_required")

    def propose(snapshot, context, *, record):
        def no_retry_generate(**request):
            if request.get("retry_transport") is not False:
                raise ValueError("review_retry_forbidden")
            return generate(**request)

        return record_review(research_review, snapshot, context, response_contract(context),
            generate=no_retry_generate, record=record, clock=clock,
            bindings=review_policy_binding())

    return propose
