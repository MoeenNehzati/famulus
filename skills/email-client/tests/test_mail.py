"""Unit tests for mail.py's pure functions — no network, no live IMAP.

connect()/cmd_list()/cmd_read()/cmd_folders() need a real IMAP server and are
exercised manually against the live IMAP accounts, not here.
"""
import email
import importlib.util
from pathlib import Path

MAIL_PY = Path(__file__).parent.parent / "_rtx" / "_imap_gateway.py"
spec = importlib.util.spec_from_file_location("mail", MAIL_PY)
mail = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mail)


# ── decode_mime_words ───────────────────────────────────────────────────────

def test_decode_mime_words_plain_ascii():
    assert mail.decode_mime_words("Hello World") == "Hello World"


def test_decode_mime_words_none_returns_empty():
    assert mail.decode_mime_words(None) == ""


def test_decode_mime_words_unfolds_rfc5322_continuation():
    raw = "Subject line that\r\n continues on the next physical line"
    assert "\r\n" not in mail.decode_mime_words(raw)
    assert mail.decode_mime_words(raw) == "Subject line that continues on the next physical line"


def test_decode_mime_words_decodes_encoded_word():
    # "Héllo" encoded as UTF-8 base64 MIME encoded-word
    raw = "=?utf-8?b?SMOpbGxv?="
    assert mail.decode_mime_words(raw) == "Héllo"


# ── parse_date ───────────────────────────────────────────────────────────────

def test_parse_date_none_returns_none():
    assert mail.parse_date(None) is None


def test_parse_date_parses_rfc2822_with_offset():
    result = mail.parse_date("Sun, 05 Jul 2026 06:25:08 +0000")
    assert result == "2026-07-05T06:25:08+00:00"


def test_parse_date_naive_gets_utc():
    # parsedate_to_datetime always attaches a tz for valid RFC 2822 dates with
    # a zone; this just confirms our fallback path doesn't crash on odd input.
    result = mail.parse_date("Sun, 05 Jul 2026 06:25:08 -0400")
    assert result == "2026-07-05T06:25:08-04:00"


# ── resolve_folder ───────────────────────────────────────────────────────────

def test_resolve_folder_aliases():
    assert mail.resolve_folder("inbox") == "INBOX"
    assert mail.resolve_folder("sent") == "[Gmail]/Sent Mail"
    assert mail.resolve_folder("trash") == "[Gmail]/Trash"
    assert mail.resolve_folder("drafts") == "[Gmail]/Drafts"
    assert mail.resolve_folder("all") == "[Gmail]/All Mail"


def test_resolve_folder_case_insensitive():
    assert mail.resolve_folder("INBOX") == "INBOX"
    assert mail.resolve_folder("Sent") == "[Gmail]/Sent Mail"


def test_resolve_folder_passthrough_for_unknown():
    assert mail.resolve_folder("github") == "github"
    assert mail.resolve_folder("[Gmail]/Starred") == "[Gmail]/Starred"


# ── attachment helpers ──────────────────────────────────────────────────────

def test_format_size_uses_human_units():
    assert mail.format_size(999) == "999 B"
    assert mail.format_size(59_241) == "59 KB"


def test_collect_attachments_extracts_metadata():
    raw = (
        "Content-Type: multipart/mixed; boundary=BOUNDARY\r\n\r\n"
        "--BOUNDARY\r\nContent-Type: text/plain\r\n\r\nBody text\r\n"
        "--BOUNDARY\r\nContent-Type: application/pdf\r\n"
        'Content-Disposition: attachment; filename="notes.pdf"\r\n\r\npdf-bytes\r\n'
        "--BOUNDARY--\r\n"
    )
    msg = email.message_from_string(raw)
    attachments = [mail.public_attachment_record(record) for record in mail.collect_attachments(msg)]
    assert attachments == [
        {
            "name": "notes.pdf",
            "content_type": "application/pdf",
            "size_bytes": 9,
            "size_human": "9 B",
            "disposition": "attachment",
        }
    ]


