import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import itertools
import random

from meowdoku.game_view import BOX, line_code
from meowdoku.generator import (format_level, generate, generate_from_seed, logic_solvable,
                                parse_seed_code, seed_code)
from meowdoku.levels import Favorites, LevelError, LevelStore, Progress, parse_level
from meowdoku.model import CAT, EMPTY, GUESS, MARK, WRONG, Game
from meowdoku.solver import solutions
from meowdoku.term import (REGION_COLORS, REGION_COLORS_DIM, PairPool, assign_slots,
                           hex_to_rgb)

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
        self.assertEqual(solutions(parse_level(LEVEL).regions), [(1, 5, 3, 0, 2, 4)])

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

    def test_forced_cell(self):
        g = new_game()
        self.assertIsNone(g.forced_cell())
        g.toggle_mark(0, 5)  # region B: (0,5), (1,5) → only B6 left
        self.assertEqual(g.forced_cell(), (1, 5))

    def test_forced_cell_ignores_rows_and_columns(self):
        g = new_game()
        for c in range(1, 6):  # row A is down to A1, but region A still has three cells
            g.toggle_mark(0, c)
        g.toggle_mark(1, 5)  # and region B (A6, B6) is fully marked, so nothing qualifies
        self.assertIsNone(g.forced_cell())

    def test_forced_cell_reading_order_and_guesses(self):
        g = new_game()
        # Region F: (3,5), (4,5), (5,4), (5,5). Guess (3,5): guesses don't count as marks.
        g.toggle_mark(4, 5)
        g.toggle_mark(5, 4)
        g.guess_mode = True
        g.toggle_mark(3, 5)
        self.assertIsNone(g.forced_cell())
        g.guess_mode = False
        g.toggle_mark(3, 5)  # promote to a mark: F is down to F6
        g.toggle_mark(0, 5)  # region B down to B6, earlier in reading order
        self.assertEqual(g.forced_cell(), (1, 5))

    def test_forced_cell_skips_solved_units(self):
        g = new_game()
        g.reveal(0, 1)  # auto-mark leaves row A, column 2 and region A solved
        cell = g.forced_cell()
        self.assertTrue(cell is None or g.board[cell[0]][cell[1]] == EMPTY)

    def test_save_round_trip(self):
        g = new_game()
        g.toggle_mark(0, 0)
        g.reveal(2, 2)  # wrong: costs a heart
        g.guess_mode = True
        g.toggle_mark(3, 3)
        g.undo()
        data = json.loads(json.dumps(g.to_dict()))
        h = Game.from_dict(g.puzzle, data)
        self.assertEqual((h.board, h.hearts, h.guess_mode), (g.board, g.hearts, True))
        self.assertTrue(h.redo())
        self.assertEqual(h.board[3][3], GUESS)
        h.undo()
        h.undo()
        self.assertEqual(h.board[2][2], EMPTY)
        self.assertEqual(h.hearts, 2)  # undo still never refunds

    def test_bad_save_rejected(self):
        p = parse_level(LEVEL)
        self.assertIsNone(Game.from_dict(p, {'board': [[0] * 5] * 5, 'hearts': 3}))
        self.assertIsNone(Game.from_dict(p, {'hearts': 3}))
        self.assertIsNone(Game.from_dict(p, {'board': [[0] * 6] * 6, 'hearts': 0}))

    def test_noop_move_not_recorded(self):
        g = new_game()
        g.reveal(0, 1)
        n = len(g.undo_stack)
        g.toggle_mark(0, 1)  # cat cells can't be marked
        self.assertEqual(len(g.undo_stack), n)


# AAACCB
# ACACCB
# CCCCCC
# DEECCF
# DEEECF
# DEEEFF
def marked(g):
    return {(r, c) for r in range(g.n) for c in range(g.n) if g.board[r][c] != EMPTY}


