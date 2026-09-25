"""DefAPI Jev decisions and approved categorical findings."""
import json
import time
import urllib.error
import urllib.request

from .models import coerce_job

JEV_URL = "https://api.defapi.org/api/v1/decisions"
JEV_MODEL = "typesafe/jev-1.13"
CHOICES = ("fit", "nonfit", "review")
# The approved Jev contract lives in one catalog: request choices and public
# explanations cannot drift apart. Changes require a fingerprint-version bump.
APPROVED_CHECKS = {'language': {'label': 'Language',
              'choices': {'pass': {'question': 'The posting language meets the supplied candidate and '
                                               'policy requirements.',
                                   'description': 'language allowed by policy',
                                   'color': 'green'},
                          'reject': {'question': 'The posting language clearly violates a supplied '
                                                 'requirement.',
                                     'description': 'language violates policy',
                                     'color': 'red'},
                          'unknown': {'question': 'The posting language or location cannot be '
                                                  'determined.',
                                      'description': 'language unclear',
                                      'color': 'unclear'}}},
 'location': {'label': 'Location and work',
              'choices': {'local': {'question': 'A local job meets the supplied office attendance '
                                                'limit.',
                                    'description': 'local arrangement allowed',
                                    'color': 'green'},
                          'remote': {'question': 'Remote work is explicitly available from a '
                                                 'candidate-eligible location.',
                                     'description': 'remote location allowed',
                                     'color': 'green'},
                          'relocation': {'question': 'Relocation or visa support makes the location '
                                                     'eligible.',
                                         'description': 'relocation supported',
                                         'color': 'green'},
                          'restricted': {'question': 'Remote location restrictions exclude the '
                                                     'candidate without a qualifying alternative.',
                                         'description': 'remote location excluded',
                                         'color': 'red'},
                          'office': {'question': 'A local job exceeds the supplied office attendance '
                                                 'limit.',
                                     'description': 'local office limit exceeded',
                                     'color': 'red'},
                          'arrangement': {'question': 'The posting lacks a qualifying remote, local, or '
                                                      'relocation arrangement.',
                                          'description': 'no qualifying work arrangement',
                                          'color': 'red'},
                          'unknown': {'question': 'A decisive location or work arrangement fact is '
                                                  'missing or ambiguous.',
                                      'description': 'location or work arrangement unclear',
                                      'color': 'unclear'}}},
 'employment': {'label': 'Employment',
                'choices': {'pass': {'question': 'The employment type meets the supplied requirements.',
                                     'description': 'employment type allowed',
                                     'color': 'green'},
                            'reject': {'question': 'The employment type clearly violates a supplied '
                                                   'requirement.',
                                       'description': 'employment type excluded',
                                       'color': 'red'},
                            'unknown': {'question': 'The employment type or arrangement cannot be '
                                                    'determined.',
                                        'description': 'employment type unclear',
                                        'color': 'unclear'}}},
 'seniority': {'label': 'Seniority',
               'choices': {'pass': {'question': 'The stated seniority is allowed by the supplied '
                                                'policy; silence does not imply junior.',
                                    'description': 'seniority allowed',
                                    'color': 'green'},
                           'reject': {'question': 'The stated seniority is excluded by the supplied '
                                                  'policy.',
                                      'description': 'seniority excluded',
                                      'color': 'red'},
                           'unknown': {'question': 'The posting gives conflicting seniority signals.',
                                       'description': 'seniority signals conflict',
                                       'color': 'unclear'}}},
 'technology_stack': {'label': 'Technology stack',
                      'choices': {'pass': {'question': 'The main technology stack meets the supplied '
                                                       'criteria and policy.',
                                           'description': 'main stack allowed',
                                           'color': 'green'},
                                  'reject': {'question': 'The main technology stack clearly violates '
                                                         'the supplied criteria or policy.',
                                             'description': 'main stack excluded',
                                             'color': 'red'},
                                  'unknown': {'question': 'The main technology or platform focus is '
                                                          'unclear.',
                                              'description': 'main stack unclear',
                                              'color': 'unclear'}}},
 'industry': {'label': 'Industry',
              'choices': {'pass': {'question': 'The employer industry is allowed by the supplied '
                                               'policy.',
                                   'description': 'industry allowed',
                                   'color': 'green'},
                          'reject': {'question': 'The employer industry is excluded by the supplied '
                                                 'policy.',
                                     'description': 'industry excluded',
                                     'color': 'red'},
                          'unknown': {'question': "The employer's industry is unclear.",
                                      'description': 'industry unclear',
                                      'color': 'unclear'}}},
 'unknown_red_flag': {'label': 'Unknown red flag',
                      'choices': {'yes': {'question': 'A clear candidate requirement is violated for a '
                                                      'reason outside the six named checks.',
                                          'description': 'another clear rejection reason',
                                          'color': 'red'},
                                  'no': {'question': 'No additional clear rejection reason is stated '
                                                     'outside the six named checks.',
                                         'description': 'no other rejection identified',
                                         'color': 'green'},
                                  'unknown': {'question': 'An additional requirement might be violated, '
                                                          'but the posting is inconclusive.',
                                              'description': 'another requirement may be unmet',
                                              'color': 'unclear'}}}}

