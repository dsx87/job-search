"""TDD for loading user-authored digest sections from a Python file."""
import pathlib

from job_search.digest.section_config import load_sections

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _write(tmp_path, body, name="sections.py"):
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return str(path)


VALID = """
from job_search.digest.sections import Section, is_remote

SECTIONS = [
    Section("Remote", "\\U0001f30d", match=is_remote),
    Section("Everything else", "\\U0001f4cb"),
]
"""


def test_a_missing_path_is_not_an_error_because_sections_are_opt_in(tmp_path):
    sections, error = load_sections(str(tmp_path / "nope.py"))
    assert sections == ()
    assert error == ""


def test_an_empty_path_disables_sections():
    assert load_sections("") == ((), "")
    assert load_sections(None) == ((), "")


def test_a_valid_file_returns_its_sections_in_order(tmp_path):
    sections, error = load_sections(_write(tmp_path, VALID))
    assert error == ""
    assert [section.name for section in sections] == ["Remote", "Everything else"]


def test_a_syntax_error_is_reported_and_disables_grouping(tmp_path):
    sections, error = load_sections(_write(tmp_path, "SECTIONS = [\n"))
    assert sections == ()
    assert "SyntaxError" in error
    assert "ungrouped" in error


def test_an_import_time_exception_is_reported(tmp_path):
    sections, error = load_sections(_write(tmp_path, "raise RuntimeError('boom')\n"))
    assert sections == ()
    assert "boom" in error


def test_a_file_without_SECTIONS_is_reported(tmp_path):
    sections, error = load_sections(_write(tmp_path, "OTHER = []\n"))
    assert sections == ()
    assert "SECTIONS" in error


def test_an_empty_SECTIONS_list_is_simply_no_sections(tmp_path):
    sections, error = load_sections(_write(tmp_path, "SECTIONS = []\n"))
    assert sections == ()
    assert error == ""


def test_SECTIONS_that_is_not_a_list_is_reported(tmp_path):
    sections, error = load_sections(_write(tmp_path, "SECTIONS = 'remote'\n"))
    assert sections == ()
    assert "list of Section" in error


def test_a_non_Section_item_is_reported(tmp_path):
    body = "SECTIONS = ['remote']\n"
    sections, error = load_sections(_write(tmp_path, body))
    assert sections == ()
    assert "SECTIONS[0]" in error


def test_an_empty_name_is_reported(tmp_path):
    body = (
        "from job_search.digest.sections import Section\n"
        "SECTIONS = [Section('  ')]\n"
    )
    sections, error = load_sections(_write(tmp_path, body))
    assert sections == ()
    assert "empty name" in error


def test_duplicate_names_are_reported(tmp_path):
    body = (
        "from job_search.digest.sections import Section\n"
        "SECTIONS = [Section('Remote'), Section('remote')]\n"
    )
    sections, error = load_sections(_write(tmp_path, body))
    assert sections == ()
    assert "repeats" in error


def test_a_section_named_other_collides_with_the_catch_all_and_is_reported(tmp_path):
    # "Other" is what the implicit catch-all is called; a user section by that
    # name would render two identical-looking sub-headings.
    body = (
        "from job_search.digest.sections import Section\n"
        "SECTIONS = [Section('other')]\n"  # case-insensitive, like the dup check
    )
    sections, error = load_sections(_write(tmp_path, body))
    assert sections == ()
    assert "reserved" in error


def test_an_unknown_applies_to_value_is_reported(tmp_path):
    body = (
        "from job_search.digest.sections import Section\n"
        "SECTIONS = [Section('Remote', applies_to=('fits', 'deferred'))]\n"
    )
    sections, error = load_sections(_write(tmp_path, body))
    assert sections == ()
    assert "deferred" in error
    assert "fits, review" in error


def test_an_empty_applies_to_is_reported(tmp_path):
    body = (
        "from job_search.digest.sections import Section\n"
        "SECTIONS = [Section('Remote', applies_to=())]\n"
    )
    sections, error = load_sections(_write(tmp_path, body))
    assert sections == ()
    assert "applies_to" in error


