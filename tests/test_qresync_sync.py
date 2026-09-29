# Copyright (c) 2025, Menno Smits
# Released subject to the New BSD License
# Please see http://en.wikipedia.org/wiki/BSD_licenses

import itertools
import unittest

from imapclient import exceptions
from imapclient.imapclient import (
    FolderSyncResult,
    SyncCheckpoint,
    _compress_uid_set,
    _parse_vanished_uids,
)
from imapclient.testable_imapclient import TestableIMAPClient

QRESYNC_CAPS = (b"IMAP4REV1", b"ENABLE", b"CONDSTORE", b"QRESYNC")


class FakeQresyncIMAP4:
    """IMAP4 protocol stub simulating a QRESYNC-capable server.

    The stub responds to the commands sent by IMAPClient with
    scripted untagged and tagged responses, mimicking the imaplib
    interface used by IMAPClient.
    """

    def __init__(self):
        self.use_uid = True
        self.state = "AUTH"
        self.sent = b""
        self.untagged_responses = {}
        self._tag_counter = itertools.count(1)
        # Scripted server-side state, configured by each test.
        self.uidvalidity = 777
        self.highestmodseq = 106
        self.select_fetch_lines = []
        self.select_vanished_lines = []
        self.search_uids = []
        self.fetch_lines = []
        self.fail_commands = set()

    # imaplib interface ------------------------------------------------
    def send(self, data):
        self.sent += data

    def _new_tag(self):
        return b"A%d" % next(self._tag_counter)

    def _command(self, name, *args):
        return self._new_tag()

    def _command_complete(self, name, tag):
        if name in self.fail_commands:
            return "NO", [name.encode("ascii") + b" failed"]
        handler = getattr(self, "_do_" + name.lower())
        return handler()

    def _untagged_response(self, typ, data, name):
        if typ == "OK":
            return typ, self.untagged_responses.pop(name, [None])
        return typ, data

    def select(self, mailbox, readonly=False):
        self.untagged_responses = {}
        if "SELECT" in self.fail_commands:
            return "NO", [b"SELECT failed"]
        typ, data = self._do_select()
        if typ == "OK":
            self.state = "SELECTED"
        return typ, data

    # Scripted command behaviour ---------------------------------------
    def _do_enable(self):
        self.untagged_responses["ENABLED"] = [b"QRESYNC"]
        return "OK", [b"ENABLE completed"]

    def _do_select(self):
        self.untagged_responses.update(
            {
                "FLAGS": [rb"(\Answered \Seen \Deleted)"],
                "EXISTS": [b"3"],
                "RECENT": [b"0"],
                "UIDVALIDITY": [str(self.uidvalidity).encode("ascii")],
                "HIGHESTMODSEQ": [str(self.highestmodseq).encode("ascii")],
                "READ-WRITE": [b""],
            }
        )
        if self.select_fetch_lines:
            self.untagged_responses["FETCH"] = list(self.select_fetch_lines)
        if self.select_vanished_lines:
            self.untagged_responses["VANISHED"] = list(self.select_vanished_lines)
        return "OK", [b"SELECT completed"]

    def _do_search(self):
        self.untagged_responses["SEARCH"] = [
            b" ".join(str(uid).encode("ascii") for uid in self.search_uids)
        ]
        return "OK", [b"SEARCH completed"]

    def _do_fetch(self):
        self.untagged_responses["FETCH"] = list(self.fetch_lines)
        return "OK", [b"FETCH completed"]


def make_client(stub, capabilities=QRESYNC_CAPS):
    client = TestableIMAPClient()
    client._imap = stub
    client._cached_capabilities = capabilities
    return client


