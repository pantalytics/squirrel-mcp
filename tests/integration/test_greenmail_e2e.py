"""End-to-end tests against a real GreenMail IMAP/SMTP server.

These exercise Squirrel's actual provider path (imap-tools + smtplib) -- no mocks,
no Soverin credentials. Marked ``integration`` so the default unit run skips them.
"""

from __future__ import annotations

import smtplib
import uuid
from email.message import EmailMessage

import pytest
from imap_tools import MailBoxUnencrypted

from squirrel_mcp.providers import OutgoingAttachment, create_mail_provider

pytestmark = pytest.mark.integration

TEST_EMAIL = "squirrel@example.com"
TEST_LOGIN = "squirrel"
TEST_PASSWORD = "squirrelpass"


def _ensure_folders(host: str, imap_port: int, *names: str) -> None:
    mb = MailBoxUnencrypted(host, port=imap_port).login(TEST_LOGIN, TEST_PASSWORD)
    try:
        existing = {f.name for f in mb.folder.list()}
        for name in names:
            if name not in existing:
                mb.folder.create(name)
    finally:
        mb.logout()


def _seed_message(
    host: str,
    smtp_port: int,
    subject: str,
    body: str,
    attachment: bool = False,
    message_id: str | None = None,
):
    msg = EmailMessage()
    msg["From"] = "sender@external.test"
    msg["To"] = TEST_EMAIL
    msg["Subject"] = subject
    # smtplib adds no Message-ID and GreenMail is not obliged to either, so a
    # test that needs to reply to this message spells one out.
    if message_id:
        msg["Message-ID"] = message_id
    msg.set_content(body)
    if attachment:
        msg.add_attachment(
            b"%PDF-1.4 fake pdf bytes",
            maintype="application",
            subtype="pdf",
            filename="report.pdf",
        )
    with smtplib.SMTP(host, smtp_port, timeout=15) as s:
        s.send_message(msg)


@pytest.fixture
def provider(greenmail):
    _ensure_folders(greenmail.imap_host, greenmail.imap_port, "Drafts", "Archive", "Sent")
    p = create_mail_provider(greenmail)
    p.connect()
    yield p, greenmail
    p.disconnect()


def _find_by_subject(messages, subject):
    return next((m for m in messages if m.subject == subject), None)


def test_list_search_read(provider):
    p, cfg = provider
    subject = f"E2E read {uuid.uuid4().hex[:8]}"
    _seed_message(cfg.smtp_host, cfg.smtp_port, subject, "Hello from GreenMail body.")

    folders = {f.name for f in p.list_folders()}
    assert "INBOX" in folders

    messages, total = p.search("INBOX", limit=50)
    assert total >= 1
    found = _find_by_subject(messages, subject)
    assert found is not None, "seeded message not found in INBOX"

    detail = p.fetch_message("INBOX", found.uid)
    assert "Hello from GreenMail" in detail.body_text
    assert detail.subject == subject


def test_attachment_roundtrip(provider):
    p, cfg = provider
    subject = f"E2E attach {uuid.uuid4().hex[:8]}"
    _seed_message(cfg.smtp_host, cfg.smtp_port, subject, "See attached.", attachment=True)

    messages, _ = p.search("INBOX", limit=50)
    found = _find_by_subject(messages, subject)
    assert found is not None and found.has_attachments

    detail = p.fetch_message("INBOX", found.uid)
    assert len(detail.attachments) == 1
    assert detail.attachments[0].filename == "report.pdf"

    payload = p.fetch_attachment("INBOX", found.uid, 0)
    assert payload.content.startswith(b"%PDF")
    assert payload.filename == "report.pdf"


def test_draft_save_and_find(provider):
    p, cfg = provider
    subject = f"E2E draft {uuid.uuid4().hex[:8]}"

    uid = p.save_draft(["someone@example.com"], subject, "Draft body", folder="Drafts")
    assert uid  # server returned the appended uid

    drafts, _ = p.search("Drafts", limit=50)
    assert _find_by_subject(drafts, subject) is not None


