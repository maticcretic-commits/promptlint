"""Tests for PromptLint structure, ambiguity, contradiction,
hygiene and testability checks, scoring, and the CLI."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from promptlint import (_is_shouting, compute_score, compute_verdict,
                        finding, lint_file, lint_text)

CLI = Path(__file__).parent.parent / "src" / "promptlint.py"
EXAMPLES = Path(__file__).parent.parent / "examples"


def run_cli(*argv, cwd=None):
    return subprocess.run(
        [sys.executable, str(CLI), *argv],
        capture_output=True, text=True, cwd=cwd)


def checks_of(text):
    return {f["check"] for f in lint_text(text)["findings"]}


def findings_of(text, check):
    return [f for f in lint_text(text)["findings"]
            if f["check"] == check]


CLEAN_BASE = (
    "You are a support helper.\n"
    "Summarize the ticket the customer sent.\n"
    "Never share internal passwords.\n"
    "Respond with a JSON object only.\n"
    "Example: {\"summary\": \"x\"}\n"
)


# ---------------------------------------------------------------------------
# The shipped examples
# ---------------------------------------------------------------------------

def test_strong_example_is_fully_clean():
    result = lint_file(EXAMPLES / "strong.md")
    assert result["findings"] == []
    assert result["score"] == 100
    assert result["verdict"] == "PASS"


def test_strong_example_scores_at_least_95():
    assert lint_file(EXAMPLES / "strong.md")["score"] >= 95


def test_weak_example_trips_the_headline_checks():
    checks = {f["check"]
              for f in lint_file(EXAMPLES / "weak.md")["findings"]}
    assert "no-output-format" in checks
    assert "placeholder" in checks
    assert "contradiction-polarity" in checks
    assert "ambiguity-cluster" in checks
    assert "unbounded-scope" in checks
    assert "duplicate-line" in checks


def test_weak_example_fails():
    result = lint_file(EXAMPLES / "weak.md")
    assert result["verdict"] == "FAIL"
    assert result["score"] < 50


def test_clean_base_passes():
    assert lint_text(CLEAN_BASE)["findings"] == []


# ---------------------------------------------------------------------------
# Structure checks
# ---------------------------------------------------------------------------

def test_no_role_fires_without_persona():
    text = ("Summarize the ticket.\nNever share passwords.\n"
            "Respond with a JSON object.\nExample: {}")
    assert "no-role" in checks_of(text)


def test_no_role_quiet_with_act_as():
    text = ("Act as a support helper.\nSummarize the ticket.\n"
            "Never share passwords.\nRespond with a JSON object.\n"
            "Example: {}")
    assert "no-role" not in checks_of(text)


def test_no_task_fires_on_pure_description():
    text = ("You are a helper. The sky is blue. Water is wet.\n"
            "Never share passwords.\nOutput format: JSON.\n"
            "Example: {}")
    assert "no-task" in checks_of(text)


def test_no_task_quiet_with_sentence_initial_verb():
    text = ("You are a helper. Summarize the ticket now.\n"
            "Never share passwords.\nRespond with a JSON object.\n"
            "Example: {}")
    assert "no-task" not in checks_of(text)


def test_no_output_format_is_high():
    fs = findings_of(
        "You are a helper.\nSummarize the ticket.\n"
        "Never share passwords.", "no-output-format")
    assert fs and fs[0]["severity"] == "high"


def test_no_constraints_fires():
    text = ("You are a helper.\nSummarize the ticket.\n"
            "Respond with a JSON object.\nExample: {}")
    assert "no-constraints" in checks_of(text)


def test_format_without_example_fires():
    text = ("You are a helper.\nSummarize the ticket.\n"
            "Never share passwords.\n"
            "Respond in JSON, exactly this shape, no other text.")
    assert "format-without-example" in checks_of(text)


def test_format_without_example_quiet_with_example():
    text = ("You are a helper.\nSummarize the ticket.\n"
            "Never share passwords.\n"
            "Respond in JSON, exactly this shape.\n"
            "Example: {\"a\": 1}")
    assert "format-without-example" not in checks_of(text)


# ---------------------------------------------------------------------------
# Ambiguity checks
# ---------------------------------------------------------------------------

def test_vague_term_fires_per_occurrence():
    fs = findings_of(CLEAN_BASE + "Use appropriate and proper "
                                  "wording here.",
                     "vague-term")
    assert len(fs) == 2
    assert all(f["severity"] == "low" for f in fs)


def test_vague_cluster_escalates_at_five():
    text = CLEAN_BASE + ("Be helpful, reply soon, use some stuff, "
                         "various things etc.")
    fs = findings_of(text, "ambiguity-cluster")
    assert fs and fs[0]["severity"] == "medium"
    assert len(findings_of(text, "vague-term")) >= 5


def test_no_cluster_below_five():
    text = CLEAN_BASE + "Use appropriate wording."
    assert "ambiguity-cluster" not in checks_of(text)


def test_unbounded_scope_fires():
    fs = findings_of(CLEAN_BASE + "Help with anything at all.",
                     "unbounded-scope")
    assert fs and fs[0]["severity"] == "medium"
    assert "unbounded-scope" in checks_of(
        CLEAN_BASE + "Do it whenever possible.")


def test_strong_example_has_no_ambiguity_findings():
    amb = [f for f in lint_file(EXAMPLES / "strong.md")["findings"]
           if f["category"] == "ambiguity"]
    assert amb == []


# ---------------------------------------------------------------------------
# Contradiction candidates
# ---------------------------------------------------------------------------

def test_polarity_pair_flags_must_vs_must_not():
    text = (CLEAN_BASE + "You must cite the policy page.\n"
            "You must not cite the policy page.")
    fs = findings_of(text, "contradiction-polarity")
    assert fs and fs[0]["severity"] == "high"


def test_polarity_pair_flags_never_vs_always():
    text = (CLEAN_BASE + "Always share the discount code.\n"
            "Never share the discount code.")
    assert findings_of(text, "contradiction-polarity")


def test_consistent_pair_does_not_flag():
    text = (CLEAN_BASE + "Always cite the policy page.\n"
            "You must cite the policy page for refunds.")
    assert "contradiction-polarity" not in checks_of(text)


def test_opposite_polarity_without_shared_topic_does_not_flag():
    text = (CLEAN_BASE + "Never share passwords with anyone.\n"
            "Always log every action you take.")
    assert "contradiction-polarity" not in checks_of(text)


def test_single_generic_shared_keyword_does_not_flag():
    # Regression: the shipped strong example was failed by exactly
    # this shape — one shared domain noun is not a contradiction.
    text = (CLEAN_BASE + "Answer questions using only the policy "
            "notes.\nNever guess a policy term.")
    assert "contradiction-polarity" not in checks_of(text)


def test_conditional_if_line_is_not_a_constraint():
    text = (CLEAN_BASE + "Answer using only the policy notes.\n"
            "If the policy notes do not cover the question, "
            "escalate it.")
    assert "contradiction-polarity" not in checks_of(text)


def test_contradiction_findings_are_labelled_candidates():
    text = (CLEAN_BASE + "You must cite the policy page.\n"
            "You must not cite the policy page.")
    fs = findings_of(text, "contradiction-polarity")
    assert "CANDIDATE" in fs[0]["message"]
    assert "human review" in fs[0]["message"]


def test_format_conflict_flags_json_vs_letter():
    text = (CLEAN_BASE + "Write a friendly letter to the customer.")
    fs = findings_of(text, "contradiction-format")
    assert fs and fs[0]["severity"] == "high"


def test_same_format_twice_does_not_flag():
    text = CLEAN_BASE + "Return JSON only, nothing else."
    assert "contradiction-format" not in checks_of(text)


def test_length_conflict_flags():
    text = CLEAN_BASE + ("Answer in one sentence.\n"
                         "Explain the reasoning in detail.")
    fs = findings_of(text, "contradiction-length")
    assert fs and fs[0]["severity"] == "medium"


def test_length_single_side_does_not_flag():
    assert "contradiction-length" not in checks_of(
        CLEAN_BASE + "Answer in one sentence.")


# ---------------------------------------------------------------------------
# Hygiene checks
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "[TODO: fill this in]",
    "[INSERT NAME HERE]",
    "Dear {{customer_name}},",
    "lorem ipsum dolor sit amet",
])
def test_placeholder_variants_fire(bad):
    fs = findings_of(CLEAN_BASE + bad, "placeholder")
    assert fs and fs[0]["severity"] == "high"


def test_duplicate_line_fires_once_with_count():
    line = "Always check the order status first."
    text = CLEAN_BASE + line + "\n" + line
    fs = findings_of(text, "duplicate-line")
    assert len(fs) == 1
    assert "2 times" in fs[0]["message"]


def test_short_repeated_lines_do_not_flag():
    text = CLEAN_BASE + "OK.\nOK.\nOK."
    assert "duplicate-line" not in checks_of(text)


def test_shouting_line_fires():
    assert "shouting-line" in checks_of(
        CLEAN_BASE + "DO NOT EVER DO THIS THING AT ALL")


def test_shouting_ratio_edges():
    assert _is_shouting("ABC DEF gh ij") is True        # exactly 60%
    assert _is_shouting("AB CDE fg hij") is False       # 50%
    assert _is_shouting("## OUTPUT FORMAT") is False    # header, 3 words
    assert _is_shouting("Normal sentence with words.") is False


def test_long_line_threshold():
    assert "long-line" not in checks_of(CLEAN_BASE + "x" * 400)
    assert "long-line" in checks_of(CLEAN_BASE + "x" * 401)


def test_excessive_exclamation_is_info():
    fs = findings_of(CLEAN_BASE + "Great work!!!", 
                     "excessive-exclamation")
    assert fs and fs[0]["severity"] == "info"
    assert "excessive-exclamation" not in checks_of(
        CLEAN_BASE + "Great work!")


# ---------------------------------------------------------------------------
# Testability checks
# ---------------------------------------------------------------------------

def test_unverifiable_ask_fires_without_source_or_format():
    text = ("You are a researcher.\nResearch the topic and cite "
            "sources for every claim.\nNever invent data.\n"
            "Respond in plain prose paragraphs.")
    fs = findings_of(text, "unverifiable-ask")
    assert fs and fs[0]["severity"] == "medium"


def test_unverifiable_ask_quiet_with_url():
    text = ("You are a researcher.\nResearch the topic and cite "
            "sources for every claim.\nNever invent data.\n"
            "Respond in plain prose paragraphs.\n"
            "Data: https://example.org/dataset")
    assert "unverifiable-ask" not in checks_of(text)


def test_no_failure_behaviour_fires():
    text = ("You are a helper.\nAnswer the user's question about "
            "the document.\nNever share passwords.\n"
            "Respond with a JSON object.\nExample: {}")
    assert "no-failure-behaviour" in checks_of(text)


def test_no_failure_behaviour_quiet_with_if_unsure():
    text = ("You are a helper.\nAnswer the user's question about "
            "the document.\nNever share passwords.\n"
            "Respond with a JSON object.\nExample: {}\n"
            "If unsure, say so and ask for clarification.")
    assert "no-failure-behaviour" not in checks_of(text)


# ---------------------------------------------------------------------------
# Scoring and verdicts
# ---------------------------------------------------------------------------

def test_score_arithmetic():
    fs = [finding("placeholder", "high", 1, "x"),
          finding("no-role", "medium", None, "x"),
          finding("vague-term", "low", 2, "x"),
          finding("excessive-exclamation", "info", 3, "x")]
    assert compute_score(fs) == 100 - 15 - 6 - 2 - 0


def test_score_floor_is_zero():
    fs = [finding("placeholder", "high", 1, "x")] * 10
    assert compute_score(fs) == 0


def test_verdict_rules():
    high = [finding("placeholder", "high", 1, "x")]
    low = [finding("vague-term", "low", 1, "x")]
    assert compute_verdict(high) == "FAIL"
    assert compute_verdict(low) == "WARN"
    assert compute_verdict([]) == "PASS"


# ---------------------------------------------------------------------------
# CLI behaviour
# ---------------------------------------------------------------------------

def test_cli_weak_example_exit_1():
    proc = run_cli("lint", str(EXAMPLES / "weak.md"))
    assert proc.returncode == 1
    assert "FAIL" in proc.stdout


def test_cli_strong_example_exit_0():
    proc = run_cli("lint", str(EXAMPLES / "strong.md"))
    assert proc.returncode == 0
    assert "100/100" in proc.stdout


def test_cli_strict_turns_warn_into_exit_1(tmp_path):
    p = tmp_path / "warn.md"
    p.write_text(CLEAN_BASE + "Use appropriate wording.\n",
                 encoding="utf-8")
    assert run_cli("lint", str(p)).returncode == 0
    assert run_cli("lint", str(p), "--strict").returncode == 1


def test_cli_dir_mode_aggregates(tmp_path):
    (tmp_path / "good.md").write_text(
        (EXAMPLES / "strong.md").read_text(encoding="utf-8"),
        encoding="utf-8")
    (tmp_path / "bad.md").write_text(
        (EXAMPLES / "weak.md").read_text(encoding="utf-8"),
        encoding="utf-8")
    proc = run_cli("lint", str(tmp_path))
    assert proc.returncode == 1
    assert "Summary" in proc.stdout
    data = json.loads(run_cli("lint", str(tmp_path),
                              "--format", "json").stdout)
    assert data["summary"]["files"] == 2
    assert data["summary"]["failed"] == 1


def test_cli_json_output_shape():
    proc = run_cli("lint", str(EXAMPLES / "weak.md"),
                   "--format", "json")
    assert proc.returncode == 1
    data = json.loads(proc.stdout)
    assert data["tool"] == "promptlint"
    assert set(data) >= {"tool", "version", "results", "summary"}
    result = data["results"][0]
    assert set(result) >= {"path", "score", "verdict", "findings",
                           "counts"}
    f0 = result["findings"][0]
    assert set(f0) >= {"check", "severity", "category", "line",
                       "message", "snippet"}


def test_cli_empty_file_fails_cleanly(tmp_path):
    p = tmp_path / "empty.md"
    p.write_text("", encoding="utf-8")
    proc = run_cli("lint", str(p))
    assert proc.returncode == 1
    assert "no-output-format" in proc.stdout
    assert lint_text("")["score"] < 100


def test_cli_missing_path_is_usage_error(tmp_path):
    proc = run_cli("lint", str(tmp_path / "nope.md"))
    assert proc.returncode == 2


def test_negation_with_exception_aligned_pair_does_not_flag():
    # Dogfood seed: PromptRE's support-agent example. Both lines
    # require user confirmation before a refund -- they AGREE.
    text = (CLEAN_BASE
            + "- `issue_refund` \u2014 refund a charge. Always confirm "
              "with the user first.\n"
            + "- Never issue a refund without explicit user "
              "confirmation.")
    assert "contradiction-polarity" not in checks_of(text)


def test_no_without_clause_pair_still_flags():
    text = (CLEAN_BASE + "Always issue refunds immediately.\n"
            "Never issue a refund.")
    assert findings_of(text, "contradiction-polarity")


def test_without_exception_unrelated_pair_still_flags():
    text = (CLEAN_BASE + "Never share data without consent.\n"
            "Always share data freely.")
    assert findings_of(text, "contradiction-polarity")


def test_cli_json_envelope_schema_and_summary():
    proc = run_cli("lint", str(EXAMPLES / "weak.md"),
                   "--format", "json")
    data = json.loads(proc.stdout)
    assert data["schema_version"] == 1
    assert isinstance(data["schema_version"], int)
    total = sum(len(r["findings"]) for r in data["results"])
    assert data["findings_summary"]["count"] == total
    assert data["findings_summary"]["highest_severity"] == "HIGH"
    clean = json.loads(run_cli("lint", str(EXAMPLES / "strong.md"),
                               "--format", "json").stdout)
    assert clean["schema_version"] == 1
    assert clean["findings_summary"] == {"count": 0,
                                          "highest_severity": "NONE"}


def test_cli_version_flag():
    proc = run_cli("--version")
    assert proc.returncode == 0
    assert proc.stdout.strip() == "promptlint 1.0.0"


def test_cli_missing_path_error_envelope(tmp_path):
    proc = run_cli("lint", str(tmp_path / "nope.md"))
    assert proc.returncode == 2
    assert "error: no such file or directory:" in proc.stderr
    envelopes = [json.loads(line) for line in
                 proc.stderr.splitlines()
                 if line.startswith("{")]
    assert envelopes and envelopes[-1]["error"]["code"] == \
        "file_not_found"
    assert envelopes[-1]["error"]["message"].startswith(
        "no such file or directory:")


def test_cli_init_scaffolds_a_failing_sample(tmp_path):
    proc = run_cli("init", "--dir", str(tmp_path))
    assert proc.returncode == 0
    assert (tmp_path / "weak-prompt.md").is_file()
    assert (tmp_path / "EXPECTED.md").is_file()
    lint = run_cli("lint", str(tmp_path / "weak-prompt.md"))
    assert lint.returncode == 1
    for check in ("no-output-format", "placeholder",
                  "contradiction-polarity", "ambiguity-cluster",
                  "unbounded-scope"):
        assert check in lint.stdout