SIGNAL_QUESTIONS = {name: {choice: detail["question"] for choice, detail in check["choices"].items()}
                    for name, check in APPROVED_CHECKS.items()}
SIGNAL_LABELS = {name: check["label"] for name, check in APPROVED_CHECKS.items()}
SIGNAL_SUMMARIES = {name: {choice: detail["description"] for choice, detail in check["choices"].items()}
                    for name, check in APPROVED_CHECKS.items()}
REJECTION_SIGNALS = {name: {choice for choice, detail in check["choices"].items()
                            if detail["color"] == "red"} for name, check in APPROVED_CHECKS.items()}


def jev_payload(job, criteria, candidate, policy, search, model=JEV_MODEL):
    return {
        "model": model,
        "state": {
            "job": {key: job[key] for key in ("title", "company", "location", "is_remote", "description")},
            "candidate_criteria": criteria,
            "candidate_settings": {
                "residency_countries": candidate.residency_countries,
                "work_authorization_countries": candidate.work_authorization_countries,
            },
            "policy_settings": {key: getattr(policy, key) for key in (
                "check_order", "require_english", "local_language_exempt",
                "excluded_industries", "excluded_platform_focuses", "rejected_seniority",
                "max_local_office_days", "allow_sponsorship_override", "allowed_languages",
                "preferred_working_hours",
            )},
            "search_settings": {key: getattr(search, key) for key in (
                "role_include_terms", "role_exclude_terms", "skill_include_groups",
                "location_exclude_terms", "relocation_regions", "remote_allowed",
                "relocation_allowed", "search_terms",
            )},
        },
        "questions": {
            "fit": {
                "type": "choice",
                "instructions": "Apply the supplied candidate criteria and settings to this posting. Choose fit only when the role qualifies, nonfit when it violates a requirement, and review when a decisive fact is missing or ambiguous. Use only stated posting facts. Answer the other questions as separate checks of the same posting.",
                "criteria": {
                    "fit": "Meets the candidate's requirements and has no unresolved decisive restriction.",
                    "nonfit": "Clearly violates a candidate requirement or exclusion.",
                    "review": "Available posting facts do not decide whether it qualifies.",
                },
            },
            **{name: {
                "type": "choice",
                "instructions": "Classify this one candidate rule using the job title, location, remote flag, and description. Use only stated facts and the supplied criteria; choose unknown for ambiguity. Do not infer employer policy from silence.",
                "criteria": choices,
            } for name, choices in SIGNAL_QUESTIONS.items()},
        },
    }


