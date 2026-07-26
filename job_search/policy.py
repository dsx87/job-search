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


def apply_policy(facts, job) -> dict:
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
