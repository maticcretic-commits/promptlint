# PromptLint 🧹

**Lint your prompts like your code.**

Code gets a linter, a reviewer and a CI gate. The prompt — the file
that actually tells the agent what to do — gets eyeballed once and
pasted in. So prompts ship with vague words nobody defined
("respond appropriately", "some details, etc."), guardrails that
quietly cancel each other out ("always share the code" three lines
above "never share the code"), and a `[TODO]` still sitting where
the refund policy was supposed to go.

PromptLint is the static check you run before a prompt ships. It
lints **one** prompt for quality defects — missing structure,
undefined ambiguity, contradiction *candidates*, hygiene leftovers,
untestable asks — and reports findings with line numbers, a 0–100
score, and a PASS / WARN / FAIL verdict.

Offline and dependency-free: stdlib-only Python, no network, no
account, fully deterministic. Same prompt, same report, every time.

```bash
python src/promptlint.py lint prompts/support-agent.md
python src/promptlint.py lint prompts/          # a whole folder
python src/promptlint.py lint prompts/support-agent.md --strict
```

## The commands

| Command | What it does |
|---------|--------------|
| `lint <file>` | Lint one prompt file (`.md` / `.txt`). Findings grouped by check, with line numbers and the offending line quoted. |
| `lint <dir>` | Lint every `.md` / `.txt` in the directory (recursive). Per-file reports plus a worst-first summary table. |
| `lint … --format json` | Machine-readable report: per-file score, verdict, counts and findings (check, severity, category, line, message, snippet). |
| `lint … --strict` | Exit `1` on **any** finding, not just highs — for teams that treat lint warnings as build errors. |
| `init` | Scaffold a deliberately weak sample prompt + `EXPECTED.md` describing what the lint must say about it. |

Exit codes: `0` = PASS or WARN, `1` = FAIL (any high finding; with
`--strict`, any finding at all), `2` = usage or IO error.

## The checks

### Structure — what a prompt must at least contain

| Check | Severity | Fires when |
|-------|----------|------------|
| `no-role` | 🟡 medium | No role/persona line ("You are …", "Act as …") — the model has to guess who it is. |
| `no-task` | 🟡 medium | No explicit task or instruction-verb block — the prompt describes but never tells. |
| `no-output-format` | 🟠 high | No output format is named anywhere — outputs drift in shape from run to run. |
| `no-constraints` | 🟡 medium | No constraints or guardrails — nothing says what the model must not do. |
| `format-without-example` | 🔵 low | A strict format is demanded but no example of it is shown. |

### Ambiguity — words that mean whatever the model wants

| Check | Severity | Fires when |
|-------|----------|------------|
| `vague-term` | 🔵 low | Each occurrence of an undefined vague term: *appropriate(ly), proper(ly), some, etc., as needed, soon, various, stuff, things, good quality, be helpful*. |
| `ambiguity-cluster` | 🟡 medium | 5+ vague terms in one prompt — individually small, together they leave the output to chance. |
| `unbounded-scope` | 🟡 medium | *anything*, *everything*, *whenever possible* — scope with no edge to stop at. |

### Contradiction **candidates** — flagged, never "proven"

| Check | Severity | Fires when |
|-------|----------|------------|
| `contradiction-polarity` | 🟠 high | Two constraint lines with opposite polarity (never/always, must/must not, only/never) share a topic — at least two content keywords. |
| `contradiction-format` | 🟠 high | Two different output formats are demanded ("respond in JSON" and "write a friendly letter"). |
| `contradiction-length` | 🟡 medium | A brevity instruction ("one sentence") sits next to a depth instruction ("explain in detail"). |

### Hygiene — leftovers from the drafting process

| Check | Severity | Fires when |
|-------|----------|------------|
| `placeholder` | 🟠 high | `[TODO]`, `[INSERT …]`, `{{unresolved}}`, lorem ipsum — the model will treat it as content. |
| `duplicate-line` | 🔵 low | The same line appears 2+ times — wasted context, confusing edits. |
| `shouting-line` | 🔵 low | A line that is ≥60% ALL-CAPS over more than 3 words. |
| `long-line` | 🔵 low | One unbroken line over 400 characters. |
| `excessive-exclamation` | ⚪ info | `!!!`, or 3+ exclamation marks on a line. Punctuation is not an instruction. |

### Testability — could you ever check the answer?

| Check | Severity | Fires when |
|-------|----------|------------|
| `unverifiable-ask` | 🟡 medium | The prompt demands facts/citations but gives no source and no citation format — the model will invent plausible-looking references. |
| `no-failure-behaviour` | 🔵 low | The task depends on user input or provided data, but nothing says what to do when it is missing or insufficient. |

## Scoring

Start at 100. High −15, medium −6, low −2, info −0. Floor 0.
**Verdict is a rule, not just a score:** any high finding means
FAIL. Findings but no highs means WARN. Clean means PASS.

## Honesty note

Every check here is **lexical, not semantic** — and the
contradiction checks say so on every finding: they flag
**candidates**, with both lines quoted, for a human to judge. This
tool never claims a semantic proof, because there isn't one to be
had from keywords.

Two guards keep the candidate list honest:

- A candidate needs **at least two shared content keywords**. One
  shared generic noun ("the policy") proves nothing — a shared verb
  plus its object ("cite the policy page" / "must not cite the
  policy page") is the shape of a real clash.
- Lines opening with **"If …"** state a condition, not an
  instruction ("if the notes do not cover the question, escalate"),
  so their "do not" never enters the pairing.

What PromptLint guarantees is narrower and more useful than magic:
the defects above never ship *silently*. Vagueness is counted,
placeholders are named, and every candidate contradiction is
printed next to the two lines that caused it.

## Quick start

```bash
# 1. Scaffold the sample weak prompt and watch it fail:
promptlint init
promptlint lint weak-prompt.md     # exits 1 — see EXPECTED.md

# 2. Gate your real prompts in CI:
promptlint lint prompts/support-agent.md
promptlint lint prompts/ --strict

# 3. Fix until it passes — that loop is the point.
```

## Example

From `examples/` — the same support-agent prompt, twice.
`weak.md` is the draft as found: no format, sixteen vague terms,
an unbounded "anything", a `[TODO]`, a duplicated line — and an
"always share" / "never share" pair about the internal discount
code. Real captured output (the 16 `vague-term` findings are
elided to the first few):

```
$ promptlint lint examples/weak.md
====================================================================
PromptLint — lint report
====================================================================
File:    examples/weak.md
Lines:   16 · Findings: 22
Score:   9/100 — ❌ FAIL

## no-output-format
  file      🟠 HIGH   No output-format specification — outputs will drift in shape from run to run. Name the format (JSON, a table, a bullet list, …).

## vague-term
  line 5    🔵 LOW    Vague term "be helpful" — it has no definition in this prompt, so the model will invent one.
             Help the customer with anything they ask about and be helpful. Give
  line 6    🔵 LOW    Vague term "appropriate(ly)" — it has no definition in this prompt, so the model will invent one.
             appropriate answers, use proper language, mention some offers etc.
  … (14 more vague-term findings) …

## ambiguity-cluster
  file      🟡 MEDIUM 16 vague terms in one prompt — individually small, together they leave the output to chance. Define them or delete them.

## unbounded-scope
  line 5    🟡 MEDIUM Unbounded scope word — "anything" / "everything" / "whenever possible" gives the model no edge to stop at.
             Help the customer with anything they ask about and be helpful. Give

## contradiction-polarity
  line 9    🟠 HIGH   Contradiction CANDIDATE — human review. Opposite constraint polarity on shared topic (code, discount, internal, share): line 9 "Always share the internal discount code when the customer asks." vs line 10 "Never share the internal discount code with anyone."
             Always share the internal discount code when the customer asks.

## placeholder
  line 14   🟠 HIGH   Leftover placeholder — resolve it before this prompt ships; the model will treat it as content.
             [TODO: add the refund steps here]

## duplicate-line
  line 13   🔵 LOW    Line repeated 2 times (lines 13, 16) — duplicates waste context and confuse edits.
             Mention some offers etc. when it fits.

❌ FAIL — 3 high finding(s). Fix the highs before this prompt ships.
```

`strong.md` is the same task rewritten — role, task, hard rules, a
JSON format *with* an example, and a failure behaviour for missing
policy coverage:

```
$ promptlint lint examples/strong.md
====================================================================
PromptLint — lint report
====================================================================
File:    examples/strong.md
Lines:   29 · Findings: 0
Score:   100/100 — ✅ PASS

✅ PASS — no problems found.
```

## Why

[PromptRE](https://github.com/maticcretic-commits/promptre) takes a
prompt apart and shows you its structure; PromptLint grades what
it finds. A prompt is the only production artifact most teams ship
with no automated check at all — no test that the guardrails don't
contradict each other, no warning that the format was never
specified, no record that a placeholder reached users. Lint is the
cheapest possible place to catch that: deterministic, offline, in
the CI step you already have.

## Part of AgentForge

PromptLint is the tenth tool in the [AgentForge](https://github.com/maticcretic-commits)
trust layer for AI agents:

- **AgentProof** — verify agent work — [github.com/maticcretic-commits/agentproof](https://github.com/maticcretic-commits/agentproof)
- **PromptRE** — reverse-engineer agent prompts — [github.com/maticcretic-commits/promptre](https://github.com/maticcretic-commits/promptre)
- **AgentCI** — regression harness for agents — [github.com/maticcretic-commits/agentci](https://github.com/maticcretic-commits/agentci)
- **SecretScan** — scan agent outputs for leaked secrets — [github.com/maticcretic-commits/secretscan](https://github.com/maticcretic-commits/secretscan)
- **CostLedger** — cost accounting for agent runs — [github.com/maticcretic-commits/costledger](https://github.com/maticcretic-commits/costledger)
- **SchemaGuard** — validate agent outputs against their contracts — [github.com/maticcretic-commits/schemaguard](https://github.com/maticcretic-commits/schemaguard)
- **SkillAudit** — audit agent skills for hidden instructions and quality — [github.com/maticcretic-commits/skillaudit](https://github.com/maticcretic-commits/skillaudit)
- **PromptDiff** — diff prompt versions — [github.com/maticcretic-commits/promptdiff](https://github.com/maticcretic-commits/promptdiff)
- **TraceTape** — the canonical run tape — [github.com/maticcretic-commits/tracetape](https://github.com/maticcretic-commits/tracetape)
- **PromptLint** — static lint for prompts — the prompt, *linted clean* (this repo)
- **ClaimCheck** — audit agent reports against their evidence — [github.com/maticcretic-commits/claimcheck](https://github.com/maticcretic-commits/claimcheck)
- **TrustBadge** — one trust score and badge for the whole family — [github.com/maticcretic-commits/trustbadge](https://github.com/maticcretic-commits/trustbadge)

AgentProof checks what an agent *did*; PromptRE checks what it was *told*; AgentCI checks it still *works* after you change it; SecretScan checks that nothing *leaked*; CostLedger checks what it *cost*; SchemaGuard checks the output still *fits the contract*; SkillAudit checks the skill was *safe to install*; PromptDiff checks what the prompt change *changed*; TraceTape keeps the whole run on *tape*; PromptLint keeps the prompt itself *linted clean*; ClaimCheck makes sure every claim is *backed*; TrustBadge puts the family's score *on one badge*.

## License

MIT
