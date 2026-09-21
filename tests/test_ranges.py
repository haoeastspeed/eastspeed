# -*- coding: utf-8 -*-
"""RangeSet / SegmentAllocator 单元测试。

运行: python -m unittest tests.test_ranges -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.ranges import RangeSet, SegmentAllocator  # noqa: E402


class TestRangeSet(unittest.TestCase):
    def test_add_merge_overlap(self):
        rs = RangeSet()
        rs.add(0, 10)
        rs.add(5, 20)
        rs.add(30, 40)
        self.assertEqual(rs.to_list(), [[0, 20], [30, 40]])

    def test_add_merge_adjacent(self):
        rs = RangeSet()
        rs.add(0, 10)
        rs.add(10, 20)
        self.assertEqual(rs.to_list(), [[0, 20]])

    def test_remove_splits(self):
        rs = RangeSet([[0, 100]])
        rs.remove_range(40, 60)
        self.assertEqual(rs.to_list(), [[0, 40], [60, 100]])
        rs.remove_range(0, 10)
        self.assertEqual(rs.to_list(), [[10, 40], [60, 100]])
        rs.remove_range(90, 100)
        self.assertEqual(rs.to_list(), [[10, 40], [60, 90]])

    def test_gaps(self):
        rs = RangeSet([[10, 20], [30, 40]])
        self.assertEqual(rs.gaps(0, 50), [[0, 10], [20, 30], [40, 50]])
        self.assertEqual(rs.covered_length(), 20)

    def test_merge_sets(self):
        a = RangeSet([[0, 10]])
        b = RangeSet([[5, 20]])
        self.assertEqual(RangeSet.merge(a, b).to_list(), [[0, 20]])


class TestSegmentAllocator(unittest.TestCase):
    def setUp(self):
        # 10 个块，每块 100 字节 => 1000 字节
        self.alloc = SegmentAllocator(1000, block_size=100)

    def test_claim_takes_block_from_tail_of_largest_gap(self):
        s, e = self.alloc.claim()
        # 最大区间 [0,1000)，从尾部领取一个 100 字节块
        self.assertEqual((s, e), (900, 1000))
        s, e = self.alloc.claim()
        # 剩余最大区间 [0,900)，继续从尾部领取
        self.assertEqual((s, e), (800, 900))

    def test_claim_covers_every_byte_once(self):
        claimed = []
        while True:
            r = self.alloc.claim()
            if r is None:
                break
            claimed.append(r)
            self.alloc.mark_done(*r)
        # 所有区间并集恰好是 [0,1000)，无重叠
        rs = RangeSet(claimed)
        self.assertEqual(rs.to_list(), [[0, 1000]])
        self.assertTrue(self.alloc.is_complete())

    def test_release_makes_range_available_again(self):
        s, e = self.alloc.claim()
        self.alloc.release(s, e)
        s2, e2 = self.alloc.claim()
        self.assertEqual((s, e), (s2, e2))

    def test_completed_blocks_are_skipped(self):
        # 前 600 字节已完成（模拟断点续传）
        alloc = SegmentAllocator(1000, 100, completed=[[0, 600]])
        claimed = []
        while True:
            r = alloc.claim()
            if r is None:
                break
            claimed.append(r)
            alloc.mark_done(*r)
        self.assertEqual(RangeSet(claimed).to_list(), [[600, 1000]])
        self.assertTrue(alloc.is_complete())

    def test_mark_done_partial_then_release(self):
        s, e = self.alloc.claim()  # (500,1000)
        self.alloc.mark_done(500, 600)
        self.alloc.release(s, e)
        # 已完成的 500-600 不会再次被领取
        claimed = []
        while True:
            r = self.alloc.claim()
            if r is None:
                break
            claimed.append(r)
            self.alloc.mark_done(*r)
        rs = RangeSet([[500, 600]] + claimed)
        self.assertEqual(rs.to_list(), [[0, 1000]])

    def test_odd_size_not_aligned(self):
        alloc = SegmentAllocator(250, 100)
        claimed = []
        while True:
            r = alloc.claim()
            if r is None:
                break
            claimed.append(r)
            alloc.mark_done(*r)
        self.assertEqual(RangeSet(claimed).to_list(), [[0, 250]])


if __name__ == "__main__":
    unittest.main(verbosity=2)