class BatchTest(unittest.TestCase):
    def test_mark_side_right(self):
        g = new_game()
        g.mark_side(3, 1, 0, 1)  # region E right of column 2: columns 3, 4 of rows D-F
        self.assertEqual(marked(g), {(3, 2), (4, 2), (4, 3), (5, 2), (5, 3)})
        self.assertEqual(len(g.undo_stack), 1)

    def test_mark_side_up_and_guess_mode(self):
        g = new_game()
        g.guess_mode = True
        g.mark_side(1, 0, -1, 0)  # region A above row B: row A only
        self.assertEqual(marked(g), {(0, 0), (0, 1), (0, 2)})
        self.assertEqual(g.board[0][0], GUESS)

    def test_mark_side_never_toggles(self):
        g = new_game()
        g.toggle_mark(0, 4)
        g.mark_side(0, 3, 0, 1)
        g.mark_side(0, 3, 0, 1)
        self.assertEqual(g.board[0][4], MARK)

    def test_assist_two_in_a_row(self):
        g = new_game()
        # Region B is (0,5), (1,5): already a vertical pair.
        self.assertTrue(g.assist(0, 5))
        expected = {(r, 5) for r in range(2, 6)} | {(0, 4), (1, 4)}
        self.assertEqual(marked(g), expected)

    def test_assist_three_in_a_row(self):
        g = new_game()
        # Region F: (3,5), (4,5), (5,4), (5,5). Mark (5,4) → (3,5), (4,5), (5,5) vertical.
        g.toggle_mark(5, 4)
        self.assertTrue(g.assist(3, 5))
        self.assertIn((0, 5), marked(g))
        self.assertIn((4, 4), marked(g))  # touches all three
        self.assertNotIn((3, 4), marked(g))  # misses (5, 5)

    def test_assist_not_applicable(self):
        g = new_game()
        self.assertFalse(g.assist(0, 0))  # region A has five open cells
        g.toggle_mark(0, 5)
        g.toggle_mark(1, 5)
        self.assertFalse(g.assist(0, 5))  # region B has none left
        self.assertEqual(len(g.undo_stack), 2)

    def test_last_open_cell(self):
        g = new_game()
        reg = g.region(0, 5)  # region B: (0,5), (1,5)
        self.assertIsNone(g.last_open_cell(reg))
        g.toggle_mark(0, 5)
        self.assertEqual(g.last_open_cell(reg), (1, 5))
        g.reveal(1, 5)
        self.assertIsNone(g.last_open_cell(reg))  # has its cat

    def test_assist_needs_one_line(self):
        g = new_game()
        # Region F: mark (4,5) → (3,5), (5,4), (5,5) not in one line.
        g.toggle_mark(4, 5)
        self.assertFalse(g.assist(3, 5))

    def test_assist_line_lock_any_count(self):
        g = new_game()
        # Region C keeps only row C (2,0)..(2,5): six open cells, all in one row.
        for cell in [(0, 3), (0, 4), (1, 1), (1, 3), (1, 4), (3, 3), (3, 4), (4, 4)]:
            g.toggle_mark(*cell)
        before = marked(g)
        self.assertTrue(g.assist(2, 0))
        self.assertEqual(marked(g), before)  # the whole row is the region: nothing else

    def test_assist_line_lock_marks_rest_of_column(self):
        g = new_game()
        # Region E: leave (3,1) and (5,1), with (4,1) marked between them.
        for cell in [(3, 2), (4, 1), (4, 2), (4, 3), (5, 2), (5, 3)]:
            g.toggle_mark(*cell)
        self.assertTrue(g.assist(3, 1))
        new = marked(g)
        for r in (0, 1, 2):
            self.assertIn((r, 1), new)  # rest of column 2
        # C·C with a marked cell between: the cells beside the middle are out too.
        self.assertIn((4, 0), new)
        self.assertNotIn((3, 0), new)  # touches (3,1) only


    def test_drag_one_undo_step(self):
        g = new_game()
        g.begin()
        target = g.drag_target(0, 0)
        for c in range(4):
            g.drag_paint(0, c, target)
        g.end()
        self.assertEqual(marked(g), {(0, 0), (0, 1), (0, 2), (0, 3)})
        g.undo()
        self.assertEqual(marked(g), set())

    def test_drag_erases_from_marked_start(self):
        g = new_game()
        g.toggle_mark(0, 0)
        g.toggle_mark(0, 2)
        g.begin()
        target = g.drag_target(0, 0)
        for c in range(3):
            g.drag_paint(0, c, target)
        g.end()
        self.assertEqual(marked(g), set())

    def test_clear_guesses(self):
        g = new_game()
        g.toggle_mark(0, 0)
        g.guess_mode = True
        g.toggle_mark(1, 1)
        g.toggle_mark(2, 2)
        g.clear_guesses()
        self.assertEqual(marked(g), {(0, 0)})
        g.undo()
        self.assertEqual(marked(g), {(0, 0), (1, 1), (2, 2)})


class GeneratorTest(unittest.TestCase):
    def test_generated_levels_are_unique_and_logical(self):
        rng = random.Random(1)
        for n in (6, 7, 8):
            p = generate(n, rng)
            self.assertEqual(solutions(p.regions), [p.solution])
            self.assertTrue(logic_solvable(p.regions))
            self.assertEqual(parse_level(format_level(p)), p)


class SeedTest(unittest.TestCase):
    def test_same_seed_same_board(self):
        self.assertEqual(generate_from_seed(7, 12345), generate_from_seed(7, 12345))

    def test_code_round_trip(self):
        self.assertEqual(seed_code(12, 0x5f3a9c21), '12-5f3a9c21')
        self.assertEqual(parse_seed_code('12-5F3A9C21'), (12, 0x5f3a9c21))

    def test_bad_codes(self):
        for code in ('5-1', '13-1', '12', '12-xyz', '12-123456789', 'a-1'):
            with self.assertRaises(ValueError, msg=code):
                parse_seed_code(code)


