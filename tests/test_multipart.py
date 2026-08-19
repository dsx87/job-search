"""TDD for the shared multipart/form-data encoder.

The encoder was lifted out of notify.telegram._tg_send_document so the file-host
uploader can reuse it; tests/test_telegram.py is the regression guard proving
that extraction changed nothing about what Telegram receives.
"""
from job_search.notify.multipart import encode


def _parts(body, boundary):
    """The body split on its boundary, dropping the preamble and the closer."""
    marker = "--{}".format(boundary).encode()
    return [chunk for chunk in body.split(marker)[1:-1]]


def _boundary_of(content_type):
    return content_type.split("boundary=", 1)[1]


def test_content_type_names_the_boundary_used_in_the_body():
    body, content_type = encode({"a": "1"}, {})

    assert content_type.startswith("multipart/form-data; boundary=")
    boundary = _boundary_of(content_type)
    assert "--{}".format(boundary).encode() in body
    assert body.endswith("--{}--\r\n".format(boundary).encode())


def test_fields_round_trip_in_order():
    body, content_type = encode({"chat_id": "42", "caption": "hello world"}, {})

    parts = _parts(body, _boundary_of(content_type))
    assert len(parts) == 2
    assert b'name="chat_id"' in parts[0] and b"42" in parts[0]
    assert b'name="caption"' in parts[1] and b"hello world" in parts[1]


def test_a_file_part_carries_the_filename_and_the_bytes_verbatim():
    content = b"%PDF-1.4 \x00\xff binary\r\n--not-a-boundary"
    body, content_type = encode({}, {"document": ("cv.pdf", content)})

    parts = _parts(body, _boundary_of(content_type))
    assert len(parts) == 1
    assert b'name="document"; filename="cv.pdf"' in parts[0]
    assert b"Content-Type: application/octet-stream" in parts[0]
    # The bytes survive untouched — no decode, no re-encode, no escaping.
    assert content in parts[0]


def test_fields_come_before_files():
    body, content_type = encode({"keep_name": "1"}, {"file": ("cv.pdf", b"x")})

    parts = _parts(body, _boundary_of(content_type))
    assert b'name="keep_name"' in parts[0]
    assert b'name="file"' in parts[1]


def test_non_ascii_field_values_survive_as_utf8():
    body, _content_type = encode({"caption": "Tailored CV — Acme ✅"}, {})

    assert "Tailored CV — Acme ✅".encode("utf-8") in body


def test_a_field_value_that_is_not_a_string_is_stringified():
    # id_length=24 reads better than "24" at the call site.
    body, _content_type = encode({"id_length": 24}, {})

    assert b"24" in body
