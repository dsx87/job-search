"""TDD for the telegra.ph node renderer."""
import datetime
import json

from job_search.digest import telegraph as tg
from job_search.digest.fixtures import oversized_context, sample_context, sample_fit
from job_search.models import Region

ALLOWED_TAGS = {
    "a", "aside", "b", "blockquote", "br", "code", "em", "figcaption", "figure",
    "h3", "h4", "hr", "i", "iframe", "img", "li", "ol", "p", "pre", "s",
    "strong", "u", "ul", "video",
}


def _walk(nodes):
    """Yield every node dict in the tree."""
    for node in nodes:
        if isinstance(node, dict):
            yield node
            for child in _walk(node.get("children") or []):
                yield child


def _all_text(nodes):
    out = []
    for node in nodes:
        if isinstance(node, dict):
            out.append(_all_text(node.get("children") or []))
        else:
            out.append(str(node))
    return " ".join(out)


def test_every_tag_is_one_telegraph_allows():
    nodes = tg.render_digest_nodes(sample_context())
    tags = {node["tag"] for node in _walk(nodes)}
    assert tags <= ALLOWED_TAGS, "forbidden tags: {}".format(tags - ALLOWED_TAGS)


def test_no_h1_or_h2_and_the_page_leads_with_h3():
    nodes = tg.render_digest_nodes(sample_context())
    assert nodes[0]["tag"] == "h3"
    assert "Job Search Digest" in _all_text([nodes[0]])
    assert "2026-08-01" in _all_text([nodes[0]])


def test_text_is_not_html_escaped():
    ctx = sample_context(fits=[sample_fit(title="R&D <Lead>", company="Delta & Sons")])
    text = _all_text(tg.render_digest_nodes(ctx))
    assert "R&D <Lead>" in text
    assert "&amp;" not in text
    assert "&lt;" not in text


def test_fit_shows_title_company_summary_and_reason():
    entry = sample_fit()
    text = _all_text(tg.render_digest_nodes(sample_context(fits=[entry])))
    assert "Senior iOS Engineer" in text
    assert "Acme" in text
    assert entry.summary in text
    assert entry.evaluation["reason"] in text


def test_http_url_becomes_a_link_and_other_urls_do_not():
    linked = tg.render_digest_nodes(sample_context(fits=[sample_fit(url="https://jobs.example.com/1")]))
    hrefs = [n["attrs"]["href"] for n in _walk(linked) if n["tag"] == "a"]
    assert "https://jobs.example.com/1" in hrefs

    unlinked = tg.render_digest_nodes(
        sample_context(fits=[sample_fit(url="mailto:jobs@example.com")], review=[], deferred=[])
    )
    assert [n for n in _walk(unlinked) if n["tag"] == "a"] == []
    assert "Senior iOS Engineer" in _all_text(unlinked)  # the card survives, just unlinked


def test_full_descriptions_are_not_on_the_page():
    ctx = sample_context(fits=[sample_fit(description="SECRET_DESCRIPTION_MARKER " * 20)])
    assert "SECRET_DESCRIPTION_MARKER" not in _all_text(tg.render_digest_nodes(ctx))


def test_sections_become_h3_with_subsection_h4():
    nodes = tg.render_digest_nodes(sample_context(grouped=True))
    h3 = [_all_text([n]) for n in nodes if n["tag"] == "h3"]
    h4 = [_all_text([n]) for n in _walk(nodes) if n["tag"] == "h4"]
    assert any("Fits" in t for t in h3)
    assert any("Remote EU" in t for t in h4)


def test_ungrouped_run_has_no_h4_but_still_lists_the_fits():
    nodes = tg.render_digest_nodes(sample_context(grouped=False))
    assert [n for n in _walk(nodes) if n["tag"] == "h4"] == []
    assert "Senior iOS Engineer" in _all_text(nodes)


def test_review_and_deferred_sections_render():
    text = _all_text(tg.render_digest_nodes(sample_context()))
    assert "Staff Engineer" in text
    assert "Mobile Engineer" in text


