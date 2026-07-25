"""Contacts (CardDAV) MCP tools -- contacts_* namespace.

Same shape as the calendar handler. Write tools require ``confirm=true``.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from ..config import SquirrelConfig
from ..error_handling import ValidationError
from ..providers import ContactsProvider
from ..schemas import (
    AddressBookList,
    AddressBookOut,
    ContactDetailOut,
    ContactList,
    ContactOut,
    ContactWriteResult,
)
from ._common import _current_sub, as_str_list, logger, require_confirm, run_blocking

_READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False)


class ContactsToolHandler:
    """Registers the contacts_* tools against a ContactsProvider."""

    def __init__(self, app: FastMCP, provider: ContactsProvider, config: SquirrelConfig):
        self.app = app
        self.provider = provider
        self.config = config
        self._register()

    async def _get_provider(self, *, writes: bool = False) -> Tuple[ContactsProvider, str]:
        if self.provider is None:
            raise ValidationError("No contacts provider available")
        _current_sub.set("stdio")
        return self.provider, "stdio"

    def _track_usage(self, sub: str, tool_name: str) -> None:
        """Overridable usage hook (no-op standalone)."""

    def _register(self):
        @self.app.tool(title="List Address Books", annotations=_READ)
        async def contacts_list_addressbooks() -> AddressBookList:
            """List address books. Use an id here as 'addressbook' elsewhere."""
            provider, sub = await self._get_provider()
            books = await run_blocking(provider, provider.list_addressbooks)
            self._track_usage(sub, "contacts_list_addressbooks")
            return AddressBookList(
                addressbooks=[AddressBookOut(id=b.id, name=b.name, count=b.count) for b in books]
            )

        @self.app.tool(title="Search Contacts", annotations=_READ)
        async def contacts_search(
            addressbook: str,
            query: Optional[str] = None,
            limit: Optional[int] = None,
            offset: int = 0,
        ) -> ContactList:
            """Search an address book (name/email/organization), A→Z, paginated."""
            provider, sub = await self._get_provider()
            eff_limit = max(1, min(limit or 50, 200))
            eff_offset = max(0, offset)
            contacts, total = await run_blocking(
                provider, provider.search_contacts, addressbook,
                query=query, limit=eff_limit, offset=eff_offset,
            )
            self._track_usage(sub, "contacts_search")
            return ContactList(
                contacts=[_contact_out(c) for c in contacts],
                total=total, limit=eff_limit, offset=eff_offset, addressbook=addressbook,
            )

        @self.app.tool(title="Read Contact", annotations=_READ)
        async def contacts_read(addressbook: str, uid: str) -> ContactDetailOut:
            """Read one contact by uid."""
            provider, sub = await self._get_provider()
            c = await run_blocking(provider, provider.get_contact, addressbook, uid)
            self._track_usage(sub, "contacts_read")
            return ContactDetailOut(
                uid=c.uid, addressbook=c.addressbook, full_name=c.full_name,
                emails=c.emails, phones=c.phones, organization=c.organization,
                title=c.title, note=c.note,
            )

        @self.app.tool(title="Create Contact", annotations=_WRITE)
        async def contacts_create(
            addressbook: str,
            full_name: str,
            emails: Optional[Any] = None,
            phones: Optional[Any] = None,
            organization: Optional[str] = None,
            confirm: bool = False,
        ) -> ContactWriteResult:
            """Create a contact. Requires confirm=true; confirm details first."""
            provider, sub = await self._get_provider(writes=True)
            require_confirm(confirm, "Creating a contact")
            uid = await run_blocking(
                provider, provider.create_contact, addressbook, full_name,
                emails=as_str_list(emails), phones=as_str_list(phones), organization=organization,
            )
            self._track_usage(sub, "contacts_create")
            return ContactWriteResult(uid=uid, addressbook=addressbook, status="Contact created")

        @self.app.tool(title="Update Contact", annotations=_WRITE)
        async def contacts_update(
            addressbook: str,
            uid: str,
            full_name: Optional[str] = None,
            emails: Optional[Any] = None,
            phones: Optional[Any] = None,
            organization: Optional[str] = None,
            confirm: bool = False,
        ) -> ContactWriteResult:
            """Update a contact. Requires confirm=true. Omitted fields are left unchanged;
            emails/phones replace the existing lists when provided."""
            provider, sub = await self._get_provider(writes=True)
            require_confirm(confirm, "Updating a contact")
            new_uid = await run_blocking(
                provider, provider.update_contact, addressbook, uid,
                full_name=full_name,
                emails=as_str_list(emails) if emails is not None else None,
                phones=as_str_list(phones) if phones is not None else None,
                organization=organization,
            )
            self._track_usage(sub, "contacts_update")
            return ContactWriteResult(uid=new_uid, addressbook=addressbook, status="Contact updated")

        @self.app.tool(title="Delete Contact", annotations=_WRITE)
        async def contacts_delete(
            addressbook: str, uid: str, confirm: bool = False
        ) -> ContactWriteResult:
            """Delete a contact. Requires confirm=true."""
            provider, sub = await self._get_provider(writes=True)
            require_confirm(confirm, "Deleting a contact")
            await run_blocking(provider, provider.delete_contact, addressbook, uid)
            self._track_usage(sub, "contacts_delete")
            return ContactWriteResult(uid=uid, addressbook=addressbook, status="Contact deleted")


def _contact_out(c) -> ContactOut:
    return ContactOut(
        uid=c.uid, addressbook=c.addressbook, full_name=c.full_name,
        emails=c.emails, phones=c.phones, organization=c.organization,
    )


def register_contacts_tools(
    app: FastMCP, provider: ContactsProvider, config: SquirrelConfig
) -> ContactsToolHandler:
    handler = ContactsToolHandler(app, provider, config)
    logger.info("Registered Squirrel contacts tools")
    return handler
