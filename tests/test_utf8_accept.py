# Copyright (c) 2026, Menno Smits
# Released subject to the New BSD License
# Please see http://en.wikipedia.org/wiki/BSD_licenses

from unittest.mock import Mock

from imapclient.exceptions import IMAPClientError
from imapclient.imap_utf7 import encode as encode_utf7

from .imapclient_test import IMAPClientTest

CHINESE_FOLDER = "中文"
CHINESE_FOLDER_UTF8 = CHINESE_FOLDER.encode("utf-8")
CHINESE_FOLDER_UTF7 = encode_utf7(CHINESE_FOLDER)


class TestEnableUTF8Accept(IMAPClientTest):
    def setUp(self):
        super(TestEnableUTF8Accept, self).setUp()
        self.command = Mock()
        self.client._raw_command_untagged = self.command
        self.client._imap.state = "AUTH"
        self.client._cached_capabilities = [b"ENABLE", b"UTF8=ACCEPT"]

    def enable_utf8(self):
        self.command.return_value = b"UTF8=ACCEPT"
        return self.client.enable("UTF8=ACCEPT")

    def test_disabled_by_default(self):
        self.assertFalse(self.client._utf8_accept_enabled)
        self.assertEqual(
            self.client._normalise_folder(CHINESE_FOLDER),
            b'"' + CHINESE_FOLDER_UTF7 + b'"',
        )

    def test_enable_switches_folder_encoding(self):
        resp = self.enable_utf8()

        self.assertEqual(resp, [b"UTF8=ACCEPT"])
        self.assertTrue(self.client._utf8_accept_enabled)
        self.assertEqual(
            self.client._normalise_folder(CHINESE_FOLDER), CHINESE_FOLDER_UTF8
        )

    def test_enable_case_insensitive(self):
        self.command.return_value = b"utf8=accept"

        self.client.enable("UTF8=ACCEPT")

        self.assertTrue(self.client._utf8_accept_enabled)

    def test_other_capabilities_do_not_switch(self):
        self.command.return_value = b"CONDSTORE"

        self.client.enable("CONDSTORE")

        self.assertFalse(self.client._utf8_accept_enabled)

    def test_empty_enabled_response_does_not_switch(self):
        self.command.return_value = b""

        resp = self.client.enable("UTF8=ACCEPT")

        self.assertEqual(resp, [])
        self.assertFalse(self.client._utf8_accept_enabled)

    def test_rejected_enable_does_not_switch(self):
        self.command.side_effect = IMAPClientError("ENABLE failed")

        self.assertRaises(IMAPClientError, self.client.enable, "UTF8=ACCEPT")
        self.assertFalse(self.client._utf8_accept_enabled)

    def test_state_not_carried_over_to_new_connection(self):
        self.enable_utf8()
        self.assertTrue(self.client._utf8_accept_enabled)

        self.setUp()

        self.assertFalse(self.client._utf8_accept_enabled)
        self.assertEqual(
            self.client._normalise_folder(CHINESE_FOLDER),
            b'"' + CHINESE_FOLDER_UTF7 + b'"',
        )


class TestUTF8FolderQuoting(IMAPClientTest):
    def setUp(self):
        super(TestUTF8FolderQuoting, self).setUp()
        self.client._utf8_accept_enabled = True

    def test_non_ascii_sent_unquoted_for_literal(self):
        # 8-bit names are sent as literals by _raw_command so no
        # quoting is applied here.
        self.assertEqual(
            self.client._normalise_folder(CHINESE_FOLDER), CHINESE_FOLDER_UTF8
        )

    def test_double_quote_uses_quoted_string(self):
        self.assertEqual(self.client._normalise_folder('Foo"Bar'), b'"Foo\\"Bar"')

    def test_backslash_uses_quoted_string(self):
        self.assertEqual(self.client._normalise_folder("Foo\\Bar"), b'"Foo\\\\Bar"')

    def test_space_uses_quoted_string(self):
        self.assertEqual(self.client._normalise_folder("Foo Bar"), b'"Foo Bar"')

    def test_empty_uses_quoted_string(self):
        self.assertEqual(self.client._normalise_folder(""), b'""')

    def test_plain_ascii_sent_as_atom(self):
        self.assertEqual(self.client._normalise_folder("INBOX"), b"INBOX")


