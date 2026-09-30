# Copyright (c) 2026, Menno Smits
# Released subject to the New BSD License
# Please see http://en.wikipedia.org/wiki/BSD_licenses

import io
import unittest

from imapclient import exceptions
from imapclient.testable_imapclient import TestableIMAPClient


class FragmentedRawIO(io.RawIOBase):
    """Delivers data in small fragments, like a busy socket would."""

    def __init__(self, data, fragment_size):
        self._data = data
        self._fragment_size = fragment_size
        self._pos = 0

    def readable(self):
        return True

    def readinto(self, buf):
        if self._pos >= len(self._data):
            return 0
        size = min(self._fragment_size, len(buf), len(self._data) - self._pos)
        buf[:size] = self._data[self._pos : self._pos + size]
        self._pos += size
        return size


class FakeStreamingIMAP4:
    """Minimal fake of the imaplib IMAP4 pieces used by fetch_stream."""

    def __init__(self, response, fragment_size=5):
        self.file = io.BufferedReader(FragmentedRawIO(response, fragment_size))
        self.tagged_commands = {}
        self.commands = []
        self.shutdown_called = False
        self._tag_counter = 0

    def _command(self, *args):
        self.commands.append(args)
        tag = b"TAG%d" % self._tag_counter
        self._tag_counter += 1
        self.tagged_commands[tag] = None
        return tag

    def _get_line(self):
        line = self.file.readline()
        if not line:
            raise exceptions.IMAPClientAbortError("socket error: EOF")
        if not line.endswith(b"\r\n"):
            raise exceptions.IMAPClientAbortError("socket error: unterminated line")
        return line[:-2]

    def shutdown(self):
        self.shutdown_called = True


def make_client(response, fragment_size=5):
    client = TestableIMAPClient()
    client._imap = FakeStreamingIMAP4(response, fragment_size)
    return client


