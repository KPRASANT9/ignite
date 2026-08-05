"""Produce M7 trace: CLI operations — IGNITE pipeline invocation.

This trace validates the universal pipeline claim by processing CLI modality
spans end-to-end. Models a realistic developer CLI session: git operations,
pytest execution, pipeline invocation, and diff review — all using CliExt
modality extensions and v0.2 classification fields.

Modality: CLI (primary).
Session structure: narrative (assess → verify → execute → measure).
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
    CliExt,
)

with TraceSession(
    agent="Bumble",
    system="ignite-dev-workflow",
    objective="Validate pipeline processes CLI modality traces end-to-end — developer workflow: git status, pytest, pipeline run, git diff",
    agent_role="explorer",
    study_channel="e833bff9-f27f-4039-80ed-fe7f38034ee6",
    output_dir="traces/explorer",
) as trace:

    # --- Span 1: git status — assess working directory state ---
    with trace.span("action_result", target="git status (assess working directory)") as s1:
        s1.classify(
            modality=Modality.CLI.value,
            request_intent=RequestIntent.QUERY.value,
            response_outcome=ResponseOutcome.SUCCESS.value,
            signal_class=SignalClass.INTENT_CARRYING.value,
            delta_from_prior=DeltaFromPrior.FULL.value,
        )
        s1.modality_ext(CliExt(
            command="git",
            subcommand="status",
            args=["--short"],
            exit_code=0,
            stdout_summary="M  packages/parser/src/ignite_parser/analyzer.py\nM  packages/trace-sdk/src/ignite_trace/extensions/db_ext.py\n?? traces/produce_m6.py",
            working_dir="/Users/dev/ignite",
            shell="zsh",
        ))
        s1.observed(
            what_happened=(
                "Checked git status before running tests. 2 modified files (analyzer, db_ext) "
                "and 1 untracked file (produce_m6.py). Working directory is dirty — need to "
                "verify tests pass before committing."
            ),
            what_learned=(
                "CLI git status is a QUERY intent — it reads state without changing it. "
                "The stdout is structured (short format) and machine-parseable. "
                "Exit code 0 means git succeeded, not that the working tree is clean. "
                "CLI operations require interpreting both exit_code AND stdout to determine "
                "the actual result."
            ),
            confidence="high",
        )
        s1.tag("cli", "git", "status", "assessment")

    # --- Span 2: git branch — verify current branch ---
    with trace.span("action_result", target="git branch --show-current (verify branch)") as s2:
        s2.depends_on(s1.span_id)
        s2.classify(
            modality=Modality.CLI.value,
            request_intent=RequestIntent.QUERY.value,
            response_outcome=ResponseOutcome.SUCCESS.value,
            signal_class=SignalClass.INTENT_CARRYING.value,
            delta_from_prior=DeltaFromPrior.PARTIAL.value,
        )
        s2.modality_ext(CliExt(
            command="git",
            subcommand="branch",
            args=["--show-current"],
            exit_code=0,
            stdout_summary="feat/multi-modality-pipeline",
            working_dir="/Users/dev/ignite",
            shell="zsh",
        ))
        s2.observed(
            what_happened=(
                "Verified current branch is feat/multi-modality-pipeline. "
                "This is the assessment phase of the CLI session: understanding "
                "the current state before taking action."
            ),
            what_learned=(
                "CLI sessions have narrative structure. Spans 1-2 are the 'assess' phase: "
                "the developer is building a mental model of the current state before "
                "proceeding. In API modality, each request is independent. In CLI modality, "
                "commands form a story with assessment → verification → execution → measurement."
            ),
            confidence="high",
        )
        s2.tag("cli", "git", "branch", "assessment")

    # --- Span 3: pytest — run test suite ---
    with trace.span("action_result", target="pytest packages/parser/tests/ (test execution)") as s3:
        s3.depends_on(s2.span_id)
        s3.classify(
            modality=Modality.CLI.value,
            request_intent=RequestIntent.QUERY.value,
            response_outcome=ResponseOutcome.SUCCESS.value,
            signal_class=SignalClass.INTENT_CARRYING.value,
            delta_from_prior=DeltaFromPrior.FULL.value,
        )
        s3.modality_ext(CliExt(
            command="pytest",
            subcommand="packages/parser/tests/",
            args=["-v", "--tb=short"],
            exit_code=0,
            stdout_summary="381 passed in 2.34s\n\ntest_parser.py: 89 passed\ntest_analyzer.py: 72 passed\ntest_optimizer.py: 58 passed\ntest_orchestrator.py: 45 passed\ntest_modality_ext.py: 42 passed\ntest_classification.py: 38 passed\ntest_feedback.py: 37 passed",
            working_dir="/Users/dev/ignite",
            shell="zsh",
        ))
        s3.observed(
            what_happened=(
                "Ran full parser test suite: 381 tests passed. This single CLI command "
                "captures the result of 381 individual test assertions — the highest "
                "intelligence compression ratio of any span type."
            ),
            what_learned=(
                "CLI is the highest-density modality. One pytest span = 381 test results. "
                "Compression ratio: 381:1. An API equivalent would require 381 separate "
                "request-response spans. This validates ProductModeller's finding that "
                "CLI is 3x denser than API per span. The stdout_summary is the key — "
                "it carries the compressed intelligence."
            ),
            confidence="high",
        )
        s3.tag("cli", "pytest", "test-suite", "high-density")

    # --- Span 4: ignite-parse — pipeline invocation on DB trace ---
    with trace.span("action_result", target="ignite-parse traces/explorer/ (pipeline invocation)") as s4:
        s4.depends_on(s3.span_id)
        s4.classify(
            modality=Modality.CLI.value,
            request_intent=RequestIntent.MUTATION.value,
            response_outcome=ResponseOutcome.SUCCESS.value,
            signal_class=SignalClass.INTENT_CARRYING.value,
            delta_from_prior=DeltaFromPrior.FULL.value,
        )
        s4.modality_ext(CliExt(
            command="ignite-parse",
            subcommand="run",
            args=["traces/explorer/", "--output", "output/"],
            exit_code=0,
            stdout_summary="Pipeline complete: 7 traces parsed, 0 errors\nModalities: api(5), db(1), message(1)\nFindings: 28 merged → 19 unique\nActions: 12 planned, 12 executed\nConverged in 2 iterations",
            working_dir="/Users/dev/ignite",
            shell="zsh",
        ))
        s4.observed(
            what_happened=(
                "Ran the IGNITE pipeline on all traces in explorer/. Pipeline processed "
                "7 traces across 3 modalities (API, DB, Message) and converged in 2 iterations. "
                "This CLI command wraps the entire Orchestrator — it is a meta-modality."
            ),
            what_learned=(
                "CLI wraps other modalities: this single CLI span processes API + DB + Message "
                "traces internally. Cross-modality nesting is real — CLI is the orchestrator "
                "modality that reveals connections between all other modalities. "
                "The pipeline CLI is itself a trace source: it generates output that could "
                "be re-ingested as CLI modality spans."
            ),
            confidence="high",
        )
        s4.correlate("M6-db-trace", "M5-webhooks")
        s4.tag("cli", "ignite-parse", "pipeline", "meta-modality", "cross-modality")

    # --- Span 5: git diff — measure changes ---
    with trace.span("action_result", target="git diff --stat (measure changes)") as s5:
        s5.depends_on(s4.span_id)
        s5.classify(
            modality=Modality.CLI.value,
            request_intent=RequestIntent.QUERY.value,
            response_outcome=ResponseOutcome.SUCCESS.value,
            signal_class=SignalClass.INTENT_CARRYING.value,
            delta_from_prior=DeltaFromPrior.PARTIAL.value,
        )
        s5.modality_ext(CliExt(
            command="git",
            subcommand="diff",
            args=["--stat"],
            exit_code=0,
            stdout_summary="packages/parser/src/ignite_parser/analyzer.py | 12 +++---\npackages/trace-sdk/src/ignite_trace/extensions/db_ext.py | 4 ++\n2 files changed, 10 insertions(+), 6 deletions(-)",
            working_dir="/Users/dev/ignite",
            shell="zsh",
        ))
        s5.observed(
            what_happened=(
                "Reviewed diff after pipeline run. 2 files changed, 10 insertions, 6 deletions. "
                "This is the measurement phase of the CLI session: verifying what changed."
            ),
            what_learned=(
                "The CLI session has a complete narrative arc: assess (git status, git branch) → "
                "verify (pytest) → execute (ignite-parse) → measure (git diff). "
                "This session_intent is 'validation' — the developer is validating that "
                "the multi-modality pipeline changes work correctly."
            ),
            confidence="high",
        )
        s5.tag("cli", "git", "diff", "measurement")

    # --- Span 6: git add + commit — state transition ---
    with trace.span("state_transition", target="git commit -m 'feat: multi-modality pipeline' (commit)") as s6:
        s6.depends_on(s5.span_id)
        s6.classify(
            modality=Modality.CLI.value,
            request_intent=RequestIntent.MUTATION.value,
            response_outcome=ResponseOutcome.SUCCESS.value,
            signal_class=SignalClass.INTENT_CARRYING.value,
            delta_from_prior=DeltaFromPrior.FULL.value,
            state_transition_subtype=StateTransitionSubtype.STATE_CHANGE.value,
        )
        s6.modality_ext(CliExt(
            command="git",
            subcommand="commit",
            args=["-m", "feat: multi-modality pipeline validation"],
            exit_code=0,
            stdout_summary="[feat/multi-modality-pipeline abc1234] feat: multi-modality pipeline validation\n3 files changed, 210 insertions(+)",
            working_dir="/Users/dev/ignite",
            shell="zsh",
        ))
        s6.observed(
            what_happened=(
                "Committed changes after validation passed. This is a state_transition: "
                "the repository state changes from uncommitted to committed. "
                "The commit is the conclusion of the narrative arc."
            ),
            what_learned=(
                "Git commit is a state_transition in CLI modality — it changes persistent state. "
                "The narrative arc completes: assess → verify → execute → measure → commit. "
                "Unlike API state transitions (which are request-response), CLI state transitions "
                "are the culmination of a narrative session."
            ),
            confidence="high",
        )
        s6.state_change("repo: uncommitted changes → committed (feat/multi-modality-pipeline)")
        s6.tag("cli", "git", "commit", "state-transition")

    # --- Findings ---

    f1 = trace.finding(
        category="protocol",
        title="CLI sessions have narrative structure: assess → verify → execute → measure → commit",
        description=(
            "Unlike API's independent request-response model, CLI sessions tell stories. "
            "This 6-span session follows a clear narrative: assess state (git status, git branch), "
            "verify preconditions (pytest 381 tests), execute work (ignite-parse pipeline), "
            "measure results (git diff), commit outcome (git commit). "
            "The pipeline should classify session_intent from the command sequence pattern."
        ),
        source_spans=[s1, s3, s4, s6],
        confidence="confirmed",
        actionability="immediate",
    )
    f1.add_evidence(
        "assess: git status + git branch → understand current state",
        "verify: pytest 381 passed → preconditions met",
        "execute: ignite-parse → do the work",
        "measure: git diff → verify results",
        "commit: git commit → persist outcome",
    )
    f1.tag("cli", "narrative", "session-intent", "validation")

    f2 = trace.finding(
        category="data_format",
        title="CLI is the highest-density modality: one pytest span = 381 test results (381:1 compression)",
        description=(
            "A single CLI span can capture orders of magnitude more information than "
            "an API span. pytest: 381:1 compression (381 test results in one span). "
            "ignite-parse: captures an entire pipeline run (7 traces, 3 modalities, "
            "19 findings, 12 actions) in one span. "
            "The intelligence density metric should weight CLI spans higher in L2 analysis."
        ),
        source_spans=[s3, s4],
        confidence="confirmed",
        actionability="immediate",
    )
    f2.add_evidence(
        "pytest: 381 test results → 1 span = 381:1 compression",
        "ignite-parse: full pipeline run → 1 span = unbounded compression",
        "API equivalent would require hundreds of separate spans",
    )
    f2.tag("cli", "density", "compression", "intelligence")

    f3 = trace.finding(
        category="protocol",
        title="CLI is a meta-modality: ignite-parse CLI span wraps API + DB + Message trace processing internally",
        description=(
            "The ignite-parse CLI command processes traces from other modalities internally. "
            "This single CLI span wraps API, DB, and Message modality processing. "
            "CLI is the orchestrator modality — it reveals cross-modality connections "
            "that individual modalities cannot show on their own."
        ),
        source_spans=[s4],
        confidence="confirmed",
        actionability="immediate",
    )
    f3.add_evidence(
        "ignite-parse processes 7 traces across 3 modalities (api, db, message)",
        "CLI is structurally a meta-modality: it wraps other modalities",
    )
    f3.tag("cli", "meta-modality", "cross-modality", "orchestrator")

    trace.summary(
        "CLI modality trace with 6 spans covering a complete developer workflow: "
        "git status (assess), git branch (verify branch), pytest (381 tests), "
        "ignite-parse (pipeline invocation), git diff (measure), git commit (state transition). "
        "3 findings: (1) CLI sessions have narrative structure (assess→verify→execute→measure→commit). "
        "(2) CLI is highest-density modality (381:1 compression for pytest). "
        "(3) CLI is meta-modality (wraps other modalities). "
        "All v0.2 fields used: modality=CLI, request_intent, response_outcome, signal_class, "
        "delta_from_prior, state_transition_subtype, modality_ext(CliExt), correlation_ids."
    )
    trace.meta("manifest_target", "M7-cli-operations")
    trace.meta("v0.2_fields_used", [
        "modality", "request_intent", "response_outcome", "signal_class",
        "delta_from_prior", "state_transition_subtype", "modality_ext",
        "correlation_ids",
    ])
    trace.meta("modalities_exercised", ["cli"])
    trace.meta("session_intent", "validation")
    trace.meta("total_spans", 6)
    trace.meta("total_findings", 3)

print(f"M7 trace written: {trace.trace_id}")