class TestUTF8RawCommands(IMAPClientTest):
    def setUp(self):
        super(TestUTF8RawCommands, self).setUp()
        self.client._utf8_accept_enabled = True
        self.client._imap._get_response.return_value = None
        self.client._imap._command_complete.return_value = ("OK", [b"done"])
        self.client._cached_capabilities = (b"LITERAL+",)

    def test_list_sends_utf8_literal(self):
        self.client._imap._untagged_response.return_value = (
            "OK",
            [b'(\\HasNoChildren) "/" "' + CHINESE_FOLDER_UTF8 + b'"'],
        )

        folders = self.client.list_folders(CHINESE_FOLDER)

        self.assertEqual(
            self.client._imap.sent,
            b"tag LIST {6+}\r\n" + CHINESE_FOLDER_UTF8 + b" *\r\n",
        )
        self.assertEqual(folders, [((b"\\HasNoChildren",), b"/", CHINESE_FOLDER)])

    def test_select_sends_utf8_literal(self):
        self.client.select_folder(CHINESE_FOLDER)

        self.assertEqual(
            self.client._imap.sent,
            b"tag SELECT {6+}\r\n" + CHINESE_FOLDER_UTF8 + b"\r\n",
        )
        self.assertEqual(self.client._imap.state, "SELECTED")

    def test_select_readonly_uses_examine(self):
        self.client.select_folder(CHINESE_FOLDER, readonly=True)

        self.assertEqual(
            self.client._imap.sent,
            b"tag EXAMINE {6+}\r\n" + CHINESE_FOLDER_UTF8 + b"\r\n",
        )

    def test_select_with_quote_uses_quoted_string(self):
        self.client.select_folder('Foo"Bar')

        self.assertEqual(self.client._imap.sent, b'tag SELECT "Foo\\"Bar"\r\n')

    def test_rename_sends_utf8_literals(self):
        other = "日本語".encode("utf-8")

        self.client.rename_folder(CHINESE_FOLDER, "日本語")

        self.assertEqual(
            self.client._imap.sent,
            b"tag RENAME {6+}\r\n" + CHINESE_FOLDER_UTF8 + b"  {9+}\r\n" + other + b"\r\n",
        )

    def test_create_sends_utf8_literal(self):
        self.client.create_folder(CHINESE_FOLDER)

        self.assertEqual(
            self.client._imap.sent,
            b"tag CREATE {6+}\r\n" + CHINESE_FOLDER_UTF8 + b"\r\n",
        )

    def test_folder_status_sends_utf8_literal(self):
        self.client._imap._untagged_response.return_value = (
            "OK",
            [
                b'"' + CHINESE_FOLDER_UTF8 + b'"'
                b" (MESSAGES 3 RECENT 0 UIDNEXT 4 UIDVALIDITY 1435636895 UNSEEN 0)"
            ],
        )

        out = self.client.folder_status(CHINESE_FOLDER)

        self.assertEqual(
            self.client._imap.sent,
            b"tag STATUS {6+}\r\n" + CHINESE_FOLDER_UTF8
            + b" (MESSAGES RECENT UIDNEXT UIDVALIDITY UNSEEN)\r\n",
        )
        self.assertEqual(out[b"MESSAGES"], 3)


class TestUTF7RawCommands(IMAPClientTest):
    def setUp(self):
        super(TestUTF7RawCommands, self).setUp()
        self.client._imap._simple_command.return_value = ("OK", [b"something"])

    def test_list_sends_modified_utf7(self):
        self.client._imap._untagged_response.return_value = (
            "LIST",
            [b'(\\HasNoChildren) "/" "' + CHINESE_FOLDER_UTF7 + b'"'],
        )

        folders = self.client.list_folders(CHINESE_FOLDER)

        self.client._imap._simple_command.assert_called_once_with(
            "LIST", b'"' + CHINESE_FOLDER_UTF7 + b'"', b'"*"'
        )
        self.assertEqual(folders, [((b"\\HasNoChildren",), b"/", CHINESE_FOLDER)])

    def test_same_python_name_as_utf8_mode(self):
        # The same mailbox must be returned as the same Python string
        # regardless of the wire encoding in use.
        self.client._imap._untagged_response.return_value = (
            "LIST",
            [b'(\\HasNoChildren) "/" "' + CHINESE_FOLDER_UTF7 + b'"'],
        )
        utf7_folders = self.client.list_folders()

        utf8_client = self._make_utf8_client()
        utf8_folders = utf8_client.list_folders()

        self.assertEqual(utf7_folders, utf8_folders)
        self.assertEqual(utf7_folders[0][2], CHINESE_FOLDER)

    def _make_utf8_client(self):
        client = type(self.client)()
        client._utf8_accept_enabled = True
        client._imap._get_response.return_value = None
        client._imap._command_complete.return_value = ("OK", [b"done"])
        client._cached_capabilities = (b"LITERAL+",)
        client._imap._untagged_response.return_value = (
            "OK",
            [b'(\\HasNoChildren) "/" "' + CHINESE_FOLDER_UTF8 + b'"'],
        )
        return client
