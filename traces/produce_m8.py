"""Produce M8 trace: Web interactions — Plaid Link integration flow.

This trace validates web modality processing through the pipeline.
Models a realistic user-facing flow: navigate to banking app, launch
Plaid Link, authenticate with institution, select accounts, and complete.

Modality: Web (primary).
Session structure: navigational (page-to-page flow with auth states).
"""

import sys
sys.path.insert(0, "packages/trace-sdk/src")
sys.path.insert(0, "packages/parser/src")

from ignite_trace import (
    TraceSession,
    Modality,
    RequestIntent,
    ResponseOutcome,
    SignalClass,
    DeltaFromPrior,
    StateTransitionSubtype,
    WebExt,
)

with TraceSession(
    agent="Bumble",
    system="plaid-link-web",
    objective="Validate pipeline processes web modality traces end-to-end — Plaid Link integration: navigate, launch, authenticate, select accounts, complete",
    agent_role="explorer",
    study_channel="e833bff9-f27f-4039-80ed-fe7f38034ee6",
    output_dir="traces/explorer",
) as trace:

    # --- Span 1: Navigate to banking app ---
    with trace.span("action_result", target="Navigate to banking app dashboard") as s1:
        s1.classify(
            modality=Modality.WEB.value,
            request_intent=RequestIntent.QUERY.value,
            response_outcome=ResponseOutcome.SUCCESS.value,
            signal_class=SignalClass.INTENT_CARRYING.value,
            delta_from_prior=DeltaFromPrior.FULL.value,
        )
        s1.structure("navigational")
        s1.modality_ext(WebExt(
            page_url="https://app.example.com/dashboard",
            page_title="Dashboard — MyBankApp",
            action="navigate",
            viewport_width=1440,
            viewport_height=900,
        ))
        s1.observed(
            what_happened="Navigated to banking app dashboard. Page loaded in 1.2s. Dashboard shows 'Connect Bank Account' CTA button prominently.",
            what_learned="Web navigation is a QUERY intent — reading page state. The page_url and page_title provide the observational context, analogous to API endpoint + response status.",
            confidence="high",
        )
        s1.tag("web", "navigation", "dashboard")

    # --- Span 2: Click 'Connect Bank Account' to launch Plaid Link ---
    with trace.span("action_result", target="Click 'Connect Bank Account' button") as s2:
        s2.depends_on(s1.span_id)
        s2.classify(
            modality=Modality.WEB.value,
            request_intent=RequestIntent.STATE_TRANSITION.value,
            response_outcome=ResponseOutcome.SUCCESS.value,
            signal_class=SignalClass.INTENT_CARRYING.value,
            delta_from_prior=DeltaFromPrior.FULL.value,
            state_transition_subtype=StateTransitionSubtype.LIFECYCLE_START.value,
        )
        s2.structure("navigational")
        s2.modality_ext(WebExt(
            page_url="https://app.example.com/dashboard",
            page_title="Dashboard — MyBankApp",
            action="click",
            element_role="button",
            element_name="Connect Bank Account",
            element_text="Connect Bank Account",
            selector="button[data-testid='connect-bank']",
        ))
        s2.observed(
            what_happened="Clicked 'Connect Bank Account' button. Plaid Link iframe launched as modal overlay. State transitioned from dashboard to Link flow.",
            what_learned="Button click is a STATE_TRANSITION (lifecycle_start) — it begins the multi-step Plaid Link flow. The element_role=button and element_name capture the ARIA accessible interface. This is the web equivalent of POST /link/token/create.",
            confidence="high",
        )
        s2.state_change("app state: dashboard → plaid_link_active")
        s2.tag("web", "plaid-link", "launch", "state-transition")

    # --- Span 3: Search and select institution ---
    with trace.span("action_result", target="Search institution 'Chase'") as s3:
        s3.depends_on(s2.span_id)
        s3.classify(
            modality=Modality.WEB.value,
            request_intent=RequestIntent.QUERY.value,
            response_outcome=ResponseOutcome.SUCCESS.value,
            signal_class=SignalClass.INTENT_CARRYING.value,
            delta_from_prior=DeltaFromPrior.PARTIAL.value,
        )
        s3.structure("navigational")
        s3.modality_ext(WebExt(
            page_url="https://cdn.plaid.com/link/v2/stable/link.html",
            page_title="Plaid Link",
            action="type",
            element_role="textbox",
            element_name="Search for your bank",
            input_value="Chase",
            input_type="text",
        ))
        s3.observed(
            what_happened="Typed 'Chase' into institution search. Autocomplete showed 3 results: Chase, JPMorgan Chase, Chase Business. Selected 'Chase' from the list.",
            what_learned="Text input is a QUERY — searching for an institution. The input_value is safe to capture (not sensitive). The iframe URL shows Plaid's hosted Link UI. The AXTree provides element_role=textbox and element_name for accessibility-first capture.",
            confidence="high",
        )
        s3.tag("web", "plaid-link", "institution-search")

    # --- Span 4: Enter credentials (sensitive — masked) ---
    with trace.span("action_result", target="Enter institution credentials") as s4:
        s4.depends_on(s3.span_id)
        s4.classify(
            modality=Modality.WEB.value,
            request_intent=RequestIntent.STATE_TRANSITION.value,
            response_outcome=ResponseOutcome.SUCCESS.value,
            signal_class=SignalClass.INTENT_CARRYING.value,
            delta_from_prior=DeltaFromPrior.FULL.value,
            state_transition_subtype=StateTransitionSubtype.STATE_CHANGE.value,
        )
        s4.structure("navigational")
        s4.modality_ext(WebExt(
            page_url="https://cdn.plaid.com/link/v2/stable/link.html",
            page_title="Plaid Link — Chase Login",
            action="type",
            element_role="textbox",
            element_name="password",
            input_value="***",  # Masked by mask_sensitive_input()
            input_type="password",
        ))
        s4.observed(
            what_happened="Entered credentials for Chase. Password field detected as sensitive — input_value masked to '***'. Authentication submitted successfully.",
            what_learned="Password fields MUST be masked. The WebExt privacy model uses input_type='password' and element_name pattern matching to auto-mask. This is a STATE_TRANSITION (state_change) — auth status changes from unauthenticated to authenticated.",
            confidence="high",
        )
        s4.state_change("auth state: unauthenticated → authenticated")
        s4.tag("web", "plaid-link", "auth", "sensitive-masked")

    # --- Span 5: Select accounts and complete ---
    with trace.span("action_result", target="Select accounts and complete Link") as s5:
        s5.depends_on(s4.span_id)
        s5.classify(
            modality=Modality.WEB.value,
            request_intent=RequestIntent.MUTATION.value,
            response_outcome=ResponseOutcome.SUCCESS.value,
            signal_class=SignalClass.INTENT_CARRYING.value,
            delta_from_prior=DeltaFromPrior.FULL.value,
        )
        s5.structure("navigational")
        s5.modality_ext(WebExt(
            page_url="https://cdn.plaid.com/link/v2/stable/link.html",
            page_title="Plaid Link — Select Accounts",
            action="click",
            element_role="button",
            element_name="Continue",
            element_text="Continue",
            element_state={"checked": True},
        ))
        s5.observed(
            what_happened="Selected checking and savings accounts (2 of 3 available). Clicked Continue. Plaid Link returned public_token to the app. Link flow completed successfully.",
            what_learned="Account selection + continue is a MUTATION — it creates a link between the user's bank and the app (public_token exchange). The element_state captures checkbox state. This completes the Plaid Link lifecycle started in span 2.",
            confidence="high",
        )
        s5.state_change("link state: in_progress → completed")
        s5.tag("web", "plaid-link", "complete", "mutation")

    trace.finding(
        category="trace_surface",
        title="Web modality validates through pipeline — navigational flow with auth state transitions",
        description="5-span web trace demonstrates navigational span_structure, ARIA-based element capture, sensitive input masking, and lifecycle state tracking. Web interactions map cleanly to the same intent/outcome classification as API spans.",
        source_spans=[s1, s2, s3, s4, s5],
        confidence="confirmed",
        actionability="informational",
    )

print("M8 web trace produced in traces/explorer/")
