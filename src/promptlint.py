#!/usr/bin/env python3
"""
PromptLint — "Lint your prompts like your code."

Prompts ship to production with the review rigour of a sticky note:
vague words nobody defined, guardrails that quietly cancel each
other out, a [TODO] still sitting where the refund policy was
supposed to go. PromptLint is the static check you run in CI before
a prompt ships — structure, ambiguity, contradiction *candidates*,
hygiene and testability — with a 0–100 score and a PASS / WARN /
FAIL verdict.

Every check is deterministic and lexical. The contradiction checks
flag CANDIDATES with both lines quoted; this tool never claims a
semantic proof, and it says so on every finding.

    promptlint lint <file-or-dir> [--format text|json] [--strict]
    promptlint init

Exit codes: 0 = PASS / WARN (with --strict: no findings at all),
1 = FAIL (any high finding; with --strict: any finding),
2 = usage / IO error. Stdlib only, no network.
"""

import argparse
import json
import re
import sys
from pathlib import Path

VERSION = "1.0.0"

LINT_EXTENSIONS = {".md", ".markdown", ".txt"}
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv"}
MAX_FILE_BYTES = 2_000_000

SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2, "info": 3}
SEVERITY_EMOJI = {"high": "🟠", "medium": "🟡", "low": "🔵", "info": "⚪"}
DEDUCTIONS = {"high": 15, "medium": 6, "low": 2, "info": 0}
VERDICT_EMOJI = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}

CHECK_ORDER = [
    "no-role", "no-task", "no-output-format", "no-constraints",
    "format-without-example",
    "vague-term", "ambiguity-cluster", "unbounded-scope",
    "contradiction-polarity", "contradiction-format",
    "contradiction-length",
    "placeholder", "duplicate-line", "shouting-line", "long-line",
    "excessive-exclamation",
    "unverifiable-ask", "no-failure-behaviour",
]
CHECK_CATEGORY = {
    "no-role": "structure", "no-task": "structure",
    "no-output-format": "structure", "no-constraints": "structure",
    "format-without-example": "structure",
    "vague-term": "ambiguity", "ambiguity-cluster": "ambiguity",
    "unbounded-scope": "ambiguity",
    "contradiction-polarity": "contradiction",
    "contradiction-format": "contradiction",
    "contradiction-length": "contradiction",
    "placeholder": "hygiene", "duplicate-line": "hygiene",
    "shouting-line": "hygiene", "long-line": "hygiene",
    "excessive-exclamation": "hygiene",
    "unverifiable-ask": "testability",
    "no-failure-behaviour": "testability",
}


def finding(check, severity, line, message, snippet=""):
    return {"check": check, "severity": severity,
            "category": CHECK_CATEGORY[check], "line": line,
            "message": message, "snippet": snippet}


def make_snippet(line):
    """A short, display-safe excerpt of an offending line."""
    s = line.strip()
    if len(s) > 120:
        s = s[:117] + "..."
    return s


def _rx(p):
    return re.compile(p, re.IGNORECASE)


# ---------------------------------------------------------------------------
# Structure signals
# ---------------------------------------------------------------------------

