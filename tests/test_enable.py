# Copyright (c) 2017, Menno Smits
# Released subject to the New BSD License
# Please see http://en.wikipedia.org/wiki/BSD_licenses

from unittest.mock import Mock

from imapclient.exceptions import IllegalStateError

from .imapclient_test import IMAPClientTest


class TestEnable(IMAPClientTest):
    def setUp(self):
        super(TestEnable, self).setUp()
        self.command = Mock()
        self.client._raw_command_untagged = self.command
        self.client._imap.state = "AUTH"
        self.client._cached_capabilities = [b"ENABLE"]

    def test_success(self):
        self.command.return_value = b"CONDSTORE"

        resp = self.client.enable("CONDSTORE")

        self.command.assert_called_once_with(
            b"ENABLE", [b"CONDSTORE"], uid=False, response_name="ENABLED", unpack=True
        )
        self.assertEqual(resp, [b"CONDSTORE"])

    def test_failed1(self):
        # When server returns an empty ENABLED response
        self.command.return_value = b""

        resp = self.client.enable("FOO")

        self.command.assert_called_once_with(
            b"ENABLE", [b"FOO"], uid=False, response_name="ENABLED", unpack=True
        )
        self.assertEqual(resp, [])

    def test_failed2(self):
        # When server returns no ENABLED response
        self.command.return_value = None

        resp = self.client.enable("FOO")

        self.command.assert_called_once_with(
            b"ENABLE", [b"FOO"], uid=False, response_name="ENABLED", unpack=True
        )
        self.assertEqual(resp, [])

    def test_multiple(self):
        self.command.return_value = b"FOO BAR"

        resp = self.client.enable("FOO", "BAR")

        self.command.assert_called_once_with(
            b"ENABLE", [b"FOO", b"BAR"], uid=False, response_name="ENABLED", unpack=True
        )
        self.assertEqual(resp, [b"FOO", b"BAR"])

    def test_wrong_state(self):
        self.client._imap.state = "SELECTED"

        self.assertRaises(
            IllegalStateError,
            self.client.enable,
            "FOO",
        )

    def test_utf8_accept_enabled(self):
        self.command.return_value = b"UTF8=ACCEPT"

        resp = self.client.enable("UTF8=ACCEPT")

        self.assertEqual(resp, [b"UTF8=ACCEPT"])
        self.assertTrue(self.client._utf8_enabled)
        self.client._imap._mode_utf8.assert_called_once_with()

    def test_utf8_accept_enabled_case_insensitive(self):
        self.command.return_value = b"utf8=accept"

        resp = self.client.enable("UTF8=ACCEPT")

        self.assertEqual(resp, [b"utf8=accept"])
        self.assertTrue(self.client._utf8_enabled)
        self.client._imap._mode_utf8.assert_called_once_with()

    def test_utf8_accept_refused(self):
        # When the server returns an empty ENABLED response the
        # session must stay in modified UTF-7 mode.
        self.command.return_value = b""

        resp = self.client.enable("UTF8=ACCEPT")

        self.assertEqual(resp, [])
        self.assertFalse(self.client._utf8_enabled)
        self.client._imap._mode_utf8.assert_not_called()

    def test_utf8_accept_not_in_response(self):
        # When the server enables something else, UTF-8 mode must not
        # be activated.
        self.command.return_value = b"CONDSTORE"

        resp = self.client.enable("CONDSTORE")

        self.assertEqual(resp, [b"CONDSTORE"])
        self.assertFalse(self.client._utf8_enabled)
        self.client._imap._mode_utf8.assert_not_called()

    def test_utf8_accept_not_enabled_by_default(self):
        # A fresh connection never inherits UTF-8 mode from a
        # previous session.
        self.assertFalse(self.client._utf8_enabled)
