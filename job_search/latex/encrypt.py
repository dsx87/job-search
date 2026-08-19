"""Standalone AES-256 PDF password protection via qpdf.

Sits beside compile.py and borrows its subprocess shape: a temp dir, one run of
the binary, read the bytes back. Nothing here touches the network.

The current Telegraph path protects the combined archive instead; this helper
is retained as a fail-closed PDF utility rather than deleting existing logic.

**Failure is never silent-plaintext.** Every way this can go wrong — qpdf
missing, a rejected flag, a timeout, an empty output — raises
EncryptionUnavailable.
"""
import os
import secrets
import subprocess
import tempfile

# ~96 bits of entropy in 16 URL-safe characters. Long enough that guessing is
# hopeless, short enough to retype from a phone if the tap-to-copy fails.
_PASSWORD_BYTES = 12

# Generous: qpdf on a Pi Zero encrypting a 50 KB PDF is well under a second, so
# anything near this is a hung process, not slow work.
_TIMEOUT_SECONDS = 60


class EncryptionUnavailable(Exception):
    """qpdf is missing or failed — the caller must not upload the plaintext."""


def new_password() -> str:
    """One fresh password per run for the hosted CV archive.

    The blast radius of a leak is one run's digest.
    """
    return secrets.token_urlsafe(_PASSWORD_BYTES)


def encrypt_pdf(pdf_bytes: bytes, password: str) -> bytes:
    """``pdf_bytes`` re-encoded with AES-256 under ``password``.

    Uses the modern flag form (qpdf 11.7+, which is what Debian trixie and
    ubuntu-latest ship). On an older host the legacy positional form is
    equivalent::

        qpdf --encrypt "" <password> 256 -- in.pdf out.pdf

    Raises EncryptionUnavailable for every failure mode, so a missing binary and
    a broken invocation take the same safe path.
    """
    if not password:
        # An empty user password produces a file every reader opens without
        # prompting — protection in name only, which is worse than none because
        # it looks protected.
        raise EncryptionUnavailable("refusing to encrypt with an empty password")
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = os.path.join(tmpdir, "in.pdf")
            target = os.path.join(tmpdir, "out.pdf")
            with open(source, "wb") as handle:
                handle.write(pdf_bytes)

            # The password is an argv element, never a shell string: no shell is
            # involved, so spaces and quotes in it are inert.
            completed = subprocess.run(
                [
                    "qpdf", "--encrypt",
                    "--user-password=" + password,
                    "--owner-password=" + password,
                    "--bits=256", "--", source, target,
                ],
                capture_output=True,
                timeout=_TIMEOUT_SECONDS,
            )
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or b"").decode(
                    "utf-8", "replace"
                ).strip()
                raise EncryptionUnavailable(
                    "qpdf exited {}: {}".format(completed.returncode, detail or "no output")
                )
            if not os.path.exists(target):
                raise EncryptionUnavailable("qpdf exited 0 but wrote no output file")
            with open(target, "rb") as handle:
                encrypted = handle.read()
    except FileNotFoundError as exc:
        raise EncryptionUnavailable("qpdf is not installed: {}".format(exc))
    except subprocess.TimeoutExpired as exc:
        raise EncryptionUnavailable("qpdf timed out after {}s".format(exc.timeout))
    except OSError as exc:
        raise EncryptionUnavailable("qpdf could not be run: {}".format(exc))
    if not encrypted:
        raise EncryptionUnavailable("qpdf produced an empty file")
    return encrypted
