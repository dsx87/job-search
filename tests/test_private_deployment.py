"""Offline contracts for the pinned private-deployment handoff."""
import os
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "prepare-private-config.sh"
ROOT = SCRIPT.parents[1]


def _private_checkout(tmp_path):
    repo = tmp_path / "app"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "prepare-private-config.sh").symlink_to(SCRIPT)
    config = repo / ".private-config" / "job-search-config"
    config.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=config, check=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"], cwd=config, check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"], cwd=config, check=True
    )
    (config / "job_search.toml").write_text("[settings]\nversion = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "job_search.toml"], cwd=config, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=config, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:example/private-config.git"],
        cwd=config,
        check=True,
    )
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=config, check=True, text=True, capture_output=True
    ).stdout.strip()
    remote = tmp_path / "private-config.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(config), str(remote)], check=True)
    key = tmp_path / "config-key"
    key.write_text("test key", encoding="utf-8")
    key.chmod(0o600)
    (repo / ".deployment.env").write_text(
        "CONFIG_REPOSITORY=example/private-config\n"
        "CONFIG_REF={}\n"
        "CONFIG_SSH_KEY={}\n".format(sha, key),
        encoding="utf-8",
    )
    (repo / ".deployment.env").chmod(0o600)
    return repo, config, sha, remote


def _check(repo):
    return subprocess.run(
        ["bash", "scripts/prepare-private-config.sh", "--check-only"],
        cwd=repo,
        text=True,
        capture_output=True,
        env={**os.environ, "PATH": os.environ["PATH"]},
    )


def _read_only_git(config, *args):
    return subprocess.run(
        ["git", *args],
        cwd=config,
        check=True,
        text=True,
        capture_output=True,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    ).stdout


def _index_metadata(config):
    index = config / ".git" / "index"
    stat = index.stat()
    return stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _sync(repo, remote):
    return subprocess.run(
        ["bash", "scripts/prepare-private-config.sh", "--sync"],
        cwd=repo,
        text=True,
        capture_output=True,
        # Git transparently maps the expected GitHub URL to a local bare
        # fixture. The helper still sees and verifies the canonical origin.
        env={
            **os.environ,
            "PATH": os.environ["PATH"],
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "url.{}.insteadOf".format(remote.as_uri()),
            "GIT_CONFIG_VALUE_0": "git@github.com:example/private-config.git",
        },
    )


def test_check_only_accepts_the_exact_clean_private_checkout_without_mutation(tmp_path):
    repo, config, sha, _remote = _private_checkout(tmp_path)
    before = _read_only_git(config, "status", "--porcelain")
    index_before = _index_metadata(config)

    result = _check(repo)

    after = _read_only_git(config, "status", "--porcelain")
    assert result.returncode == 0, result.stderr
    assert "Private configuration is pinned and ready." in result.stdout
    assert before == after == ""
    assert _index_metadata(config) == index_before
    assert _read_only_git(config, "rev-parse", "HEAD").strip() == sha


def test_check_only_rejects_a_checkout_at_the_wrong_revision(tmp_path):
    repo, _config, _sha, _remote = _private_checkout(tmp_path)
    deployment = repo / ".deployment.env"
    lines = deployment.read_text(encoding="utf-8").splitlines()
    deployment.write_text(
        "\n".join(
            "CONFIG_REF=" + "0" * 40 if line.startswith("CONFIG_REF=") else line
            for line in lines
        ) + "\n",
        encoding="utf-8",
    )

    result = _check(repo)

    assert result.returncode != 0
    assert "does not match CONFIG_REF" in result.stderr


def test_sync_initializes_a_clean_nested_checkout_from_the_exact_local_pin(tmp_path):
    repo, config, sha, remote = _private_checkout(tmp_path)
    config.rename(repo / "seed-config")
    secrets = repo / ".env"
    secrets.write_text("TELEGRAM_BOT_TOKEN=keep-me\n", encoding="utf-8")

    result = _sync(repo, remote)

    assert result.returncode == 0, result.stderr
    assert (repo / ".private-config" / "job-search-config" / "job_search.toml").is_file()
    assert subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo / ".private-config" / "job-search-config",
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip() == sha
    assert secrets.read_text(encoding="utf-8") == "TELEGRAM_BOT_TOKEN=keep-me\n"


def test_personal_workflows_pin_the_private_checkout_without_persisting_key_credentials():
    for name in ("job_search.yml", "tailor_cv.yml"):
        workflow = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert "vars.CONFIG_REPOSITORY" in workflow
        assert "vars.CONFIG_REF" in workflow
        assert "secrets.CONFIG_DEPLOY_KEY" in workflow
        assert "path: .private-config/job-search-config" in workflow
        assert "persist-credentials: false" in workflow
        assert "rev-parse HEAD" in workflow
    daily = (ROOT / ".github" / "workflows" / "job_search.yml").read_text(encoding="utf-8")
    assert "github.event_name != 'schedule' || vars.PERSONAL_RUNS_ENABLED == 'true'" in daily
    assert "steps.state_checkout.outcome == 'success'" in daily


def test_public_rendering_never_writes_a_pdf_back_to_git():
    render = (ROOT / ".github" / "workflows" / "render_base_cv.yml").read_text(encoding="utf-8")
    guard = (ROOT / ".github" / "workflows" / "verify_cv_one_page.yml").read_text(encoding="utf-8")
    assert "contents: read" in render
    assert "actions/upload-artifact" in render
    assert "git add -f" not in render
    assert "git commit" not in render
    assert "git push" not in render
    assert "BASE_TEX_FILE=avery_example_base.tex" in render
    assert "python3 -m job_search.tests.page_limit_guard" in guard
    assert "python3 -m job_search.tests.one_page_guard --fixture avery_example_base.tex" in guard
    assert "pytest tests/test_fictional_candidate_flow.py" in guard


def test_pi_setup_never_enables_or_restarts_a_service():
    setup = (ROOT / "scripts" / "setup-rpi.sh").read_text(encoding="utf-8")
    assert 'prepare-private-config.sh" --sync' in setup
    assert 'prepare-private-config.sh" --check-only' in setup
    assert "tomli>=2.0,<2.4" in setup
    assert "systemctl enable --now" not in setup
    assert "systemctl restart" not in setup


def test_pi_runtime_forces_the_verified_nested_toml_after_loading_environment_files():
    wrapper = (ROOT / "scripts" / "run_pipeline.sh").read_text(encoding="utf-8")
    setup = (ROOT / "scripts" / "setup-rpi.sh").read_text(encoding="utf-8")
    selector = "$REPO/.private-config/job-search-config/job_search.toml"
    assert 'export JOB_SEARCH_SETTINGS_FILE="{}"'.format(selector) in wrapper
    assert "JOB_SEARCH_SETTINGS_FILE=${JOB_SEARCH_SETTINGS_FILE:-" not in wrapper
    assert "readonly REPO" in wrapper
    assert setup.count("/usr/bin/env JOB_SEARCH_SETTINGS_FILE=${REPO}/.private-config/job-search-config/job_search.toml") == 2
