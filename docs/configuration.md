# Runtime composition

The pipeline has an optional Python composition layer for replacing prompts,
providers, filtering policy, candidate identity, CV rendering, section loading,
and output delivery. Existing installations do not need a config file: when
`job_search_config.py` is absent, the built-in behavior is unchanged.

> **Security boundary:** the composition file is trusted executable code and
> `job_search_config.py` is intentionally allowed by the repository's
> deny-by-default `.gitignore`. Treat it like application source and review it
> before every commit. Never put credentials, tokens, private CV values, or
> secret-derived output in it. Those values belong only in environment
> variables, a mode-600 `.env`, or dedicated GitHub Actions secrets.

A composition module may explicitly import a separately installed package;
the core does not discover plugins or install their dependencies.

## Loading and checking configuration

The loader looks for `job_search_config.py` in the working directory. Its
absence is optional. Set `JOB_SEARCH_CONFIG_FILE` to use another path; an
explicit path must exist. A present file that cannot be imported, does not
export `configure`, returns the wrong type, or has incompatible capabilities is
an error.

Every module exports one function:

```python
from dataclasses import replace


def configure(defaults, settings):
    return replace(defaults, candidate_filter=MyFilter())
```

`defaults` is a `job_search.components.Components` instance and `settings` is
the effective `PipelineConfig`. Use `dataclasses.replace` to retain every
component you are not changing. The module is loaded directly from its source
path with Python's standard import machinery; normal imports inside it still
work.

Validate a configuration without scraping, changing state, calling an LLM,
compiling a CV, or delivering output:

```bash
python -m job_search.pipeline --check-config
JOB_SEARCH_CONFIG_FILE=config/production.py \
  python -m job_search.pipeline --check-config
```

The command prints effective scalar settings and component class names as JSON.
API keys, tokens, and chat identifiers are redacted. Runtime commands validate
composition before state synchronization, scraping, or network activity.

Start with the tested example:

```bash
cp job_search_config.example.py job_search_config.py
python -m job_search.pipeline --check-config
```

The example is a no-op until one of its documented `JOB_SEARCH_*` variables is
set. It demonstrates file-backed prompts, profile naming, XeLaTeX selection,
and filesystem output.

## Scalar and file settings

`PipelineConfig.from_env()` remains the source for ordinary values and secrets.
All legacy provider names continue to work. The composition module overlays
objects after those settings are read.

| Environment variable | Default | Purpose |
|---|---|---|
| `JOB_SEARCH_CONFIG_FILE` | optional `job_search_config.py` | trusted composition module |
| `SEEN_JOBS_FILE` | `seen_jobs.json` | persistent seen/retry state |
| `CRITERIA_FILE` | `criteria.md` | human-readable criteria and default evaluation fingerprint input |
| `CV_TAILORING_PROMPT_FILE` | `cv_tailoring_prompt.md` | compatibility instruction file |
| `BASE_TEX_FILE` | `igor_pivnyk_cv_base_updated.tex` | default base CV source |
| `OUT_PDF_FILE` | `igor_pivnyk_cv_base_updated.pdf` | rendered base-document output |
| `SECTIONS_FILE` | `sections.py` | default soft-failing section configuration |
| `LLM_PRIMARY_AUTH_MODE` | `bearer` | `bearer` or explicit `none` for a local OpenAI-compatible server |
| `LLM_FALLBACK_AUTH_MODE` | `bearer` | fallback equivalent |

`criteria.md` remains part of the built-in evaluator fingerprint, so changing
it reopens previously rejected jobs. The executable built-in decisions live in
`job_search/policy.py`; the criteria document does not itself execute policy.

`cv_tailoring_prompt.md` is retained for compatible paths and deployments. The
current default tailorer asks the model to select existing bullet indices and
renders that selection deterministically, so this instruction file is not part
of the bullet-selection prompt.

## Component contracts

Contracts are structural protocols: custom classes match by attributes and
method signatures and do not need to inherit project classes.

