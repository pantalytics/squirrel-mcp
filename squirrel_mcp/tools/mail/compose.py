"""mail_create_draft, mail_edit_draft, mail_send, mail_send_draft.

Replying: three of these take a ``reply_to_uid``. It does two separate jobs,
and they live in different places on purpose.

* The *visible* half is here: the recipients and the subject a reply should
  have are derived from the message being answered, so a client that only
  knows "reply to this" cannot get them subtly wrong (a missing "Re:", the
  Reply-To ignored, the wrong participant dropped on a reply-all).
* The *threading* half is the provider's: ``reply_to_uid`` travels down to it
  untouched, and each backend threads the way its transport threads -- headers
  for IMAP/SMTP, a conversation for an API backend. The tool layer neither
  knows nor cares which.

``mail_send_draft`` is the same split read from the other end. It takes a uid
and *nothing else*: a draft is already a complete message, so re-reading its
fields here and handing them to ``send`` would put a second, subtly different
message on the wire -- a new Message-ID, a rebuilt MIME tree, and whatever the
draft carried that these arguments do not model (an embedded image's
``multipart/related``, the threading that makes it a reply) quietly gone. The
user approved what they read in the draft; that is what leaves.
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

from mcp.types import ToolAnnotations

from ...error_handling import ValidationError
from ...schemas import DraftResult, SendResult
from .._common import (
    as_bodies,
    as_str_list,
    bare_addresses,
    reply_subject,
    require_confirm,
    run_blocking,
)
from .attachments import resolve_attachments


async def _resolve_reply(
    provider: Any,
    reply_to_uid: str,
    reply_to_folder: str,
    *,
    to: List[str],
    cc: List[str],
    subject: Optional[str],
    reply_all: bool,
) -> Tuple[List[str], List[str], str, Optional[str]]:
    """Fill in what a reply inherits from the message it answers.

    Returns ``(to, cc, subject, in_reply_to)``. Anything the caller passed
    explicitly wins -- this only fills blanks -- except ``reply_all``, which
    always adds the remaining participants to cc.
    """
    original = await run_blocking(provider, provider.fetch_message, reply_to_folder, reply_to_uid)
    me = (getattr(provider, "email", "") or "").strip().lower()

    if not to:
        # Reply-To is what the sender asked replies to go to; From is only the
        # fallback. Ignoring it sends a mailing-list reply to the wrong place.
        to = bare_addresses(getattr(original, "reply_to_addrs", None) or [original.from_addr])

    if reply_all:
        addressed = {a.lower() for a in to}
        for addr in bare_addresses(list(original.to_addrs) + list(original.cc_addrs)):
            lowered = addr.lower()
            # Never cc the mailbox that is doing the replying, and never list
            # someone twice across to/cc.
            if lowered != me and lowered not in addressed and lowered not in {c.lower() for c in cc}:
                cc.append(addr)

    return to, cc, subject or reply_subject(original.subject), original.message_id


class ComposeToolsMixin:
    """Create/update drafts and send mail (send requires confirmation)."""

    def _register_compose_tools(self):
        @self.app.tool(
            title="Draft Mail",
            annotations=ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=False,
                idempotentHint=False,
                openWorldHint=True,
            ),
        )
        async def mail_create_draft(
            to: Optional[Any] = None,
            subject: Optional[str] = None,
            body: Optional[str] = None,
            cc: Optional[Any] = None,
            bcc: Optional[Any] = None,
            folder: str = "Drafts",
            reply_to_uid: Optional[str] = None,
            reply_to_folder: str = "INBOX",
            reply_all: bool = False,
            attachments: Optional[Any] = None,
            body_format: str = "text",
            account: Optional[str] = None,
        ) -> DraftResult:
            """Save a new draft to the Drafts folder (nothing is sent).

            ``to``/``cc``/``bcc`` accept a list of addresses or a comma-separated
            string. Prefer drafting and letting the user review over sending directly.
            ``account`` picks which configured email account the draft belongs to
            (id or address from mail_list_accounts); omit with a single account.

            To draft a REPLY, pass ``reply_to_uid`` (the uid of the message being
            answered, from mail_search/mail_read) and ``reply_to_folder``. The
            draft then threads onto that message, and ``to`` and ``subject`` may
            be omitted -- they are taken from the original. ``reply_all=true``
            also cc's the other participants.

            ATTACHMENTS: a list of objects, each either
            {"source_uid": "412", "source_index": 0, "source_folder": "INBOX"}
            to re-send a file already in the mailbox (indexes come from
            mail_read) or {"filename": "x.pdf", "content_base64": "..."} for
            bytes you supply. ALWAYS prefer the first form when forwarding
            something the user received -- it copies nothing through you.

            FORMAT: ``body`` is plain text unless ``body_format="html"``, in
            which case ``body`` *is* the HTML and the plain-text half of the
            message is derived from it -- so you write the message once, in
            whichever form suits it. Default to text; reach for html when the
            content needs it (a link the user should see as a link, a list, an
            embedded image), not to decorate a message the user asked to keep
            plain.

            EMBEDDING AN IMAGE: needs ``body_format="html"``. Mark the
            attachment ``"inline": true`` with a ``"content_id"`` and refer to
            it from the HTML as ``<img src="cid:that-id">``. With a plain-text
            body an inline file simply arrives as an ordinary attachment.
            """
            provider, sub = await self._get_provider(account, writes=True)
            if body is None:
                raise ValidationError("'body' is required")
            body, html_body = as_bodies(body, body_format)
            files = await resolve_attachments(
                provider, attachments, default_folder=reply_to_folder
            )
            recipients = as_str_list(to)
            cc_list = as_str_list(cc)
            in_reply_to = None
            if reply_to_uid:
                recipients, cc_list, subject, in_reply_to = await _resolve_reply(
                    provider,
                    reply_to_uid,
                    reply_to_folder,
                    to=recipients,
                    cc=cc_list,
                    subject=subject,
                    reply_all=reply_all,
                )
            if not recipients:
                raise ValidationError("'to' is required (at least one recipient)")
            if subject is None:
                raise ValidationError("'subject' is required")
            uid = await run_blocking(
                provider,
                provider.save_draft,
                recipients,
                subject,
                body,
                cc=cc_list,
                bcc=as_str_list(bcc),
                folder=folder,
                reply_to_uid=reply_to_uid,
                reply_to_folder=reply_to_folder,
                attachments=files,
                body_html=html_body,
            )
            self._track_usage(sub, "mail_create_draft")
            return DraftResult(
                uid=uid,
                folder=folder,
                status="Reply draft saved" if reply_to_uid else "Draft saved",
                from_address=getattr(provider, "email", None) or None,
                subject=subject,
                recipients=recipients,
                in_reply_to=in_reply_to,
                attachments=[a.filename for a in files],
            )

        @self.app.tool(
            title="Edit Draft",
            annotations=ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=False,
                idempotentHint=False,
                openWorldHint=True,
            ),
        )
        async def mail_edit_draft(
            uid: str,
            to: Any,
            subject: str,
            body: str,
            cc: Optional[Any] = None,
            bcc: Optional[Any] = None,
            folder: str = "Drafts",
            attachments: Optional[Any] = None,
            body_format: str = "text",
            account: Optional[str] = None,
        ) -> DraftResult:
            """Replace an existing draft with new content. Returns the new uid.

            (The draft is rewritten, so the uid changes -- use the returned one.)
            Pass the same ``account`` the draft was created in. A draft created
            as a reply stays in its thread; to turn an ordinary draft into a
            reply, save a new one with mail_create_draft's ``reply_to_uid``.

            ATTACHMENTS: omit the argument and whatever the draft already
            carries is kept -- fixing a typo does not drop the file. Pass a list
            (same shape as mail_create_draft) to replace them, or [] to strip them.
            ``body_format`` is rewritten like the rest of the draft, so
            re-pass ``"html"`` when editing a message that had an HTML body.
            """
            provider, sub = await self._get_provider(account, writes=True)
            recipients = as_str_list(to)
            if not recipients:
                raise ValidationError("'to' is required (at least one recipient)")
            body, html_body = as_bodies(body, body_format)
            # None and [] mean different things here, so the resolve is
            # conditional: omitted keeps the draft's own files, [] clears them.
            files = (
                None
                if attachments is None
                else await resolve_attachments(provider, attachments, default_folder=folder)
            )
            new_uid = await run_blocking(
                provider,
                provider.update_draft,
                folder,
                uid,
                recipients,
                subject,
                body,
                cc=as_str_list(cc),
                bcc=as_str_list(bcc),
                attachments=files,
                body_html=html_body,
            )
            self._track_usage(sub, "mail_edit_draft")
            return DraftResult(
                uid=new_uid,
                folder=folder,
                status="Draft updated",
                from_address=getattr(provider, "email", None) or None,
                subject=subject,
                recipients=recipients,
                # None here means the draft kept its own files, which is not
                # the same fact as "it has none" -- say so rather than imply it.
                attachments=[a.filename for a in files] if files is not None else None,
            )

        @self.app.tool(
            title="Send Draft",
            annotations=ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=True,
                idempotentHint=False,
                openWorldHint=True,
            ),
        )
        async def mail_send_draft(
            uid: str,
            folder: str = "Drafts",
            confirm: bool = False,
            account: Optional[str] = None,
        ) -> SendResult:
            """Send a draft that already exists. OUTGOING -- requires confirm=true.

            Use this for a message the user has been reviewing: it sends the
            draft EXACTLY as it stands -- its recipients, its attachments, its
            embedded images, its place in a thread -- and then removes it from
            ``folder``. Do NOT re-send the same text through mail_send instead:
            that composes a second message and leaves the draft behind.

            ``uid`` is the draft's uid, as returned by mail_create_draft /
            mail_edit_draft or found with mail_search in the Drafts folder.
            Pass the same ``account`` the draft lives in.

            Read the draft with mail_read first and show the user its
            recipients, subject, body and attachments -- there are no arguments
            here to show them instead -- then call again with confirm=true.

            THE COPY IN SENT: ``saved_to_sent`` says whether the message was
            also filed in the account's Sent folder (``sent_folder`` names it).
            ``draft_removed`` says whether the draft is gone. False for either
            means the message DID go out -- say so and do NOT send again.
            """
            provider, sub = await self._get_provider(account, writes=True)
            send_draft = getattr(provider, "send_draft", None)
            if send_draft is None:
                # A backend that silently did nothing here would leave the user
                # believing a reviewed message had been sent, so say what will
                # work instead rather than pretending.
                raise ValidationError(
                    "This account's mail backend cannot send an existing draft. "
                    "Send the message with mail_send and delete the draft, or "
                    "use an IMAP/SMTP account."
                )
            require_confirm(confirm, "Sending a draft")
            result = await run_blocking(provider, send_draft, folder, uid)
            self._track_usage(sub, "mail_send_draft")
            return SendResult(
                status="Draft sent",
                message_id=result.get("message_id"),
                recipients=result.get("recipients", []),
                from_address=getattr(provider, "email", None) or None,
                subject=result.get("subject"),
                attachments=result.get("attachments", []),
                saved_to_sent=result.get("saved_to_sent"),
                sent_folder=result.get("sent_folder"),
                draft_removed=result.get("draft_removed"),
            )

        @self.app.tool(
            title="Send Mail",
            annotations=ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=True,
                idempotentHint=False,
                openWorldHint=True,
            ),
        )
        async def mail_send(
            to: Optional[Any] = None,
            subject: Optional[str] = None,
            body: Optional[str] = None,
            cc: Optional[Any] = None,
            bcc: Optional[Any] = None,
            reply_to_uid: Optional[str] = None,
            reply_to_folder: str = "INBOX",
            reply_all: bool = False,
            attachments: Optional[Any] = None,
            body_format: str = "text",
            confirm: bool = False,
            account: Optional[str] = None,
        ) -> SendResult:
            """Send a message. OUTGOING -- requires confirm=true.

            Before calling with confirm=true, show the user the exact recipients,
            subject and body and get explicit approval. Without confirm=true this
            refuses and sends nothing. ``account`` picks which configured email
            account to send from (id or address from mail_list_accounts) -- name
            it to the user as part of the approval; omit with a single account.

            ANSWERING A MESSAGE: pass ``reply_to_uid`` (its uid, from
            mail_search/mail_read) and the ``reply_to_folder`` it lives in.
            Without it the message goes out as a NEW conversation even if the
            subject starts with "Re:" -- some clients will guess it back into
            the thread, most will not. With it, ``to`` and ``subject`` may be
            omitted and are taken from the original (Reply-To if the sender set
            one, otherwise From; subject prefixed "Re:"), and ``reply_all=true``
            cc's the other participants. The result's ``in_reply_to`` is the
            message this answered -- tell the user it went out as a reply.

            ATTACHMENTS: a list of objects, each either
            {"source_uid": "412", "source_index": 0, "source_folder": "INBOX"}
            to send on a file already in the mailbox (indexes come from
            mail_read) or {"filename": "x.pdf", "content_base64": "..."} for
            bytes you supply. Prefer the first when forwarding something the
            user received. Name every attachment in the approval you ask for --
            a file leaving the mailbox is as much a decision as the recipient.

            FORMAT: ``body`` is plain text unless ``body_format="html"``, in
            which case ``body`` *is* the HTML and the plain-text half of the
            message is derived from it -- so you write the message once, in
            whichever form suits it. Default to text; reach for html when the
            content needs it (a link the user should see as a link, a list, an
            embedded image), not to decorate a message the user asked to keep
            plain.

            EMBEDDING AN IMAGE: needs ``body_format="html"``. Mark the
            attachment ``"inline": true`` with a ``"content_id"`` and refer to
            it from the HTML as ``<img src="cid:that-id">``. With a plain-text
            body an inline file simply arrives as an ordinary attachment.

            THE COPY IN SENT: ``saved_to_sent`` says whether the message was
            also filed in the account's Sent folder (``sent_folder`` names it).
            False means it went out but the copy failed -- tell the user it was
            sent and that they will not find it in Sent, and do NOT send again.
            """
            provider, sub = await self._get_provider(account, writes=True)
            if body is None:
                raise ValidationError("'body' is required")
            body, html_body = as_bodies(body, body_format)
            # Resolved before the confirm gate: a wrong uid or bad base64 must
            # fail as a validation error while nothing has been sent, not after.
            files = await resolve_attachments(
                provider, attachments, default_folder=reply_to_folder
            )
            recipients = as_str_list(to)
            cc_list = as_str_list(cc)
            in_reply_to = None
            if reply_to_uid:
                recipients, cc_list, subject, in_reply_to = await _resolve_reply(
                    provider,
                    reply_to_uid,
                    reply_to_folder,
                    to=recipients,
                    cc=cc_list,
                    subject=subject,
                    reply_all=reply_all,
                )
            if not recipients:
                raise ValidationError("'to' is required (at least one recipient)")
            if subject is None:
                raise ValidationError("'subject' is required")
            require_confirm(confirm, "Sending mail")
            result = await run_blocking(
                provider,
                provider.send,
                recipients,
                subject,
                body,
                cc=cc_list,
                bcc=as_str_list(bcc),
                reply_to_uid=reply_to_uid,
                reply_to_folder=reply_to_folder,
                attachments=files,
                body_html=html_body,
            )
            self._track_usage(sub, "mail_send")
            return SendResult(
                status="Sent as a reply" if reply_to_uid else "Sent",
                message_id=result.get("message_id"),
                recipients=result.get("recipients", []),
                from_address=getattr(provider, "email", None) or None,
                subject=subject,
                in_reply_to=in_reply_to,
                attachments=[a.filename for a in files],
                # Whether the copy landed in Sent is the backend's to report --
                # it is the only thing that knows whether its transport files
                # one itself. A reply travels this same path, so it is filed
                # exactly like any other message.
                saved_to_sent=result.get("saved_to_sent"),
                sent_folder=result.get("sent_folder"),
            )