def test_deferred_entries_are_a_list():
    nodes = tg.render_digest_nodes(sample_context())
    assert any(n["tag"] == "ul" for n in _walk(nodes))


def test_source_and_sections_warnings_appear_near_the_top():
    ctx = sample_context(source_warning="linkedin timed out", sections_error="bad predicate")
    nodes = tg.render_digest_nodes(ctx)
    head = _all_text(nodes[:4])
    assert "linkedin timed out" in head
    assert "bad predicate" in head


def test_a_raising_predicate_is_reported_not_fatal():
    def boom(_entry):
        raise ValueError("nope")

    from job_search.digest.sections import Section

    ctx = sample_context(sections=(Section("Broken", "", ("fits",), boom),))
    text = _all_text(tg.render_digest_nodes(ctx))
    assert "Senior iOS Engineer" in text     # every card survives
    assert "Broken" in text and "ValueError" in text


def test_index_url_becomes_a_back_link_when_given():
    nodes = tg.render_digest_nodes(sample_context(), index_url="https://telegra.ph/Index-08-01")
    hrefs = [n["attrs"]["href"] for n in _walk(nodes) if n["tag"] == "a"]
    assert "https://telegra.ph/Index-08-01" in hrefs

    plain = tg.render_digest_nodes(sample_context())
    assert "All digests" not in _all_text(plain)


def test_usage_summary_is_on_the_page():
    ctx = sample_context()
    assert ctx.usage_summary in _all_text(tg.render_digest_nodes(ctx))


def test_oversized_run_is_trimmed_under_the_limit_and_says_so():
    nodes = tg.render_digest_nodes(oversized_context())
    assert tg.content_size(nodes) <= tg.CONTENT_LIMIT_BYTES
    assert "more" in _all_text(nodes)


def test_normal_run_is_nowhere_near_the_limit_and_is_untrimmed():
    nodes = tg.render_digest_nodes(sample_context())
    assert tg.content_size(nodes) < 20_000
    assert "and 0 more" not in _all_text(nodes)


def test_content_size_measures_utf8_bytes_of_the_json():
    nodes = [{"tag": "p", "children": ["é"]}]
    assert tg.content_size(nodes) == len(json.dumps(nodes, ensure_ascii=False).encode("utf-8"))


def test_page_title_is_short_dated_and_carries_a_random_token():
    title = tg.digest_page_title(datetime.date(2026, 8, 1))
    assert title.startswith("Job Digest 2026-08-01 ")
    token = title.rsplit(" ", 1)[-1]
    assert len(token) == 8 and all(c in "0123456789abcdef" for c in token)
    # Short enough that Telegraph's slug truncation cannot drop the token.
    assert len(title) < 64
    assert tg.digest_page_title(datetime.date(2026, 8, 1)) != title  # random each call


def test_page_title_token_can_be_pinned_for_tests():
    assert tg.digest_page_title(datetime.date(2026, 8, 1), token="deadbeef") == (
        "Job Digest 2026-08-01 deadbeef"
    )


def test_region_and_remote_show_up_in_the_meta_line():
    ctx = sample_context(fits=[sample_fit(is_remote=True, region=Region.EU)], review=[], deferred=[])
    text = _all_text(tg.render_digest_nodes(ctx))
    assert "Remote" in text
    assert "Europe" in text


def test_fit_without_evaluation_still_renders():
    ctx = sample_context(fits=[sample_fit(evaluation=False)], review=[], deferred=[])
    text = _all_text(tg.render_digest_nodes(ctx))
    assert "Senior iOS Engineer" in text
    assert "Previously matched." in text