def test_save_attachment_records_filters_and_avoids_collisions(tmp_path):
    attachments = [
        {
            "name": "notes.pdf",
            "content_type": "application/pdf",
            "size_bytes": 3,
            "size_human": "3 B",
            "disposition": "attachment",
            "_payload": b"one",
        },
        {
            "name": "notes.pdf",
            "content_type": "application/pdf",
            "size_bytes": 3,
            "size_human": "3 B",
            "disposition": "attachment",
            "_payload": b"two",
        },
        {
            "name": "other.txt",
            "content_type": "text/plain",
            "size_bytes": 5,
            "size_human": "5 B",
            "disposition": "attachment",
            "_payload": b"three",
        },
    ]

    saved = mail.save_attachment_records(
        attachments,
        tmp_path,
        selected_names={"notes.pdf"},
        uid="42",
        subject="Example",
    )

    assert [item["attachment"] for item in saved] == ["notes.pdf", "notes.pdf"]
    assert Path(saved[0]["saved_to"]).name == "notes.pdf"
    assert Path(saved[1]["saved_to"]).name == "notes-2.pdf"
    assert (tmp_path / "notes.pdf").read_bytes() == b"one"
    assert (tmp_path / "notes-2.pdf").read_bytes() == b"two"
    assert not (tmp_path / "other.txt").exists()


# ── format_read_output ──────────────────────────────────────────────────────

def test_format_read_output_omits_threading_headers_when_absent():
    msg = email.message_from_string(
        "Subject: Hello\r\nFrom: a@example.com\r\nTo: b@example.com\r\n"
        "Date: Sun, 05 Jul 2026 12:00:00 +0000\r\nMessage-ID: <1@example.com>\r\n"
        "Content-Type: text/plain\r\n\r\nBody text"
    )
    out = mail.format_read_output(msg)
    assert "In-Reply-To" not in out
    assert "References" not in out
    assert "Attachments: none" in out
    assert "Message-ID: <1@example.com>" in out
    assert out.endswith("Body text")


def test_format_read_output_includes_threading_headers_when_present():
    msg = email.message_from_string(
        "Subject: Re: Hello\r\nFrom: a@example.com\r\nTo: b@example.com\r\n"
        "Date: Sun, 05 Jul 2026 12:05:00 +0000\r\nMessage-ID: <2@example.com>\r\n"
        "In-Reply-To: <1@example.com>\r\nReferences: <1@example.com>\r\n"
        "Content-Type: text/plain\r\n\r\nReply text"
    )
    out = mail.format_read_output(msg)
    assert "In-Reply-To: <1@example.com>" in out
    assert "References: <1@example.com>" in out


def test_format_read_output_lists_attachments():
    raw = (
        "Subject: Files\r\nFrom: a@example.com\r\nTo: b@example.com\r\n"
        "Date: Sun, 05 Jul 2026 12:05:00 +0000\r\nMessage-ID: <2@example.com>\r\n"
        "Content-Type: multipart/mixed; boundary=BOUNDARY\r\n\r\n"
        "--BOUNDARY\r\nContent-Type: text/plain\r\n\r\nReply text\r\n"
        "--BOUNDARY\r\nContent-Type: application/zip\r\n"
        'Content-Disposition: attachment; filename="lessons.zip"\r\n\r\nabc\r\n'
        "--BOUNDARY--\r\n"
    )
    msg = email.message_from_string(raw)
    out = mail.format_read_output(msg)
    assert "Attachments:" in out
    assert "- lessons.zip (application/zip, 3 B)" in out


# ── extract_body ─────────────────────────────────────────────────────────────

def test_extract_body_plain_non_multipart():
    msg = email.message_from_string("Content-Type: text/plain\r\n\r\nHello body")
    assert mail.extract_body(msg) == "Hello body"