ROLE_PATTERNS = [
    _rx(r"\byou are\b"), _rx(r"\byour role is\b"), _rx(r"\bact as\b"),
    _rx(r"\brole\s*:"), _rx(r"\bpersona\b"),
    _rx(r"^#{1,6}\s.*\b(role|persona|who you are)\b"),
]
TASK_VERBS = {
    "summarize", "summarise", "write", "answer", "explain", "extract",
    "list", "generate", "analyze", "analyse", "classify", "translate",
    "review", "produce", "return", "respond", "describe", "identify",
    "create", "draft", "check", "find", "compare", "convert",
    "rewrite", "help", "resolve", "give", "mention", "fix", "provide",
    "decide", "recommend", "outline", "compose", "build", "calculate",
    "search", "handle", "process", "prepare", "grade", "score",
    "diagnose", "plan", "suggest", "correct", "complete", "fill",
}
TASK_PATTERNS = [
    _rx(r"\byour task is\b"), _rx(r"\btask\s*:"),
    _rx(r"^#{1,6}\s.*\b(task|instructions?|mission|job)\b"),
]
FORMAT_SPEC_PATTERNS = [
    _rx(r"\bjson\b"), _rx(r"\bxml\b"), _rx(r"\byaml\b"),
    _rx(r"\bcsv\b"), _rx(r"\bmarkdown\b"), _rx(r"\bformat\b"),
    _rx(r"\brespond\s+(only\s+)?with\b"), _rx(r"\banswer\s+in\b"),
    _rx(r"\breply\s+with\b"), _rx(r"\bbullet\b"), _rx(r"\btable\b"),
    _rx(r"\btemplate\b"), _rx(r"```"),
]
CONSTRAINT_PATTERNS = [
    _rx(r"\bnever\b"), _rx(r"\bmust\b"), _rx(r"\bdo not\b"),
    _rx(r"\bdon'?t\b"), _rx(r"\bshould not\b"), _rx(r"\bat most\b"),
    _rx(r"\bat least\b"), _rx(r"\bavoid\b"), _rx(r"\bforbidden\b"),
    _rx(r"\bprohibited\b"), _rx(r"\bnot allowed\b"), _rx(r"\bonly\b"),
    _rx(r"^#{1,6}\s.*\b(rules|constraints|guardrails)\b"),
]
STRICT_FORMAT_CUES = [
    _rx(r"\bjson\b"), _rx(r"\bexactly\b"), _rx(r"\bstrictly\b"),
    _rx(r"\bmust follow\b"), _rx(r"```"),
]
EXAMPLE_MARKERS = [
    _rx(r"\bexample\b"), _rx(r"\bfor instance\b"), _rx(r"\be\.g\."),
    _rx(r"```"),
]

# ---------------------------------------------------------------------------
# Ambiguity inventory
# ---------------------------------------------------------------------------

VAGUE_TERMS = [
    ("appropriate(ly)", _rx(r"\bappropriate(?:ly)?\b")),
    ("proper(ly)", _rx(r"\bproper(?:ly)?\b")),
    ("some", _rx(r"\bsome\b")),
    ("etc.", _rx(r"\betc\.")),
    ("as needed", _rx(r"\bas needed\b")),
    ("soon", _rx(r"\bsoon\b")),
    ("various", _rx(r"\bvarious\b")),
    ("stuff", _rx(r"\bstuff\b")),
    ("things", _rx(r"\bthings\b")),
    ("good quality", _rx(r"\bgood quality\b")),
    ("be helpful", _rx(r"\bbe helpful\b")),
]
UNBOUNDED_PATTERNS = [
    _rx(r"\banything\b"), _rx(r"\beverything\b"),
    _rx(r"\bwhenever possible\b"),
]

# ---------------------------------------------------------------------------
# Contradiction signals
# ---------------------------------------------------------------------------

PROHIBITION_RX = _rx(r"\b(never|must\s+not|mustn'?t|do\s+not|don'?t|"
                     r"not\s+allowed|forbidden|prohibited|avoid)\b")
REQUIREMENT_RX = _rx(r"\b(must|shall|always|required|mandatory|only)\b")

STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for",
    "with", "without", "when", "while", "is", "are", "was", "were",
    "be", "been", "it", "its", "this", "that", "these", "those",
    "you", "your", "yours", "we", "our", "they", "them", "their",
    "as", "at", "by", "from", "into", "about", "over", "under",
    "per", "via", "if", "then", "than", "so", "such", "any", "all",
    "each", "every", "does", "did", "not", "can", "could", "will",
    "would", "may", "might", "has", "have", "had", "but", "also",
    "more", "most", "other", "others", "one", "two", "get", "use",
    "used", "using",
}
POLARITY_WORDS = {
    "never", "always", "must", "shall", "only", "avoid", "required",
    "mandatory", "forbidden", "prohibited", "allowed", "don't",
    "mustn't",
}

