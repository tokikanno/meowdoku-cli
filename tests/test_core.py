import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from meowdoku.levels import LevelError, LevelStore, parse_level
from meowdoku.model import CAT, EMPTY, GUESS, MARK, WRONG, Game
from meowdoku.solver import solve

LEVEL = """6
AAACCB
ACACCB
CCCCCC
DEECCF
DEEECF
DEEEFF
# solution: 1 5 3 0 2 4
"""


def new_game():
    return Game(parse_level(LEVEL))


class ParseTest(unittest.TestCase):
    def test_parse(self):
        p = parse_level(LEVEL)
        self.assertEqual(p.n, 6)
        self.assertEqual(p.regions[0], (0, 0, 0, 2, 2, 1))
        self.assertEqual(p.solution, (1, 5, 3, 0, 2, 4))

    def test_missing_solution_is_solved(self):
        text = LEVEL.rsplit('#', 1)[0]
        self.assertEqual(parse_level(text).solution, (1, 5, 3, 0, 2, 4))

    def test_solution_is_unique(self):
        self.assertEqual(solve(parse_level(LEVEL).regions), [(1, 5, 3, 0, 2, 4)])

    def test_bad_shape(self):
        with self.assertRaises(LevelError):
            parse_level('3\nAAB\nAB\nCCC\n')


class GameTest(unittest.TestCase):
    def test_mark_mode(self):
        g = new_game()
        g.toggle_mark(0, 0)
        self.assertEqual(g.board[0][0], MARK)
        g.toggle_mark(0, 0)
        self.assertEqual(g.board[0][0], EMPTY)

    def test_guess_mode(self):
        g = new_game()
        g.guess_mode = True
        g.toggle_mark(0, 0)
        self.assertEqual(g.board[0][0], GUESS)
        g.guess_mode = False
        g.toggle_mark(0, 0)  # a guess is promoted to a certain mark
        self.assertEqual(g.board[0][0], MARK)
        g.guess_mode = True
        self.assertFalse(g.toggle_mark(0, 0))  # never demoted
        self.assertEqual(g.board[0][0], MARK)

    def test_reveal_cat_auto_marks(self):
        g = new_game()
        g.guess_mode = True
        g.toggle_mark(0, 4)
        self.assertEqual(g.reveal(0, 1), 'cat')
        self.assertEqual(g.board[0][1], CAT)
        self.assertEqual(g.board[0][4], MARK)  # row, guess promoted
        self.assertEqual(g.board[5][1], MARK)  # column
        self.assertEqual(g.board[1][2], MARK)  # diagonal neighbour
        self.assertEqual(g.board[1][0], MARK)  # region A
        self.assertEqual(g.board[3][3], EMPTY)
        self.assertTrue(g.undo())
        self.assertEqual(g.board[0][4], GUESS)
        self.assertEqual(sum(st != EMPTY for row in g.board for st in row), 1)

    def test_auto_mark_off(self):
        g = new_game()
        g.auto_mark = False
        g.reveal(0, 1)
        self.assertEqual(sum(st != EMPTY for row in g.board for st in row), 1)

    def test_wrong_costs_heart_not_refunded(self):
        g = new_game()
        self.assertEqual(g.reveal(0, 0), 'wrong')
        self.assertEqual((g.board[0][0], g.hearts), (WRONG, 2))
        self.assertIsNone(g.reveal(0, 0))
        g.undo()
        self.assertEqual((g.board[0][0], g.hearts), (EMPTY, 2))
        g.redo()
        self.assertEqual((g.board[0][0], g.hearts), (WRONG, 1))

    def test_lose(self):
        g = new_game()
        for c in (0, 2, 3):
            g.reveal(0, c)
        self.assertTrue(g.lost and g.over)
        self.assertFalse(g.undo())
        self.assertIsNone(g.reveal(0, 1))

    def test_win(self):
        g = new_game()
        for r, c in enumerate(g.puzzle.solution):
            g.reveal(r, c)
        self.assertTrue(g.won)
        self.assertEqual(g.hearts, 3)

    def test_redo_cleared_by_new_move(self):
        g = new_game()
        g.toggle_mark(0, 0)
        g.undo()
        g.toggle_mark(1, 1)
        self.assertFalse(g.redo())

    def test_noop_move_not_recorded(self):
        g = new_game()
        g.reveal(0, 1)
        n = len(g.undo_stack)
        g.toggle_mark(0, 1)  # cat cells can't be marked
        self.assertEqual(len(g.undo_stack), n)


class StoreTest(unittest.TestCase):
    def test_local_bundle(self):
        with tempfile.TemporaryDirectory() as d:
            data = json.dumps({'schemaVersion': 1, 'pack': '6', 'levels': [LEVEL]}).encode()
            (Path(d) / '6.json').write_bytes(data)
            manifest = {'packs': {'6': {'count': 1, 'file': '6.json',
                                        'sha256': hashlib.sha256(data).hexdigest()}}}
            (Path(d) / 'manifest.json').write_text(json.dumps(manifest))
            store = LevelStore(d)
            self.assertEqual(store.packs, ['6'])
            self.assertEqual(store.load('6', 1).n, 6)
            with self.assertRaises(LevelError):
                store.load('6', 2)
            with self.assertRaises(LevelError):
                store.load('9', 1)

    def test_local_bundle_bad_hash(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / '6.json').write_text('{}')
            manifest = {'packs': {'6': {'count': 1, 'file': '6.json', 'sha256': '0' * 64}}}
            (Path(d) / 'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaises(LevelError):
                LevelStore(d).load('6', 1)


if __name__ == '__main__':
    unittest.main()
