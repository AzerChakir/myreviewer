"""Prompt templates for the analysis stages.

The templates mirror the summary-augmented prompting idea validated in
`../../utils.py` (a structured `[SUMMARY]` of intent improves comprehension):
here the model first produces an intent summary, then clusters the diff into
concerns while using that summary as context.

NIM chat endpoints do not guarantee JSON mode, so stages read structured
text blocks (e.g. `SUMMARY:`, `CONCERNS: ... TITLE/FILES/...`) that are parsed
robustly by the stage modules.
"""

SUMMARIZE_SYSTEM = (
    "You are a senior software engineer and code reviewer. You produce "
    "precise, concise natural-language summaries of what a pull request "
    "intends to change and why."
)

SUMMARIZE_USER = """Analyze the pull request below and write a structured summary of its overall intent. Do not list every file. State the problem the change solves, the approach it takes, the subsystems it affects, and any notable risks or unknowns.

PR title: {title}
PR description: {description}
Commit messages:
{commit_messages}

DECLARED REQUIREMENTS:
{requirements}

DIFF:
{diff}

Return EXACTLY this format (nothing else):

SUMMARY: <one-line intent of the whole PR>
PROBLEM: <the problem this change solves>
APPROACH: <how the change solves it>
SUBSYSTEMS: <affected subsystems/modules, comma-separated, or 'none'>
RISKS: <notable risks, unknowns or migrations, or 'none'>

If no requirements were provided, say so briefly on the RISKS line.
"""

CLUSTER_SYSTEM = (
    "You are a code review architect. Given a pull request diff you identify "
    "the distinct logical concerns (independent, separately reviewable topics) "
    "the change addresses, assign every changed file to a concern, and flag "
    "mixed concerns where unrelated topics got bundled together."
)

CLUSTER_USER = """Group the changes of the pull request into distinct concerns. A concern is a coherent, independently reviewable topic (e.g. 'add user avatar upload', 'fix slow orders query'). A large or mixed PR usually contains several concerns spread across files. Assign every file in the diff to exactly one concern; if a file truly mixes unrelated ideas, assign it to its dominant concern, set MIXED=yes for that concern, and explain in MIXED_NOTE.

PR title: {title}
PR description: {description}
PR summary: {summary}

DIFF:
{diff}

Return your answer in EXACTLY this format (no extra prose around it):

SUMMARY: <one-line restatement of the overall intent>
CONCERNS:
1. TITLE: <short name>
   RATIONALE: <1-2 sentences why these files form one concern>
   FILES: <comma-separated file paths>
   CHANGE_TYPE: <add_only | modify | remove_only | mixed>
   MIXED: <yes | no>
   MIXED_NOTE: <explain bundling if MIXED=yes, otherwise 'none'>
2. ...

Guidelines:
- Cover every file from the diff.
- CHANGE_TYPE reflects the dominant edit type across the concern's files.
- Keep concerns semantically disjoint; splitting a huge PR into 2-5 concerns is normal, a single huge concern is itself a finding.
"""

PLAN_SYSTEM = (
    "You are an expert on reviewer-friendly pull request structure. You turn "
    "a concern decomposition into a concrete, actionable split plan."
)

PLAN_USER = """Given the decomposition below, produce a reviewer-friendly plan to split the change into smaller pull requests.

One suggested PR per concern, named with a clear imperative title. Recommend an order (independent changes first, riskiest/refactors last). Note any file that must move between suggested PRs to keep each one self-contained, and call out dependency churn or formatting-only noise.

PR title: {title}
PR description: {description}
PR summary: {summary}

CONCERNS:
{concerns_text}

Return the plan as markdown with these sections:
## Suggested PRs
- **PR 1 — <title>** (files: <paths>): <why> 
- ...

## Ordering
<one paragraph: recommended merge order and why>

## Notes for the author
- <specific advice: mixed files to split, lockfile churn, noise, wording>
"""

