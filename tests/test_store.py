# -*- coding: utf-8 -*-
# Copyright (c) 2016, Menno Smits
# Released subject to the New BSD License
# Please see http://en.wikipedia.org/wiki/BSD_licenses

from unittest.mock import Mock, patch, sentinel

from imapclient.exceptions import CapabilityError, IMAPClientError
from imapclient.imapclient import (
    ANSWERED,
    DELETED,
    DRAFT,
    FLAGGED,
    RECENT,
    SEEN,
    ConditionalStoreResult,
)

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


class TestConditionalStore(IMAPClientTest):
    def setUp(self):
        super(TestConditionalStore, self).setUp()
        self.client._cached_capabilities = (b"CONDSTORE",)

    def test_partial_modified_reports_conflict_and_keeps_updates(self):
        def uid_store(*args):
            self.client._imap.untagged_responses["MODIFIED"] = [b"3:4,6"]
            return (
                "OK",
                [
                    b"1 (FLAGS (foo) UID 1)",
                    b"2 (FLAGS (foo) UID 2)",
                ],
            )

        self.client._imap.uid.side_effect = uid_store

        result = self.client.add_flags([1, 2, 3, 4, 6], "foo", unchanged_since=100)

        self.client._imap.uid.assert_called_once_with(
            "store", b"1,2,3,4,6", b"+FLAGS", "(foo)", "(UNCHANGEDSINCE 100)"
        )
        self.assertEqual(
            result,
            ConditionalStoreResult(
                flags={1: (b"foo",), 2: (b"foo",)},
                modified=(3, 4, 6),
                unsolicited_flags={},
                use_uid=True,
            ),
        )

    def test_methods_send_their_flag_operation(self):
        methods = (
            (self.client.set_flags, b"FLAGS"),
            (self.client.add_flags, b"+FLAGS"),
            (self.client.remove_flags, b"-FLAGS"),
        )

        for method, command in methods:
            self.client._imap.uid.reset_mock(return_value=False, side_effect=False)
            self.client._imap.uid.side_effect = None
            self.client._imap.uid.return_value = (
                "OK",
                [b"1 (FLAGS (foo) UID 1)"],
            )

            method([1], "foo", unchanged_since=100)

            self.client._imap.uid.assert_called_once_with(
                "store", b"1", command, "(foo)", "(UNCHANGEDSINCE 100)"
            )

    def test_silent_returns_modified_and_unsolicited_flags(self):
        def uid_store(*args):
            self.client._imap.untagged_responses["MODIFIED"] = [b"2"]
            return ("OK", [b"5 (UID 9 FLAGS (\\Seen))"])

        self.client._imap.uid.side_effect = uid_store

        result = self.client.add_flags(
            [1, 2], "foo", silent=True, unchanged_since=100
        )

        self.client._imap.uid.assert_called_once_with(
            "store", b"1,2", b"+FLAGS.SILENT", "(foo)", "(UNCHANGEDSINCE 100)"
        )
        self.assertEqual(result.flags, {})
        self.assertEqual(result.modified, (2,))
        self.assertEqual(result.unsolicited_flags, {9: (SEEN,)})

    def test_modified_no_response_is_a_partial_conflict_not_failure(self):
        def uid_store(*args):
            self.client._imap.untagged_responses["MODIFIED"] = [b"2"]
            self.client._imap.untagged_responses["FETCH"] = [
                b"1 (FLAGS (foo) UID 1)"
            ]
            return ("NO", [None])

        self.client._imap.uid.side_effect = uid_store

        result = self.client.add_flags([1, 2], "foo", unchanged_since=100)

        self.assertEqual(result.flags, {1: (b"foo",)})
        self.assertEqual(result.modified, (2,))

    def test_all_messages_modified_without_fetch_responses(self):
        def uid_store(*args):
            self.client._imap.untagged_responses["MODIFIED"] = [b"1,2"]
            return ("OK", [None])

        self.client._imap.uid.side_effect = uid_store

        result = self.client.add_flags([1, 2], "foo", unchanged_since=100)

        self.assertEqual(result.flags, {})
        self.assertEqual(result.modified, (1, 2))

    def test_no_without_modified_is_still_a_failure(self):
        self.client._imap.uid.return_value = (
            "NO",
            [b"[UNAVAILABLE] Conditional store is unavailable"],
        )

        with self.assertRaises(IMAPClientError):
            self.client.add_flags([1], "foo", unchanged_since=100)

    def test_unsolicited_flags_are_not_dropped_or_treated_as_results(self):
        self.client._imap.uid.return_value = (
            "OK",
            [
                b"1 (UID 1 FLAGS (foo))",
                b"2 (UID 2 FLAGS (foo))",
                b"9 (UID 9 FLAGS (\\Seen bar))",
            ],
        )

        result = self.client.set_flags([1, 2], "foo", unchanged_since=100)

        self.assertEqual(result.flags, {1: (b"foo",), 2: (b"foo",)})
        self.assertEqual(result.unsolicited_flags, {9: (SEEN, b"bar")})

    def test_missing_capability_does_not_send_unconditional_store(self):
        self.client._cached_capabilities = ()

        with self.assertRaises(CapabilityError):
            self.client.add_flags([1], "foo", unchanged_since=100)

        self.client._imap.uid.assert_not_called()
        self.client._imap.store.assert_not_called()

    def test_invalid_unchanged_since(self):
        for value in (-1, True, "100"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.client.add_flags([1], "foo", unchanged_since=value)

        self.client._imap.uid.assert_not_called()
        self.client._imap.store.assert_not_called()

    def test_sequence_mode_uses_non_uid_store_and_reports_sequence_numbers(self):
        self.client.use_uid = False

        def store(*args):
            self.client._imap.untagged_responses["MODIFIED"] = [b"3"]
            return (
                "OK",
                [
                    b"1 (FLAGS (foo))",
                    b"2 (FLAGS (foo))",
                ],
            )

        self.client._imap.store.side_effect = store

        result = self.client.add_flags([1, 2, 3], "foo", unchanged_since=100)

        self.client._imap.store.assert_called_once_with(
            b"1,2,3", b"+FLAGS", "(foo)", "(UNCHANGEDSINCE 100)"
        )
        self.client._imap.uid.assert_not_called()
        self.assertEqual(result.flags, {1: (b"foo",), 2: (b"foo",)})
        self.assertEqual(result.modified, (3,))
        self.assertFalse(result.use_uid)

    def test_unconditional_sequence_mode_returns_sequence_keyed_flags(self):
        self.client.use_uid = False
        self.client._imap.store.return_value = (
            "OK", [b"1 (FLAGS (foo))"]
        )

        result = self.client.add_flags([1], "foo")

        self.client._imap.store.assert_called_once_with(
            b"1", b"+FLAGS", "(foo)"
        )
        self.assertEqual(result, {1: (b"foo",)})
        self.client._imap.uid.assert_not_called()

    def test_expunge_during_uid_store_does_not_move_uid_conflicts(self):
        self.client._imap.untagged_responses["EXPUNGE"] = [b"1"]

        def uid_store(*args):
            self.client._imap.untagged_responses["MODIFIED"] = [b"4"]
            return (
                "OK",
                [
                    b"1 (FLAGS (foo) UID 2)",
                    b"2 (FLAGS (foo) UID 3)",
                ],
            )

        self.client._imap.uid.side_effect = uid_store

        result = self.client.add_flags([2, 3, 4], "foo", unchanged_since=100)

        self.assertEqual(result.flags, {2: (b"foo",), 3: (b"foo",)})
        self.assertEqual(result.modified, (4,))
        self.client._imap.store.assert_not_called()
