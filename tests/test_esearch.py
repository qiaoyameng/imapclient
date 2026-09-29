# Copyright (c) 2014, Menno Smits
# Released subject to the New BSD License
# Please see http://en.wikipedia.org/wiki/BSD_licenses

from unittest.mock import Mock

from imapclient.exceptions import CapabilityError, ProtocolError
from imapclient.response_types import UidRanges

from .imapclient_test import IMAPClientTest


class TestUidRanges(IMAPClientTest):
    def test_empty(self):
        ranges = UidRanges()
        self.assertEqual(len(ranges), 0)
        self.assertFalse(ranges)
        self.assertEqual(list(ranges), [])
        self.assertEqual(ranges.ranges, ())
        self.assertNotIn(1, ranges)

    def test_single_range(self):
        ranges = UidRanges([(4, 8)])
        self.assertEqual(len(ranges), 5)
        self.assertTrue(ranges)
        self.assertEqual(list(ranges), [4, 5, 6, 7, 8])
        self.assertEqual(ranges.ranges, ((4, 8),))

    def test_reversed_range_is_normalised(self):
        ranges = UidRanges([(8, 4)])
        self.assertEqual(ranges.ranges, ((4, 8),))
        self.assertEqual(list(ranges), [4, 5, 6, 7, 8])

    def test_unsorted_and_overlapping_ranges_are_merged(self):
        ranges = UidRanges([(20, 25), (1, 3), (2, 10), (7, 7)])
        self.assertEqual(ranges.ranges, ((1, 10), (20, 25)))
        self.assertEqual(len(ranges), 16)

    def test_adjacent_ranges_are_merged(self):
        ranges = UidRanges([(1, 3), (4, 6)])
        self.assertEqual(ranges.ranges, ((1, 6),))

    def test_non_contiguous_ranges_are_kept(self):
        ranges = UidRanges([(1, 3), (7, 7), (20, 25)])
        self.assertEqual(ranges.ranges, ((1, 3), (7, 7), (20, 25)))
        self.assertEqual(len(ranges), 10)
        self.assertEqual(list(ranges), [1, 2, 3, 7, 20, 21, 22, 23, 24, 25])

    def test_membership(self):
        ranges = UidRanges([(1, 3), (7, 7), (20, 25)])
        for uid in (1, 2, 3, 7, 20, 25):
            self.assertIn(uid, ranges)
        for uid in (0, 4, 6, 8, 19, 26, 100):
            self.assertNotIn(uid, ranges)
        self.assertNotIn("7", ranges)
        self.assertNotIn(None, ranges)

    def test_indexing(self):
        ranges = UidRanges([(1, 3), (7, 7), (20, 25)])
        self.assertEqual(ranges[0], 1)
        self.assertEqual(ranges[2], 3)
        self.assertEqual(ranges[3], 7)
        self.assertEqual(ranges[4], 20)
        self.assertEqual(ranges[-1], 25)
        self.assertEqual(ranges[-5], 21)
        self.assertRaises(IndexError, lambda: ranges[10])
        self.assertRaises(IndexError, lambda: ranges[-11])

    def test_slice_returns_lazy_ranges(self):
        ranges = UidRanges([(1, 3), (7, 7), (20, 25)])
        sliced = ranges[1:8]
        self.assertIsInstance(sliced, UidRanges)
        self.assertEqual(sliced.ranges, ((2, 3), (7, 7), (20, 23)))
        self.assertEqual(list(sliced), [2, 3, 7, 20, 21, 22, 23])

    def test_slice_boundaries(self):
        ranges = UidRanges([(1, 3), (7, 7), (20, 25)])
        self.assertEqual(ranges[:].ranges, ranges.ranges)
        self.assertEqual(ranges[3:4].ranges, ((7, 7),))
        self.assertEqual(ranges[4:].ranges, ((20, 25),))
        self.assertEqual(ranges[:2].ranges, ((1, 2),))
        self.assertEqual(ranges[100:200].ranges, ())
        self.assertEqual(ranges[-6:-2].ranges, ((20, 23),))

    def test_stepped_slice_returns_list(self):
        ranges = UidRanges([(1, 3), (20, 25)])
        self.assertEqual(ranges[::2], [1, 3, 21, 23, 25])
        self.assertEqual(ranges[::-1], [25, 24, 23, 22, 21, 20, 3, 2, 1])

    def test_huge_range_is_not_expanded(self):
        ranges = UidRanges([(1, 2**32 - 1)])
        self.assertEqual(len(ranges), 2**32 - 1)
        self.assertIn(2**32 - 1, ranges)
        self.assertIn(2**31, ranges)
        self.assertNotIn(2**32, ranges)
        self.assertEqual(ranges[0], 1)
        self.assertEqual(ranges[-1], 2**32 - 1)
        sliced = ranges[10:20]
        self.assertIsInstance(sliced, UidRanges)
        self.assertEqual(sliced.ranges, ((11, 20),))
        # repr shows the ranges, not the individual UIDs
        self.assertEqual(repr(ranges), "UidRanges([(1, 4294967295)])")

    def test_equality(self):
        self.assertEqual(UidRanges([(1, 3)]), UidRanges([(3, 1)]))
        self.assertEqual(UidRanges([(1, 2), (3, 4)]), UidRanges([(1, 4)]))
        self.assertNotEqual(UidRanges([(1, 3)]), UidRanges([(1, 4)]))
        self.assertNotEqual(UidRanges([(1, 3)]), [1, 2, 3])
        self.assertEqual(hash(UidRanges([(1, 3)])), hash(UidRanges([(3, 1)])))

    def test_invalid_uids(self):
        self.assertRaises(ValueError, UidRanges, [(0, 5)])
        self.assertRaises(ValueError, UidRanges, [(-3, -1)])


