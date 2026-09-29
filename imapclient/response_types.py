# Copyright (c) 2014, Menno Smits
# Released subject to the New BSD License
# Please see http://en.wikipedia.org/wiki/BSD_licenses

import dataclasses
import datetime
from bisect import bisect_right
from email.utils import formataddr
from typing import Any, Iterable, Iterator, List, Optional, Tuple, TYPE_CHECKING, Union

from .typing_imapclient import _Atom
from .util import to_unicode


@dataclasses.dataclass
class Envelope:
    r"""Represents envelope structures of messages. Returned when parsing
    ENVELOPE responses.

    :ivar date: A datetime instance that represents the "Date" header.
    :ivar subject: A string that contains the "Subject" header.
    :ivar from\_: A tuple of Address objects that represent one or more
      addresses from the "From" header, or None if header does not exist.
    :ivar sender: As for from\_ but represents the "Sender" header.
    :ivar reply_to: As for from\_ but represents the "Reply-To" header.
    :ivar to: As for from\_ but represents the "To" header.
    :ivar cc: As for from\_ but represents the "Cc" header.
    :ivar bcc: As for from\_ but represents the "Bcc" recipients.
    :ivar in_reply_to: A string that contains the "In-Reply-To" header.
    :ivar message_id: A string that contains the "Message-Id" header.

    A particular issue to watch out for is IMAP's handling of "group
    syntax" in address fields. This is often encountered as a
    recipient header of the form::

        undisclosed-recipients:;

    but can also be expressed per this more general example::

        A group: a@example.com, B <b@example.org>;

    This example would yield the following Address tuples::

      Address(name=None, route=None, mailbox=u'A group', host=None)
      Address(name=None, route=None, mailbox=u'a', host=u'example.com')
      Address(name=u'B', route=None, mailbox=u'b', host=u'example.org')
      Address(name=None, route=None, mailbox=None, host=None)

    The first Address, where ``host`` is ``None``, indicates the start
    of the group. The ``mailbox`` field contains the group name. The
    final Address, where both ``mailbox`` and ``host`` are ``None``,
    indicates the end of the group.

    See :rfc:`3501#section-7.4.2` and :rfc:`2822` for further details.

    """

    date: Optional[datetime.datetime]
    subject: bytes
    from_: Optional[Tuple["Address", ...]]
    sender: Optional[Tuple["Address", ...]]
    reply_to: Optional[Tuple["Address", ...]]
    to: Optional[Tuple["Address", ...]]
    cc: Optional[Tuple["Address", ...]]
    bcc: Optional[Tuple["Address", ...]]
    in_reply_to: bytes
    message_id: bytes


@dataclasses.dataclass
class Address:
    """Represents electronic mail addresses. Used to store addresses in
    :py:class:`Envelope`.

    :ivar name: The address "personal name".
    :ivar route: SMTP source route (rarely used).
    :ivar mailbox: Mailbox name (what comes just before the @ sign).
    :ivar host: The host/domain name.

    As an example, an address header that looks like::

        Mary Smith <mary@foo.com>

    would be represented as::

        Address(name=u'Mary Smith', route=None, mailbox=u'mary', host=u'foo.com')

    See :rfc:`2822` for more detail.

    See also :py:class:`Envelope` for information about handling of
    "group syntax".
    """

    name: bytes
    route: bytes
    mailbox: bytes
    host: bytes

    def __str__(self) -> str:
        if self.mailbox and self.host:
            address = to_unicode(self.mailbox) + "@" + to_unicode(self.host)
        else:
            address = to_unicode(self.mailbox or self.host)

        return formataddr((to_unicode(self.name), address))


class SearchIds(List[int]):
    """
    Contains a list of message ids as returned by IMAPClient.search().

    The *modseq* attribute will contain the MODSEQ value returned by
    the server (only if the SEARCH command sent involved the MODSEQ
    criteria). See :rfc:`4551` for more details.
    """

    def __init__(self, *args: Any):
        super().__init__(*args)
        self.modseq: Optional[int] = None


