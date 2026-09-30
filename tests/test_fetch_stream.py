import imaplib
import os
import re
import threading
import unittest

from imapclient import exceptions
from imapclient.imapclient import FetchLiteralChunk, IMAPClient
from imapclient.testable_imapclient import TestableIMAPClient


class PipeSocket:
    """Minimal socket stand-in backed by OS pipes.

    Real sockets can't be used in every environment, but this
    exercises the exact same imaplib code paths (makefile/read/write)
    and lets tests control fragmentation and abrupt disconnects.
    """

    def __init__(self, read_fd, write_fd):
        self._read_fd = read_fd
        self._write_fd = write_fd
        self._file = None
        self.closed = False

    def makefile(self, mode):
        self._file = os.fdopen(self._read_fd, "rb")
        return self._file

    def sendall(self, data):
        while data:
            written = os.write(self._write_fd, data)
            data = data[written:]

    def settimeout(self, timeout):
        pass

    def shutdown(self, how):
        pass

    def close(self):
        self.closed = True
        if self._file is not None and not self._file.closed:
            self._file.close()
        try:
            os.close(self._write_fd)
        except OSError:
            pass


def socket_pair():
    a_to_b_read, a_to_b_write = os.pipe()
    b_to_a_read, b_to_a_write = os.pipe()
    return (
        PipeSocket(b_to_a_read, a_to_b_write),
        PipeSocket(a_to_b_read, b_to_a_write),
    )


class FakeIMAP4(imaplib.IMAP4):
    """A real imaplib.IMAP4 wired to one end of a (pipe) socket pair."""

    def __init__(self, sock):
        self._pair_sock = sock
        super().__init__("localhost", 143)

    def _create_socket(self, timeout=None):
        return self._pair_sock

    def _connect(self):
        # Skip the greeting exchange; pretend we are ready to go.
        self.tagpre = imaplib.Int2AP(4096)
        self.tagre = re.compile(
            br"(?P<tag>" + self.tagpre + br"\d+) (?P<type>[A-Z]+) (?P<data>.*)",
            re.ASCII,
        )
        self.state = "SELECTED"


class SocketPairClient(IMAPClient):
    def __init__(self, sock):
        self._pair_sock = sock
        super().__init__("localhost", ssl=False)

    def _create_IMAP4(self):
        return FakeIMAP4(self._pair_sock)


def send_fragmented(sock, data, fragment=5):
    """Send *data* in small pieces to exercise partial socket reads."""
    for i in range(0, len(data), fragment):
        sock.sendall(data[i : i + fragment])


def read_command_tag(fileobj):
    line = fileobj.readline()
    if not line:
        raise AssertionError("expected a command from the client, got EOF")
    return line.split(b" ", 1)[0]


class FakeServer:
    """Runs *handler* (a callable taking the server socket) in a thread."""

    def __init__(self, handler):
        self.handler = handler
        self.errors = []

    def __enter__(self):
        self.client_sock, self.server_sock = socket_pair()
        self.thread = threading.Thread(target=self._run)
        self.thread.daemon = True
        self.thread.start()
        self.client = SocketPairClient(self.client_sock)
        return self.client

    def _run(self):
        try:
            self.handler(self.server_sock)
        except Exception as e:  # noqa: BLE001 - reported to the test
            self.errors.append(e)
        finally:
            try:
                self.server_sock.close()
            except OSError:
                pass

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.thread.join(timeout=5)
        self.client_sock.close()
        assert not self.thread.is_alive(), "fake server did not finish"
        if self.errors:
            raise AssertionError("fake server errors: %r" % (self.errors,))


def reassemble(chunks):
    return b"".join(chunk.data for chunk in chunks)