class TestEsearch(IMAPClientTest):
    def setUp(self):
        super(TestEsearch, self).setUp()
        self.client._cached_capabilities = (b"ESEARCH",)
        self.client._raw_command = Mock()
        self.client._raw_command.return_value = ("OK", [b""])
        self.set_response(b'(TAG "tag") UID COUNT 3 MIN 4 MAX 8 ALL 4:8')

    def set_response(self, *lines):
        self.client._imap.untagged_responses = {"ESEARCH": list(lines)}

    def check_call(self, expected_args):
        self.client._raw_command.assert_called_once_with(
            b"SEARCH", expected_args, tag="tag"
        )

    def test_no_support(self):
        self.client._cached_capabilities = (b"BLAH",)
        self.assertRaises(CapabilityError, self.client.esearch)

    def test_default_return_options(self):
        result = self.client.esearch()

        self.check_call([b"RETURN", b"(ALL)", b"ALL"])
        self.assertEqual(result["ALL"], UidRanges([(4, 8)]))
        self.assertEqual(result["COUNT"], 3)
        self.assertEqual(result["MIN"], 4)
        self.assertEqual(result["MAX"], 8)

    def test_all_return_options(self):
        result = self.client.esearch(
            ["TEXT", "hello"], return_options=["COUNT", "MIN", "MAX", "ALL"]
        )

        self.check_call([b"RETURN", b"(ALL COUNT MAX MIN)", b"TEXT", b"hello"])
        self.assertEqual(
            result,
            {"COUNT": 3, "MIN": 4, "MAX": 8, "ALL": UidRanges([(4, 8)])},
        )

    def test_return_options_as_string(self):
        self.client.esearch(return_options="COUNT")
        self.check_call([b"RETURN", b"(COUNT)", b"ALL"])

    def test_charset(self):
        self.client.esearch(["TEXT", "foo"], charset="UTF-8")
        self.check_call(
            [b"RETURN", b"(ALL)", b"CHARSET", b"UTF-8", b"TEXT", b"foo"]
        )

    def test_invalid_return_option(self):
        self.assertRaises(ValueError, self.client.esearch, return_options=["BOGUS"])
        self.assertRaises(ValueError, self.client.esearch, return_options=[])

    def test_huge_range(self):
        self.set_response(
            b'(TAG "tag") UID COUNT 4294967295 MIN 1 MAX 4294967295 ALL 1:4294967295'
        )

        result = self.client.esearch()

        all_uids = result["ALL"]
        self.assertIsInstance(all_uids, UidRanges)
        self.assertEqual(len(all_uids), 4294967295)
        self.assertEqual(all_uids.ranges, ((1, 4294967295),))
        self.assertIn(4294967295, all_uids)
        self.assertNotIn(4294967296, all_uids)
        self.assertEqual(all_uids[-1], 4294967295)
        self.assertEqual(all_uids[1000000:1000003].ranges, ((1000001, 1000003),))

    def test_reversed_range(self):
        self.set_response(b'(TAG "tag") UID ALL 8:4')

        result = self.client.esearch()

        self.assertEqual(result["ALL"].ranges, ((4, 8),))
        self.assertEqual(list(result["ALL"]), [4, 5, 6, 7, 8])

    def test_empty_result(self):
        self.set_response(b'(TAG "tag") UID COUNT 0')

        result = self.client.esearch(return_options=["COUNT", "MIN", "MAX", "ALL"])

        self.assertEqual(result["COUNT"], 0)
        self.assertNotIn("MIN", result)
        self.assertNotIn("MAX", result)
        self.assertEqual(result["ALL"], UidRanges())
        self.assertEqual(len(result["ALL"]), 0)
        self.assertFalse(result["ALL"])

    def test_non_contiguous_result(self):
        self.set_response(b'(TAG "tag") UID COUNT 10 ALL 1:3,7,20:25')

        result = self.client.esearch()

        all_uids = result["ALL"]
        self.assertEqual(all_uids.ranges, ((1, 3), (7, 7), (20, 25)))
        self.assertEqual(len(all_uids), 10)
        self.assertIn(7, all_uids)
        self.assertNotIn(8, all_uids)
        self.assertEqual(list(all_uids), [1, 2, 3, 7, 20, 21, 22, 23, 24, 25])
        self.assertEqual(all_uids[2:5].ranges, ((3, 3), (7, 7), (20, 20)))

    def test_missing_tag_correlator_is_accepted(self):
        self.set_response(b"UID COUNT 2 ALL 1:2")

        result = self.client.esearch()

        self.assertEqual(result["ALL"], UidRanges([(1, 2)]))

    def test_tag_mismatch(self):
        self.set_response(b'(TAG "othertag") UID COUNT 2 ALL 1:2')

        self.assertRaises(ProtocolError, self.client.esearch)

    def test_uid_indicator_missing(self):
        # use_uid is on but the response lacks the UID indicator
        self.set_response(b'(TAG "tag") COUNT 2 ALL 1:2')

        self.assertRaises(ProtocolError, self.client.esearch)

    def test_uid_indicator_unexpected(self):
        self.client.use_uid = False
        self.set_response(b'(TAG "tag") UID COUNT 2 ALL 1:2')

        self.assertRaises(ProtocolError, self.client.esearch)

    def test_non_uid_search(self):
        self.client.use_uid = False
        self.set_response(b'(TAG "tag") COUNT 2 ALL 1:2')

        result = self.client.esearch()

        self.assertEqual(result["COUNT"], 2)
        self.assertEqual(result["ALL"], UidRanges([(1, 2)]))

    def test_no_esearch_response(self):
        self.client._imap.untagged_responses = {}

        self.assertRaises(ProtocolError, self.client.esearch)

    def test_malformed_response(self):
        self.set_response(b'(TAG "tag") UID COUNT')
        self.assertRaises(ProtocolError, self.client.esearch)

        self.set_response(b'(TAG "tag") UID BOGUS 3')
        self.assertRaises(ProtocolError, self.client.esearch)

        self.set_response(b'(TAG "tag") UID ALL 1:*')
        self.assertRaises(ProtocolError, self.client.esearch)

    def test_str_response(self):
        self.set_response('(TAG "tag") UID COUNT 1 ALL 5')

        result = self.client.esearch()

        self.assertEqual(result["ALL"], UidRanges([(5, 5)]))

    def test_end_to_end_wire_format(self):
        # Use the real _raw_command to check what goes on the wire and
        # that the ESEARCH response is consumed.
        del self.client._raw_command
        self.client._imap._command_complete.return_value = ("OK", [b""])
        self.set_response(b'(TAG "tag") UID COUNT 2 ALL 1:2')

        result = self.client.esearch(["UNSEEN"])

        self.assertEqual(
            self.client._imap.sent, b"tag UID SEARCH RETURN (ALL) UNSEEN\r\n"
        )
        self.assertEqual(result["COUNT"], 2)
        self.assertEqual(result["ALL"], UidRanges([(1, 2)]))
        self.assertNotIn("ESEARCH", self.client._imap.untagged_responses)