class UidRanges:
    """A set of message UIDs stored as sorted, non-overlapping,
    inclusive ``(start, end)`` ranges.

    This is used for the ``ALL`` return data item of
    :py:meth:`imapclient.IMAPClient.esearch` so that search results
    containing millions of UIDs don't have to be expanded into
    individual integers.

    Ranges given to the constructor may be unsorted, overlapping or
    reversed (eg. ``(8, 4)``); they are normalised and merged on
    construction.

    Membership tests (``uid in ranges``), ``len()``, integer indexing
    and slicing are all performed arithmetically on the ranges and
    never expand the set. Slices with a step of 1 (the default) return
    another (lazy) :class:`UidRanges`; stepped slices return a list
    containing only the selected UIDs.

    Only iteration walks every UID. This includes ``for`` loops,
    ``list()``, ``tuple()``, ``set()`` and anything else that consumes
    the iterator. Avoid these on very large results.
    """

    __slots__ = ("_ranges", "_starts")

    def __init__(self, ranges: Iterable[Tuple[int, int]] = ()) -> None:
        normalised = []
        for start, end in ranges:
            start, end = int(start), int(end)
            if start > end:
                start, end = end, start
            if start < 1:
                raise ValueError("UIDs must be positive integers")
            normalised.append((start, end))
        normalised.sort()
        merged: List[Tuple[int, int]] = []
        for start, end in normalised:
            if merged and start <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        self._ranges = tuple(merged)
        self._starts = tuple(start for start, _ in merged)

    @property
    def ranges(self) -> Tuple[Tuple[int, int], ...]:
        """The stored ranges as a tuple of inclusive ``(start, end)`` pairs."""
        return self._ranges

    def __len__(self) -> int:
        return sum(end - start + 1 for start, end in self._ranges)

    def __bool__(self) -> bool:
        return bool(self._ranges)

    def __contains__(self, uid: object) -> bool:
        if not isinstance(uid, int):
            return False
        idx = bisect_right(self._starts, uid)
        if idx == 0:
            return False
        start, end = self._ranges[idx - 1]
        return start <= uid <= end

    def _uid_at(self, pos: int) -> int:
        for start, end in self._ranges:
            range_len = end - start + 1
            if pos < range_len:
                return start + pos
            pos -= range_len
        raise IndexError("UidRanges index out of range")

    def __getitem__(
        self, item: Union[int, slice]
    ) -> Union[int, List[int], "UidRanges"]:
        if isinstance(item, slice):
            start, stop, step = item.indices(len(self))
            if step != 1:
                return [self._uid_at(pos) for pos in range(start, stop, step)]
            return self._clip(start, stop)
        pos = item
        if pos < 0:
            pos += len(self)
        if pos < 0:
            raise IndexError("UidRanges index out of range")
        return self._uid_at(pos)

    def _clip(self, lo: int, hi: int) -> "UidRanges":
        """Return the UIDs at positions in ``[lo, hi)`` as a new UidRanges."""
        clipped = []
        pos = 0
        for start, end in self._ranges:
            range_lo = pos
            pos += end - start + 1
            overlap_lo, overlap_hi = max(lo, range_lo), min(hi, pos)
            if overlap_lo < overlap_hi:
                clipped.append(
                    (start + overlap_lo - range_lo, start + overlap_hi - range_lo - 1)
                )
        return UidRanges(clipped)

    def __iter__(self) -> Iterator[int]:
        for start, end in self._ranges:
            yield from range(start, end + 1)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, UidRanges):
            return NotImplemented
        return self._ranges == other._ranges

    def __ne__(self, other: object) -> bool:
        eq = self.__eq__(other)
        return eq if eq is NotImplemented else not eq

    def __hash__(self) -> int:
        return hash(self._ranges)

    def __repr__(self) -> str:
        return "%s(%r)" % (type(self).__name__, list(self._ranges))


_BodyDataType = Tuple[Union[bytes, int, "BodyData"], "_BodyDataType"]


class BodyData(_BodyDataType):
    """
    Returned when parsing BODY and BODYSTRUCTURE responses.
    """

    @classmethod
    def create(cls, response: Tuple[_Atom, ...]) -> "BodyData":
        # In case of multipart messages we will see at least 2 tuples
        # at the start. Nest these in to a list so that the returned
        # response tuple always has a consistent number of elements
        # regardless of whether the message is multipart or not.
        if isinstance(response[0], tuple):
            # Multipart, find where the message part tuples stop
            parts = []
            for i, part in enumerate(response):
                if isinstance(part, bytes):
                    break
                if TYPE_CHECKING:
                    assert isinstance(part, tuple)
                parts.append(part)
            # The runtime representation of BodyData mixes converted
            # BodyData/list items with raw, not-yet-converted response
            # atoms. _BodyDataType can't precisely express this shape, so
            # the construction below can't be typed exactly.
            body_parts = ([cls.create(part) for part in parts],) + response[i:]
            return cls(body_parts)  # type: ignore[arg-type]
        return cls(response)  # type: ignore[arg-type]

    @property
    def is_multipart(self) -> bool:
        return isinstance(self[0], list)