class TestFetchStream(unittest.TestCase):
    def test_empty_message_list_sends_nothing(self):
        client = TestableIMAPClient()
        self.assertEqual(list(client.fetch_stream([], ["BODY[1]"])), [])
        self.assertEqual(client._imap.sent, b"")

    def test_invalid_chunk_size(self):
        client = TestableIMAPClient()
        with self.assertRaises(ValueError):
            list(client.fetch_stream(1, ["BODY[1]"], chunk_size=0))

    def test_streams_literal_with_metadata_over_fragmented_socket(self):
        # Literal content deliberately contains bytes that look like
        # IMAP protocol responses; they must be read as data according
        # to the declared literal size.
        payload = (
            b"* 99 FETCH (BODY[] {5}\r\n"
            b"a1 OK this is not a real tagged response\r\n"
            b"+ idling\r\n"
            b"\x00\xff binary \x01\x02"
        ) * 4

        def server(sock):
            f = sock.makefile("rb")
            tag = read_command_tag(f)
            send_fragmented(sock, b"* 1 FETCH (UID 42 BODY[1] {%d}\r\n" % len(payload))
            send_fragmented(sock, payload)
            send_fragmented(sock, b" FLAGS (\\Seen))\r\n")
            send_fragmented(sock, tag + b" OK Fetch completed\r\n")
            tag = read_command_tag(f)
            send_fragmented(sock, tag + b" OK Noop done\r\n")

        with FakeServer(server) as client:
            chunks = list(client.fetch_stream(1, ["BODY[1]"], chunk_size=16))

            self.assertTrue(chunks)
            self.assertTrue(all(isinstance(c, FetchLiteralChunk) for c in chunks))
            self.assertTrue(all(len(c.data) <= 16 for c in chunks))
            self.assertEqual(reassemble(chunks), payload)
            self.assertTrue(all(c.seq == 1 for c in chunks))
            self.assertTrue(all(c.uid == 42 for c in chunks))
            self.assertTrue(all(c.section == b"BODY[1]" for c in chunks))
            self.assertEqual(
                [c.offset for c in chunks], list(range(0, len(payload), 16))
            )

            # The connection must be clean for the next command.
            client.noop()

    def test_multiple_literals_and_messages(self):
        payload1 = b"first literal * 7 FETCH lookalike\r\n"
        payload2 = b"second"
        payload3 = b"third literal"

        def server(sock):
            f = sock.makefile("rb")
            tag = read_command_tag(f)
            send_fragmented(
                sock,
                b"* 3 FETCH (UID 101 BODY[1] {%d}\r\n" % len(payload1),
                fragment=3,
            )
            send_fragmented(sock, payload1, fragment=3)
            send_fragmented(sock, b" BODY[2]<20> {%d}\r\n" % len(payload2), fragment=3)
            send_fragmented(sock, payload2, fragment=3)
            send_fragmented(sock, b")\r\n", fragment=3)
            send_fragmented(
                sock,
                b"* 4 FETCH (UID 102 BODY[1] {%d}\r\n" % len(payload3),
                fragment=3,
            )
            send_fragmented(sock, payload3, fragment=3)
            send_fragmented(sock, b")\r\n", fragment=3)
            send_fragmented(sock, tag + b" OK Fetch completed\r\n", fragment=3)
            tag = read_command_tag(f)
            send_fragmented(sock, tag + b" OK Noop done\r\n")

        with FakeServer(server) as client:
            chunks = list(client.fetch_stream([101, 102], ["BODY[1]", "BODY[2]"]))

            self.assertEqual(
                [(c.seq, c.uid, c.section) for c in chunks],
                [
                    (3, 101, b"BODY[1]"),
                    (3, 101, b"BODY[2]"),
                    (4, 102, b"BODY[1]"),
                ],
            )
            self.assertEqual(chunks[0].data, payload1)
            self.assertEqual(chunks[0].offset, 0)
            self.assertEqual(chunks[1].data, payload2)
            # Partial BODY[2]<20> response: offset starts at the origin.
            self.assertEqual(chunks[1].offset, 20)
            self.assertEqual(chunks[2].data, payload3)
            self.assertEqual(chunks[2].offset, 0)

            client.noop()

    def test_early_close_drains_remaining_response(self):
        payload = b"x" * 100000

        def server(sock):
            f = sock.makefile("rb")
            tag = read_command_tag(f)
            send_fragmented(sock, b"* 1 FETCH (UID 42 BODY[1] {%d}\r\n" % len(payload))
            send_fragmented(sock, payload, fragment=4096)
            send_fragmented(sock, b")\r\n")
            send_fragmented(sock, tag + b" OK Fetch completed\r\n")
            # If the client failed to drain, this read would see
            # attachment fragments instead of the NOOP command (and
            # the sends above would likely have failed with EPIPE).
            tag = read_command_tag(f)
            send_fragmented(sock, tag + b" OK Noop done\r\n")

        with FakeServer(server) as client:
            stream = client.fetch_stream(1, ["BODY[1]"], chunk_size=1024)
            first = next(stream)
            self.assertEqual(first.offset, 0)
            self.assertEqual(len(first.data), 1024)
            stream.close()

            client.noop()

    def test_mid_literal_disconnect(self):
        payload = b"short"

        def server(sock):
            f = sock.makefile("rb")
            read_command_tag(f)
            # Declare a large literal, send only a fragment of it,
            # then drop the connection.
            send_fragmented(sock, b"* 1 FETCH (UID 42 BODY[1] {100000}\r\n")
            send_fragmented(sock, payload)
            sock.close()

        with FakeServer(server) as client:
            with self.assertRaises(exceptions.IMAPClientAbortError):
                list(client.fetch_stream(1, ["BODY[1]"]))

    def test_disconnect_before_tagged_response_on_early_close(self):
        payload = b"y" * 1000

        def server(sock):
            f = sock.makefile("rb")
            read_command_tag(f)
            send_fragmented(sock, b"* 1 FETCH (UID 42 BODY[1] {%d}\r\n" % len(payload))
            send_fragmented(sock, payload)
            # No tagged response; the server drops the connection
            # while the client is draining.
            sock.close()

        with FakeServer(server) as client:
            stream = client.fetch_stream(1, ["BODY[1]"], chunk_size=16)
            next(stream)
            # Draining fails, so the connection is closed instead of
            # leaking fragments into subsequent commands.
            stream.close()
            self.assertTrue(client._imap.file.closed)

    def test_tagged_no_raises_and_connection_stays_usable(self):
        def server(sock):
            f = sock.makefile("rb")
            tag = read_command_tag(f)
            send_fragmented(sock, tag + b" NO No such message\r\n")
            tag = read_command_tag(f)
            send_fragmented(sock, tag + b" OK Noop done\r\n")

        with FakeServer(server) as client:
            with self.assertRaises(exceptions.IMAPClientError):
                list(client.fetch_stream(1, ["BODY[1]"]))
            client.noop()

    def test_section_with_spaces_and_empty_literal(self):
        payload = b"From: a@b.c\r\n"

        def server(sock):
            f = sock.makefile("rb")
            tag = read_command_tag(f)
            send_fragmented(
                sock,
                b"* 1 FETCH (UID 7 BODY[HEADER.FIELDS (FROM TO)] {%d}\r\n"
                % len(payload),
            )
            send_fragmented(sock, payload)
            send_fragmented(sock, b" BODY[3] {0}\r\n")
            send_fragmented(sock, b")\r\n")
            send_fragmented(sock, tag + b" OK Fetch completed\r\n")
            tag = read_command_tag(f)
            send_fragmented(sock, tag + b" OK Noop done\r\n")

        with FakeServer(server) as client:
            chunks = list(
                client.fetch_stream(
                    7, ["BODY[HEADER.FIELDS (FROM TO)]", "BODY[3]"], chunk_size=4
                )
            )

            # The zero-length literal yields no chunks and does not
            # confuse the parser.
            self.assertTrue(chunks)
            self.assertTrue(
                all(c.section == b"BODY[HEADER.FIELDS (FROM TO)]" for c in chunks)
            )
            self.assertTrue(all(c.uid == 7 for c in chunks))
            self.assertEqual(reassemble(chunks), payload)

            client.noop()

    def test_normal_fetch_return_form_unchanged(self):
        payload = b"Subject: hi\r\n\r\nbody\r\n"

        def server(sock):
            f = sock.makefile("rb")
            tag = read_command_tag(f)
            send_fragmented(sock, b"* 1 FETCH (UID 42 BODY[1] {%d}\r\n" % len(payload))
            send_fragmented(sock, payload)
            send_fragmented(sock, b" FLAGS (\\Seen))\r\n")
            send_fragmented(sock, tag + b" OK Fetch completed\r\n")

        with FakeServer(server) as client:
            result = client.fetch(42, ["BODY[1]"])

            self.assertEqual(
                result,
                {42: {b"SEQ": 1, b"FLAGS": (b"\\Seen",), b"BODY[1]": payload}},
            )


if __name__ == "__main__":
    unittest.main()
