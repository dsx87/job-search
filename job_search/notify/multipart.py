"""multipart/form-data encoding (stdlib only).

Lifted out of ``notify.telegram._tg_send_document`` when the file-host uploader
needed the same body shape. Two clients, one encoder: a boundary bug or a
mis-quoted filename can only be written once.

Kept deliberately small — no streaming, no chunking. Both callers hold the whole
payload in memory already (a tailored CV is tens of KB), and a generator body
would break ``urllib``'s Content-Length handling on the 3.9 floor.
"""

# Fixed rather than random: the callers' payloads are PDFs and short ASCII
# fields, none of which can contain this string, and a constant boundary keeps
# the encoded body byte-for-byte reproducible in tests.
BOUNDARY = "PipelineBoundary8a3f1d6e"


def _field_part(boundary, name, value) -> bytes:
    return (
        "--{}\r\n"
        'Content-Disposition: form-data; name="{}"\r\n'
        "\r\n"
        "{}\r\n".format(boundary, name, value)
    ).encode("utf-8")


def _file_part(boundary, name, filename, content) -> bytes:
    header = (
        "--{}\r\n"
        'Content-Disposition: form-data; name="{}"; filename="{}"\r\n'
        "Content-Type: application/octet-stream\r\n"
        "\r\n".format(boundary, name, filename)
    ).encode("utf-8")
    return header + content + b"\r\n"


def encode(fields, files, boundary=BOUNDARY):
    """``(body, content_type)`` for a form of ``fields`` and ``files``.

    ``fields`` maps a name to a value (stringified); ``files`` maps a name to a
    ``(filename, bytes)`` pair whose bytes are written verbatim. Fields are
    emitted first, which is what both APIs expect of their metadata.
    """
    body = b"".join(
        [_field_part(boundary, name, value) for name, value in fields.items()]
        + [
            _file_part(boundary, name, filename, content)
            for name, (filename, content) in files.items()
        ]
        + ["--{}--\r\n".format(boundary).encode("utf-8")]
    )
    return body, "multipart/form-data; boundary={}".format(boundary)
