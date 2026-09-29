# -*- coding: utf-8 -*-
# Copyright (c) 2016, Menno Smits
# Released subject to the New BSD License
# Please see http://en.wikipedia.org/wiki/BSD_licenses

from unittest.mock import Mock, patch, sentinel

from imapclient import exceptions
from imapclient.imapclient import ANSWERED, DELETED, DRAFT, FLAGGED, RECENT, SEEN

from .imapclient_test import IMAPClientTest


class TestFlagsConsts(IMAPClientTest):
    def test_flags_are_bytes(self):
        for flag in DELETED, SEEN, ANSWERED, FLAGGED, DRAFT, RECENT:
            if not isinstance(flag, bytes):
                self.fail("%r flag is not bytes" % flag)


class TestFlags(IMAPClientTest):
    def setUp(self):
        super(TestFlags, self).setUp()
        self.client._command_and_check = Mock()

    def test_get(self):
        with patch.object(
            self.client,
            "fetch",
            autospec=True,
            return_value={123: {b"FLAGS": [b"foo", b"bar"]}, 444: {b"FLAGS": [b"foo"]}},
        ):
            out = self.client.get_flags(sentinel.messages)
            self.client.fetch.assert_called_with(sentinel.messages, ["FLAGS"])
            self.assertEqual(out, {123: [b"foo", b"bar"], 444: [b"foo"]})

    def test_set(self):
        self.check(self.client.set_flags, b"FLAGS")

    def test_add(self):
        self.check(self.client.add_flags, b"+FLAGS")

    def test_remove(self):
        self.check(self.client.remove_flags, b"-FLAGS")

    def check(self, meth, expected_command):
        self._check(meth, expected_command)
        self._check(meth, expected_command, silent=True)
        self._check(meth, expected_command, unsolicited_noise=True)
        self._check(meth, expected_command, unsolicited_noise=True, silent=True)

    def _check(self, meth, expected_command, unsolicited_noise=False, silent=False):
        if silent:
            expected_command += b".SILENT"

        noise = [b"46 (UID 148606 FLAGS (\\Seen))"] if unsolicited_noise else []

        cc = self.client._command_and_check
        cc.return_value = (
            [
                b"11 (FLAGS (blah foo) UID 1)",
                b"11 (UID 1 OTHER (dont))",
                b"22 (FLAGS (foo) UID 2)",
            ]
            + noise
            + [b"22 (UID 2 OTHER (care))"]
        )
        resp = meth([1, 2], "foo", silent=silent)
        cc.assert_called_once_with("store", b"1,2", expected_command, "(foo)", uid=True)
        if silent:
            self.assertIsNone(resp)
        else:
            self.assertEqual(
                resp,
                {
                    1: (b"blah", b"foo"),
                    2: (b"foo",),
                },
            )

        cc.reset_mock()