| Component | Required shape |
|---|---|
| `prompts` | `revision`; builders `fact_extraction`, `job_summary`, `cv_bullet_selection`, `compiler_repair` |
| `llm` | `generate(...)` and `usage_summary()` |
| `candidate_filter` | `revision` and `include(job) -> bool` |
| `evaluator` | `revision`, `evaluate(llm, criteria, job) -> dict`, and `fingerprint(criteria) -> str`; optionally `requires_criteria = False` |
| `profile` | `CandidateProfile` data and validation behavior |
| `cv_renderer` | `media_types`, `render_tailored(...)`, and `render_base(llm=None)` returning `CVArtifact` |
| `section_provider` | `load() -> (sections, error)` |
| `output_renderer` | `kind` and pure `render_notice`, `render_fit`, `render_digest` methods |
| `output_backend` | accepted renderer/media kinds, `cv_mode`, and atomic notice/fit/digest delivery methods |

An output backend declares `cv_mode = "required"` or `"disabled"`. Required
mode keeps the verified-artifact retry lifecycle. Disabled mode skips tailoring
and compilation; a successful configured delivery completes the fit. Manual
`--tailor` is rejected during configuration validation for a disabled backend.
Renderer kinds and CV media types must intersect the backend's accepted
capabilities or validation fails.

For digest delivery, `DigestOutcome` is an explicit completion receipt. Success
requires `delivered=True` and `notification_sent=True`; a required-CV backend
must also report `cv_sent >= len(artifacts)` for the artifacts passed to
`deliver_digest`. Returning the shorthand `DigestOutcome(True)` therefore does
not complete the batch: it schedules delivery retries and makes the run exit
nonzero. On a partial result, set `notification_sent=True` only if the user was
actually notified so retry reconciliation can avoid duplicating that notice.

Evaluators require `CRITERIA_FILE` by default for compatibility. A replacement
that completely owns its policy and fingerprint may declare
`requires_criteria = False`; configuration preflight and runtime then neither
require nor read that file, and both evaluator methods receive an empty string
for their `criteria` argument.

The default `LatexCompiler(executable="pdflatex")` retains two compiler passes,
LLM repair for eligible failures, deterministic shrinking, and exact one-page
enforcement. A `CVArtifact` carries `filename`, `media_type`, and `content`
bytes. Existing digest callers may continue using `pdf_bytes` and
`cv_filename`.

## File-backed prompts and revisions

`FilePromptSet` uses `string.Template` placeholders. A nonempty revision is
required. That revision participates in the default evaluator fingerprint, so
a behavioral prompt change can reopen previous non-fits.

```python
from dataclasses import replace
from job_search.components import FilePromptSet


def configure(defaults, settings):
    prompts = FilePromptSet(
        revision="team-prompts-2026-08-20",
        fact_extraction_file="prompts/facts.txt",
        job_summary_file="prompts/summary.txt",
        cv_bullet_selection_file="prompts/cv-selection.txt",
        compiler_repair_file="prompts/compiler-repair.txt",
    )
    return replace(defaults, prompts=prompts)
```

The fact and summary templates receive `$title`, `$company`, `$location`,
`$is_remote`, and `$description`. CV selection also receives
`$resume_bullets` and `$candidate_name`. Compiler repair receives `$tex_source` and
`$compiler_errors`. Use `$$` for a literal dollar sign. Omitted file arguments
fall back individually to `DefaultPromptSet`.

The loader automatically rebuilds the built-in evaluator and CV renderer when
only `prompts` changes. If a custom evaluator has its own prompt relationship,
replace both components explicitly.

## Local OpenAI-compatible inference

No composition module is needed for a local endpoint. LM Studio is the tested
example: start its local server, load a model that supports structured output,
and set:

```dotenv
LLM_PRIMARY_SCHEME=openai
LLM_PRIMARY_MODEL=your-loaded-model-id
LLM_PRIMARY_API_BASE=http://127.0.0.1:1234/v1
LLM_PRIMARY_AUTH_MODE=none
LLM_PRIMARY_API_KEY=
```