FORMAT_DEMAND_PATTERNS = [
    ("JSON", _rx(r"\bjson\b")),
    ("XML", _rx(r"\bxml\b")),
    ("YAML", _rx(r"\byaml\b")),
    ("CSV", _rx(r"\bcsv\b")),
    ("a table", _rx(r"\b(markdown\s+)?table\b")),
    ("a bullet list", _rx(r"\bbullet(ed)?\s+(list|points)\b"
                          r"|\bbullet\s+points\b")),
    ("a numbered list", _rx(r"\bnumbered\s+list\b")),
    ("a letter", _rx(r"\b(friendly\s+)?letter\b")),
    ("plain prose", _rx(r"\bplain\s+prose\b|\bprose\s+paragraph\b")),
]
SHORT_LENGTH_CUES = [
    _rx(r"\bone sentence\b"), _rx(r"\bsingle sentence\b"),
    _rx(r"\bone line\b"), _rx(r"\bbe brief\b"), _rx(r"\bconcise\b"),
    _rx(r"\bshort answer\b"), _rx(r"\bbriefly\b"),
]
LONG_LENGTH_CUES = [
    _rx(r"\bexplain in detail\b"), _rx(r"\bin detail\b"),
    _rx(r"\bdetailed\b"), _rx(r"\bcomprehensive\b"),
    _rx(r"\bthorough(ly)?\b"), _rx(r"\bat length\b"),
    _rx(r"\bin[- ]depth\b"),
]

# ---------------------------------------------------------------------------
# Hygiene signals
# ---------------------------------------------------------------------------

PLACEHOLDER_PATTERNS = [
    _rx(r"\[\s*(todo|tbd|fixme|insert|placeholder)\b[^\]]*\]"),
    _rx(r"\{\{[^}\n]+\}\}"),
    _rx(r"\blorem ipsum\b"),
]

# ---------------------------------------------------------------------------
# Testability signals
# ---------------------------------------------------------------------------