def test_move_between_folders(provider):
    p, cfg = provider
    subject = f"E2E move {uuid.uuid4().hex[:8]}"
    _seed_message(cfg.smtp_host, cfg.smtp_port, subject, "Move me.")

    inbox, _ = p.search("INBOX", limit=50)
    target = _find_by_subject(inbox, subject)
    assert target is not None

    moved = p.move("INBOX", [target.uid], "Archive")
    assert moved == 1

    archive, _ = p.search("Archive", limit=50)
    assert _find_by_subject(archive, subject) is not None


def test_flag_and_unflag_round_trip(provider):
    """The one operation a fake cannot honestly check.

    ``flag`` writes a raw ``UID STORE`` (see the comment in ``imap.py`` on why
    it does not use imap-tools' own helper), so its command syntax is only ever
    proved by a real IMAP server -- a mock would happily accept a malformed one.
    """
    p, cfg = provider
    subject = f"E2E flag {uuid.uuid4().hex[:8]}"
    _seed_message(cfg.smtp_host, cfg.smtp_port, subject, "Flag me.")

    inbox, _ = p.search("INBOX", limit=50)
    target = _find_by_subject(inbox, subject)
    assert target is not None
    assert "\\Flagged" not in target.flags

    assert p.flag("INBOX", [target.uid]) == 1
    inbox, _ = p.search("INBOX", limit=50)
    assert "\\Flagged" in _find_by_subject(inbox, subject).flags

    # And the server does the narrowing, which is the whole point of the
    # filter -- "what have I flagged" must not mean paging the folder.
    only, _ = p.search("INBOX", flagged_only=True, limit=50)
    assert _find_by_subject(only, subject) is not None
    assert all("\\Flagged" in m.flags for m in only)

    assert p.flag("INBOX", [target.uid], flagged=False) == 1
    inbox, _ = p.search("INBOX", limit=50)
    assert "\\Flagged" not in _find_by_subject(inbox, subject).flags
    only, _ = p.search("INBOX", flagged_only=True, limit=50)
    assert _find_by_subject(only, subject) is None


def test_mark_read_and_unread_round_trip(provider):
    """\\Seen against a real server, and against the unread filter.

    Same reason as the flag round trip: ``set_seen`` writes its own ``UID
    STORE``. The extra thing proved here is that the server then agrees --
    ``unseen_only`` is answered by IMAP's own ``UNSEEN`` key, so a marker the
    tool set has to be the one the search reads.
    """
    p, cfg = provider
    subject = f"E2E seen {uuid.uuid4().hex[:8]}"
    _seed_message(cfg.smtp_host, cfg.smtp_port, subject, "Mark me.")

    inbox, _ = p.search("INBOX", limit=50)
    target = _find_by_subject(inbox, subject)
    assert target is not None
    assert "\\Seen" not in target.flags
    unread, _ = p.search("INBOX", unseen_only=True, limit=50)
    assert _find_by_subject(unread, subject) is not None

    assert p.set_seen("INBOX", [target.uid]) == 1
    inbox, _ = p.search("INBOX", limit=50)
    assert "\\Seen" in _find_by_subject(inbox, subject).flags
    unread, _ = p.search("INBOX", unseen_only=True, limit=50)
    assert _find_by_subject(unread, subject) is None

    assert p.set_seen("INBOX", [target.uid], seen=False) == 1
    inbox, _ = p.search("INBOX", limit=50)
    assert "\\Seen" not in _find_by_subject(inbox, subject).flags
    unread, _ = p.search("INBOX", unseen_only=True, limit=50)
    assert _find_by_subject(unread, subject) is not None


