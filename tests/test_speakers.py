import numpy as np

from voice_notes.speakers import label_lines, merge_clusters
from voice_notes.transcript import Segment


def unit(*v):
    v = np.asarray(v, dtype=float)
    return v/np.linalg.norm(v)


def test_pieces_of_one_voice_are_joined_and_tiny_ones_folded_in():
    turns = [(0, 60, 'a'), (60, 70, 'b'), (70, 100, 'c'), (100, 101, 'd'), (101, 130, 'a2')]
    prints = {'a': unit(1, 0, 0), 'a2': unit(.95, .1, 0),       # same person, split by clustering
              'b': unit(0, 1, 0), 'c': unit(0, .2, 1),          # two other people
              'd': unit(.1, .9, .1)}                              # one second: folded into the closest (b)
    merged = merge_clusters(turns, prints, min_seconds=8)
    assert [c for _, _, c in merged] == ['a', 'b', 'c', 'b', 'a']


def test_lines_take_the_speaker_they_overlap_most_numbered_in_order():
    turns = [(0, 5, 7), (5, 9, 3), (9, 20, 7)]
    segs = [Segment(0, 4, 'a'), Segment(4, 8.5, 'b'), Segment(9.5, 12, 'c'), Segment(30, 31, 'late')]
    assert label_lines(segs, turns) == ['S1', 'S2', 'S1', 'S1']   # 'late' falls back to the nearest turn
