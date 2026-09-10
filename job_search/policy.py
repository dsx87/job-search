"""Deterministic fit policy over extracted facts — the executable form of
criteria.md (audit Findings 17, 18).

apply_policy consumes a normalized facts dict (job_search/llm/facts.py) and the
job, and returns a verdict of "fit", "nonfit", or "uncertain" plus a reason and
timezone note. Decisions are auditable and independent of prompt wording. A
verdict that would REJECT a job on a positive claim (crypto, cross-platform,
junior, an authorization blocker, a US/Canada-only restriction) is downgraded to
"uncertain" unless the claim's evidence snippet is grounded in the posting text,
so a hallucinated blocker surfaces for review instead of silently dropping a
good job.

Two gates sit outside that grounding scheme because they are decided directly
from the posting text (no LLM fact to hallucinate), so they reject hard: the
description must be written in English, and a non-remote role must explicitly
state remote work or offer relocation/visa sponsorship. Israeli roles are exempt
from both (Igor is local; some postings are in Hebrew).
"""
import hashlib
import json
import re
from .location.classify import is_israel_job, remote_residency_restriction
from .models import coerce_job
from .text import collapse_ws, is_probably_english

_US_CA_GROUP = {"US", "USA", "UNITED STATES", "CA", "CANADA"}
_TZ_NOTE = "Role requires US working hours (Igor is UTC+3) — review the timezone mismatch."


def _decision(verdict, reason, timezone_note=None):
    return {"verdict": verdict, "reason": reason, "timezone_note": timezone_note}


def _grounded(facts, field, text):
    """True when the evidence snippet for `field` appears in the posting text."""
    snippet = str((facts.get("evidence") or {}).get(field) or "").strip()
    if not snippet:
        return False
    return collapse_ws(snippet).lower() in collapse_ws(text).lower()