class FavoritesTest(unittest.TestCase):
    def test_add_find_load(self):
        with tempfile.TemporaryDirectory() as d:
            fav = Favorites(Path(d) / 'favorites')
            self.assertEqual(fav.ids(), [])
            puzzle = parse_level(LEVEL)
            self.assertIsNone(fav.find(puzzle))
            idx, path = fav.add(format_level(puzzle, ['seed: 6-0000002a']))
            self.assertEqual((idx, path.name), (1, '0001.txt'))
            self.assertIn('# seed: 6-0000002a', path.read_text())
            self.assertEqual(fav.find(puzzle), 1)
            self.assertEqual(fav.load(1), puzzle)
            other = generate(7, random.Random(1))
            self.assertEqual(fav.add(format_level(other))[0], 2)
            self.assertEqual(fav.count(), 2)
            with self.assertRaises(LevelError):
                fav.load(3)

    def test_remove_keeps_ids(self):
        with tempfile.TemporaryDirectory() as d:
            fav = Favorites(d)
            other = format_level(generate(7, random.Random(1)))
            for text in (LEVEL, other, LEVEL):
                fav.add(text)
            fav.remove(1)
            self.assertEqual(fav.ids(), [2, 3])
            self.assertEqual(fav.load(2), parse_level(other))
            with self.assertRaises(LevelError):
                fav.load(1)
            self.assertEqual(fav.add(LEVEL)[0], 4)

    def test_forget(self):
        with tempfile.TemporaryDirectory() as d:
            p = Progress(Path(d) / 'progress.json')
            p.record('fav', 2, 3, 10.0)
            p.record('fav', 3, 1, 20.0)
            p.put_save('fav', 2, {'board': []})
            p.set_last('fav', 2)
            p.forget('fav', 2)
            p = Progress(Path(d) / 'progress.json')
            self.assertEqual((p.get('fav', 2), p.best_time('fav', 2), p.get_save('fav', 2)), (0, None, None))
            self.assertEqual(p.get('fav', 3), 1)
            self.assertEqual(p.last, {})


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


class ProgressTest(unittest.TestCase):
    def test_record_keeps_best(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'sub' / 'progress.json'
            p = Progress(path)
            p.record('8', 3, 2)
            p.record('8', 3, 1)
            p.set_last('8', 3)
            p = Progress(path)  # reload from disk
            self.assertEqual(p.get('8', 3), 2)
            self.assertEqual(p.cleared('8', range(1, 5)), 1)
            self.assertEqual(p.unsolved('8', range(1, 5)), [1, 2, 4])
            self.assertEqual(p.last, {'pack': '8', 'level': 3})

    def test_times_and_saves(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'progress.json'
            p = Progress(path)
            p.put_save('6', 1, {'board': [], 'elapsed': 3})
            p.record('6', 1, 3, 90.0)
            p.record('6', 1, 2, 120.0)
            p = Progress(path)
            self.assertEqual(p.best_time('6', 1), 90.0)
            self.assertIsNone(p.get_save('6', 1))  # clearing a level drops its save
            p.put_save('6', 2, {'x': 1})
            p.drop_save('6', 2)
            self.assertEqual(Progress(path).saves, {})

    def test_corrupt_file_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'progress.json'
            path.write_text('not json')
            self.assertEqual(Progress(path).stars, {})


class ColorTest(unittest.TestCase):
    def test_slots_are_distinct_and_avoid_reserved(self):
        rgbs = [hex_to_rgb(h) for h in REGION_COLORS + REGION_COLORS_DIM]
        slots = assign_slots(rgbs, reserved=[16, 231, 226])
        self.assertEqual(len(set(slots)), len(rgbs))
        self.assertFalse({16, 231, 226} & set(slots))
        self.assertTrue(all(16 <= s <= 255 for s in slots))


class PairPoolTest(unittest.TestCase):
    def test_reuses_pairs_from_old_frames(self):
        inits = []
        pool = PairPool(250, 256, lambda pid, fg, bg: inits.append((pid, fg, bg)))
        first = [pool.get(fg, 0) for fg in range(6)]
        self.assertEqual(first, list(range(250, 256)))
        self.assertEqual(pool.get(0, 0), 250)  # cached, no new init
        self.assertEqual(len(inits), 6)
        self.assertEqual(pool.get(99, 0), 0)  # all six in use this frame
        pool.new_frame()
        pool.get(1, 0)
        pid = pool.get(99, 0)  # recycles the least recently used: (0, 0)'s pair
        self.assertEqual(pid, 250)
        self.assertEqual(inits[-1], (250, 99, 0))
        self.assertNotEqual(pool.get(0, 0), 250)  # (0, 0) had to get a new number
        self.assertTrue(all(p < 256 for p, _, _ in inits))


class LineCodeTest(unittest.TestCase):
    def test_rows_differ_in_two_bits(self):
        for n in range(6, 13):
            codes = [line_code(y, n) for y in range(60)]
            for a in range(60):
                for b in range(a + 1, min(60, a + (1 << (n - 1)))):
                    self.assertGreaterEqual(bin(codes[a] ^ codes[b]).count('1'), 2, (n, a, b))


class BoxTest(unittest.TestCase):
    def test_every_weight_combination(self):
        for key in itertools.product(range(3), repeat=4):
            if any(key):
                self.assertIn(key, BOX)
        self.assertEqual(BOX[1, 1, 1, 1], '┼')
        self.assertEqual(BOX[2, 2, 1, 1], '╂')
        self.assertEqual(BOX[0, 2, 0, 2], '┏')


if __name__ == '__main__':
    unittest.main()