class TestCondstoreFlags(IMAPClientTest):
    def setUp(self):
        super(TestCondstoreFlags, self).setUp()
        self.client._imap.capabilities = [b"IMAP4REV1", b"CONDSTORE"]
        self.client._imap.uid = Mock()
        self.client._imap.untagged_responses = {}

    def mock_store_response(self, typ, tagged, fetch=None):
        # Mirror imaplib: on OK the FETCH untagged responses are
        # returned by uid(); on NO the tagged response text is
        # returned and the FETCH responses stay buffered.
        fetch = list(fetch or [])
        if typ == "OK":
            self.client._imap.uid.return_value = (typ, fetch)
        else:
            self.client._imap.uid.return_value = (typ, tagged)
            self.client._imap.untagged_responses = {"FETCH": fetch}

    def assert_store_command(self, expected_command, expected_flags="(foo)"):
        self.client._imap.uid.assert_called_once_with(
            "STORE",
            b"1,2",
            b"(UNCHANGEDSINCE 999)",
            expected_command,
            expected_flags,
        )

    def test_add_unchanged_since_success(self):
        self.mock_store_response(
            "OK",
            [b"Store completed"],
            fetch=[
                b"11 (FLAGS (\\Seen foo) UID 1 MODSEQ (1000))",
                b"22 (FLAGS (foo) UID 2 MODSEQ (1000))",
            ],
        )
        resp, failed = self.client.add_flags([1, 2], "foo", unchanged_since=999)
        self.assert_store_command(b"+FLAGS")
        self.assertEqual(resp, {1: (b"\\Seen", b"foo"), 2: (b"foo",)})
        self.assertEqual(failed, set())

    def test_remove_unchanged_since(self):
        self.mock_store_response("OK", [b"Store completed"])
        resp, failed = self.client.remove_flags([1, 2], "foo", unchanged_since=999)
        self.assert_store_command(b"-FLAGS")
        self.assertEqual(resp, {})
        self.assertEqual(failed, set())

    def test_set_unchanged_since(self):
        self.mock_store_response("OK", [b"Store completed"])
        resp, failed = self.client.set_flags([1, 2], "foo", unchanged_since=999)
        self.assert_store_command(b"FLAGS")
        self.assertEqual(resp, {})
        self.assertEqual(failed, set())

    def test_unchanged_since_silent(self):
        self.mock_store_response("OK", [b"Store completed"])
        resp, failed = self.client.add_flags(
            [1, 2], "foo", silent=True, unchanged_since=999
        )
        self.assert_store_command(b"+FLAGS.SILENT")
        self.assertIsNone(resp)
        self.assertEqual(failed, set())

    def test_partial_conflict_returns_updated_and_failed(self):
        # The server updated UID 1 but UID 2 was modified by another
        # client: the update of UID 1 must not be lost and the
        # conflict must not be reported as a total failure.
        self.mock_store_response(
            "NO",
            [b"[MODIFIED 2] Conditional STORE failed"],
            fetch=[b"11 (FLAGS (\\Seen foo) UID 1 MODSEQ (1001))"],
        )
        resp, failed = self.client.add_flags([1, 2], "foo", unchanged_since=999)
        self.assertEqual(resp, {1: (b"\\Seen", b"foo")})
        self.assertEqual(failed, {2})
        # Buffered responses are drained so they cannot leak into
        # later commands.
        self.assertEqual(self.client._imap.untagged_responses, {})

    def test_modified_response_with_ranges(self):
        self.mock_store_response(
            "NO",
            [b"[MODIFIED 2,4:5] Conditional STORE failed"],
            fetch=[b"11 (FLAGS (foo) UID 1 MODSEQ (1001))"],
        )
        resp, failed = self.client.set_flags([1, 2, 4, 5], "foo", unchanged_since=999)
        self.assertEqual(resp, {1: (b"foo",)})
        self.assertEqual(failed, {2, 4, 5})

    def test_expunge_mid_update_does_not_confuse_conflict_report(self):
        # The server expunges a message while the conditional STORE is
        # in progress. Because UID mode is used, the MODIFIED report
        # and the returned flags still refer to the right messages.
        self.client._imap.uid.return_value = (
            "NO",
            [b"[MODIFIED 105,107] Conditional STORE failed"],
        )
        self.client._imap.untagged_responses = {
            "FETCH": [
                b"1 (UID 101 FLAGS (\\Seen) MODSEQ (917))",
                b"3 (UID 103 FLAGS (\\Seen foo) MODSEQ (920))",
            ],
            "EXPUNGE": [b"2"],
        }
        resp, failed = self.client.add_flags(
            [101, 103, 105, 107], "foo", unchanged_since=916
        )
        self.client._imap.uid.assert_called_once_with(
            "STORE",
            b"101,103,105,107",
            b"(UNCHANGEDSINCE 916)",
            b"+FLAGS",
            "(foo)",
        )
        self.assertEqual(resp, {101: (b"\\Seen",), 103: (b"\\Seen", b"foo")})
        self.assertEqual(failed, {105, 107})
        self.assertEqual(self.client._imap.untagged_responses, {"EXPUNGE": [b"2"]})

    def test_unchanged_since_requires_condstore(self):
        self.client._imap.capabilities = [b"IMAP4REV1"]
        self.assertRaises(
            exceptions.CapabilityError,
            self.client.add_flags,
            [1, 2],
            "foo",
            unchanged_since=999,
        )
        self.client._imap.uid.assert_not_called()

    def test_unchanged_since_requires_uid_mode(self):
        self.client.use_uid = False
        self.assertRaises(
            exceptions.IMAPClientError,
            self.client.add_flags,
            [1, 2],
            "foo",
            unchanged_since=999,
        )
        self.client._imap.uid.assert_not_called()

    def test_no_unchanged_since_keeps_old_return_type(self):
        self.client._command_and_check = Mock(
            return_value=[b"11 (FLAGS (foo) UID 1)"]
        )
        resp = self.client.add_flags([1], "foo")
        self.assertEqual(resp, {1: (b"foo",)})


class TestGmailLabels(IMAPClientTest):
    def setUp(self):
        super(TestGmailLabels, self).setUp()
        self.client._command_and_check = Mock()

    def test_get(self):
        with patch.object(
            self.client,
            "fetch",
            autospec=True,
            return_value={
                123: {b"X-GM-LABELS": [b"foo", b"&AUE-abel"]},
                444: {b"X-GM-LABELS": [b"foo"]},
            },
        ):
            out = self.client.get_gmail_labels(sentinel.messages)
            self.client.fetch.assert_called_with(sentinel.messages, [b"X-GM-LABELS"])
            self.assertEqual(out, {123: ["foo", "Łabel"], 444: ["foo"]})

    def test_set(self):
        self.check(self.client.set_gmail_labels, b"X-GM-LABELS")

    def test_add(self):
        self.check(self.client.add_gmail_labels, b"+X-GM-LABELS")

    def test_remove(self):
        self.check(self.client.remove_gmail_labels, b"-X-GM-LABELS")

    def check(self, meth, expected_command):
        self._check(meth, expected_command)
        self._check(meth, expected_command, silent=True)
        self._check(meth, expected_command, unsolicited_noise=True)
        self._check(meth, expected_command, unsolicited_noise=True, silent=True)

    def _check(self, meth, expected_command, unsolicited_noise=False, silent=False):
        if silent:
            expected_command += b".SILENT"

        noise = [b"46 (UID 148606 FLAGS (\\Seen))"] if unsolicited_noise else []

        cc = self.client._command_and_check
        cc.return_value = (
            [
                b'11 (X-GM-LABELS (&AUE-abel "f\\"o\\"o") UID 1)',
                b'22 (X-GM-LABELS ("f\\"o\\"o") UID 2)',
                b"11 (UID 1 FLAGS (dont))",
            ]
            + noise
            + [b"22 (UID 2 FLAGS (care))"]
        )
        resp = meth([1, 2], 'f"o"o', silent=silent)
        cc.assert_called_once_with(
            "store", b"1,2", expected_command, '("f\\"o\\"o")', uid=True
        )
        if silent:
            self.assertIsNone(resp)
        else:
            self.assertEqual(
                resp,
                {
                    1: ["Łabel", 'f"o"o'],
                    2: [
                        'f"o"o',
                    ],
                },
            )

        cc.reset_mock()