def test_flag_does_not_expunge_deleted_messages(provider):
    """Flagging must not take other messages with it.

    imap-tools' ``mb.flag`` follows every STORE with an EXPUNGE, which would
    permanently drop whatever another client left marked \\Deleted in the same
    folder. That is why ``flag`` issues the STORE itself; this pins the reason.
    """
    p, cfg = provider
    keep = f"E2E keep {uuid.uuid4().hex[:8]}"
    doomed = f"E2E doomed {uuid.uuid4().hex[:8]}"
    _seed_message(cfg.smtp_host, cfg.smtp_port, keep, "Flag me.")
    _seed_message(cfg.smtp_host, cfg.smtp_port, doomed, "Another client marked me deleted.")

    inbox, _ = p.search("INBOX", limit=50)
    keep_uid = _find_by_subject(inbox, keep).uid
    doomed_uid = _find_by_subject(inbox, doomed).uid

    # A second client marks one message \Deleted without expunging -- exactly
    # what a desktop mail client in "mark as deleted" mode leaves behind.
    mb = MailBoxUnencrypted(cfg.imap_host, port=cfg.imap_port).login(TEST_LOGIN, TEST_PASSWORD)
    try:
        mb.folder.set("INBOX")
        # Raw STORE, not mb.flag -- the helper's own EXPUNGE would remove the
        # message here and there would be nothing left to test.
        mb.client.uid("STORE", doomed_uid, "+FLAGS", "(\\Deleted)")
    finally:
        mb.logout()  # LOGOUT, unlike CLOSE, leaves \Deleted messages in place

    p.flag("INBOX", [keep_uid])

    inbox, _ = p.search("INBOX", limit=50)
    assert _find_by_subject(inbox, doomed) is not None, "flagging expunged a deleted message"
    assert "\\Flagged" in _find_by_subject(inbox, keep).flags


def test_send_delivers_to_self(provider):
    p, cfg = provider
    subject = f"E2E send {uuid.uuid4().hex[:8]}"

    result = p.send([TEST_EMAIL], subject, "Sent via Squirrel over SMTP.")
    assert TEST_EMAIL in result["recipients"]

    inbox, _ = p.search("INBOX", limit=50)
    assert _find_by_subject(inbox, subject) is not None


def test_send_files_a_copy_in_sent(provider):
    """Delivery and the sender's own record are two different things.

    Only a real server proves this one: the APPEND has to be accepted, the
    copy has to come back out of a SEARCH of that folder, and it has to carry
    the flag that keeps it out of the unread count. GreenMail advertises no
    SPECIAL-USE attributes, so this also exercises the ``Sent`` fallback.
    """
    p, _ = provider
    subject = f"E2E sent copy {uuid.uuid4().hex[:8]}"

    result = p.send([TEST_EMAIL], subject, "This one should be in Sent afterwards.")
    assert result["saved_to_sent"] is True

    sent, _total = p.search(result["sent_folder"], limit=50)
    copy = _find_by_subject(sent, subject)
    assert copy is not None, f"the message is not in {result['sent_folder']}"
    assert "\\Seen" in copy.flags  # mail you wrote yourself is not unread


def test_a_reply_is_filed_in_sent_with_its_thread(provider):
    """A reply goes through the same door, so it lands in Sent as a reply --
    headers and all, which is what a mail client threads the copy on."""
    p, cfg = provider
    subject = f"E2E sent reply {uuid.uuid4().hex[:8]}"
    parent_id = f"<{uuid.uuid4().hex}@external.test>"
    _seed_message(cfg.smtp_host, cfg.smtp_port, subject, "Original.", message_id=parent_id)

    inbox, _ = p.search("INBOX", limit=50)
    original = _find_by_subject(inbox, subject)
    assert original is not None

    reply_subject = f"Re: {subject}"
    result = p.send([TEST_EMAIL], reply_subject, "Answering.", reply_to_uid=original.uid)
    assert result["saved_to_sent"] is True

    sent, _total = p.search(result["sent_folder"], limit=50)
    copy = _find_by_subject(sent, reply_subject)
    assert copy is not None
    assert p.fetch_message(result["sent_folder"], copy.uid).message_id == result["message_id"]


