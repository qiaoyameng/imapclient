# Copyright (c) 2026, Menno Smits
# Released subject to the New BSD License
# Please see http://en.wikipedia.org/wiki/BSD_licenses

from unittest.mock import Mock

from imapclient.imap_utf7 import encode as encode_utf7
from imapclient.testable_imapclient import TestableIMAPClient as _TestableIMAPClient

from .imapclient_test import IMAPClientTest

FOLDER = "中文邮箱"
FOLDER_UTF8 = FOLDER.encode("utf-8")
FOLDER_UTF7 = encode_utf7(FOLDER)


class UTF8FolderTestCase(IMAPClientTest):
    def enable_utf8(self):
        self.client._cached_capabilities = [b"ENABLE", b"UTF8=ACCEPT", b"LITERAL+"]
        self.client._imap.state = "AUTH"
        self.client._raw_command_untagged = Mock(return_value=b"UTF8=ACCEPT")
        self.client.enable("UTF8=ACCEPT")
        self.client._imap.sent = b""

    def mock_command_ok(self):
        self.client._imap._command_complete.return_value = ("OK", [b"done"])


class TestNormaliseFolderUTF8(UTF8FolderTestCase):
    def test_utf7_used_by_default(self):
        self.assertEqual(
            self.client._normalise_folder(FOLDER), b'"' + FOLDER_UTF7 + b'"'
        )

    def test_utf8_used_once_enabled(self):
        self.enable_utf8()
        self.assertEqual(self.client._normalise_folder(FOLDER), FOLDER_UTF8)

    def test_ascii_folder_unquoted_when_safe(self):
        self.enable_utf8()
        self.assertEqual(self.client._normalise_folder("INBOX"), b"INBOX")

    def test_quotes_and_backslashes_escaped(self):
        self.enable_utf8()
        self.assertEqual(
            self.client._normalise_folder('Foo"Bar\\Baz'),
            b'"Foo\\"Bar\\\\Baz"',
        )

    def test_non_ascii_with_specials_uses_literal_form(self):
        self.enable_utf8()
        normalised = self.client._normalise_folder('中文"邮箱')
        # 8-bit, so it will be sent as a literal containing the raw
        # (unescaped) name.
        self.assertEqual(normalised.original, '中文"邮箱'.encode("utf-8"))

    def test_failed_enable_keeps_utf7(self):
        self.client._cached_capabilities = [b"ENABLE", b"UTF8=ACCEPT"]
        self.client._imap.state = "AUTH"
        self.client._raw_command_untagged = Mock(return_value=b"")

        self.client.enable("UTF8=ACCEPT")

        self.assertFalse(self.client._utf8_enabled)
        self.assertEqual(
            self.client._normalise_folder(FOLDER), b'"' + FOLDER_UTF7 + b'"'
        )

    def test_new_connection_uses_utf7(self):
        self.enable_utf8()
        self.assertTrue(self.client._utf8_enabled)

        # A reconnect (new client instance) must not inherit UTF-8 mode.
        new_client = _TestableIMAPClient()
        self.assertFalse(new_client._utf8_enabled)
        self.assertEqual(
            new_client._normalise_folder(FOLDER), b'"' + FOLDER_UTF7 + b'"'
        )


class TestSelectFolderUTF8(UTF8FolderTestCase):
    def test_select_sends_utf8_literal(self):
        self.enable_utf8()
        self.mock_command_ok()

        self.client.select_folder(FOLDER)

        sent = self.client._imap.sent
        self.assertEqual(
            sent,
            b"tag SELECT {%d+}\r\n" % len(FOLDER_UTF8) + FOLDER_UTF8 + b"\r\n",
        )

    def test_select_readonly_sends_examine(self):
        self.enable_utf8()
        self.mock_command_ok()

        self.client.select_folder(FOLDER, readonly=True)

        sent = self.client._imap.sent
        self.assertEqual(
            sent,
            b"tag EXAMINE {%d+}\r\n" % len(FOLDER_UTF8) + FOLDER_UTF8 + b"\r\n",
        )

    def test_select_sends_utf7_when_not_enabled(self):
        self.client._imap.select.return_value = ("OK", [b"1"])

        self.client.select_folder(FOLDER)

        self.client._imap.select.assert_called_once_with(
            b'"' + FOLDER_UTF7 + b'"', False
        )

    def test_select_ascii_with_specials_uses_quoted_string(self):
        self.enable_utf8()
        self.client._imap.select.return_value = ("OK", [b"1"])

        self.client.select_folder('Foo"Bar\\Baz')

        self.client._imap.select.assert_called_once_with(
            b'"Foo\\"Bar\\\\Baz"', False
        )