DIAGNOSER_SYSTEM = (
    "You are a meticulous code reviewer. You judge whether a pull request is "
    "well-scoped and reviewer-friendly, using both quantitative signals and "
    "the concern decomposition."
)

DIAGNOSER_USER = """Assess the reviewability of the pull request described below.

PR title: {title}
PR stats:
{stats}
CONCERNS:
{concerns_text}
MIXED CONCERNS:
{mixed_text}

List reviewer-friendliness problems as a numbered markdown list. For each, give a severity label (critical / warning / info), the problem, and if relevant the files involved. Examples of problems: too many files, diff too large to review, mixed concerns, dependency churn, formatting noise, tests missing for a concern. End with a line 'VERDICT: single PR | multiple PRs' recommending one or the other. If nothing serious, the verdict may be 'single PR'.
"""

CLUSTER_PROMPT_BASE = """Concern summary for the planner:
"""

CHANGE_LOG_SYSTEM = (
    "You are a meticulous change reviewer. Given a pull request diff and its "
    "concern decomposition, you produce a clear, precise change log that "
    "compares what the code did BEFORE the PR with what it does AFTER it, "
    "concern by concern, together with the impact."
)

CHANGE_LOG_USER = """Compare the OLD (pre-PR) code with the NEW (post-PR) code for every concern below and write a precise, human-readable change log. Reference concrete behaviour and specific edited code (not just file names); reviewers should be able to understand the change's before/after without loading the whole diff.

PR title: {title}
PR summary: {summary}

CONCERNS:
{concerns_text}

DIFF:
{diff}

Return EXACTLY this format (one block per concern, nothing else):

CHANGE_LOG:
AREA 1: <concern title>
OLD: <what the code did before the change; deleted/edited behaviour>
NEW: <what the code does now; added/edited behaviour>
IMPACT: <behavioural impact, risks, migrations, notes for reviewers>
FILES: <comma-separated file paths>
AREA 2: ...

If a concern is untouched or noise, say so explicitly in OLD/NEW instead of skipping it.
"""

POST_REVIEW_SYSTEM = (
    "You are a senior code reviewer. After reading a change you review the "
    "resulting NEW code (correctness, edge cases, regressions, security, "
    "performance, maintainability) and report findings ordered by severity, "
    "then give an overall verdict on whether the new code is ready to merge."
)

POST_REVIEW_USER = """Review the NEW code introduced by this pull request — not its PR structure, and do not restate the change log or the split plan. Look for genuine bugs, edge cases, regressions, security/performance issues, and maintainability problems the change introduces. List only findings grounded in the diff; if the code is clean, say so in the verdict.

PR title: {title}
PR summary: {summary}

CONCERNS:
{concerns_text}

CHANGE LOG (old -> new):
{change_log_text}

DECLARED REQUIREMENTS:
{requirements}

DIFF:
{diff}

Return EXACTLY this format (nothing else):

POST_REVIEW:
FINDING 1: <critical | important | minor | nit>
TITLE: <short title>
BODY: <2-4 sentences: what is wrong, where, and why it matters>
FILES: <comma-separated file paths>
FINDING 2: ...
POST_REVIEW_VERDICT: <one paragraph: overall quality of the new code and any must-fix before merge>
"""

BUG_REVIEW_SYSTEM = (
    "You are a meticulous senior software engineer performing a deep review of "
    "the NEW code introduced by a pull request. You hunt for real bugs and "
    "risks that would bite users or maintainers, and you verify the change "
    "against the project's stated requirements. You only report issues you can "
    "point at in the diff, and incompatibilities are flagged and cited — never "
    "resolved automatically. The human reviewer keeps the final decision on "
    "whether to accept or reject the merge."
)