FACTUAL_DEMAND_PATTERNS = [
    _rx(r"\bcit(e|es|ation|ations|ing)\b"), _rx(r"\bsources?\b"),
    _rx(r"\bfacts?\b"), _rx(r"\bstatistics?\b"),
    _rx(r"\baccording to\b"), _rx(r"\bresearch\b"),
    _rx(r"\blook up\b"), _rx(r"\bbrowse\b"), _rx(r"\breferences?\b"),
]
SOURCE_GIVEN_PATTERNS = [
    _rx(r"https?://"),
    _rx(r"\bsources?\b[^.\n]{0,60}\b(format|as follows|list|section)\b"),
    _rx(r"\bcit\w*\b[^.\n]{0,60}\b(as|using|in)\b"),
    _rx(r"\b(policy|source|reference)\s+(notes|documents|material|text)\b"),
    _rx(r"\bprovided\s+(below|above|here)\b"),
]
NEEDS_DATA_PATTERNS = [
    _rx(r"\buser'?s\s+(input|question|message|request|document)\b"),
    _rx(r"\bcustomer'?s\s+(input|question|message|request)\b"),
    _rx(r"\bprovided\s+(document|text|data|file|notes)\b"),
    _rx(r"\bthe input\b"), _rx(r"\buploaded\b"),
]
FAILURE_BEHAVIOUR_PATTERNS = [
    _rx(r"\bif\s+unsure\b"), _rx(r"\bif\s+you\s+(don't|do\s+not)\s+know\b"),
    _rx(r"\bif\s+not\s+sure\b"), _rx(r"\bif\s+missing\b"),
    _rx(r"\bif\b[^.\n]{0,80}\b(do\s+not|does\s+not|don't)\s+"
        r"(cover|contain|include|mention)\b"),
    _rx(r"\bask\s+(for\s+)?clarification\b"),
    _rx(r"\bsay\s+so\b"), _rx(r"\bcannot\s+find\b"),
    _rx(r"\bnot\s+available\b"), _rx(r"\bno\s+information\b"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _any(patterns, text):
    return any(p.search(text) for p in patterns)


def _first_match_line(patterns, lines):
    for i, line in enumerate(lines, 1):
        if _any(patterns, line):
            return i
    return None


def _polarity(line):
    if PROHIBITION_RX.search(line):
        return "prohibition"
    if REQUIREMENT_RX.search(line):
        return "requirement"
    return None


def _content_keywords(line):
    words = set()
    for tok in re.findall(r"[a-z][a-z0-9']+", line.lower()):
        if tok.endswith("'s"):
            tok = tok[:-2]
        if (len(tok) >= 4 and tok not in STOPWORDS
                and tok not in POLARITY_WORDS):
            words.add(tok)
    return words


def _stem_match(a, b):
    """Light stemming: equal after lowercasing, or one is a prefix
    of the other with a common prefix of at least 5 characters
    (confirm ~ confirmation, refund ~ refunds)."""
    if a == b:
        return True
    if a.startswith(b) or b.startswith(a):
        return min(len(a), len(b)) >= 5
    return False


def _keywords_match(kw_a, kw_b):
    return any(_stem_match(a, b) for a in kw_a for b in kw_b)


def _shared_keywords(kw_a, kw_b):
    """Content keywords of kw_a that match a keyword in kw_b
    under light stemming."""
    return {a for a in kw_a
            if any(_stem_match(a, b) for b in kw_b)}


_WITHOUT_RX = _rx(r"\bwithout\b\s*(.*)$")
_NEGATION_RX = _rx(r"\b(never|must\s+not|mustn'?t|do\s+not|don'?t|"
                   r"not)\b")


def _exception_keywords(line):
    """Keywords of the exception clause of a negation-with-
    exception line ("Never X without Y"), or None when the line
    is not of that shape. "Never X without Y" is a CONDITIONAL
    prohibition -- X is allowed exactly when Y holds."""
    m = _WITHOUT_RX.search(line)
    if not m or not _NEGATION_RX.search(line):
        return None
    return _content_keywords(m.group(1))


def _is_shouting(line):
    words = line.split()
    if len(words) <= 3:
        return False
    alpha = [c for c in line if c.isalpha()]
    if len(alpha) < 10:
        return False
    upper = sum(1 for c in alpha if c.isupper())
    return upper / len(alpha) >= 0.60


def _has_task_verb(lines):
    # An instruction verb counts at the start of a line OR at the
    # start of any sentence inside it ("You are a helper. Summarize
    # the ticket." instructs, even though the line opens with "You").
    for line in lines:
        stripped = re.sub(r"^[\s#>*\-•\d.)]+", "", line).strip()
        if not stripped:
            continue
        for sentence in re.split(r"[.!?]\s+", stripped):
            words = re.findall(r"[A-Za-z']+", sentence)
            if not words:
                continue
            if words[0].lower() in TASK_VERBS:
                return True
            if words[0].lower() == "please" and len(words) > 1 \
                    and words[1].lower() in TASK_VERBS:
                return True
    return False


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_structure(text, lines):
    out = []
    if not _any(ROLE_PATTERNS, text):
        out.append(finding(
            "no-role", "medium", None,
            "No role or persona line — the model has to guess who it "
            "is supposed to be. Add a \"You are …\" line."))
    if not _any(TASK_PATTERNS, text) and not _has_task_verb(lines):
        out.append(finding(
            "no-task", "medium", None,
            "No explicit task or instruction verb block — the prompt "
            "describes but never tells the model what to do."))
    if not _any(FORMAT_SPEC_PATTERNS, text):
        out.append(finding(
            "no-output-format", "high", None,
            "No output-format specification — outputs will drift in "
            "shape from run to run. Name the format (JSON, a table, "
            "a bullet list, …)."))
    if not _any(CONSTRAINT_PATTERNS, text):
        out.append(finding(
            "no-constraints", "medium", None,
            "No constraints or guardrails anywhere — nothing in the "
            "prompt says what the model must not do."))
    if _any(FORMAT_SPEC_PATTERNS, text) \
            and _any(STRICT_FORMAT_CUES, text) \
            and not _any(EXAMPLE_MARKERS, text):
        out.append(finding(
            "format-without-example", "low", None,
            "The prompt demands a strict output format but shows no "
            "example of it — one filled-in example pins the shape "
            "better than any description."))
    return out


def check_ambiguity(lines):
    out = []
    occurrences = 0
    for i, line in enumerate(lines, 1):
        for label, rx in VAGUE_TERMS:
            for _m in rx.finditer(line):
                occurrences += 1
                out.append(finding(
                    "vague-term", "low", i,
                    f"Vague term \"{label}\" — it has no definition in "
                    f"this prompt, so the model will invent one.",
                    make_snippet(line)))
    if occurrences >= 5:
        out.append(finding(
            "ambiguity-cluster", "medium", None,
            f"{occurrences} vague terms in one prompt — individually "
            "small, together they leave the output to chance. Define "
            "them or delete them."))
    for i, line in enumerate(lines, 1):
        if _any(UNBOUNDED_PATTERNS, line):
            out.append(finding(
                "unbounded-scope", "medium", i,
                "Unbounded scope word — \"anything\" / \"everything\" "
                "/ \"whenever possible\" gives the model no edge to "
                "stop at.",
                make_snippet(line)))
    return out


def check_contradictions(lines):
    out = []
    # Opposite-polarity constraint lines sharing significant keywords.
    # Two guards against noise: a line opening with "If …" states a
    # condition ("if the notes do not cover…"), not an instruction,
    # so its markers do not count; and a candidate needs at least
    # TWO shared content keywords — one shared generic noun ("the
    # policy") proves nothing, a shared verb + object does.
    # Third guard: a negation-with-exception line ("Never issue a
    # refund without explicit user confirmation") is a CONDITIONAL
    # prohibition -- it allows the act exactly when its exception
    # clause holds. When the other line shares at least one keyword
    # (under light stemming) with that exception clause ("Always
    # confirm with the user first" mandates the confirmation the
    # exception requires), the two lines AGREE: the pair is aligned,
    # not contradictory, and is skipped. Shared-topic keywords are
    # likewise matched under light stemming (refund ~ refunds).
    marked = []
    for i, line in enumerate(lines, 1):
        head = re.sub(r"^[\s#>*\-•]+", "", line).lower()
        if head.startswith("if "):
            continue
        pol = _polarity(line)
        if pol:
            marked.append((i, line, pol, _content_keywords(line)))
    pairs = 0
    for a in range(len(marked)):
        for b in range(a + 1, len(marked)):
            i, line_a, pol_a, kw_a = marked[a]
            j, line_b, pol_b, kw_b = marked[b]
            if pol_a == pol_b:
                continue
            shared = _shared_keywords(kw_a, kw_b)
            if len(shared) < 2:
                continue
            exc_a = _exception_keywords(line_a)
            if exc_a and _keywords_match(exc_a, kw_b):
                continue
            exc_b = _exception_keywords(line_b)
            if exc_b and _keywords_match(exc_b, kw_a):
                continue
            pairs += 1
            if pairs <= 10:
                out.append(finding(
                    "contradiction-polarity", "high", i,
                    "Contradiction CANDIDATE — human review. Opposite "
                    f"constraint polarity on shared topic "
                    f"({', '.join(sorted(shared))}): line {i} "
                    f"\"{line_a.strip()}\" vs line {j} "
                    f"\"{line_b.strip()}\"",
                    make_snippet(line_a)))
    # Two different output formats demanded.
    demanded = {}
    for i, line in enumerate(lines, 1):
        for name, rx in FORMAT_DEMAND_PATTERNS:
            if rx.search(line) and name not in demanded:
                demanded[name] = (i, line)
    if len(demanded) >= 2:
        (name_a, (i, line_a)), (name_b, (j, line_b)) = \
            list(demanded.items())[:2]
        out.append(finding(
            "contradiction-format", "high", i,
            "Contradiction CANDIDATE — human review. Two different "
            f"output formats are demanded: {name_a} (line {i}: "
            f"\"{line_a.strip()}\") and {name_b} (line {j}: "
            f"\"{line_b.strip()}\"). The model cannot fully obey "
            "both.",
            make_snippet(line_a)))
    # Length conflict.
    short_at = _first_match_line(SHORT_LENGTH_CUES, lines)
    long_at = _first_match_line(LONG_LENGTH_CUES, lines)
    if short_at and long_at and short_at != long_at:
        out.append(finding(
            "contradiction-length", "medium", short_at,
            "Contradiction CANDIDATE — human review. A brevity "
            f"instruction (line {short_at}: "
            f"\"{lines[short_at - 1].strip()}\") sits next to a "
            f"depth instruction (line {long_at}: "
            f"\"{lines[long_at - 1].strip()}\").",
            make_snippet(lines[short_at - 1])))
    return out


def check_hygiene(lines):
    out = []
    for i, line in enumerate(lines, 1):
        if _any(PLACEHOLDER_PATTERNS, line):
            out.append(finding(
                "placeholder", "high", i,
                "Leftover placeholder — resolve it before this "
                "prompt ships; the model will treat it as content.",
                make_snippet(line)))
        if _is_shouting(line):
            out.append(finding(
                "shouting-line", "low", i,
                "ALL-CAPS line — shouting does not add emphasis for "
                "a model; it just burns tokens.",
                make_snippet(line)))
        if len(line) > 400:
            out.append(finding(
                "long-line", "low", i,
                f"Unbroken line of {len(line)} characters — hard to "
                "review and easy to edit badly. Wrap it.",
                make_snippet(line)))
        if "!!!" in line or line.count("!") >= 3:
            out.append(finding(
                "excessive-exclamation", "info", i,
                "Excessive exclamation — punctuation is not an "
                "instruction.",
                make_snippet(line)))
    seen = {}
    for i, line in enumerate(lines, 1):
        norm = re.sub(r"\s+", " ", line.strip().lower())
        if len(norm) >= 20:
            seen.setdefault(norm, []).append(i)
    for norm, at in seen.items():
        if len(at) >= 2:
            out.append(finding(
                "duplicate-line", "low", at[0],
                f"Line repeated {len(at)} times (lines "
                f"{', '.join(str(n) for n in at)}) — duplicates "
                "waste context and confuse edits.",
                make_snippet(lines[at[0] - 1])))
    return out


def check_testability(text, lines):
    out = []
    factual_at = _first_match_line(FACTUAL_DEMAND_PATTERNS, lines)
    if factual_at and not _any(SOURCE_GIVEN_PATTERNS, text):
        out.append(finding(
            "unverifiable-ask", "medium", factual_at,
            "The prompt asks for facts or citations but gives no "
            "source and no citation format — the model will invent "
            "plausible-looking references.",
            make_snippet(lines[factual_at - 1])))
    if _any(NEEDS_DATA_PATTERNS, text) \
            and not _any(FAILURE_BEHAVIOUR_PATTERNS, text):
        out.append(finding(
            "no-failure-behaviour", "low", None,
            "The task depends on input or provided data, but the "
            "prompt never says what to do when it is missing or "
            "insufficient — add an \"if unsure / if not covered\" "
            "line."))
    return out


# ---------------------------------------------------------------------------
# Linting
# ---------------------------------------------------------------------------

def compute_score(findings):
    return max(0, 100 - sum(DEDUCTIONS[f["severity"]] for f in findings))


def compute_verdict(findings):
    if any(f["severity"] == "high" for f in findings):
        return "FAIL"
    if findings:
        return "WARN"
    return "PASS"


def lint_text(text, path="<text>"):
    lines = text.splitlines()
    findings = []
    findings += check_structure(text, lines)
    findings += check_ambiguity(lines)
    findings += check_contradictions(lines)
    findings += check_hygiene(lines)
    findings += check_testability(text, lines)
    findings.sort(key=lambda f: (SEVERITY_RANK[f["severity"]],
                                 f["line"] or 0, f["check"]))
    return {
        "path": path,
        "lines": len(lines),
        "findings": findings,
        "counts": {sev: sum(1 for f in findings
                            if f["severity"] == sev)
                   for sev in SEVERITY_RANK},
        "score": compute_score(findings),
        "verdict": compute_verdict(findings),
    }


def lint_file(path):
    p = Path(path)
    if p.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"file too large to lint: {p} "
                         f"({p.stat().st_size} bytes)")
    text = p.read_text(encoding="utf-8", errors="replace")
    return lint_text(text, path=str(p))


def collect_prompt_files(path):
    p = Path(path)
    if p.is_file():
        return [p]
    if p.is_dir():
        files = []
        for child in sorted(p.rglob("*")):
            if any(part in SKIP_DIRS for part in child.parts):
                continue
            if child.is_file() and child.suffix.lower() \
                    in LINT_EXTENSIONS:
                files.append(child)
        return files
    return []


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_lint_text(result):
    print("=" * 68)
    print("PromptLint — lint report")
    print("=" * 68)
    print(f"File:    {result['path']}")
    print(f"Lines:   {result['lines']} · "
          f"Findings: {len(result['findings'])}")
    print(f"Score:   {result['score']}/100 — "
          f"{VERDICT_EMOJI[result['verdict']]} {result['verdict']}")
    findings = result["findings"]
    if not findings:
        print("\n✅ PASS — no problems found.")
        return
    by_check = {}
    for f in findings:
        by_check.setdefault(f["check"], []).append(f)
    for check in CHECK_ORDER:
        if check not in by_check:
            continue
        print(f"\n## {check}")
        for f in sorted(by_check[check],
                        key=lambda x: x["line"] or 0):
            where = f"line {f['line']}" if f["line"] else "file"
            print(f"  {where:<9} {SEVERITY_EMOJI[f['severity']]} "
                  f"{f['severity'].upper():<6} {f['message']}")
            if f["snippet"]:
                print(f"             {f['snippet']}")
    highs = result["counts"]["high"]
    if result["verdict"] == "FAIL":
        print(f"\n❌ FAIL — {highs} high finding(s). Fix the highs "
              "before this prompt ships.")
    else:
        print(f"\n⚠️ WARN — no high findings, but "
              f"{len(findings)} thing(s) worth a human look.")


def print_summary_table(results):
    print("\n## Summary")
    print(f"  {'file':<44} {'score':>5}  {'verdict':<7} findings")
    for r in sorted(results,
                    key=lambda x: (SEVERITY_RANK.get(
                        {"FAIL": "high", "WARN": "medium",
                         "PASS": "info"}[x["verdict"]], 3),
                                   x["score"])):
        name = r["path"]
        if len(name) > 44:
            name = "…" + name[-43:]
        print(f"  {name:<44} {r['score']:>5}  "
              f"{VERDICT_EMOJI[r['verdict']]} {r['verdict']:<5} "
              f"{len(r['findings'])}")


def _emit_error(code, message):
    """Print the human error line plus the JSON error envelope,
    both to stderr (see CONVENTIONS.md)."""
    print(f"error: {message}", file=sys.stderr)
    print(json.dumps({"error": {"code": code, "message": message}}),
          file=sys.stderr)


# ---------------------------------------------------------------------------
# init scaffold
# ---------------------------------------------------------------------------

SAMPLE_WEAK_PROMPT = """\
# Email Helper

You are an email helper. Help me write emails about anything, be
helpful and reply soon with good quality stuff, various things etc.

Always send the email exactly as I wrote it.
Never send the email without my review.

[TODO: add the sign-off rules]
"""

SAMPLE_EXPECTED = """\
# What PromptLint should say about weak-prompt.md

`promptlint lint weak-prompt.md` must FAIL (exit 1). Expected:

- `no-output-format` (high) — no format is ever named.
- `placeholder` (high) — the [TODO] line is still in the file.
- `contradiction-polarity` (high, CANDIDATE) — "Always send the
  email exactly as I wrote it" vs "Never send the email without my
  review" share the topic *send / email* with opposite polarity.
- `unbounded-scope` (medium) — "anything".
- `ambiguity-cluster` (medium) — "be helpful", "soon",
  "good quality", "stuff", "various", "etc." is six vague terms.
- `vague-term` (low) — one finding per occurrence above.

The scaffold is deliberately weak. Fix it until it passes, and you
have learned the tool.
"""


def cmd_init(args):
    base = Path(args.dir) if args.dir else Path(".")
    targets = {"weak-prompt.md": SAMPLE_WEAK_PROMPT,
               "EXPECTED.md": SAMPLE_EXPECTED}
    for name, content in targets.items():
        dest = base / name
        if dest.exists() and not args.force:
            _emit_error("already_exists",
                        f"{dest} exists (use --force to overwrite)")
            return 2
    base.mkdir(parents=True, exist_ok=True)
    for name, content in targets.items():
        (base / name).write_text(content, encoding="utf-8")
        print(f"wrote {base / name}")
    print("\nTry it:")
    print("  promptlint lint weak-prompt.md        # expect FAIL")
    print("Then fix weak-prompt.md until it passes.")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_lint(args):
    p = Path(args.path)
    if not p.exists():
        _emit_error("file_not_found",
                    f"no such file or directory: {p}")
        return 2
    files = collect_prompt_files(p)
    if not files:
        _emit_error("no_prompt_files",
                    f"no prompt files (.md/.txt) found under {p}")
        return 2
    results = []
    for f in files:
        try:
            results.append(lint_file(f))
        except (OSError, ValueError) as e:
            _emit_error("read_error", f"{e}")
            return 2
    if args.format == "json":
        all_findings = [f for r in results for f in r["findings"]]
        if any(f["severity"] == "high" for f in all_findings):
            highest = "HIGH"
        elif all_findings:
            highest = "INFO"
        else:
            highest = "NONE"
        print(json.dumps(
            {"tool": "promptlint", "version": VERSION,
             "schema_version": 1,
             "findings_summary": {"count": len(all_findings),
                                  "highest_severity": highest},
             "results": results,
             "summary": {
                 "files": len(results),
                 "failed": sum(1 for r in results
                               if r["verdict"] == "FAIL"),
                 "worst_score": min(r["score"] for r in results),
                 "findings": sum(len(r["findings"])
                                 for r in results)}},
            indent=2, ensure_ascii=False))
    else:
        for r in results:
            print_lint_text(r)
            print()
        if len(results) > 1:
            print_summary_table(results)
            failed = sum(1 for r in results
                         if r["verdict"] == "FAIL")
            if failed:
                print(f"\n❌ FAIL — {failed} of {len(results)} "
                      "prompt(s) failed the lint.")
            elif any(r["findings"] for r in results):
                print(f"\n⚠️ WARN — no failures, but "
                      f"{sum(len(r['findings']) for r in results)} "
                      "finding(s) across the folder.")
            else:
                print(f"\n✅ PASS — all {len(results)} prompt(s) "
                      "are clean.")
    if any(r["verdict"] == "FAIL" for r in results):
        return 1
    if args.strict and any(r["findings"] for r in results):
        return 1
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="promptlint",
        description="Static lint for agent prompts — structure, "
                    "ambiguity, contradiction candidates, hygiene, "
                    "testability. Lint your prompts like your code.")
    ap.add_argument("--version", action="version",
                    version=f"promptlint {VERSION}")
    ap.add_argument("--no-color", action="store_true",
                    help="disable ANSI color output (promptlint "
                         "emits no color; accepted harmlessly)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    l = sub.add_parser("lint", help="lint one prompt file, or every "
                                    ".md/.txt prompt in a directory")
    l.add_argument("path", help="prompt file or directory of prompts")
    l.add_argument("--format", choices=("text", "json"),
                   default="text")
    l.add_argument("--strict", action="store_true",
                   help="exit 1 on ANY finding, not just highs")
    l.set_defaults(fn=cmd_lint)

    i = sub.add_parser("init", help="scaffold a sample weak prompt "
                                    "+ EXPECTED.md")
    i.add_argument("--force", action="store_true")
    i.add_argument("--dir", help="directory to write the sample "
                                 "files into")
    i.set_defaults(fn=cmd_init)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