def test_a_non_callable_match_is_reported(tmp_path):
    body = (
        "from job_search.digest.sections import Section\n"
        "SECTIONS = [Section('Remote', match='is_remote')]\n"
    )
    sections, error = load_sections(_write(tmp_path, body))
    assert sections == ()
    assert "not callable" in error


def test_the_smoke_check_reports_a_broken_predicate_but_keeps_the_sections(tmp_path):
    # `is_remot` is a typo. Without the smoke check this would only surface as a
    # render-time AttributeError; here it is named while the config is loaded.
    body = (
        "from job_search.digest.sections import Section\n"
        "SECTIONS = [Section('Remote', match=lambda e: e.job.is_remot)]\n"
    )
    sections, error = load_sections(_write(tmp_path, body))
    assert [section.name for section in sections] == ["Remote"]
    assert "Remote" in error
    assert "AttributeError" in error


def test_the_smoke_check_covers_entries_with_no_evaluation(tmp_path):
    # FitEntry.evaluation is Optional; a predicate that assumes it is a dict
    # would crash on a fit decided without one.
    body = (
        "from job_search.digest.sections import Section\n"
        "SECTIONS = [Section('Senior', match=lambda e: e.evaluation['facts'])]\n"
    )
    sections, error = load_sections(_write(tmp_path, body))
    assert [section.name for section in sections] == ["Senior"]
    assert "TypeError" in error


def test_a_non_iterable_applies_to_is_reported_instead_of_raising(tmp_path):
    body = (
        "from job_search.digest.sections import Section\n"
        "SECTIONS = [Section('Remote', applies_to=5)]\n"
    )
    sections, error = load_sections(_write(tmp_path, body))
    assert sections == ()
    assert "applies_to" in error


def test_a_broken_applies_to_iterator_does_not_escape_load_sections(tmp_path):
    # A predicate is one thing, but applies_to itself could be some exotic
    # iterable whose __iter__ raises. The outer guard in load_sections is the
    # last line of defense against that, not _validate alone.
    body = (
        "from job_search.digest.sections import Section\n"
        "\n"
        "class Explodes:\n"
        "    def __iter__(self):\n"
        "        raise ValueError('nope')\n"
        "\n"
        "SECTIONS = [Section('Remote', applies_to=Explodes())]\n"
    )
    sections, error = load_sections(_write(tmp_path, body))
    assert sections == ()
    assert error != ""


def test_a_sys_exit_in_the_config_is_reported_instead_of_killing_the_process(tmp_path):
    # sys.exit() raises SystemExit, not Exception; a bare `except Exception`
    # would let it fly straight past the never-raises contract.
    body = "import sys\nsys.exit(3)\n"
    sections, error = load_sections(_write(tmp_path, body))
    assert sections == ()
    assert "SystemExit" in error


def test_more_than_MAX_REPORTED_problems_notes_the_remainder(tmp_path):
    body = (
        "from job_search.digest.sections import Section\n"
        "SECTIONS = [Section(''), Section(''), Section(''), Section(''), Section('')]\n"
    )
    sections, error = load_sections(_write(tmp_path, body))
    assert sections == ()
    assert "(+2 more)" in error


def test_a_path_that_exists_but_is_not_a_file_is_reported(tmp_path):
    # A directory (or anything else failing os.path.isfile) is a config typo,
    # not the opt-out that a genuinely missing path represents.
    sections, error = load_sections(str(tmp_path))
    assert sections == ()
    assert "not a file" in error


def test_loading_registers_nothing_importable_under_the_name_sections(tmp_path):
    # A user file called sections.py must never become `import sections`, or it
    # could shadow job_search.digest.sections for anything that imports loosely.
    import sys

    before = set(sys.modules)
    load_sections(_write(tmp_path, VALID))
    added = set(sys.modules) - before
    assert "sections" not in added
    assert "job_search_user_sections" not in added


def test_the_shipped_example_config_loads_cleanly():
    # sections.example.py is what a user copies to sections.py; if it stopped
    # loading, every new user would start from a broken config.
    sections, error = load_sections(str(_REPO_ROOT / "sections.example.py"))
    assert error == ""
    assert [section.name for section in sections] == [
        "Israel",
        "Remote — Worldwide",
        "EU relocation",
        "Everything else",
    ]