def _apply_legacy_policy(facts, job) -> dict:
    """Return {"verdict", "reason", "timezone_note"} for the given facts + job."""
    job = coerce_job(job)
    text = job.description
    tz = _TZ_NOTE if facts.get("requires_us_hours") == "yes" else None

    # Language gate — the description must be written in English (Israeli roles
    # exempt; some are in Hebrew and need no sponsorship). Decided directly from
    # the posting text, so a hard nonfit is safe: no LLM fact to hallucinate.
    if not is_israel_job(job) and not is_probably_english(text):
        return _decision("nonfit", "Job description is not written in English.")

    def reject(field, reason, unverified_reason):
        if _grounded(facts, field, text):
            return _decision("nonfit", reason)
        return _decision("uncertain", unverified_reason, tz)

    if facts.get("industry_crypto_web3") == "yes":
        return reject(
            "industry_crypto_web3",
            "Crypto/Web3 company — excluded by criteria.",
            "Possibly a crypto/Web3 company, but the posting does not clearly say so (unverified) — review.",
        )
    platform = facts.get("platform_focus")
    if platform == "cross_platform":
        return reject(
            "platform_focus",
            "Primary stack is a cross-platform framework, not native iOS/macOS.",
            "Primary stack may be cross-platform, but it is not clearly stated (unverified) — review.",
        )
    if platform == "other":
        return reject(
            "platform_focus",
            "Role is not iOS/macOS focused.",
            "Role may not be iOS/macOS focused, but it is unclear (unverified) — review.",
        )
    if facts.get("seniority") == "junior":
        return reject(
            "seniority",
            "Explicitly junior/entry-level role.",
            "Seniority may be junior, but it is not clearly stated (unverified) — review.",
        )

    # An accept reason must not assert more than the facts do. platform_focus
    # "unknown" reaches the accepting branches below (only cross_platform/other
    # reject), and those used to report "iOS/macOS role" regardless (finding 15).
    focus = "iOS/macOS" if platform == "ios_macos" else "iOS/macOS focus unconfirmed"

    arrangement = facts.get("work_arrangement")
    if is_israel_job(job):
        if arrangement in ("hybrid", "onsite") and facts.get("office_days_4plus") == "yes":
            return reject(
                "office_days_4plus",
                "Israeli role requires 4+ office days per week.",
                "Israeli role may require 4+ office days, but it is unclear (unverified) — review.",
            )
        return _decision(
            "fit", f"Israeli role ({focus}) meeting office-attendance criteria.", tz
        )

    # Residency restriction carried in the authoritative location field: a
    # source-marked remote role tied to a geography that excludes Israel is
    # unavailable, and relocation cannot help a remote role. Aggregator sources
    # (arc, remotive, jobicy, himalayas, RSS) put this lock in `location`, not the
    # description, so the LLM never sees it. A grounded sponsorship offer still
    # overrides. Israeli roles already returned above.
    if job.is_remote and remote_residency_restriction(job) == "restricted":
        where = job.location.strip()
        if where.lower().startswith("remote"):
            where = where[len("remote"):].lstrip(" -–—,:").strip() or job.location.strip()
        if facts.get("offers_sponsorship") == "yes" and _grounded(facts, "offers_sponsorship", text):
            return _decision(
                "fit",
                f"Remote role tied to {where}, but relocation/visa sponsorship is offered.",
                tz,
            )
        return _decision(
            "nonfit",
            f"Remote role restricted to {where} — residency required there (you are in Israel).",
        )

    employment = facts.get("employment_type")
    if employment in ("contract", "freelance", "part_time") and arrangement != "remote":
        label = employment.replace("_", " ")
        if arrangement in ("onsite", "hybrid"):
            return reject(
                "work_arrangement",
                f"{label} role that is not fully remote.",
                f"{label} role; on-site/hybrid arrangement not clearly stated (unverified) — review.",
            )
        return _decision("uncertain", f"{label} role with an unclear work arrangement — verify.", tz)

    if arrangement == "remote":
        if facts.get("remote_geo_scope") == "restricted":
            # criteria.md: "Skip a remote role that is restricted to a specific
            # country or region Igor cannot work from ... UNLESS it offers
            # relocation/visa sponsorship." The location-based branch above
            # honored that exception; this description-based one ignored it, so
            # the same job got opposite verdicts depending on where the
            # restriction was written down (finding 15).
            if facts.get("offers_sponsorship") == "yes" and _grounded(
                facts, "offers_sponsorship", text
            ):
                return _decision(
                    "fit",
                    f"Remote role ({focus}) with a residency restriction, but "
                    "relocation/visa sponsorship is offered.",
                    tz,
                )
            countries = [c.upper() for c in facts.get("restricted_to_countries") or []]
            if countries and set(countries) <= _US_CA_GROUP:
                grounded = _grounded(facts, "authorization_blocker", text) or _grounded(
                    facts, "restricted_to_countries", text
                )
                if grounded:
                    return _decision("nonfit", "Remote role restricted to US/Canada residents only.")
                return _decision(
                    "uncertain",
                    "Remote role may be US/Canada-only, but the restriction is not clearly stated (unverified) — review.",
                    tz,
                )
            if countries:
                return _decision(
                    "uncertain",
                    "Remote role restricted to " + ", ".join(countries) + " — verify eligibility.",
                    tz,
                )
            return _decision("uncertain", "Remote role with an unclear residency restriction — verify eligibility.", tz)
        return _decision("fit", f"Fully remote role ({focus}).", tz)

    if facts.get("authorization_blocker") == "yes":
        return reject(
            "authorization_blocker",
            "Non-remote role with an explicit work-authorization blocker.",
            "A work-authorization blocker may apply, but it is unclear (unverified) — review.",
        )
    # The posting must EXPLICITLY state remote work, or offer relocation/visa
    # sponsorship. A grounded sponsorship offer accepts; a role that is silent on
    # all three is no longer auto-accepted (the former EU leniency is removed).
    if facts.get("offers_sponsorship") == "yes" and _grounded(facts, "offers_sponsorship", text):
        return _decision("fit", "On-site/hybrid role offering relocation/visa sponsorship.", tz)
    if facts.get("offers_sponsorship") == "yes":
        return _decision("uncertain", "Sponsorship is mentioned but unverified in the posting — review.", tz)
    if arrangement in ("onsite", "hybrid"):
        return reject(
            "work_arrangement",
            "On-site/hybrid role with no remote option or stated relocation/visa sponsorship.",
            "On-site/hybrid arrangement not clearly stated (unverified) — review.",
        )
    return _decision("uncertain", "Work arrangement and sponsorship are unclear — verify before applying.", tz)