def test_extract_body_multipart_prefers_plain():
    raw = (
        "Content-Type: multipart/alternative; boundary=BOUNDARY\r\n\r\n"
        "--BOUNDARY\r\nContent-Type: text/plain\r\n\r\nPlain version\r\n"
        "--BOUNDARY\r\nContent-Type: text/html\r\n\r\n<p>HTML version</p>\r\n"
        "--BOUNDARY--\r\n"
    )
    msg = email.message_from_string(raw)
    assert mail.extract_body(msg) == "Plain version"


def test_extract_body_multipart_falls_back_to_html_stripped():
    raw = (
        "Content-Type: multipart/alternative; boundary=BOUNDARY\r\n\r\n"
        "--BOUNDARY\r\nContent-Type: text/html\r\n\r\n<p>Only HTML <b>here</b></p>\r\n"
        "--BOUNDARY--\r\n"
    )
    msg = email.message_from_string(raw)
    assert mail.extract_body(msg) == "Only HTML here"


def test_extract_body_skips_attachments():
    raw = (
        "Content-Type: multipart/mixed; boundary=BOUNDARY\r\n\r\n"
        "--BOUNDARY\r\nContent-Type: text/plain\r\n\r\nThe real body\r\n"
        "--BOUNDARY\r\nContent-Type: application/pdf\r\n"
        'Content-Disposition: attachment; filename="f.pdf"\r\n\r\nnotplaintext\r\n'
        "--BOUNDARY--\r\n"
    )
    msg = email.message_from_string(raw)
    assert mail.extract_body(msg) == "The real body"


# ── parse_filters / envelope_matches ────────────────────────────────────────

def test_parse_filters_exact_and_regex():
    filters = mail.parse_filters(["subject=Hello", "from~=example\\.com"])
    assert filters == [("subject", "=", "Hello"), ("from", "~=", "example\\.com")]


def test_parse_filters_invalid_raises_systemexit():
    import pytest
    with pytest.raises(SystemExit):
        mail.parse_filters(["not-a-filter"])


def _envelope(**kwargs):
    base = {"id": "1", "flags": [], "subject": "", "from": "", "date": "", "message_id": ""}
    base.update(kwargs)
    return base


def test_envelope_matches_exact():
    env = _envelope(subject="Hello World")
    assert mail.envelope_matches(env, [("subject", "=", "Hello World")])
    assert not mail.envelope_matches(env, [("subject", "=", "Something else")])


def test_envelope_matches_exact_or_comma_separated():
    env = _envelope(subject="B")
    assert mail.envelope_matches(env, [("subject", "=", "A,B,C")])


def test_envelope_matches_regex_case_insensitive():
    env = _envelope(subject="ICML 2026 CHECKIN BARCODE")
    assert mail.envelope_matches(env, [("subject", "~=", "icml 2026")])


def test_envelope_matches_regex_phrase_with_spaces():
    env = _envelope(subject="Visit us at ICML 2026 Booth")
    assert mail.envelope_matches(env, [("subject", "~=", "ICML 2026")])
    assert not mail.envelope_matches(env, [("subject", "~=", "ICML 2027")])


def test_envelope_matches_and_across_keys():
    env = _envelope(subject="CHECKIN", **{"from": "do-not-reply@icml.cc"})
    assert mail.envelope_matches(env, [("subject", "~=", "CHECKIN"), ("from", "~=", "icml")])
    assert not mail.envelope_matches(env, [("subject", "~=", "CHECKIN"), ("from", "~=", "example")])


def test_envelope_matches_flags_list_regex():
    env = _envelope(flags=["\\Answered", "\\Seen"])
    assert mail.envelope_matches(env, [("flags", "~=", "Answered")])
    assert not mail.envelope_matches(env, [("flags", "~=", "Flagged")])


def test_envelope_matches_bad_regex_falls_back_to_substring():
    env = _envelope(subject="a(b")
    # "(b" is invalid as a regex (unbalanced paren); should fall back to literal substring
    assert mail.envelope_matches(env, [("subject", "~=", "(b")])