class TestQresyncSync(unittest.TestCase):
    def setUp(self):
        self.stub = FakeQresyncIMAP4()
        self.client = make_client(self.stub)

    def test_incremental_sync(self):
        # While the client was offline, UIDs 2 and 4 were expunged and
        # UID 3 changed flags; UID 6 is a new message.
        self.stub.select_fetch_lines = [
            b"1 (UID 3 FLAGS (\\Seen) MODSEQ (105))",
            b"2 (UID 6 FLAGS () MODSEQ (106))",
        ]
        self.stub.select_vanished_lines = [b"(EARLIER) 2,4"]

        result = self.client.sync_folder("INBOX", 777, 100, {1, 2, 3, 4, 5})

        self.assertIsInstance(result, FolderSyncResult)
        self.assertFalse(result.full_sync)
        self.assertEqual(result.vanished, frozenset({2, 4}))
        self.assertEqual(set(result.changed), {3, 6})
        self.assertEqual(result.changed[3][b"FLAGS"], (b"\\Seen",))
        self.assertEqual(result.changed[3][b"MODSEQ"], (105,))
        self.assertEqual(result.changed[6][b"MODSEQ"], (106,))
        self.assertEqual(
            result.checkpoint, SyncCheckpoint(uidvalidity=777, highestmodseq=106)
        )
        # QRESYNC was enabled and the checkpoint sent with SELECT.
        self.assertIn(b"ENABLE QRESYNC", self.stub.sent)
        self.assertIn(b'SELECT "INBOX" (QRESYNC (777 100 1:5))', self.stub.sent)
        self.assertEqual(self.stub.state, "SELECTED")

    def test_unsolicited_fetch_and_vanished_are_collected(self):
        # Unsolicited responses triggered by other clients (not by the
        # sync request itself) must be collected too.
        self.stub.select_fetch_lines = [
            b"1 (UID 3 FLAGS (\\Seen) MODSEQ (105))",
            b"5 (UID 8 FLAGS (\\Deleted) MODSEQ (106))",
        ]
        self.stub.select_vanished_lines = [b"(EARLIER) 2", b"9"]

        result = self.client.sync_folder("INBOX", 777, 100, {1, 2, 3})

        self.assertFalse(result.full_sync)
        self.assertEqual(result.vanished, frozenset({2, 9}))
        self.assertEqual(set(result.changed), {3, 8})
        self.assertEqual(result.changed[8][b"FLAGS"], (b"\\Deleted",))

    def test_uidvalidity_change_invalidates_known_uids(self):
        # The folder was recreated while the client was offline.
        self.stub.uidvalidity = 999
        self.stub.search_uids = [10, 11]
        self.stub.fetch_lines = [
            b"1 (UID 10 FLAGS ())",
            b"2 (UID 11 FLAGS (\\Seen))",
        ]

        result = self.client.sync_folder("INBOX", 777, 100, {1, 2, 3})

        self.assertTrue(result.full_sync)
        # Old UIDs are all invalid so nothing can be reported vanished.
        self.assertEqual(result.vanished, frozenset())
        self.assertEqual(set(result.changed), {10, 11})
        self.assertEqual(
            result.checkpoint, SyncCheckpoint(uidvalidity=999, highestmodseq=106)
        )

    def test_full_sync_fallback_without_qresync(self):
        client = make_client(self.stub, capabilities=(b"IMAP4REV1", b"CONDSTORE"))
        self.stub.search_uids = [1, 3]
        self.stub.fetch_lines = [
            b"1 (UID 1 FLAGS (\\Seen))",
            b"2 (UID 3 FLAGS ())",
        ]

        result = client.sync_folder("INBOX", 777, 100, {1, 2, 3})

        self.assertTrue(result.full_sync)
        self.assertEqual(result.vanished, frozenset({2}))
        self.assertEqual(set(result.changed), {1, 3})
        self.assertEqual(
            result.checkpoint, SyncCheckpoint(uidvalidity=777, highestmodseq=106)
        )
        # No QRESYNC negotiation should have been attempted.
        self.assertNotIn(b"QRESYNC", self.stub.sent)

    def test_failed_select_yields_no_checkpoint(self):
        self.stub.fail_commands.add("SELECT")

        with self.assertRaises(exceptions.IMAPClientError):
            self.client.sync_folder("INBOX", 777, 100, {1, 2, 3})

        self.assertEqual(self.stub.state, "AUTH")

    def test_failed_full_sync_yields_no_checkpoint(self):
        # The sync fails part way through (after SELECT and SEARCH);
        # the error must propagate so the caller keeps its old
        # checkpoint.
        client = make_client(self.stub, capabilities=(b"IMAP4REV1",))
        self.stub.search_uids = [1, 2]
        self.stub.fail_commands.add("FETCH")

        with self.assertRaises(exceptions.IMAPClientError):
            client.sync_folder("INBOX", 777, 100, {1, 2, 3})

    def test_requires_use_uid(self):
        self.client.use_uid = False
        with self.assertRaises(exceptions.IllegalStateError):
            self.client.sync_folder("INBOX", 777, 100, {1, 2, 3})


class TestUidSetHelpers(unittest.TestCase):
    def test_compress_uid_set(self):
        self.assertEqual(_compress_uid_set({41, 43, 44, 45, 2212}), b"41,43:45,2212")
        self.assertEqual(_compress_uid_set({7}), b"7")
        self.assertEqual(_compress_uid_set({1, 2, 3}), b"1:3")

    def test_parse_vanished_uids(self):
        self.assertEqual(
            _parse_vanished_uids(b"(EARLIER) 41,43:45"), {41, 43, 44, 45}
        )
        self.assertEqual(_parse_vanished_uids(b"300:302"), {300, 301, 302})
        self.assertEqual(_parse_vanished_uids(b"9"), {9})

    def test_parse_vanished_uids_bad(self):
        with self.assertRaises(exceptions.ProtocolError):
            _parse_vanished_uids(b"not a uid set!")


if __name__ == "__main__":
    unittest.main()
