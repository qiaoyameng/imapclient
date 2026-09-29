import unittest
from unittest.mock import Mock

from imapclient import exceptions
from imapclient.testable_imapclient import TestableIMAPClient as IMAPClient


class QresyncSyncTest(unittest.TestCase):
    """Tests for IMAPClient.sync_folder using a stubbed IMAP protocol
    stream (wire-level responses fed through _get_response)."""

    def setUp(self):
        self.client = IMAPClient()
        self.client._cached_capabilities = [b"ENABLE", b"CONDSTORE", b"QRESYNC"]
        self.client._imap.state = "AUTH"
        self.client.enable = Mock(return_value=[b"QRESYNC"])
        self.client._imap._command = Mock(return_value="tag")
        self.client._imap.tagged_commands = {"tag": None}

    def feed(self, lines, tagged=("OK", [b"SELECT completed"])):
        responses = iter(lines)

        def fake_get_response():
            try:
                return next(responses)
            except StopIteration:
                self.client._imap.tagged_commands["tag"] = tagged
                return b"tag " + tagged[0].encode("ascii") + b" " + tagged[1][0]

        self.client._imap._get_response = fake_get_response

    def test_offline_deletions_and_flag_changes(self):
        self.feed(
            [
                # Flag change made while the client was offline.
                b"* 3 FETCH (UID 7 FLAGS (\\Seen) MODSEQ (121))",
                # Unsolicited FETCH (no MODSEQ) caused by another client.
                b"* 5 FETCH (UID 12 FLAGS (\\Deleted \\Seen))",
                # Messages expunged while the client was offline.
                b"* VANISHED (EARLIER) 4,9:10",
                # Unsolicited VANISHED not tied to the checkpoint.
                b"* VANISHED 20",
                b"* OK [UIDVALIDITY 6789] UIDs valid",
            ],
            tagged=("OK", [b"[HIGHESTMODSEQ 121] SELECT completed"]),
        )

        result = self.client.sync_folder("INBOX", 6789, 100, {4, 7, 9, 10, 12})

        self.assertFalse(result.full_sync)
        self.assertFalse(result.uidvalidity_changed)
        self.assertEqual(sorted(result.fetch), [7, 12])
        self.assertEqual(result.fetch[7][b"FLAGS"], (b"\\Seen",))
        self.assertEqual(result.fetch[7][b"MODSEQ"], (121,))
        self.assertEqual(result.fetch[12][b"FLAGS"], (b"\\Deleted", b"\\Seen"))
        self.assertEqual(result.vanished, [4, 9, 10, 20])
        self.assertEqual(result.checkpoint, (6789, 121))

    def test_qresync_select_command(self):
        self.feed([b"* OK [UIDVALIDITY 6789] UIDs valid"])

        self.client.sync_folder("INBOX", 6789, 100, {7, 9, 12})

        self.client.enable.assert_called_once_with("QRESYNC")
        args = self.client._imap._command.call_args[0]
        self.assertEqual(args[0], "SELECT")
        self.assertEqual(args[1], b'"INBOX"')
        self.assertEqual(args[2], b"(QRESYNC (6789 100 7,9,12))")
        self.assertEqual(self.client._imap.state, "SELECTED")

    def test_qresync_select_command_without_known_uids(self):
        self.feed([b"* OK [UIDVALIDITY 6789] UIDs valid"])

        self.client.sync_folder("INBOX", 6789, 100, ())

        args = self.client._imap._command.call_args[0]
        self.assertEqual(args[2], b"(QRESYNC (6789 100))")

    def test_uidvalidity_reset_invalidates_known_uids(self):
        self.feed(
            [
                b"* 1 FETCH (UID 100 FLAGS () MODSEQ (5))",
                b"* 2 FETCH (UID 101 FLAGS (\\Seen) MODSEQ (6))",
                b"* OK [UIDVALIDITY 9999] UIDs valid",
                b"* OK [HIGHESTMODSEQ 6] ok",
            ]
        )

        result = self.client.sync_folder("INBOX", 6789, 100, {4, 7, 9})

        self.assertTrue(result.uidvalidity_changed)
        # Old UIDs are meaningless: nothing can be reported as vanished.
        self.assertEqual(result.vanished, [])
        self.assertEqual(sorted(result.fetch), [100, 101])
        self.assertEqual(result.checkpoint, (9999, 6))

    def test_nomodseq_checkpoint(self):
        self.feed(
            [
                b"* OK [UIDVALIDITY 6789] UIDs valid",
                b"* OK [NOMODSEQ] no modsequences here",
            ]
        )

        result = self.client.sync_folder("INBOX", 6789, 100, {7})

        self.assertEqual(result.checkpoint, (6789, None))

    def test_no_checkpoint_on_failed_select(self):
        self.feed([], tagged=("NO", [b"mailbox gone"]))

        with self.assertRaises(exceptions.IMAPClientError):
            self.client.sync_folder("INBOX", 6789, 100, {7})

        # The connection is back in the authenticated state and the
        # caller keeps its previous checkpoint (no result produced).
        self.assertEqual(self.client._imap.state, "AUTH")

    def test_no_checkpoint_without_uidvalidity(self):
        self.feed([b"* OK [HIGHESTMODSEQ 121] ok"])

        with self.assertRaises(exceptions.ProtocolError):
            self.client.sync_folder("INBOX", 6789, 100, {7})

    def test_bye_aborts_sync(self):
        self.feed([b"* BYE server going away"])

        with self.assertRaises(exceptions.IMAPClientAbortError):
            self.client.sync_folder("INBOX", 6789, 100, {7})

    def test_enable_refused_falls_back_to_full_sync(self):
        self.client.enable = Mock(return_value=[])
        self._stub_full_select()
        self.client.fetch = Mock(return_value={7: {b"FLAGS": ()}})

        result = self.client.sync_folder("INBOX", 6789, 100, {7})

        self.assertTrue(result.full_sync)
        self.client._imap._command.assert_not_called()

    def _stub_full_select(self, exists=b"2", uidvalidity=b"6789", modseq=b"121"):
        self.client._imap.select = Mock(return_value=("OK", [exists]))
        self.client._imap.untagged_responses = {
            "EXISTS": [exists],
            "UIDVALIDITY": [uidvalidity],
            "HIGHESTMODSEQ": [modseq],
        }