_DEFAULT_CHECK_ORDER = (
    "language",
    "role_match",
    "excluded_industry",
    "excluded_platform_focus",
    "minimum_seniority",
    "local_office_attendance",
    "remote_location_residency",
    "nonremote_nonpermanent_employment",
    "remote_fact_residency",
    "nonremote_work_authorization",
    "nonremote_sponsorship",
    "nonremote_arrangement",
)

_EVALUATION_SEARCH_FIELDS = (
    "role_include_terms", "role_exclude_terms", "skill_include_groups",
    "location_exclude_terms", "relocation_regions", "max_age_days",
    "remote_allowed", "relocation_allowed", "search_terms",
    "query_locations", "results_per_query",
)
_EVALUATION_CANDIDATE_FIELDS = (
    "residency_countries", "work_authorization_countries",
)
_EVALUATION_POLICY_FIELDS = (
    "check_order", "require_english", "local_language_exempt",
    "allowed_languages", "excluded_industries", "excluded_platform_focuses",
    "rejected_seniority", "max_local_office_days",
    "allow_sponsorship_override", "preferred_working_hours",
)


def _json_value(value):
    """Return a stable JSON value from the immutable settings surface."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (tuple, list, frozenset, set)):
        return [_json_value(item) for item in value]
    if hasattr(value, "id"):
        return str(value.id)
    return str(value)


def evaluation_configuration_revision(search, candidate, policy):
    """Fingerprint eligibility settings without leaking CV identity/options.

    The allow-list is intentional: rendering limits, names, employer history,
    private placeholders, and any future CV-only candidate field cannot reopen
    prior job decisions.  The output is stable across process runs and does not
    depend on object identity or mutable module state.
    """
    payload = {
        "facts": "generic-facts-v1",
        "policy": "named-checks-v1",
        "search": {
            field: _json_value(_option(search, field, ()))
            for field in _EVALUATION_SEARCH_FIELDS
        },
        "candidate": {
            field: _json_value(_option(candidate, field, ()))
            for field in _EVALUATION_CANDIDATE_FIELDS
        },
        "checks": {
            field: _json_value(_option(policy, field, ()))
            for field in _EVALUATION_POLICY_FIELDS
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "evaluation-config-v1:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _option(value, name, default):
    return getattr(value, name, default) if value is not None else default


def _check_ids(policy):
    checks = _option(policy, "check_order", _DEFAULT_CHECK_ORDER)
    result = []
    for check in checks:
        name = getattr(check, "id", check)
        name = str(name).strip()
        if name:
            result.append(name)
    return tuple(result)


def _candidate_countries(candidate, name):
    return frozenset(
        str(country).strip().upper()
        for country in _option(candidate, name, ())
        if str(country).strip()
    )


def _advertised_locations(location):
    """Read country/region candidates from the shared location resolver.

    The import is local so the policy stays importable during a partial upgrade;
    a missing resolver means an unclassified location, which is reviewable rather
    than inventing an eligibility claim.
    """
    try:
        from .location.countries import advertised_locations
    except ImportError:
        return ()
    return advertised_locations(location)


def _candidate_is_local(job, residences):
    for target in _advertised_locations(job.location):
        country = str(getattr(target, "country", "")).upper()
        if country and country in residences:
            return True
    return False


def _candidate_is_explicitly_local(job, residences, work_authorization):
    """True only when residence and authorization cover the same country."""
    for target in _advertised_locations(job.location):
        country = str(getattr(target, "country", "")).upper()
        if country and country in residences and country in work_authorization:
            return True
    return False


def _candidate_can_reside_in(targets, residences):
    if not targets:
        return None
    try:
        from .location.countries import EU_COUNTRY_CODES
    except ImportError:
        EU_COUNTRY_CODES = frozenset()
    concrete_target = False
    unknown_target = False
    for target in targets:
        country = str(getattr(target, "country", "")).upper()
        region = str(getattr(target, "region", "")).upper()
        if country:
            concrete_target = True
            if country in residences:
                return True
            # A country carrying a convenient region label (Germany is in the
            # EU) remains a country-specific restriction.  Do not let a
            # French resident satisfy a Germany-only posting through EU.
            continue
        if region:
            concrete_target = True
            if region == "EU" and residences.intersection(EU_COUNTRY_CODES):
                return True
            continue
        unknown_target = True
    # An unknown alternative may be eligible even when a known alternative is
    # not.  Preserve it as a reviewable uncertainty instead of silently
    # rejecting or treating it as unrestricted.
    if unknown_target:
        return None
    return False if concrete_target else None


_WORLDWIDE_LOCATION_RE = re.compile(
    r"(?<![a-z0-9])(?:worldwide|global(?:ly)?|work from anywhere|anywhere)(?![a-z0-9])"
)
_BROAD_UNKNOWN_LOCATION_RE = re.compile(
    r"(?<![a-z0-9])(?:emea|europe|eea)(?![a-z0-9])"
)
_REMOTE_ONLY_LOCATION_RE = re.compile(
    r"^\s*(?:fully\s+)?remote(?:[-–—,:]?\s*(?:first|role|position))?\s*$"
)
_REMOTE_EXCLUSION_RE = re.compile(r"\b(?:except|excluding|but\s+not|not\s+in)\b")
_REMOTE_QUALIFIED_ANYWHERE_RE = re.compile(
    r"\b(?:anywhere|worldwide|global(?:ly)?|work\s+from\s+anywhere)\s+in\s+(.+)$", re.IGNORECASE
)
_REMOTE_QUALIFIED_ONLY_RE = re.compile(
    r"\b(?:anywhere|worldwide|global(?:ly)?)\s*[-–—,:]?\s*(.+?)\s+only\s*$", re.IGNORECASE
)


def _remote_location_scope(location):
    """Classify the advertised remote location without inventing residence.

    The geographic resolver deliberately represents both unknown text and
    worldwide as blank targets.  Policy needs the distinction: Worldwide is
    unrestricted, while EMEA and an unrecognised alternative require review.
    """
    raw = str(location or "").strip()
    value = raw.lower()
    if _REMOTE_EXCLUSION_RE.search(value):
        return "ambiguous", ()
    # "Anywhere in Germany" and "Global - US only" look worldwide at a
    # glance, but each narrows eligibility.  Resolve the qualifier rather than
    # letting the resolver's intentionally blank worldwide target erase it.
    match = _REMOTE_QUALIFIED_ANYWHERE_RE.search(raw) or _REMOTE_QUALIFIED_ONLY_RE.search(raw)
    if match:
        qualifier = match.group(1).strip(" .,-–—:")
        if len(qualifier) == 2:
            qualifier = qualifier.upper()
        targets = _advertised_locations(qualifier)
        if any(str(getattr(target, "country", "")) or str(getattr(target, "region", "")) for target in targets):
            return "restricted", targets
        return "ambiguous", ()
    if _WORLDWIDE_LOCATION_RE.search(value) or _REMOTE_ONLY_LOCATION_RE.match(value):
        return "unrestricted", ()
    if _BROAD_UNKNOWN_LOCATION_RE.search(value):
        return "unknown", ()
    return "restricted", ()


def _fact_location_targets(values):
    """Resolve fact country codes/names using the same shared country parser."""
    targets = []
    for value in values or ():
        if not str(value).strip():
            continue
        resolved = _advertised_locations(str(value))
        if not resolved:
            targets.append(object())
        else:
            targets.extend(resolved)
    return tuple(targets)


def _generic_timezone_note(facts, policy):
    if facts.get("requires_us_hours") != "yes":
        return None
    preferred = tuple(_option(policy, "preferred_working_hours", ()))
    if preferred:
        return "Role requires US working hours — review against preferred working hours: {}.".format(
            ", ".join(str(value) for value in preferred)
        )
    return "Role requires US working hours — review the timezone mismatch."


def _generic_reject(facts, text, field, reason, unverified_reason, timezone_note):
    if _grounded(facts, field, text):
        return _decision("nonfit", reason, timezone_note)
    return _decision("uncertain", unverified_reason, timezone_note)


def _generic_policy(facts, job, candidate, policy, search=None):
    """Apply only named, ordered checks over generic candidate settings.

    Check IDs select built-in handlers; configuration cannot execute predicates or
    templates.  Every fact-derived rejection goes through ``_generic_reject`` so
    an ungrounded model claim remains a review item.
    """
    job = coerce_job(job)
    text = job.description
    residences = _candidate_countries(candidate, "residency_countries")
    work_authorization = _candidate_countries(candidate, "work_authorization_countries")
    local = _candidate_is_local(job, residences)
    tz = _generic_timezone_note(facts, policy)
    arrangement = facts.get("work_arrangement")
    sponsorship_grounded = (
        facts.get("offers_sponsorship") == "yes"
        and _grounded(facts, "offers_sponsorship", text)
    )
    sponsorship_override = bool(_option(policy, "allow_sponsorship_override", True))

    for check_id in _check_ids(policy):
        if check_id == "language":
            allowed = frozenset(
                str(value).strip().lower()
                for value in _option(policy, "allowed_languages", ())
                if str(value).strip()
            )
            if not allowed and _option(policy, "require_english", True):
                allowed = frozenset(("english",))
            if not allowed:
                continue
            if local and _option(policy, "local_language_exempt", True):
                continue
            language = str(facts.get("description_language", "")).strip().lower()
            if language and language not in allowed:
                return _generic_reject(
                    facts, text, "description_language", "Job description is not written in an allowed language.",
                    "Job description may not be written in an allowed language, but this is unverified — review.", tz,
                )
            if not language:
                # A deterministic English heuristic may establish English;
                # no equivalent inference is safe for the other configured
                # languages.  A missing extracted fact therefore remains
                # reviewable for Japanese, Hebrew, etc.
                if "english" in allowed and is_probably_english(text):
                    continue
                return _decision("uncertain", "Job description language is unclear — review.", tz)

        elif check_id == "role_match":
            required_groups = tuple(getattr(search, "skill_include_groups", ()) or ())
            configured_roles = tuple(getattr(search, "role_include_terms", ()) or ())
            if facts.get("role_match") == "no":
                return _generic_reject(
                    facts, text, "role_match", "Role does not match the configured target roles.",
                    "Role may not match the configured target roles, but this is unverified — review.", tz,
                )
            if configured_roles and facts.get("role_match") != "yes":
                return _decision("uncertain", "Configured role match is unclear — review.", tz)
            matched_skills = frozenset(str(value).strip().lower() for value in facts.get("matched_required_skills") or () if str(value).strip())
            for group in required_groups:
                normalized = frozenset(str(value).strip().lower() for value in group if str(value).strip())
                if normalized and not matched_skills.intersection(normalized):
                    return _decision("uncertain", "Configured required-skill match is unclear — review.", tz)

        elif check_id == "excluded_industry":
            excluded = frozenset(str(value).strip().lower() for value in _option(policy, "excluded_industries", ()) if str(value).strip())
            industries = frozenset(str(value).strip().lower() for value in facts.get("industries") or () if str(value).strip())
            if industries.intersection(excluded):
                return _generic_reject(
                    facts, text, "industries", "Posting belongs to an excluded industry.",
                    "Posting may belong to an excluded industry, but this is unverified — review.", tz,
                )
            # ``industry_crypto_web3`` is retained for source compatibility;
            # "crypto_web3" is its reusable configuration value.
            if facts.get("industry_crypto_web3") == "yes" and ({"crypto_web3", "crypto", "web3"} & excluded):
                return _generic_reject(
                    facts, text, "industry_crypto_web3", "Posting belongs to an excluded industry.",
                    "Posting may belong to an excluded industry, but this is unverified — review.", tz,
                )

        elif check_id == "excluded_platform_focus":
            excluded = frozenset(str(value).strip().lower() for value in _option(policy, "excluded_platform_focuses", ()) if str(value).strip())
            if facts.get("platform_focus") in excluded:
                return _generic_reject(
                    facts, text, "platform_focus", "Primary platform focus is excluded.",
                    "Primary platform focus may be excluded, but this is unverified — review.", tz,
                )

        elif check_id == "minimum_seniority":
            rejected = frozenset(str(value).strip().lower() for value in _option(policy, "rejected_seniority", ()) if str(value).strip())
            if facts.get("seniority") in rejected:
                return _generic_reject(
                    facts, text, "seniority", "Role seniority is excluded.",
                    "Role seniority may be excluded, but this is unverified — review.", tz,
                )

        elif check_id == "local_office_attendance":
            maximum = max(0, min(7, int(_option(policy, "max_local_office_days", 5))))
            office_days = facts.get("office_days_per_week", "unknown")
            if local and office_days != "unknown" and int(office_days) > maximum:
                return _generic_reject(
                    facts, text, "office_days_per_week", "Local role exceeds the permitted office attendance.",
                    "Local role may exceed the permitted office attendance, but this is unverified — review.", tz,
                )
            if local and maximum < 4 and arrangement in ("hybrid", "onsite") and facts.get("office_days_4plus") == "yes":
                return _generic_reject(
                    facts, text, "office_days_4plus", "Local role exceeds the permitted office attendance.",
                    "Local role may exceed the permitted office attendance, but this is unverified — review.", tz,
                )

        elif check_id == "remote_location_residency" and job.is_remote:
            scope, qualified_targets = _remote_location_scope(job.location)
            if scope == "unrestricted":
                continue
            targets = qualified_targets or _advertised_locations(job.location)
            eligible = _candidate_can_reside_in(targets, residences)
            # A known advertised target the candidate can reside in is enough
            # even if a second broad target (Germany / EMEA) is unresolved.
            if eligible is True:
                continue
            if scope in ("unknown", "ambiguous"):
                return _decision("uncertain", "Remote location eligibility is unclear — verify residency requirements.", tz)
            if eligible is False:
                if sponsorship_override and sponsorship_grounded:
                    continue
                return _decision("nonfit", "Remote role is restricted to a location where the candidate does not reside.", tz)
            if eligible is None:
                return _decision("uncertain", "Remote location eligibility is unclear — verify residency requirements.", tz)

        elif check_id == "nonremote_nonpermanent_employment":
            employment = facts.get("employment_type")
            if employment in ("contract", "freelance", "part_time") and arrangement != "remote":
                # The legacy policy's local exception covers a candidate who
                # can both reside and work in this exact advertised country.
                # It is deliberately narrower than a residence-only shortcut.
                if _candidate_is_explicitly_local(job, residences, work_authorization):
                    continue
                if arrangement in ("onsite", "hybrid"):
                    return _generic_reject(
                        facts, text, "work_arrangement", "Non-remote non-permanent role is excluded.",
                        "Non-remote arrangement may make this non-permanent role ineligible, but this is unverified — review.", tz,
                    )
                return _decision("uncertain", "Non-permanent role with an unclear work arrangement — verify.", tz)

        elif check_id == "remote_fact_residency" and arrangement == "remote" and facts.get("remote_geo_scope") == "restricted":
            targets = _fact_location_targets(facts.get("restricted_to_countries"))
            eligible = _candidate_can_reside_in(targets, residences)
            if eligible is False:
                if sponsorship_override and sponsorship_grounded:
                    continue
                grounded = _grounded(facts, "restricted_to_countries", text) or _grounded(facts, "authorization_blocker", text)
                if grounded:
                    return _decision("nonfit", "Remote role has a residency restriction the candidate does not meet.", tz)
                return _decision("uncertain", "Remote residency restriction may make the candidate ineligible, but this is unverified — review.", tz)
            if eligible is None:
                return _decision("uncertain", "Remote role has an unclear residency restriction — verify eligibility.", tz)

        elif check_id == "nonremote_work_authorization":
            # The identifier is retained for compatibility with stored config,
            # but an explicit authorization requirement applies to every
            # arrangement.  Remote residency does not grant permission to work
            # in the advertised country.
            fact_targets = _fact_location_targets(facts.get("restricted_to_countries"))
            targets = fact_targets or _advertised_locations(job.location)
            can_work = _candidate_can_reside_in(targets, work_authorization)
            if facts.get("authorization_blocker") == "yes":
                if can_work is True:
                    return _decision("fit", "Candidate satisfies the stated work-authorization location.", tz)
                if can_work is None:
                    return _decision(
                        "uncertain",
                        "A work-authorization requirement is stated, but its location is unclear — review.",
                        tz,
                    )
                return _generic_reject(
                    facts, text, "authorization_blocker", "Role has a work-authorization requirement the candidate does not meet.",
                    "A work-authorization requirement may apply, but this is unverified — review.", tz,
                )

        elif check_id == "nonremote_sponsorship" and arrangement != "remote":
            if sponsorship_grounded:
                return _decision("fit", "Non-remote role offers relocation or visa sponsorship.", tz)
            if facts.get("offers_sponsorship") == "yes":
                return _decision("uncertain", "Sponsorship is mentioned but unverified in the posting — review.", tz)

        elif check_id == "nonremote_arrangement" and arrangement in ("onsite", "hybrid"):
            targets = _advertised_locations(job.location)
            if _candidate_can_reside_in(targets, work_authorization) is True:
                return _decision("fit", "Candidate is eligible for the advertised non-remote location.", tz)
            return _generic_reject(
                facts, text, "work_arrangement", "On-site or hybrid role has no eligible location or sponsorship.",
                "On-site or hybrid arrangement may be ineligible, but this is unverified — review.", tz,
            )

    if arrangement == "remote":
        return _decision("fit", "Remote role meets configured eligibility.", tz)
    # An explicitly local candidate who also has authorization for that exact
    # advertised country can proceed when the posting leaves its arrangement
    # unstated.  Residence by itself never establishes this exception.
    if _candidate_is_explicitly_local(job, residences, work_authorization):
        return _decision("fit", "Candidate is eligible for the advertised local location.", tz)
    return _decision("uncertain", "Work arrangement and eligibility are unclear — verify before applying.", tz)


def apply_policy(facts, job, candidate=None, policy=None, search=None) -> dict:
    """Return a deterministic fit decision.

    Omitting candidate and policy is the temporary legacy compatibility API.
    Supplying either selects the reusable, named-check policy and never consults
    this repository's Israel/iOS constants.
    """
    if candidate is None and policy is None:
        return _apply_legacy_policy(facts, job)
    return _generic_policy(facts, job, candidate, policy, search=search)