No-auth mode omits the `Authorization` header while retaining the OpenAI
chat-completions JSON-schema request. It is explicit and is never inferred from
a loopback URL. The URL is relative to the machine running the pipeline: a
GitHub-hosted runner cannot reach LM Studio on your laptop. See the
[LM Studio server guide](https://lmstudio.ai/docs/developer/core/server) and
[structured-output guide](https://lmstudio.ai/docs/developer/openai-compat/structured-output).

## A custom provider

Implement `LLMProvider` and wrap it in `LLMClient` to retain usage aggregation,
fallback routing, and circuit-breaker behavior. This sketch assumes the user
installed `acme_llm`; it is not a core dependency.

```python
from dataclasses import replace
from acme_llm import Client
from job_search.llm.clients import LLMClient


class AcmeProvider:
    scheme = "acme"
    model = "acme-structured-v2"
    requires_api_key = True

    def __init__(self, api_key):
        self.client = Client(api_key=api_key)

    def generate(self, prompt, temperature=0.0, json_mode=False,
                 response_schema=None):
        return self.client.generate(
            prompt=prompt,
            temperature=temperature,
            json_schema=response_schema,
        )


def configure(defaults, settings):
    provider = AcmeProvider(settings.llm_primary_api_key)
    return replace(defaults, llm=LLMClient(provider))
```

If a service replaces `LLMClient` entirely, it implements `generate(...)` and
`usage_summary()`. It is then responsible for its own telemetry, fallback, and
breaker semantics.

## Custom filter, evaluator, and non-default candidate

The candidate filter runs before any evaluation LLM call. The evaluator must
return the existing shape: `fit`, `reason`, `timezone_note`, `verdict`, and
`facts`. Its fingerprint controls when stored non-fit decisions are reopened.

```python
from dataclasses import replace
import hashlib

from job_search.components import CandidateProfile


class ProductEngineerFilter:
    revision = "product-engineering-filter-v2"

    def include(self, job):
        title = str(getattr(job, "title", "")).lower()
        return "engineer" in title and "qa" not in title


class ProductEvaluator:
    revision = "product-policy-v3"

    def evaluate(self, llm, criteria, job):
        is_fit = "swift" in str(getattr(job, "description", "")).lower()
        return {
            "fit": is_fit,
            "verdict": "fit" if is_fit else "nonfit",
            "reason": "Swift product role" if is_fit else "No Swift signal",
            "timezone_note": None,
            "facts": {},
        }

    def fingerprint(self, criteria):
        material = (criteria + "\n" + self.revision).encode("utf-8")
        return hashlib.sha256(material).hexdigest()[:16]


def configure(defaults, settings):
    profile = CandidateProfile(
        display_name="Ada Example",
        base_tex_path="ada_cv.tex",
        rendered_base_path="ada_cv.pdf",
        cv_filename_prefix="ada_example_cv",
        employer_order=("Example Labs", "Earlier Studio"),
        forbidden_claim_patterns=(r"invented patent", r"security clearance"),
        private_placeholders={
            "((PHONE))": "ADA_CV_PHONE",
            "((EMAIL))": "ADA_CV_EMAIL",
        },
        revision="ada-profile-v4",
    )
    return replace(
        defaults,
        candidate_filter=ProductEngineerFilter(),
        evaluator=ProductEvaluator(),
        profile=profile,
    )
```

Private placeholder values are read from the named environment variables only
when the document is compiled. Do not embed them in the profile or source file.
When changing profile semantics, replace or rebuild the CV renderer as shown in
the next section; the loader rebuilds the built-in renderer automatically when
only the profile changes.

## XeLaTeX and custom CV artifacts

Use the default deterministic renderer with another LaTeX executable:

```python
from dataclasses import replace
from job_search.components import DefaultCVRenderer, LatexCompiler


def configure(defaults, settings):
    compiler = LatexCompiler(
        executable="xelatex",
        prompts=defaults.prompts,
        profile=defaults.profile,
    )
    renderer = DefaultCVRenderer(
        settings,
        defaults.profile,
        prompts=defaults.prompts,
        compiler=compiler,
    )
    return replace(defaults, cv_renderer=renderer)
```

Install XeLaTeX on every host that runs this configuration. The command name is
passed as one executable, not a shell command or arbitrary argument string.

A whole renderer can produce another artifact type when the backend accepts it:

```python
from dataclasses import replace
from job_search.components import CVArtifact
from job_search.output import FilesystemOutputBackend, HtmlOutputRenderer


class TextCVRenderer:
    media_types = ("text/plain",)

    def render_tailored(self, llm, job, evaluation=None):
        body = "Candidate summary for {} at {}\n".format(job.title, job.company)
        return CVArtifact("candidate-summary.txt", "text/plain", body.encode())

    def render_base(self, llm=None):
        return CVArtifact("candidate-base.txt", "text/plain", b"Candidate base\n")


def configure(defaults, settings):
    return replace(
        defaults,
        cv_renderer=TextCVRenderer(),
        output_renderer=HtmlOutputRenderer(),
        output_backend=FilesystemOutputBackend("build/job-digest", cv_mode="required"),
    )
```

## Sections without a file

The default `SectionProvider` keeps the existing `sections.py` loader: a
missing or invalid file is a soft presentation fallback and the digest remains
ungrouped. A composition module may provide sections directly:

```python
from dataclasses import replace
from job_search.digest.sections import Section, is_remote


class StaticSections:
    def load(self):
        return (Section("Remote", "🌍", match=is_remote),), ""


def configure(defaults, settings):
    return replace(defaults, section_provider=StaticSections())
```

## Filesystem, plain messages, and Telegram

The built-in HTML/filesystem pair stages a complete hidden generation and
atomically promotes `index.html` and its artifacts together:

```python
from dataclasses import replace
from job_search.output import FilesystemOutputBackend, HtmlOutputRenderer


def configure(defaults, settings):
    return replace(
        defaults,
        output_renderer=HtmlOutputRenderer(),
        output_backend=FilesystemOutputBackend(
            "build/job-digest", cv_mode="required"
        ),
    )
```

Set `cv_mode="disabled"` for an HTML-only digest. In that mode Telegram
credentials are not required and CV work is skipped.

With no output override, the built-in renderer/backend preserve the current
Telegram behavior: per-job messages, the HTML ZIP digest, optional
Telegraph/X0 delivery, fallback, retraction, alerts, and summaries. The inbound
Telegram command bot remains a separate Telegram-only surface; composition
does not generalize its commands.

For a text messaging service, `PlainMessageBackend` adapts a callable and
declares `cv_mode="disabled"`:

```python
from dataclasses import replace
from job_search.output import PlainMessageBackend, PlainTextOutputRenderer


def send_whatsapp(message):
    # Call your chosen provider here. Read its token from the environment.
    # Raise on failure so the existing retry state records the failed delivery.
    raise NotImplementedError


def configure(defaults, settings):
    return replace(
        defaults,
        output_renderer=PlainTextOutputRenderer(),
        output_backend=PlainMessageBackend(send_whatsapp),
    )
```

This is the expected shape of a WhatsApp adapter, not a maintained built-in
integration. A document-capable adapter should instead implement the full
`OutputBackend`, declare accepted media types and `cv_mode="required"`, and
return `job_search.pipeline.stages.DeliveryOutcome` for each fit plus
`job_search.components.DigestOutcome` for a digest.

## Deployment

A tracked `job_search_config.py` is picked up automatically. This is an
intentional exception to the repository's deny-by-default ignore rules: it must
contain reviewed composition code only. GitHub Actions also supports a
multiline `JOB_SEARCH_CONFIG_PY` Actions secret; each relevant workflow writes
that value to `job_search_config.py` after checkout. That secret is a transport
for source code, not a place for credentials or private values. Separately
install any imports your module needs in the deployment workflow.

On a persistent host such as a Raspberry Pi, either keep a reviewed
`job_search_config.py` in the checkout or set an absolute path in `.env`:

```dotenv
JOB_SEARCH_CONFIG_FILE=/home/pi/job-search-config/production.py
```

The core and the example remain Python 3.9-compatible and standard-library
only. Source discovery stays under the existing enable/disable controls; it is
not part of runtime composition. Custom components intentionally change their
owned behavior, while built-in defaults retain the existing state and retry
lifecycle.