BUG_REVIEW_USER = """Deep-review the NEW code of this pull request (not its PR structure — that is judged elsewhere). Two jobs:

1) DISCOVER REAL BUGS AND PROBLEMS in the new code: logic errors, edge cases, concurrency issues, security holes, API-contract breaks, migration issues, error-handling gaps, performance traps, missing tests. Every finding must be grounded in specific code from the diff, and must include a concrete suggested fix.

2) CHECK THE CHANGE AGAINST THE PROJECT REQUIREMENTS: for each declared requirement decide whether the change satisfies it, only partially satisfies it, misses it, or cannot be verified from the diff. Cite the code that shows each status. State any real incompatibility plainly in REQUIREMENTS_VERDICT so a human reviewer can decide whether to accept or reject the PR.

PR title: {title}
PR summary: {summary}

CONCERNS:
{concerns_text}

CHANGE LOG (old -> new):
{change_log_text}

DECLARED REQUIREMENTS:
{requirements}

DIFF:
{diff}

Return EXACTLY this format (nothing else, no prose before or after):

BUGS:
BUG 1:
SEVERITY: <critical | important | minor | nit>
TYPE: <logic | edge-case | concurrency | security | api-contract | migration | error-handling | tests | performance>
TITLE: <short title>
LOCATION: <path:line or hunk reference>
DETAIL: <2-4 sentences: what is wrong, where, and why it matters>
FIX: <concrete suggested fix>
BUG 2:
...
REQUIREMENTS:
REQ 1:
REQUIREMENT: <requirement text>
STATUS: <satisfied | partial | unmet | unverified>
EVIDENCE: <cite the code/hunk that shows this status>
REQ 2:
...
REQUIREMENTS_VERDICT: <one paragraph: does the change meet the project requirements; list any incompatibilities explicitly>
MERGE_READINESS: <ready | fix-before-merge | rework>

Rules:
- Be terse: TITLE <= 12 words, DETAIL 1-3 short sentences, FIX <= 2 sentences, LOCATION one path:line.
- The REQUIREMENTS_VERDICT and MERGE_READINESS lines are the last two lines; always emit them, at most 4 sentences for REQUIREMENTS_VERDICT.
- If a requirement cannot be checked from the diff, mark it 'unverified' — do not invent an outcome.
- Empty BUGS block is fine when the code is clean; never invent findings.
- MERGE_READINESS is advisory only: 'ready', 'fix-before-merge' (specific findings must be fixed), or 'rework' (the approach itself is wrong).
"""

BUG_REVIEW_VERIFY_SYSTEM = (
    "You are the final verification pass of a code review. You critique a "
    "draft list of bug findings and requirement checks: drop weak, incorrect "
    "or speculative findings, correct severities and types, merge duplicates, "
    "tighten wording, and re-rank. Keep only what a confident, strict senior "
    "reviewer would actually send."
)

BUG_REVIEW_VERIFY_USER = """Critique and revise the draft review below. Keep the same BUGS / REQUIREMENTS / REQUIREMENTS_VERDICT / MERGE_READINESS format.

- Remove findings not directly grounded in the diff.
- Merge duplicates (keep the strongest wording).
- Fix wrong severities or bug types.
- Keep requirement checks accurate to the evidence; do not soften real incompatibilities in REQUIREMENTS_VERDICT.
- Keep MERGE_READINESS.

PR title: {title}

DRAFT REVIEW:
{draft}

Return ONLY the revised block, in the same format.
"""

ASK_SYSTEM = (
    "You are a senior software engineer reviewing a pull request. Answer the "
    "reviewer's question precisely and concisely, grounding every claim in the "
    "provided diff and PR context. If the answer is not determinable from the "
    "diff, say so explicitly rather than guessing."
)

ASK_USER = """Answer the question about the pull request below, using only the diff and context provided.

PR title: {title}
PR description: {description}

COMMIT MESSAGES:
{commit_messages}

DIFF:
{diff}

QUESTION:
{question}

Answer in markdown, 1-4 short paragraphs. Refer to specific files/functions
when helpful. If the diff does not contain enough information, say what is
missing.
"""