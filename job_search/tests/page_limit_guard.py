#!/usr/bin/env python3
"""Integration guard for verified one-, two-, and three-page limits.

The established ``one_page_guard`` keeps its historical density/shrink check.
This guard isolates the generalized policy with a fictional, self-contained
LaTeX document: explicit ``\\newpage`` commands make the expected physical
page count unambiguous and avoid a real candidate's content or any LLM call.

Run the standard synthetic matrix with::

    python -m job_search.tests.page_limit_guard

For a later fixture, pass all three expectations explicitly::

    python -m job_search.tests.page_limit_guard --fixture fixture.tex \\
        --expected-pages 2 --max-pages 2
"""
import argparse
import sys

from ..latex.compile import LatexCompiler


def fictional_tex(pages):
    """Return a minimal document with exactly ``pages`` intentional pages."""
    if isinstance(pages, bool) or not isinstance(pages, int) or pages < 1:
        raise ValueError("pages must be a positive integer")
    body = []
    for page in range(1, pages + 1):
        body.append("Fictional CV test content, page {}.".format(page))
        if page != pages:
            body.append("\\newpage")
    return "\\documentclass{{article}}\n\\begin{{document}}\n{}\n\\end{{document}}\n".format(
        "\n".join(body)
    )


def verify(tex, expected_pages, max_pages):
    """Compile once without repair/shrink and verify exact count plus policy."""
    compiler = LatexCompiler()
    result = compiler.compile_base(tex, max_pages=max_pages)
    expected_ok = 1 <= expected_pages <= max_pages
    if result.page_count != expected_pages:
        raise AssertionError(
            "expected {} physical pages, got {} ({})".format(
                expected_pages, result.page_count, result.error_excerpt
            )
        )
    if result.ok != expected_ok:
        raise AssertionError(
            "{} pages with a {}-page limit: expected ok={}, got ok={} ({})".format(
                expected_pages, max_pages, expected_ok, result.ok, result.error_excerpt
            )
        )
    return result


def _parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", help="optional LaTeX fixture for a focused limit check")
    parser.add_argument("--expected-pages", type=int)
    parser.add_argument("--max-pages", type=int)
    args = parser.parse_args(argv)
    if args.fixture and (args.expected_pages is None or args.max_pages is None):
        parser.error("--fixture requires --expected-pages and --max-pages")
    if not args.fixture and (args.expected_pages is not None or args.max_pages is not None):
        parser.error("--expected-pages/--max-pages require --fixture")
    return args


def main(argv=None):
    args = _parse_args(argv)
    try:
        if args.fixture:
            with open(args.fixture, encoding="utf-8") as handle:
                tex = handle.read()
            verify(tex, args.expected_pages, args.max_pages)
            print("PASS: fixture has {} pages within its {}-page limit.".format(
                args.expected_pages, args.max_pages
            ))
            return 0

        for pages, max_pages, expected_ok in (
            (1, 1, True),
            (2, 2, True),
            (3, 3, True),
            (2, 1, False),
            (3, 2, False),
        ):
            result = verify(fictional_tex(pages), pages, max_pages)
            state = "accepted" if expected_ok else "rejected"
            print("PASS: {} pages {} by a {}-page limit.".format(
                pages, state, max_pages
            ))
            assert result.ok is expected_ok
    except Exception as exc:
        print("FAIL: {}".format(exc), file=sys.stderr)
        return 1
    print("ALL PAGE-LIMIT GUARD CHECKS PASSED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
