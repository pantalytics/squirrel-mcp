"""mail_search -- paginated search over one folder.

The grammar, the folding and the reasons live in ``squirrel_mcp.search_query``.
What is here is the policy on top of it, and it is deliberately not in any
backend: parse once, ask the provider, verify what a summary can prove, and --
only when that found nothing -- give up the weakest term and ask again, saying
so in the result. Both backends therefore widen the same way, in the same
order, and report it in the same field.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, Dict, List, Optional, Tuple

from mcp.types import ToolAnnotations

from ...schemas import MessageSummary, SearchResult
from ...search_query import MailQuery, parse, verify, widen
from .._common import logger, run_blocking

# How many times a search may widen before giving up. Each step is another
# round trip to the mailbox, and a query whose terms are simply not there
# should cost a few requests, not a crawl of the folder.
MAX_WIDENING_STEPS = 4


def _takes_parsed(search: Callable) -> bool:
    """Whether this provider's ``search`` reads the parsed query.

    Asked of the signature rather than of a capability property, because a
    provider written before ``parsed`` existed cannot declare anything and
    should not have to: the raw ``query`` is still authoritative and it will
    answer with it.

    The parameter has to be named. A ``**kwargs`` would *accept* it without
    raising and then ignore it, which is the one case that must not read as
    yes: the widening ladder weakens ``parsed`` while ``raw`` stays what the
    caller typed, so a backend going by ``raw`` alone would be asked the
    identical question once per rung.
    """
    try:
        return "parsed" in inspect.signature(search).parameters
    except (TypeError, ValueError):  # builtins, C callables, exotic mocks
        return False


class QueryToolsMixin:
    """Search a folder, newest first, with pagination for large mailboxes."""

    async def _search_pass(
        self,
        provider,
        folder: str,
        parsed: MailQuery,
        *,
        unseen_only: bool,
        flagged_only: bool,
        since: Optional[str],
        limit: int,
        offset: int,
    ) -> Tuple[List, int]:
        """One round trip, with the summaries the backend cannot prove removed.

        ``verify`` only ever drops a message whose *visible* fields refute the
        query, so ``total`` is corrected downwards by however many this page
        lost. It stays the server's count otherwise -- an estimate for a query
        reaching into bodies we never see, which is what it always was.
        """
        kwargs: Dict[str, Any] = dict(
            unseen_only=unseen_only,
            flagged_only=flagged_only,
            since=since,
            limit=limit,
            offset=offset,
        )
        if _takes_parsed(provider.search):
            # ``parsed`` is an addition to the protocol, so it is offered rather
            # than assumed: a backend from before it existed keeps its raw
            # ``query`` and answers exactly as it used to.
            kwargs["parsed"] = parsed
        messages, total = await run_blocking(
            provider, provider.search, folder, parsed.raw or None, **kwargs
        )
        kept = verify(parsed, messages)
        return kept, max(0, total - (len(messages) - len(kept)))

    def _register_query_tools(self):
        @self.app.tool(
            title="Search Mail",
            annotations=ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=True,
            ),
        )
        async def mail_search(
            query: Optional[str] = None,
            folder: str = "INBOX",
            limit: Optional[int] = None,
            offset: int = 0,
            unseen_only: bool = False,
            flagged_only: bool = False,
            since: Optional[str] = None,
            account: Optional[str] = None,
        ) -> SearchResult:
            """Search one folder, newest first.

            Args:
                query: What to look for. Words are matched independently and
                    ALL of them must appear somewhere in the message, in any
                    order -- so "iris klooster" finds a signature reading
                    "Iris  van 't Klooster". Supported syntax, the same as any
                    mail client:

                      from:iris          the sender only, not every mention
                      to: cc: subject: body:   the other fields
                      "fiscal localization"    those words together, in order
                      -newsletter        must NOT appear
                      invoice OR factuur  either one
                      has:attachment     only messages carrying a file

                    Prefer several plain words over one long phrase: a phrase
                    has to survive the exact spacing, punctuation and line
                    folding the mail was written with, and words do not.
                    Accents and apostrophes are ignored, so "jose" finds
                    "José". Omit the argument entirely to list the folder.
                folder: Folder to search (default "INBOX"). Get names from
                    mail_list_folders.
                limit: Page size. Defaults to the server default, capped at the max.
                offset: Number of messages to skip (for paging).
                unseen_only: If true, only unread messages -- what is left
                    after a mail_mark_read sweep, and what "clean up my inbox"
                    works through.
                flagged_only: If true, only messages carrying the \\Flagged
                    marker (set with mail_flag). This is the way to answer
                    "what have I flagged" -- the server does the filtering, so
                    do not page a whole folder and sift it yourself.
                since: ISO date (YYYY-MM-DD) lower bound on the message date.
                account: Which email account to search (id or address from
                    mail_list_accounts). Omit when only one is configured.

            If nothing matches every term, the search retries without the least
            distinctive ones rather than returning empty. Check ``matched``:
            "partial" means ``dropped_terms`` were ignored to get these results,
            and the user should be told which -- do not present them as exact.

            The mailbox can be large -- page with limit/offset rather than
            pulling everything. ``total`` tells you how many match in this
            folder.
            """
            provider, sub = await self._get_provider(account)
            default_limit = self.config.default_limit if self.config else 25
            max_limit = self.config.max_limit if self.config else 100
            eff_limit = default_limit if limit is None else limit
            eff_limit = max(1, min(eff_limit, max_limit))
            eff_offset = max(0, offset)

            parsed = parse(query)
            searched = parsed.describe()
            messages, total = await self._search_pass(
                provider,
                folder,
                parsed,
                unseen_only=unseen_only,
                flagged_only=flagged_only,
                since=since,
                limit=eff_limit,
                offset=eff_offset,
            )

            matched = "all"
            dropped: List[str] = []
            # Only widen for a query that actually asked for something and came
            # back with nothing. A later page being empty is the end of the
            # results, not a failed match, so paging never re-runs the ladder.
            #
            # And only against a backend that reads the parsed query: widening
            # weakens `parsed` while `raw` stays what the caller typed, so a
            # backend going by `raw` alone would be asked the identical
            # question several times over and answer nothing several times.
            can_widen = _takes_parsed(provider.search)
            if parsed and not messages and eff_offset == 0 and can_widen:
                for step, (candidate, given_up) in enumerate(widen(parsed)):
                    if step >= MAX_WIDENING_STEPS:
                        break
                    messages, total = await self._search_pass(
                        provider,
                        folder,
                        candidate,
                        unseen_only=unseen_only,
                        flagged_only=flagged_only,
                        since=since,
                        limit=eff_limit,
                        offset=eff_offset,
                    )
                    if messages:
                        logger.info(
                            "mail_search widened %r to %s (gave up %s)",
                            parsed.raw,
                            candidate.describe(),
                            given_up,
                        )
                        searched, dropped = candidate.describe(), given_up
                        break
                # Anything found from here on matched something less than what
                # was asked for, so it is reported as partial even when the
                # step was only loosening a phrase into its words.
                matched = "partial" if messages else "none"

            self._track_usage(sub, "mail_search")
            return SearchResult(
                messages=[
                    MessageSummary(
                        uid=m.uid,
                        folder=m.folder,
                        subject=m.subject,
                        from_addr=m.from_addr,
                        to_addrs=m.to_addrs,
                        date=m.date,
                        flags=m.flags,
                        size=m.size,
                        has_attachments=m.has_attachments,
                        preview=m.preview,
                    )
                    for m in messages
                ],
                total=total,
                limit=eff_limit,
                offset=eff_offset,
                folder=folder,
                matched=matched,
                searched_terms=searched,
                dropped_terms=dropped,
            )