def test_reply_lands_in_the_thread(provider):
    """The whole point, against a real server: send a reply to a message that
    is actually in the mailbox, then read the delivered copy back and check the
    threading headers a mail client reads to build a conversation.

    A fake provider can only prove we asked for threading; only a round trip
    proves the headers survived the append, the SMTP submission and the
    delivery."""
    p, cfg = provider
    subject = f"E2E thread {uuid.uuid4().hex[:8]}"
    parent_id = f"<{uuid.uuid4().hex}@external.test>"
    _seed_message(
        cfg.smtp_host, cfg.smtp_port, subject, "Original message.", message_id=parent_id
    )

    inbox, _ = p.search("INBOX", limit=50)
    original = _find_by_subject(inbox, subject)
    assert original is not None
    assert p.fetch_message("INBOX", original.uid).message_id == parent_id

    reply_subject = f"Re: {subject}"
    p.send([TEST_EMAIL], reply_subject, "Answering.", reply_to_uid=original.uid)

    # Read the delivered reply's raw headers -- what the recipient's client sees.
    mb = MailBoxUnencrypted(cfg.imap_host, port=cfg.imap_port).login(TEST_LOGIN, TEST_PASSWORD)
    try:
        delivered = [
            m for m in mb.fetch(mark_seen=False) if (m.subject or "") == reply_subject
        ]
        assert delivered, "reply was not delivered"
        headers = delivered[-1].headers
        assert (headers.get("in-reply-to") or ("",))[0].strip() == parent_id
        assert parent_id in (headers.get("references") or ("",))[0]
    finally:
        mb.logout()


def test_a_reply_draft_keeps_its_thread_through_an_edit(provider):
    """Drafting a reply and then fixing a typo in it is the ordinary path, and
    the edit rewrites the message -- so this is where the threading would be
    lost without anyone noticing until the send."""
    p, cfg = provider
    subject = f"E2E draft thread {uuid.uuid4().hex[:8]}"
    parent_id = f"<{uuid.uuid4().hex}@external.test>"
    _seed_message(
        cfg.smtp_host, cfg.smtp_port, subject, "Original message.", message_id=parent_id
    )

    inbox, _ = p.search("INBOX", limit=50)
    original = _find_by_subject(inbox, subject)
    assert original is not None

    draft_subject = f"Re: {subject}"
    uid = p.save_draft(
        [TEST_EMAIL], draft_subject, "First take", folder="Drafts",
        reply_to_uid=original.uid, reply_to_folder="INBOX",
    )
    assert uid
    new_uid = p.update_draft("Drafts", uid, [TEST_EMAIL], draft_subject, "Second take")

    mb = MailBoxUnencrypted(cfg.imap_host, port=cfg.imap_port).login(TEST_LOGIN, TEST_PASSWORD)
    try:
        mb.folder.set("Drafts")
        edited = list(mb.fetch(uid_list=[new_uid], mark_seen=False, limit=1))
        assert edited, "edited draft not found"
        assert (edited[0].headers.get("in-reply-to") or ("",))[0].strip() == parent_id
        assert "Second take" in (edited[0].text or "")
    finally:
        mb.logout()


def test_send_with_an_attachment_arrives_intact(provider):
    """The claim that matters: a real SMTP server accepts the message and a
    real IMAP server hands the same bytes back. Everything below this -- the
    multipart, the base64, the filename encoding -- is only worth anything if
    it survives an actual round trip."""
    p, cfg = provider
    subject = f"E2E attach send {uuid.uuid4().hex[:8]}"
    payload = b"%PDF-1.4 " + uuid.uuid4().hex.encode() * 8

    p.send(
        [TEST_EMAIL],
        subject,
        "See attached.",
        attachments=[
            OutgoingAttachment(
                filename="jaarrekening.pdf", content_type="application/pdf", content=payload
            )
        ],
    )

    inbox, _ = p.search("INBOX", limit=50)
    delivered = _find_by_subject(inbox, subject)
    assert delivered is not None, "message with attachment was not delivered"
    assert delivered.has_attachments

    detail = p.fetch_message("INBOX", delivered.uid)
    (att,) = detail.attachments
    assert att.filename == "jaarrekening.pdf"
    assert att.content_type == "application/pdf"
    assert p.fetch_attachment("INBOX", delivered.uid, 0).content == payload
    # The covering note is still readable text, not swallowed by the multipart.
    assert "See attached." in detail.body_text