def parse_jev_response(data, expected_questions=("fit",)):
    if not isinstance(data, dict) or not isinstance(data.get("model"), str) or not data["model"]:
        raise ValueError("Jev response lacks actual model")
    answers = data.get("answers")
    if not isinstance(answers, dict):
        raise ValueError("Jev response lacks answers")
    parsed = {}
    for name in expected_questions:
        answer = answers.get(name)
        if not isinstance(answer, dict) or answer.get("type") != "choice":
            raise ValueError(f"Jev response lacks {name} choice answer")
        choice, probabilities = answer.get("choice"), answer.get("probabilities")
        labels = CHOICES if name == "fit" else tuple(SIGNAL_QUESTIONS[name])
        if choice not in labels or not isinstance(probabilities, dict) or set(probabilities) != set(labels):
            raise ValueError(f"Jev response has invalid {name} choice or probability labels")
        if any(type(p) not in (int, float) or not 0 <= p <= 1 for p in probabilities.values()):
            raise ValueError(f"Jev response has invalid {name} probabilities")
        if abs(sum(probabilities.values()) - 1) > .02:
            raise ValueError(f"Jev {name} probabilities do not sum to one")
        parsed[name] = {"choice": choice, "probabilities": probabilities}
    return {"verdict": parsed["fit"]["choice"], "probabilities": parsed["fit"]["probabilities"],
            "signals": {name: answer for name, answer in parsed.items() if name != "fit"},
            "model": data["model"]}


def call_jev(payload, api_key, opener=urllib.request.urlopen, url=JEV_URL):
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
        method="POST",
    )
    for attempt in range(3):
        try:
            with opener(request, timeout=90) as response:
                raw = json.load(response)
                return {**parse_jev_response(raw, payload["questions"]), "raw": raw}
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 529) or attempt == 2:
                raise
            time.sleep(2 ** attempt)


def findings(result):
    """Approved labels and descriptions for the categorical Jev answers."""
    rows = []
    for name, answer in result["signals"].items():
        choice = answer["choice"]
        if name == "unknown_red_flag" and choice == "no":
            continue
        color = ("red" if choice in REJECTION_SIGNALS.get(name, ()) else
                 "unclear" if choice == "unknown" else "green")
        rows.append({"color": color, "label": SIGNAL_LABELS[name],
                     "description": SIGNAL_SUMMARIES[name][choice]})
    return rows


def relevant_findings(rows):
    """Keep every warning and the two most useful positive checks in digests."""
    priority = {"Location and work": 0, "Technology stack": 1,
                "Employment": 2, "Seniority": 3, "Language": 4, "Industry": 5}
    positives = sorted((row for row in rows if row["color"] == "green"),
                       key=lambda row: priority.get(row["label"], 99))[:2]
    return [row for row in rows if row["color"] != "green"] + positives


def evaluate_job(api_key, criteria, job, *, candidate, policy, search,
                 opener=urllib.request.urlopen, url=JEV_URL):
    """Return the pipeline's fit/review/nonfit shape from Jev's choices."""
    result = call_jev(jev_payload(coerce_job(job), criteria, candidate, policy, search),
                      api_key, opener=opener, url=url)
    flags = findings(result)
    named_red = any(flag["color"] == "red" and flag["label"] != "Unknown red flag"
                    for flag in flags)
    verdict = result["verdict"]
    if (verdict == "fit" and any(flag["color"] == "red" for flag in flags)) or (
        verdict == "nonfit" and not named_red
    ):
        verdict = "review"
    reason = {
        "fit": "Jev classified this posting as a fit.",
        "review": "Jev's answer or findings need review; check the posting.",
        "nonfit": "Jev identified a named rejection criterion.",
    }[verdict]
    return {"fit": verdict == "fit", "verdict": verdict, "reason": reason,
            "signals": {name: answer["choice"] for name, answer in result["signals"].items()},
            "findings": flags, "jev_model": result["model"]}