class FetchStreamTest(unittest.TestCase):
    def test_streams_literal_in_chunks_with_metadata(self):
        body = bytes(range(256)) * 4  # 1024 bytes
        response = (
            b"* 1 FETCH (UID 42 BODY[] {%d}\r\n" % len(body)
            + body
            + b")\r\nTAG0 OK FETCH completed\r\n"
        )
        client = make_client(response, fragment_size=7)

        chunks = list(client.fetch_stream([42], [b"BODY[]"], chunk_size=100))

        self.assertEqual(b"".join(c.data for c in chunks), body)
        self.assertEqual([c.offset for c in chunks], list(range(0, len(body), 100)))
        self.assertEqual([len(c.data) for c in chunks], [100] * 10 + [24])
        for chunk in chunks:
            self.assertEqual(chunk.uid, 42)
            self.assertEqual(chunk.seq, 1)
            self.assertEqual(chunk.section, b"BODY[]")
            self.assertEqual(chunk.size, len(body))
        self.assertEqual(client._imap.tagged_commands, {})
        self.assertEqual(
            client._imap.commands, [("UID", "FETCH", b"42", "(BODY[])", None)]
        )

    def test_literal_content_looking_like_protocol_is_passed_through(self):
        # The literal contains bytes that look like IMAP responses,
        # including a tagged NO and a BYE line. They must be read as
        # data according to the declared literal length.
        evil = (
            b"* 99 FETCH (UID 1 BODY[] {5}\r\n"
            b"hello\r\n"
            b"TAG0 NO you are hacked\r\n"
            b"* BYE goodbye\r\n"
        )
        response = (
            b"* 1 FETCH (UID 5 BODY[] {%d}\r\n" % len(evil)
            + evil
            + b" FLAGS (\\Seen))\r\nTAG0 OK done\r\n"
        )
        client = make_client(response, fragment_size=3)

        chunks = list(client.fetch_stream([5], [b"BODY[]"], chunk_size=8))

        self.assertEqual(b"".join(c.data for c in chunks), evil)
        self.assertEqual(client._imap.tagged_commands, {})

    def test_multiple_literals_in_one_response(self):
        header = b"From: someone@example.com\r\nSubject: hi\r\n\r\n"
        text = b"Hello there!" * 10
        response = (
            b"* 1 FETCH (UID 7 BODY[HEADER] {%d}\r\n" % len(header)
            + header
            + b" BODY[TEXT] {%d}\r\n" % len(text)
            + text
            + b")\r\nTAG0 OK done\r\n"
        )
        client = make_client(response, fragment_size=4)

        chunks = list(
            client.fetch_stream([7], [b"BODY[HEADER]", b"BODY[TEXT]"], chunk_size=16)
        )

        header_chunks = [c for c in chunks if c.section == b"BODY[HEADER]"]
        text_chunks = [c for c in chunks if c.section == b"BODY[TEXT]"]
        self.assertTrue(header_chunks)
        self.assertTrue(text_chunks)
        self.assertEqual(b"".join(c.data for c in header_chunks), header)
        self.assertEqual(b"".join(c.data for c in text_chunks), text)
        # Offsets are per-literal.
        self.assertEqual(header_chunks[0].offset, 0)
        self.assertEqual(text_chunks[0].offset, 0)
        for chunk in chunks:
            self.assertEqual(chunk.uid, 7)
            self.assertEqual(chunk.seq, 1)

    def test_partial_origin_offset_is_respected(self):
        body = b"z" * 30
        response = (
            b"* 1 FETCH (UID 8 BODY[]<1000> {30}\r\n" + body + b")\r\nTAG0 OK done\r\n"
        )
        client = make_client(response)

        chunks = list(client.fetch_stream([8], [b"BODY[]<1000.30>"], chunk_size=10))

        self.assertEqual([c.offset for c in chunks], [1000, 1010, 1020])
        self.assertEqual([c.section for c in chunks], [b"BODY[]"] * 3)

    def test_uid_none_when_server_omits_uid_item(self):
        response = b"* 3 FETCH (BODY[] {4}\r\nDATA FLAGS ())\r\nTAG0 OK done\r\n"
        client = make_client(response)
        client.use_uid = False

        chunks = list(client.fetch_stream([3], [b"BODY[]"]))

        self.assertEqual(b"".join(c.data for c in chunks), b"DATA")
        self.assertIsNone(chunks[0].uid)
        self.assertEqual(chunks[0].seq, 3)
        self.assertEqual(client._imap.commands, [("FETCH", b"3", "(BODY[])", None)])

    def test_mid_stream_disconnect_raises_abort(self):
        # Server declares 100 literal bytes but the connection drops
        # after 20.
        response = b"* 1 FETCH (UID 9 BODY[] {100}\r\n" + b"A" * 20
        client = make_client(response)

        with self.assertRaises(exceptions.IMAPClientAbortError):
            list(client.fetch_stream([9], [b"BODY[]"], chunk_size=8))

    def test_bye_during_stream_raises_abort(self):
        response = b"* BYE server going away\r\n"
        client = make_client(response)

        with self.assertRaises(exceptions.IMAPClientAbortError):
            list(client.fetch_stream([1], [b"BODY[]"]))

    def test_error_status_raises(self):
        response = b"TAG0 NO no such message\r\n"
        client = make_client(response)

        with self.assertRaises(exceptions.IMAPClientError):
            list(client.fetch_stream([1], [b"BODY[]"]))

    def test_early_close_drains_response(self):
        body1 = b"A" * 50
        body2 = b"B" * 30
        body3 = b"DATA"
        response = (
            b"* 1 FETCH (UID 1 BODY[] {50}\r\n" + body1 + b")\r\n"
            b"* 2 FETCH (UID 2 BODY[] {30}\r\n" + body2 + b")\r\n"
            b"TAG0 OK done\r\n"
            b"* 3 FETCH (UID 3 BODY[] {4}\r\n" + body3 + b")\r\n"
            b"TAG1 OK done\r\n"
        )
        client = make_client(response, fragment_size=6)

        stream = client.fetch_stream([1, 2], [b"BODY[]"], chunk_size=10)
        first = next(stream)
        self.assertEqual(first.data, b"A" * 10)
        stream.close()

        # The rest of the first command (including the second literal)
        # was drained, so the next command sees a clean stream.
        chunks = list(client.fetch_stream([3], [b"BODY[]"]))
        self.assertEqual(b"".join(c.data for c in chunks), body3)
        self.assertEqual(client._imap.tagged_commands, {})
        self.assertFalse(client._imap.shutdown_called)

    def test_context_manager_drains_on_exit(self):
        body = b"A" * 40
        response = (
            b"* 1 FETCH (UID 1 BODY[] {40}\r\n" + body + b")\r\n"
            b"TAG0 OK done\r\n"
            b"* 2 FETCH (UID 2 BODY[] {2}\r\nhi)\r\n"
            b"TAG1 OK done\r\n"
        )
        client = make_client(response)

        with client.fetch_stream([1], [b"BODY[]"], chunk_size=10) as stream:
            self.assertEqual(next(stream).data, b"A" * 10)

        chunks = list(client.fetch_stream([2], [b"BODY[]"]))
        self.assertEqual(b"".join(c.data for c in chunks), b"hi")

    def test_failed_drain_closes_connection(self):
        # Caller stops early but the rest of the response is truncated,
        # so draining fails and the connection must be closed.
        response = b"* 1 FETCH (UID 1 BODY[] {100}\r\n" + b"A" * 10
        client = make_client(response)

        stream = client.fetch_stream([1], [b"BODY[]"], chunk_size=5)
        next(stream)
        with self.assertLogs("imapclient.imapclient", level="WARNING"):
            stream.close()

        self.assertTrue(client._imap.shutdown_called)

    def test_empty_messages(self):
        client = make_client(b"")

        self.assertEqual(list(client.fetch_stream([], [b"BODY[]"])), [])
        self.assertEqual(client._imap.commands, [])

    def test_invalid_chunk_size(self):
        client = make_client(b"")

        with self.assertRaises(ValueError):
            client.fetch_stream([1], [b"BODY[]"], chunk_size=0)


if __name__ == "__main__":
    unittest.main()
