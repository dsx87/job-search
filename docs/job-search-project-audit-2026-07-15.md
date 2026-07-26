# Job Search Project Audit

**Date:** 2026-07-15
**Scope:** Read-only architecture, logic, search-quality, AI, cost, performance, state, TUI, workflow, and test review
**Implementation status:** Remediation complete; orders 1–8 done

## Remediation progress

| Order | Status | Scope / evidence |
|---|---|---|
| 1 | Completed before this change | Findings 1–2; commits `4109cab`, `ac7d314`, `ccae58a`, with follow-ups `80374ca`, `a2dd063`, `fe4fe77`, `60dc777` |
| 2 | Completed before this change | Findings 3–5; commits `30b75e2`, `ceb37d3`, `b0f8d9f`, `43f884a`, `2218938`, `4306581`, with documentation in `a9cf42f` |
| 3 | Partially completed — **see the 2026-07-25 correction below** | Configurable provider model/API-base plumbing landed and is in use. The Gemini 3.5 Flash migration was later **deliberately reverted** (commit `08a12a2`), so finding 6's dated shutdown risk is **still open**. Benchmark explicitly waived by user; offline request-contract coverage used. |
| 4a | Completed in this change | Canonical `Job` contract used end to end; commit `ef8e276` |
| 4b | Completed in this change | Canonical job identities shared across filtering, state, delivery, sources, and TUI; commits `b65f2d8`, `211bfa7` |
| 4c | Completed in this change | End-to-end source-health reporting, fatal-outage safeguards, and partial-refresh retention; commit `fb2554b` |
| 4d | Completed in this change | Union-safe evaluation-lifecycle markers (content/criteria signature, verdict, first/last seen) enable reopening changed/reopened postings while preserving legacy suppression; commit `9591a11` |
| 5 | Completed in this change | Duplicate records merged to keep the richest description/URL/date/region, and section-aware excerpting keeps late eligibility restrictions before AI; commit `5bc3e8b` |
| 6 | Completed in this change | Schema-constrained fact extraction + deterministic policy (executable criteria.md) with evidence grounding; low-confidence cases routed to an `uncertain` review section; commit `c1f66bb`. Confident-reject sampling deferred as observability. |
| 7 | Completed in this change | Model selects existing base bullets; deterministic renderer rebuilds the CV from the trusted base (verbatim, fabrication-free, always a compilable subset); commit `4d7b1fd`. Rephrasing/skill-reorder scoped out to preserve the no-fabrication guarantee. |
| 8 | Completed in this change | Token telemetry (input/output/thinking/cached), a XeLaTeX concurrency cap, and reduced JobSpy queries; commit `6722e95`. Micro-batching, context caching, and scrape checkpoints deferred as live-only (see below). |

> **Historical context:** The findings below preserve the project state and
> present-tense wording recorded during the original audit. Orders 1 and 2 have
> since resolved findings 1–5, and this change resolves finding 6; the progress
> table above is the authoritative current status.

## Executive summary

At audit time, the project had a solid modular foundation and all 123 offline
tests passed. The audit identified several realistic correctness problems that
could miss good jobs, permanently discard insufficiently analyzed jobs, send
misleading notifications, or deliver unverified CVs. See the remediation table
above for their current status.

The most important findings are:

1. The deterministic prefilter contradicts the written EU relocation criteria and drops valid EU jobs before AI evaluation.
2. The daily pipeline evaluates empty or tiny descriptions and can permanently mark those jobs as non-fits.
3. A match can be marked seen even when its tailored CV was not generated or delivered.
4. CV validation and one-page checks log unresolved failures but still allow automatic delivery.
5. The primary Gemini model is hardcoded, its configuration fields are ineffective, and the selected model is scheduled for shutdown on October 16, 2026.
6. Search performance is dominated by overlapping JobSpy calls, non-cancellable source threads, and resource-insensitive concurrency.

## High-priority findings

### 1. EU jobs are rejected before the AI sees them — resolved in order 1

The criteria accept jobs in Germany or other EU countries unless they explicitly contain authorization or sponsorship blockers. The deterministic filter instead requires positive relocation wording for every non-remote EU job.

Evidence:

- [criteria.md](../criteria.md#L12-L17)
- [job_search/filters/rules.py](../job_search/filters/rules.py#L234-L252)

A full-time Berlin iOS role with Swift/UIKit and no blocker was reproduced as being removed. This is likely the largest source of false negatives.

Recommended change: make the deterministic stage high-recall. For EU jobs, reject only on explicit blockers and let the structured eligibility stage resolve ambiguity.

### 2. Daily evaluation permanently rejects jobs with missing or tiny descriptions — resolved in order 1

`MIN_JOB_TEXT_LEN` protects manual tailoring, but the daily pipeline does not use it. Link-based sources may provide only anchor text, and LinkedIn descriptions may be blank. The AI still evaluates them; if it returns a non-fit verdict, the job is immediately marked seen forever.

Evidence:

- [job_search/sources/parsers.py](../job_search/sources/parsers.py#L211-L239)
- [job_search/pipeline/run.py](../job_search/pipeline/run.py#L128-L150)
- [job_search/pipeline/cli.py](../job_search/pipeline/cli.py#L19-L32)

The audit reproduced a four-character description being evaluated and having its URL and title/company keys persisted after rejection.

Recommended change: add a daily data-quality/enrichment stage. Insufficient descriptions should be fetched from the posting URL, retried, or placed in an `unknown` queue rather than classified as non-fit.

### 3. A match can be marked seen without receiving its CV — resolved in order 2

`send_fit()` catches document-upload failures and does not rethrow them. `run_daily()` then marks the job seen because the text notification succeeded. A tailoring failure can similarly produce a payload with no document and still count as successfully delivered.

Evidence:

- [job_search/pipeline/stages.py](../job_search/pipeline/stages.py#L124-L155)
- [job_search/pipeline/run.py](../job_search/pipeline/run.py#L179-L194)

Recommended change: track separate states such as `evaluated`, `notification_sent`, `cv_generated`, and `cv_sent`. Only complete the job when the required delivery policy is satisfied.

### 4. The pipeline can report “none matched” when matches actually failed — resolved in order 2

Whenever `sent == 0`, the completion message says no posting matched. That includes cases where fits existed but preparation or Telegram delivery failed.

Evidence: [job_search/pipeline/run.py](../job_search/pipeline/run.py#L196-L205)

The audit reproduced a fit whose Telegram send failed; the resulting completion message still said that none matched the criteria.

Recommended change: report separate counts for evaluated, non-fit, fit, preparation-failed, notification-sent, and delivery-failed.

### 5. CV verification logs failures but automatically sends the invalid result — resolved in order 2

After the corrective tailoring retry, unresolved fabrication violations are logged as `REVIEW BEFORE SENDING`, but the invalid LaTeX is returned and delivered automatically.

Evidence: [job_search/llm/tailor.py](../job_search/llm/tailor.py#L58-L66)

The page guard has the same problem: if shrinking cannot reach one page, `compile_with_fixes()` still returns success. It also treats the mere existence of a PDF as success even if `xelatex` returned a non-zero exit status.

Evidence:

- [job_search/latex/compile.py](../job_search/latex/compile.py#L89-L102)
- [job_search/latex/compile.py](../job_search/latex/compile.py#L127-L149)
- [job_search/latex/onepage.py](../job_search/latex/onepage.py#L52-L77)

All three paths were confirmed with controlled in-memory tests.

Recommended change: unresolved factual violations, compilation errors, unknown page counts, or multi-page results should block automated delivery or enter a manual-review queue.

### 6. The primary model is hardcoded and nearing shutdown — **half resolved; see the correction at the end of this section**

`gemini-2.5-flash` is hardcoded in the client. Although `PipelineConfig` exposes model and API-base fields, they are never passed to the client.

Evidence:

- [job_search/config.py](../job_search/config.py#L31-L48)
- [job_search/llm/clients.py](../job_search/llm/clients.py#L23-L53)

Google currently lists `gemini-2.5-flash` for shutdown on October 16, 2026, with `gemini-3.5-flash` as the recommended replacement: [official Gemini deprecation schedule](https://ai.google.dev/gemini-api/docs/deprecations?hl=en).

Recommended change: make model selection effective end-to-end and benchmark the replacement against a fixed labeled set before migration.

> **Correction (2026-07-25).** Only the plumbing half of this finding is
> resolved. `LLMClient.from_config` does thread scheme/model/base from config, but
> commit `08a12a2` deliberately flipped the primary *back* to `gemini-2.5-flash`
> ("steadier than 3.5") — a quality call, not a regression. The **October 16, 2026
> shutdown risk is therefore still open**, and this table previously asserted
> otherwise. Mitigations landed on 2026-07-25 rather than a forced migration:
> `LLM_MODEL_SHUTDOWN_DATES` in `config.py` records the date; the run log and the
> digest footer warn from 120 days out; and `LLM_MODEL_REJECT_STATUS` (400/404)
> makes a retired model fail loudly once and disable the primary for the run
> instead of paying a doomed request per job. Note the fallback key is what turns
> a shutdown into a degraded run rather than a total outage.

## Search precision and deduplication

### 7. Deduplication keeps whichever duplicate source finishes first

Source results are accumulated in completion order, then `dedup()` keeps the first record. That winner may have the shortest description, worse URL, missing date, or poorer location while a richer duplicate is discarded.

Evidence:

- [job_search/sources/fetch.py](../job_search/sources/fetch.py#L123-L157)
- [job_search/filters/rules.py](../job_search/filters/rules.py#L255-L269)

Recommended change: merge duplicate records and preserve the richest cleaned description, all source names and IDs, the best canonical application URL, structured locations and remote restrictions, and the most trustworthy date.

### 8. Jobs without URLs collapse incorrectly

The URL dedup key becomes an empty string. Once the first URL-less job is accepted, every later URL-less job is treated as a duplicate regardless of title or company. Two unrelated URL-less jobs were reproduced as becoming one.

Recommended change: skip the URL comparison when no URL exists and use a fallback source ID or normalized content identity.

### 9. Permanent seen keys cannot recognize updated or reopened jobs

State is a set of URL and title/company/location strings. It has no description hash, posting date, verdict, criteria version, or expiry. A corrected description or genuinely reopened position can remain skipped forever.

Evidence:

- [job_search/state/seen_jobs.py](../job_search/state/seen_jobs.py#L14-L36)
- [job_search/pipeline/run.py](../job_search/pipeline/run.py#L95-L116)

Recommended change: use structured records containing canonical identity, content hash, first/last seen, evaluation version, verdict, and delivery state.

### 10. Several realistic lexical cases are wrong

Confirmed examples from [job_search/filters/rules.py](../job_search/filters/rules.py#L140-L246) and [job_search/filters/keywords.py](../job_search/filters/keywords.py#L4-L68):

- A Berlin job mentioning collaboration with an India team is rejected as an India job because the exclusion scans the full description.
- `Remote work is not available` is interpreted as remote evidence.
- Common spellings such as `CoreData` and plain `Combine` are missing.
- The test suite accepts `core database` as a match for `core data`, creating false positives.
- Israeli jobs are classified as `UNKNOWN`; common locations such as Ramat Gan are absent even though the CV itself lists Ramat Gan.

Recommended change: use token-aware parsing, negation handling, technology aliases, and structured country/remote-scope fields. India exclusion should primarily inspect the job location or explicit candidate-location restrictions, not incidental description text.

### 11. Descriptions are truncated before important requirements may appear

Evaluation reads only the first 5,000 characters; tailoring reads 7,000. Restrictions such as `US residents only`, sponsorship rules, office attendance, and required skills frequently appear near the end.

Evidence:

- [job_search/llm/eval.py](../job_search/llm/eval.py#L13-L31)
- [job_search/llm/tailor.py](../job_search/llm/tailor.py#L16-L40)

A restriction immediately after character 5,000 was reproduced as invisible to evaluation. Several sources also retain raw HTML, so markup consumes part of this limited window.

Recommended change: clean text once and retain section-aware content: title and summary plus complete requirements, location, eligibility, employment, and working-arrangement sections.

## Performance and cost

### 12. JobSpy performs 66 scraper calls per run

The generic source executes five queries across twelve countries sequentially, followed by four global LinkedIn searches and two Israel searches. The generic searches request up to 30-day-old results every day.

Evidence: [job_search/sources/jobspy_sources.py](../job_search/sources/jobspy_sources.py#L29-L108)

Recommended change:

- Reduce overlapping queries.
- Use incremental per-source checkpoints.
- Split query/location work into independently reportable tasks so partial results are retained.
- Fetch metadata first and full descriptions only for unseen, plausible candidates where the source permits it.

### 13. Abandoned source threads continue running

The scrape budget stops waiting for a source, but its daemon thread is not cancelled. Threads waiting on the semaphore can even start after the pipeline has moved into AI evaluation.

Evidence: [job_search/sources/fetch.py](../job_search/sources/fetch.py#L106-L148)

Recommended change: use cancellable subprocesses or cooperative cancellation. At minimum, prevent queued work from starting after the deadline and isolate slow JobSpy/Playwright sources from later CPU-heavy stages.

### 14. Concurrency is not resource-aware

Up to twelve evaluation calls and eight tailoring-plus-`xelatex` jobs run concurrently. Eight parallel XeLaTeX compilations can make a low-core device or GitHub runner slower through contention, while twelve simultaneous LLM calls can trigger the circuit breaker and move the entire run to Qwen.

Recommended change: separate limits for provider request rate, CV generation, XeLaTeX compilation, and browser-backed scraping. XeLaTeX likely needs only one or two workers on a typical small runner.

### 15. Tailoring repeatedly sends a large static prompt

The static tailoring instructions plus base LaTeX are approximately 22,500 characters, roughly 5,600 Gemini tokens before the job description. The extracted instructions also include irrelevant shell-compilation and report-generation directions.

Evidence:

- [job_search/config.py](../job_search/config.py#L99-L105)
- [cv_tailoring_prompt.md](../cv_tailoring_prompt.md#L118-L171)

Recommended changes:

- Reduce the prompt to the factual profile and tailoring constraints.
- Put common static content first and use context caching. Gemini and Qwen both currently support caching repeated prefixes: [Gemini caching](https://ai.google.dev/gemini-api/docs/caching?hl=en), [Qwen caching](https://www.alibabacloud.com/help/en/model-studio/context-cache).
- Record input, output, thinking, and cached token usage rather than only call counts: [Gemini token accounting](https://ai.google.dev/gemini-api/docs/tokens).
- Tailor only strong or top-ranked matches automatically; generate CVs for borderline matches on demand.

### 16. Batching is worthwhile for evaluation, not full CV generation

Two useful modes are available:

1. Immediate micro-batches: evaluate approximately five to ten jobs in one schema-constrained request using stable job IDs.
2. Delayed provider batches: fetch in one run, submit evaluations, then deliver results in a later run.

Both Gemini and Qwen currently advertise asynchronous batch inference at 50% of real-time cost: [Gemini Batch API](https://ai.google.dev/gemini-api/docs/batch-api), [Qwen batch inference](https://www.alibabacloud.com/help/en/model-studio/batch-inference/).

Keep CV generation to one job per response. Combining several complete LaTeX documents increases truncation and cross-job contamination.

## Better AI verification design

### 17. Extract facts first, then apply policy deterministically

Instead of asking one model for a direct Boolean verdict, ask it for structured facts:

- Primary platform and stack
- Seniority
- Employment type
- Remote scope and allowed countries
- Sponsorship and authorization blockers
- Office days
- Industry
- Timezone requirements
- Evidence snippets and explicit `unknown` fields

Then apply `criteria.md` in Python. This makes decisions auditable and prevents prompt wording from silently changing policy.

Gemini supports JSON Schema-constrained structured output; the current client only requests generic JSON MIME type: [official structured-output documentation](https://ai.google.dev/gemini-api/docs/structured-output?lang=rest).

### 18. Verify selectively instead of doubling every call

A cost-effective verification policy would:

- Verify every proposed match before expensive tailoring.
- Verify uncertain or low-information rejects.
- Audit a small sample of confident rejects to estimate false negatives.
- Require evidence snippets to exist in the cleaned posting.
- Send disagreements to an `uncertain` Telegram section rather than discarding them.

### 19. Generate structured CV edits, not complete LaTeX

Have the model return a small object containing:

- Chosen summary facts
- Bullet IDs and their ordering
- Approved rephrasing
- Skill ordering
- Explicitly omitted items

A deterministic renderer should build LaTeX from the trusted base. This would substantially reduce output tokens, compilation failures, fabricated content, and repair calls.

## Code-structure consistency

The package layout is good, but internal structure is not yet consistent:

- `Job` objects become incomplete dictionaries mid-pipeline; description is omitted and manually reattached. The TUI has a separate serializer. See [job_search/models.py](../job_search/models.py#L55-L66) and [job_search/state/job_store.py](../job_search/state/job_store.py#L12-L25).
- URL and job-identity logic is duplicated across filters, pipeline state, and TUI with different normalization.
- `ScraperConfig` is unused, and most `PipelineConfig` fields are decorative rather than effective.
- Source adapters inconsistently swallow failures, so the orchestrator cannot distinguish a healthy source with zero jobs from a failed source.
- `criteria.md`, the CV master profile, and the actual base CV have drifted. For example, criteria accept remote contract/freelance roles while the profile says full-time only.
- Tests are mainly characterization tests. There is no `run_daily` orchestration test, source-health or partial-refresh test, Telegram HTML-escaping test, daily description-quality test, or XeLaTeX return-code test.
- README still says the cron runs at 07:00 UTC, while the workflow now runs at 11:00 UTC: [README.md](../README.md#L13-L18), [.github/workflows/job_search.yml](../.github/workflows/job_search.yml#L3-L6).
- Dependencies are unpinned, leaving CI vulnerable to upstream JobSpy or Playwright changes.

## Additional realistic reliability issues

- The TUI deletes all stored jobs after an empty or partial refresh and never updates metadata for an existing URL. Refresh errors are assigned to a message that is not displayed once loading becomes false. See [job_search/state/job_store.py](../job_search/state/job_store.py#L61-L78) and [job_search/tui.py](../job_search/tui.py#L54-L62).
- Telegram HTML fields are not escaped. Company names such as `R&D`, values containing `<`, malformed URLs, or LLM reasons containing HTML-like characters can make delivery fail. See [job_search/pipeline/stages.py](../job_search/pipeline/stages.py#L58-L77).
- Scraper HTTP explicitly disables TLS certificate verification, unlike the other HTTP clients. See [job_search/http.py](../job_search/http.py#L35-L64).
- A dedicated LinkedIn source only looks back 48 hours, so a source outage lasting more than two days can create permanent search gaps.
- A first run evaluates every undated listing regardless of likely age, which can create an unexpectedly expensive initial run.

## Recommended implementation order

1. Fix criteria/prefilter mismatches and add the daily description-quality gate.
2. Correct delivery state, false summaries, CV guard behavior, and XeLaTeX success checks.
3. Make model selection genuinely configurable and migrate from Gemini 2.5 Flash.
4a. Introduce one canonical job model and use it end to end.
4b. Centralize canonical job identities across filtering, state, delivery, sources, and TUI.
4c. ~~Introduce source-health results that distinguish empty success from source failure.~~ Completed in `fb2554b`.
4d. ~~Replace permanent string-only seen state with structured lifecycle state.~~ Completed in `9591a11`.
5. ~~Add description enrichment and duplicate merging before AI.~~ Completed in `5bc3e8b`.
6. ~~Move evaluation to structured fact extraction plus deterministic policy and selective verification.~~ Completed in `c1f66bb`.
7. ~~Replace generated full LaTeX with structured CV edits and deterministic rendering.~~ Completed in `4d7b1fd`.
8. ~~Optimize JobSpy queries, worker limits, caching, batching, and token telemetry.~~ Token telemetry, XeLaTeX worker cap, and JobSpy query reduction completed in `6722e95`; batching/caching/scrape-checkpoints deferred as live-only.

## Verification and limitations

- Existing offline suite at audit time: **123 tests passed**.
- Offline suite after orders 1–3/4a–4c: **348 tests passed**.
- Offline suite after completing orders 4d–8: **472 tests passed**.
- Additional audit verification: **nine originally uncovered behaviors reproduced with in-memory assertions**.
- The original exploration was read-only; remediation changes are tracked in the table above.
- Every order was verified offline only, per the constraints below — no live behavior was exercised:
  - Real XeLaTeX integration was not run because `xelatex` was unavailable locally (the CV renderer and one-page guard are verified structurally).
  - No live scraping or paid Gemini/Qwen calls were made; new LLM behavior (structured fact extraction, schema-constrained output, CV bullet selection, token telemetry) is verified with request-contract and in-memory tests. The deterministic policy in `policy.py` is exhaustively unit-tested against `criteria.md`, but the model's real-world fact-extraction accuracy is not measured here.
- Deferred as live-only follow-ups (order 8): micro-batch / delayed-batch evaluation, provider context caching, and incremental scrape checkpoints / metadata-first fetching — each needs the live provider batch/cache API or live scraping to validate.
