"""Telegram Bot API delivery (stdlib only).

The low-level _tg_send_* functions take (bot_token, chat_id) explicitly; the
TelegramClient wraps a single (token, chat_id) so the pipeline stages can be
handed one client instead of reading module globals.
"""
import json
import urllib.request


def _tg_send_message(bot_token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()


def _tg_send_document(bot_token: str, chat_id: str, filename: str, content: bytes, caption: str) -> None:
    boundary = "PipelineBoundary8a3f1d6e"
    crlf = b"\r\n"

    def part_field(name: str, value: str) -> bytes:
        return (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n'
            f"\r\n"
            f"{value}\r\n"
        ).encode()

    body = (
        part_field("chat_id", chat_id)
        + part_field("caption", caption)
        + (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'
            f"Content-Type: application/octet-stream\r\n"
            f"\r\n"
        ).encode()
        + content
        + crlf
        + f"--{boundary}--\r\n".encode()
    )

    url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        resp.read()


class TelegramClient:
    """A (token, chat_id)-bound view over the send functions, built once in
    run.py / cli.py and injected into the pipeline stages."""

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id

    def send_message(self, text: str) -> None:
        _tg_send_message(self.bot_token, self.chat_id, text)

    def send_document(self, filename: str, content: bytes, caption: str) -> None:
        _tg_send_document(self.bot_token, self.chat_id, filename, content, caption)
