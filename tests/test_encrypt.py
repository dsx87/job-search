"""TDD for the qpdf-backed PDF encryption helper.

The safety property under test is negative: when anything about qpdf goes wrong
the module must raise EncryptionUnavailable rather than hand back plaintext,
because its one caller uploads the result to a public file host.
"""
import shutil
import subprocess

import pytest

from job_search.latex import encrypt

_HAS_QPDF = shutil.which("qpdf") is not None


class _Completed:
    def __init__(self, returncode, stderr=b"", stdout=b""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout


# ── passwords ─────────────────────────────────────────────────────────────────

def test_new_password_is_url_safe_and_long_enough():
    password = encrypt.new_password()

    assert len(password) >= 16
    # URL-safe base64 alphabet: it travels in a Telegram message and gets
    # pasted into a PDF reader, so no quoting surprises.
    assert all(char.isalnum() or char in "-_" for char in password), password


def test_new_password_varies_between_runs():
    assert len({encrypt.new_password() for _ in range(20)}) == 20


# ── failure modes ─────────────────────────────────────────────────────────────

def test_missing_qpdf_raises_encryption_unavailable(monkeypatch):
    def boom(*_args, **_kwargs):
        raise FileNotFoundError("qpdf")

    monkeypatch.setattr(encrypt.subprocess, "run", boom)

    with pytest.raises(encrypt.EncryptionUnavailable):
        encrypt.encrypt_pdf(b"%PDF-1.4 x", "pw")


def test_a_nonzero_exit_raises_encryption_unavailable(monkeypatch):
    # A broken invocation must take the same safe path as a missing binary:
    # both mean "no ciphertext", and the caller falls back to the ZIP.
    monkeypatch.setattr(
        encrypt.subprocess, "run",
        lambda *_a, **_kw: _Completed(2, stderr=b"qpdf: unknown option"),
    )

    with pytest.raises(encrypt.EncryptionUnavailable) as excinfo:
        encrypt.encrypt_pdf(b"%PDF-1.4 x", "pw")
    assert "unknown option" in str(excinfo.value)


def test_a_timeout_raises_encryption_unavailable(monkeypatch):
    def boom(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("qpdf", 60)

    monkeypatch.setattr(encrypt.subprocess, "run", boom)

    with pytest.raises(encrypt.EncryptionUnavailable):
        encrypt.encrypt_pdf(b"%PDF-1.4 x", "pw")


def test_a_missing_output_file_raises_rather_than_returning_plaintext(monkeypatch):
    # qpdf exiting 0 without writing anything would otherwise read as success.
    monkeypatch.setattr(encrypt.subprocess, "run", lambda *_a, **_kw: _Completed(0))

    with pytest.raises(encrypt.EncryptionUnavailable):
        encrypt.encrypt_pdf(b"%PDF-1.4 x", "pw")


def test_an_empty_password_is_refused(monkeypatch):
    # An empty user password encrypts to something any reader opens silently —
    # indistinguishable from no protection at all for the threat this guards.
    monkeypatch.setattr(encrypt.subprocess, "run", lambda *_a, **_kw: _Completed(0))

    with pytest.raises(encrypt.EncryptionUnavailable):
        encrypt.encrypt_pdf(b"%PDF-1.4 x", "")


def test_the_password_is_passed_as_an_argument_not_a_shell_string(monkeypatch):
    captured = {}

    def fake_run(cmd, **_kwargs):
        captured["cmd"] = cmd
        # Write the expected output so the call reports success.
        with open(cmd[-1], "wb") as handle:
            handle.write(b"%PDF-1.4 encrypted")
        return _Completed(0)

    monkeypatch.setattr(encrypt.subprocess, "run", fake_run)
    encrypt.encrypt_pdf(b"%PDF-1.4 x", "p@ss w'ord")

    cmd = captured["cmd"]
    assert cmd[0] == "qpdf"
    assert "--user-password=p@ss w'ord" in cmd
    assert "--owner-password=p@ss w'ord" in cmd
    assert "--bits=256" in cmd


# ── the real thing ────────────────────────────────────────────────────────────

def _one_page_pdf() -> bytes:
    """A structurally valid one-page PDF, xref offsets and all.

    Hand-written rather than shipped as a fixture so the test needs no build
    artifact — but it must be *valid*: qpdf exits non-zero on a damaged file
    (having repaired it), which encrypt_pdf correctly treats as a failure, so a
    sloppy fixture would fail this test for the wrong reason.
    """
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << >> >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, xref_at,
    )
    return bytes(out)


@pytest.mark.requires_qpdf
@pytest.mark.skipif(not _HAS_QPDF, reason="qpdf is not installed")
def test_a_real_pdf_encrypts_and_only_opens_with_the_password(tmp_path):
    """The one test that proves the qpdf invocation itself is right.

    Everything above stubs subprocess, so a wrong flag would sail past them and
    only show up in production as a permanent ZIP fallback.
    """
    source = _one_page_pdf()
    password = encrypt.new_password()

    encrypted = encrypt.encrypt_pdf(source, password)

    assert encrypted.startswith(b"%PDF")
    assert encrypted != source
    path = tmp_path / "enc.pdf"
    path.write_bytes(encrypted)

    right = subprocess.run(
        ["qpdf", "--decrypt", "--password=" + password, str(path), str(tmp_path / "ok.pdf")],
        capture_output=True,
    )
    assert right.returncode == 0, right.stderr

    wrong = subprocess.run(
        ["qpdf", "--decrypt", "--password=definitely-not-it", str(path),
         str(tmp_path / "no.pdf")],
        capture_output=True,
    )
    assert wrong.returncode != 0
    assert b"invalid password" in wrong.stderr.lower()
