"""HTML -> plain text, used in both directions of the mail path.

*Reading*: a message whose only body is ``text/html`` still has to be readable,
so the markup is stripped down to text rather than handed on as tags.

*Writing*: ``body_format="html"`` composes the plain-text alternative out of
the same HTML. That is what lets a caller choose **one** format and pass
**one** body: the two halves of a ``multipart/alternative`` say the same thing
by construction, instead of by the caller writing the message twice and the
plain-text half quietly drifting.

Deliberately not a renderer. Block elements become line breaks and list items
become dashes, because a mail flattened into one paragraph is unreadable; a
table stays a table's worth of words, and CSS is dropped on the floor.
"""

from __future__ import annotations

import html as _html
import re

# script/style/head carry text that is not content -- stripped whole, because
# removing only the tags would leave the CSS in the message body.
_DROP_RE = re.compile(r"(?is)<(script|style|head)\b[^>]*>.*?</\1>")
_ITEM_RE = re.compile(r"(?i)<li\b[^>]*>")
_BREAK_RE = re.compile(
    r"(?i)</?(br|p|div|tr|ul|ol|h[1-6]|blockquote|table|section|article)\b[^>]*>"
)
_TAG_RE = re.compile(r"<[^>]+>")


def html_to_text(html: str) -> str:
    """Flatten an HTML body to the text a plain-text client should show."""
    text = _DROP_RE.sub(" ", html)
    text = _ITEM_RE.sub("\n- ", text)
    text = _BREAK_RE.sub("\n", text)
    # Remaining tags are inline (<b>, <a>, <span>): replaced by a space rather
    # than removed, so "<td>a</td><td>b</td>" does not read as one word.
    text = _TAG_RE.sub(" ", text)
    text = _html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