def test_several_attachments_all_survive(provider):
    p, cfg = provider
    subject = f"E2E attach many {uuid.uuid4().hex[:8]}"
    files = [
        OutgoingAttachment("one.txt", "text/plain", b"first"),
        OutgoingAttachment("two.csv", "text/csv", b"a,b\n1,2\n"),
        OutgoingAttachment("three.bin", "application/octet-stream", bytes(range(256))),
    ]
    p.send([TEST_EMAIL], subject, "Three files.", attachments=files)

    inbox, _ = p.search("INBOX", limit=50)
    delivered = _find_by_subject(inbox, subject)
    assert delivered is not None

    detail = p.fetch_message("INBOX", delivered.uid)
    by_name = {a.filename: a for a in detail.attachments}
    assert set(by_name) == {"one.txt", "two.csv", "three.bin"}
    got = {
        a.filename: p.fetch_attachment("INBOX", delivered.uid, a.index).content
        for a in detail.attachments
    }
    assert got["three.bin"] == bytes(range(256)), "binary payload was mangled in transit"
    assert got["two.csv"] == b"a,b\n1,2\n"


def test_forwarding_re_attaches_without_touching_the_bytes(provider):
    """The cheap path end to end: read a file off one message and hang it on
    another, which is what `source_uid` does in the tool layer."""
    p, cfg = provider
    seeded = f"E2E attach source {uuid.uuid4().hex[:8]}"
    _seed_message(cfg.smtp_host, cfg.smtp_port, seeded, "Here it is.", attachment=True)

    inbox, _ = p.search("INBOX", limit=50)
    original = _find_by_subject(inbox, seeded)
    assert original is not None
    payload = p.fetch_attachment("INBOX", original.uid, 0)

    forwarded = f"E2E attach forward {uuid.uuid4().hex[:8]}"
    p.send(
        [TEST_EMAIL],
        forwarded,
        "Passing this on.",
        attachments=[
            OutgoingAttachment(payload.filename, payload.content_type, payload.content)
        ],
    )

    inbox, _ = p.search("INBOX", limit=50)
    delivered = _find_by_subject(inbox, forwarded)
    assert delivered is not None
    detail = p.fetch_message("INBOX", delivered.uid)
    assert detail.attachments[0].filename == "report.pdf"
    assert p.fetch_attachment("INBOX", delivered.uid, 0).content == payload.content


def test_a_draft_keeps_its_attachment_through_an_edit(provider):
    """Against a real server this time: the edit re-appends the message, so
    the file has to be read back off the old revision and written again."""
    p, cfg = provider
    subject = f"E2E draft attach {uuid.uuid4().hex[:8]}"
    payload = b"contract bytes " + uuid.uuid4().hex.encode()

    uid = p.save_draft(
        [TEST_EMAIL], subject, "First take", folder="Drafts",
        attachments=[OutgoingAttachment("contract.pdf", "application/pdf", payload)],
    )
    assert uid
    new_uid = p.update_draft("Drafts", uid, [TEST_EMAIL], subject, "Second take")

    detail = p.fetch_message("Drafts", new_uid)
    assert "Second take" in detail.body_text
    (att,) = detail.attachments
    assert att.filename == "contract.pdf"
    assert p.fetch_attachment("Drafts", new_uid, 0).content == payload


