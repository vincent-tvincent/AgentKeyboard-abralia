# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

import unittest
from macos_pause_listener import PauseEdges, PauseTap

class PauseListenerEdgeTests(unittest.TestCase):
    def test_repeat_down_and_unmatched_up_do_not_duplicate_click(self):
        edges = PauseEdges()
        self.assertIsNone(edges.feed("device-key", False, 1))
        self.assertIsNone(edges.feed("device-key", True, 2))
        self.assertIsNone(edges.feed("device-key", True, 2.1))
        self.assertEqual(edges.feed("device-key", False, 3), PauseTap(2, 3))
        self.assertIsNone(edges.feed("device-key", False, 3.1))

    def test_duplicate_keyboard_interface_values_are_coalesced(self):
        edges = PauseEdges()
        edges.feed("boot", True, 1)
        edges.feed("nkro", True, 1)
        self.assertEqual(edges.feed("boot", False, 1.01), PauseTap(1, 1.01))
        self.assertIsNone(edges.feed("nkro", False, 1.012))
        edges.feed("boot", True, 2)
        self.assertEqual(edges.feed("boot", False, 2.01), PauseTap(2, 2.01))

    def test_out_of_order_release_is_rejected(self):
        edges = PauseEdges()
        edges.feed("key", True, 10)
        self.assertIsNone(edges.feed("key", False, 9))


if __name__ == "__main__":
    unittest.main()