class TestRenameFolderUTF8(UTF8FolderTestCase):
    def test_rename_sends_utf8_literals(self):
        self.enable_utf8()
        self.mock_command_ok()
        new_name = "新邮箱"
        new_name_utf8 = new_name.encode("utf-8")

        self.client.rename_folder(FOLDER, new_name)

        sent = self.client._imap.sent
        self.assertEqual(
            sent,
            b"tag RENAME {%d+}\r\n" % len(FOLDER_UTF8)
            + FOLDER_UTF8
            + b"  {%d+}\r\n" % len(new_name_utf8)
            + new_name_utf8
            + b"\r\n",
        )


class TestListFoldersUTF8(UTF8FolderTestCase):
    def list_response(self, name):
        return (
            "LIST",
            [b'(\\HasNoChildren) "/" "' + name + b'"'],
        )

    def test_list_sends_utf8_literal(self):
        self.enable_utf8()
        self.mock_command_ok()
        self.client._imap._untagged_response.return_value = ("LIST", [])

        self.client.list_folders(FOLDER, "*")

        sent = self.client._imap.sent
        self.assertEqual(
            sent,
            b"tag LIST {%d+}\r\n" % len(FOLDER_UTF8) + FOLDER_UTF8 + b" *\r\n",
        )

    def test_list_response_decoded_as_utf8(self):
        self.enable_utf8()
        self.client._imap._simple_command.return_value = ("OK", [b"done"])
        self.client._imap._untagged_response.return_value = self.list_response(
            FOLDER_UTF8
        )

        folders = self.client.list_folders()

        self.assertEqual(folders, [((b"\\HasNoChildren",), b"/", FOLDER)])

    def test_list_response_decoded_as_utf7_when_not_enabled(self):
        self.client._imap._simple_command.return_value = ("OK", [b"done"])
        self.client._imap._untagged_response.return_value = self.list_response(
            FOLDER_UTF7
        )

        folders = self.client.list_folders()

        self.assertEqual(folders, [((b"\\HasNoChildren",), b"/", FOLDER)])

    def test_same_python_name_in_both_modes(self):
        # The same mailbox must be presented as the same Python string
        # regardless of the wire encoding in use.
        self.client._imap._simple_command.return_value = ("OK", [b"done"])
        self.client._imap._untagged_response.return_value = self.list_response(
            FOLDER_UTF7
        )
        utf7_folders = self.client.list_folders()

        self.enable_utf8()
        self.mock_command_ok()
        self.client._imap._untagged_response.return_value = self.list_response(
            FOLDER_UTF8
        )
        utf8_folders = self.client.list_folders()

        self.assertEqual(utf7_folders, utf8_folders)
        self.assertEqual(utf8_folders[0][2], FOLDER)


class TestNamespaceUTF8(UTF8FolderTestCase):
    def test_namespace_decoded_as_utf8(self):
        self.enable_utf8()
        self.client._cached_capabilities = [b"NAMESPACE"]
        self.client._imap.namespace.return_value = (
            "OK",
            [b'(("' + FOLDER_UTF8 + b'/" "/")) NIL NIL'],
        )

        namespace = self.client.namespace()

        self.assertEqual(namespace.personal, ((FOLDER + "/", "/"),))

    def test_namespace_decoded_as_utf7_when_not_enabled(self):
        self.client._cached_capabilities = [b"NAMESPACE"]
        self.client._imap.namespace.return_value = (
            "OK",
            [b'(("' + FOLDER_UTF7 + b'/" "/")) NIL NIL'],
        )

        namespace = self.client.namespace()

        self.assertEqual(namespace.personal, ((FOLDER + "/", "/"),))