def test_an_inline_image_arrives_related_to_the_html_that_shows_it(provider):
    """The placement, proved against a real server rather than in a tree.

    Building the right structure and *delivering* it are different facts: the
    image has to reach the recipient inside multipart/related, beside the HTML,
    carrying the Content-ID the cid: URL names -- or they get a broken image
    and a stray paperclip. The plain-text half must survive too, since that is
    what a client without HTML shows.
    """
    p, cfg = provider
    subject = f"E2E inline {uuid.uuid4().hex[:8]}"
    p.send(
        [TEST_EMAIL], subject, "Plain text version.",
        body_html='<p>Logo: <img src="cid:logo-1"></p>',
        attachments=[
            OutgoingAttachment(
                filename="logo.png", content_type="image/png",
                content=b"\x89PNG-inline", inline=True, content_id="logo-1",
            ),
        ],
    )

    mb = MailBoxUnencrypted(cfg.imap_host, port=cfg.imap_port).login(TEST_LOGIN, TEST_PASSWORD)
    try:
        delivered = [m for m in mb.fetch(mark_seen=False) if (m.subject or "") == subject]
        assert delivered, "inline message was not delivered"
        raw = delivered[-1].obj  # the delivered message, already parsed

        related = None
        for part in raw.walk():
            if part.get_content_type() == "multipart/related":
                related = part
        assert related is not None, "no multipart/related in the delivered message"
        assert [q.get_content_type() for q in related.get_payload()] == [
            "text/html", "image/png",
        ]

        embedded = related.get_payload()[1]
        assert embedded["Content-ID"] == "<logo-1>"
        assert embedded.get_content_disposition() == "inline"
        assert embedded.get_payload(decode=True) == b"\x89PNG-inline"

        # Both bodies arrived: offering a rich one must not cost a plain-text
        # client the message.
        assert "Plain text version." in (delivered[-1].text or "")
    finally:
        mb.logout()


def test_a_name_with_awkward_spacing_is_found_by_its_words(provider):
    """The reported bug, against a real IMAP server.

    A signature reaches the mailbox as ``Iris  van 't Klooster`` -- two spaces,
    an apostrophe -- and the query is typed the way a person says it. The old
    behaviour sent that whole string as one ``TEXT`` key, which is a substring
    of the raw message and is simply not there. Each word as its own key is.
    """
    p, cfg = provider
    marker = uuid.uuid4().hex[:8]
    subject = f"E2E spacing {marker}"
    _seed_message(
        cfg.smtp_host,
        cfg.smtp_port,
        subject,
        f"Super!\r\nIris  van 't Klooster {marker}\r\nOperations manager\r\n",
    )

    found, _ = p.search("INBOX", f"Iris van 't Klooster {marker}", limit=50)
    assert _find_by_subject(found, subject) is not None

    # Out of order, and in the wrong case.
    reordered, _ = p.search("INBOX", f"{marker} KLOOSTER iris", limit=50)
    assert _find_by_subject(reordered, subject) is not None

    # What is deliberately NOT asserted here: that quoting the same string
    # fails. Whether a literal matches this body is the server's own business
    # -- RFC 3501 lets it match TEXT however it likes, and they differ -- so
    # pinning it would pin GreenMail rather than Squirrel. That the query
    # leaves as one key per word is pinned where it is decided, in
    # tests/test_mail_search.py.


def test_a_scoped_term_asks_the_server_about_that_header_only(provider):
    p, cfg = provider
    marker = uuid.uuid4().hex[:8]
    subject = f"E2E scoped {marker}"
    _seed_message(cfg.smtp_host, cfg.smtp_port, subject, f"body mentions {marker}")

    by_sender, _ = p.search("INBOX", f"from:sender@external.test {marker}", limit=50)
    assert _find_by_subject(by_sender, subject) is not None

    # The marker is in the body, not the From header, so scoping it there finds
    # nothing -- which is the whole point of being able to scope.
    wrong_field, _ = p.search("INBOX", f"from:{marker}", limit=50)
    assert _find_by_subject(wrong_field, subject) is None


def test_an_excluded_word_removes_the_message(provider):
    p, cfg = provider
    marker = uuid.uuid4().hex[:8]
    subject = f"E2E exclude {marker}"
    _seed_message(cfg.smtp_host, cfg.smtp_port, subject, f"{marker} unsubscribe here")

    kept, _ = p.search("INBOX", marker, limit=50)
    assert _find_by_subject(kept, subject) is not None

    excluded, _ = p.search("INBOX", f"{marker} -unsubscribe", limit=50)
    assert _find_by_subject(excluded, subject) is None