def test_fits_over_budget_trims_review_and_deferred_to_zero_but_keeps_headings():
    """Fits alone blow the 60 KB budget -- trimming bottoms out at keep=0.

    oversized_context() only forces the ladder down to keep=3 (its bulk is in
    review, which the ladder trims), so it never exercises the keep=0 rung.
    This context puts the bulk in fits instead, which are never trimmed, so
    every rung leaves review and deferred trimmed to nothing while fits alone
    keep the run over budget.
    """
    fits = [
        sample_fit(
            title="Fit Role {}".format(index), company="Company {}".format(index),
            url="https://jobs.example.com/fit-{}".format(index),
            summary="Summary sentence. " * 200,
            reason="Reason sentence. " * 200,
        )
        for index in range(12)
    ]
    ctx = sample_context(grouped=False, fits=fits)
    nodes = tg.render_digest_nodes(ctx)

    # No empty <ul> anywhere -- when a list trims to nothing, its <ul> must
    # not be emitted at all, not even as an empty children array.
    assert not any(n["tag"] == "ul" and not n.get("children") for n in _walk(nodes))

    # The "... and N more" lines exist (review and deferred were trimmed to
    # zero) and each has its section heading above it, not orphaned.
    review_heading = next(
        i for i, n in enumerate(nodes)
        if n["tag"] == "h3" and "Needs review" in _all_text([n])
    )
    review_more = next(i for i, n in enumerate(nodes) if "more to review" in _all_text([n]))
    assert review_heading < review_more

    deferred_heading = next(
        i for i, n in enumerate(nodes)
        if n["tag"] == "h3" and "Deferred" in _all_text([n])
    )
    deferred_more = next(i for i, n in enumerate(nodes) if "more deferred" in _all_text([n]))
    assert deferred_heading < deferred_more

    # Headings show the true (pre-trim) totals, matching the top counts line.
    text = _all_text(nodes)
    assert "Needs review ({})".format(len(ctx.review)) in text
    assert "Deferred ({})".format(len(ctx.deferred)) in text


def test_partially_trimmed_review_heading_shows_true_total_not_trimmed_count():
    """oversized_context() trims review down to 3 survivors; the heading
    must still report the true total of 40, matching the top counts line --
    not the 3 cards that actually render."""
    ctx = oversized_context()
    nodes = tg.render_digest_nodes(ctx)
    text = _all_text(nodes)
    assert "Needs review ({})".format(len(ctx.review)) in text
    assert "Needs review (3)" not in text


# ── index page ────────────────────────────────────────────────────────────────

def test_index_lists_pages_newest_first_as_links():
    pages = [
        {"title": "Job Digest 2026-08-01 deadbeef", "url": "https://telegra.ph/a"},
        {"title": "Job Digest 2026-07-31 cafebabe", "url": "https://telegra.ph/b"},
    ]
    nodes = tg.render_index_nodes(pages)
    hrefs = [n["attrs"]["href"] for n in _walk(nodes) if n["tag"] == "a"]
    assert hrefs == ["https://telegra.ph/a", "https://telegra.ph/b"]


def test_index_link_labels_hide_the_random_token():
    nodes = tg.render_index_nodes([
        {"title": "Job Digest 2026-08-01 deadbeef", "url": "https://telegra.ph/a"},
    ])
    text = _all_text(nodes)
    assert "Job Digest 2026-08-01" in text
    assert "deadbeef" not in text


def test_index_tags_are_allowed_and_it_leads_with_h3():
    nodes = tg.render_index_nodes([
        {"title": "Job Digest 2026-08-01 deadbeef", "url": "https://telegra.ph/a"},
    ])
    assert {n["tag"] for n in _walk(nodes)} <= ALLOWED_TAGS
    assert nodes[0]["tag"] == "h3"
    assert tg.INDEX_TITLE in _all_text([nodes[0]])


def test_empty_index_says_so_rather_than_rendering_an_empty_list():
    nodes = tg.render_index_nodes([])
    assert [n for n in _walk(nodes) if n["tag"] == "ul"] == []
    assert "No digests" in _all_text(nodes)


def test_index_is_capped_at_the_page_list_limit():
    pages = [{"title": "Job Digest 2026-08-01 deadbeef",
              "url": "https://telegra.ph/u{}".format(i)}
             for i in range(500)]
    hrefs = [n["attrs"]["href"] for n in _walk(tg.render_index_nodes(pages)) if n["tag"] == "a"]
    assert len(hrefs) == 200