class FullSyncFallbackTest(unittest.TestCase):
    """Full sync fallback when the server lacks QRESYNC."""

    def setUp(self):
        self.client = IMAPClient()
        self.client._cached_capabilities = [b"IMAP4rev1"]
        self.client._imap.state = "AUTH"
        self.client._imap.select = Mock(return_value=("OK", [b"2"]))
        self.client._imap.untagged_responses = {
            "EXISTS": [b"2"],
            "UIDVALIDITY": [b"6789"],
            "HIGHESTMODSEQ": [b"121"],
        }

    def test_fallback_computes_vanished_locally(self):
        self.client.fetch = Mock(
            return_value={7: {b"FLAGS": ()}, 12: {b"FLAGS": (b"\\Seen",)}}
        )

        result = self.client.sync_folder("INBOX", 6789, 100, {7, 9, 12})

        self.assertTrue(result.full_sync)
        self.assertFalse(result.uidvalidity_changed)
        self.assertEqual(sorted(result.fetch), [7, 12])
        self.assertEqual(result.vanished, [9])
        self.assertEqual(result.checkpoint, (6789, 121))
        self.client.fetch.assert_called_once_with("1:*", [b"FLAGS"])

    def test_fallback_without_checkpoint(self):
        self.client.fetch = Mock(return_value={7: {b"FLAGS": ()}})

        result = self.client.sync_folder("INBOX")

        self.assertTrue(result.full_sync)
        self.assertTrue(result.uidvalidity_changed)
        self.assertEqual(result.vanished, [])
        self.assertEqual(sorted(result.fetch), [7])
        self.assertEqual(result.checkpoint, (6789, 121))

    def test_fallback_uidvalidity_reset(self):
        self.client._imap.untagged_responses["UIDVALIDITY"] = [b"9999"]
        self.client.fetch = Mock(return_value={100: {b"FLAGS": ()}})

        result = self.client.sync_folder("INBOX", 6789, 100, {7, 9})

        self.assertTrue(result.uidvalidity_changed)
        self.assertEqual(result.vanished, [])
        self.assertEqual(result.checkpoint, (9999, 121))

    def test_fallback_empty_folder(self):
        self.client._imap.untagged_responses["EXISTS"] = [b"0"]
        self.client.fetch = Mock()

        result = self.client.sync_folder("INBOX", 6789, 100, {7, 9})

        self.assertEqual(result.fetch, {})
        self.assertEqual(result.vanished, [7, 9])
        self.client.fetch.assert_not_called()

    def test_requires_use_uid(self):
        self.client.use_uid = False

        with self.assertRaises(ValueError):
            self.client.sync_folder("INBOX", 6789, 100, {7})
