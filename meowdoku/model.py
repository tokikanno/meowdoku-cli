"""Game state: cell marks, reveals, hearts and undo/redo. No I/O here."""

from dataclasses import dataclass, field

EMPTY, MARK, GUESS, CAT, WRONG = range(5)

HEARTS_MAX = 3


@dataclass(frozen=True)
class Puzzle:
    n: int
    regions: tuple  # regions[r][c] -> region id 0..n-1
    solution: tuple  # solution[r] -> column of the cat in row r


@dataclass
class Move:
    """One undo step: cell diffs (r, c, prev, cur) plus the hearts it cost."""
    diffs: list = field(default_factory=list)
    cost: int = 0


class Game:
    def __init__(self, puzzle, hearts=HEARTS_MAX):
        self.puzzle = puzzle
        self.n = puzzle.n
        self.board = [[EMPTY] * self.n for _ in range(self.n)]
        self.hearts = hearts
        self.auto_mark = True
        self.guess_mode = False
        self.undo_stack = []
        self.redo_stack = []
        self._move = None  # open transaction: {(r, c): prev}, hearts before

    # ── state ────────────────────────────────────────────────────────────────

    @property
    def cats(self):
        return sum(row.count(CAT) for row in self.board)

    @property
    def won(self):
        return self.cats == self.n

    @property
    def lost(self):
        return self.hearts <= 0

    @property
    def over(self):
        return self.won or self.lost

    def region(self, r, c):
        return self.puzzle.regions[r][c]

    def in_bounds(self, r, c):
        return 0 <= r < self.n and 0 <= c < self.n

    def region_cells(self, reg):
        return [(r, c) for r in range(self.n) for c in range(self.n) if self.region(r, c) == reg]

    # ── transactions ─────────────────────────────────────────────────────────
    # Every change goes through _set inside a begin/end pair, so a whole
    # gesture (a keypress, a batch, a drag) becomes exactly one undo step.

    def begin(self):
        if self._move is None:
            self._move = ({}, self.hearts)

    def end(self):
        if self._move is None:
            return
        prevs, hearts_before = self._move
        self._move = None
        diffs = [(r, c, prev, self.board[r][c])
                 for (r, c), prev in prevs.items() if prev != self.board[r][c]]
        cost = hearts_before - self.hearts
        if diffs or cost:
            self.undo_stack.append(Move(diffs, cost))
            self.redo_stack.clear()

    @property
    def in_move(self):
        return self._move is not None

    def _set(self, r, c, val):
        prevs = self._move[0]
        prevs.setdefault((r, c), self.board[r][c])
        self.board[r][c] = val

    # ── actions ──────────────────────────────────────────────────────────────

    def mark_target(self, r, c):
        """What Space would turn this cell into, or None if it can't be marked."""
        cur = self.board[r][c]
        if cur in (CAT, WRONG):
            return None
        if self.guess_mode:
            if cur == MARK:
                return None
            return EMPTY if cur == GUESS else GUESS
        return EMPTY if cur == MARK else MARK

    def toggle_mark(self, r, c):
        if self.over:
            return False
        target = self.mark_target(r, c)
        if target is None:
            return False
        self.begin()
        self._set(r, c, target)
        self.end()
        return True

    def reveal(self, r, c):
        """Return 'cat', 'wrong' or None (nothing happened)."""
        if self.over or self.board[r][c] in (CAT, WRONG):
            return None
        self.begin()
        if self.puzzle.solution[r] == c:
            self._set(r, c, CAT)
            if self.auto_mark:
                self._auto_mark(r, c)
            result = 'cat'
        else:
            self.hearts -= 1
            self._set(r, c, WRONG)
            result = 'wrong'
        self.end()
        return result

    def forced_cell(self):
        """A cell that must hold a cat: the only one left unmarked in a region (checked
        first, as that's what a player looks for), else in a row or column. Units that
        already have a cat are skipped; ties go to reading order. Guesses count as
        unmarked: they're tentative, and revealing on a guess could cost a heart."""
        n = self.n
        regions = [self.region_cells(reg) for reg in range(n)]
        lines = ([[(r, c) for c in range(n)] for r in range(n)]
                 + [[(r, c) for r in range(n)] for c in range(n)])
        for units in (regions, lines):
            forced = set()
            for unit in units:
                states = [self.board[r][c] for r, c in unit]
                if CAT in states:
                    continue
                open_cells = [cell for cell, st in zip(unit, states) if st in (EMPTY, GUESS)]
                if len(open_cells) == 1:
                    forced.add(open_cells[0])
            if forced:
                return min(forced)
        return None

    def _auto_mark(self, r, c):
        n, reg = self.n, self.region(r, c)
        for i in range(n):
            for j in range(n):
                if (i == r or j == c or self.region(i, j) == reg
                        or (abs(i - r) <= 1 and abs(j - c) <= 1)):
                    if self.board[i][j] in (EMPTY, GUESS):
                        self._set(i, j, MARK)

    # ── batch operations ─────────────────────────────────────────────────────
    # These write the current mode's mark (× or △) and never toggle, so running
    # one twice is harmless. Each is one undo step.

    @property
    def mode_mark(self):
        return GUESS if self.guess_mode else MARK

    def _paint(self, r, c, target):
        """Mark (target = mode mark) or erase (target = EMPTY) one cell, if allowed.
        Marking turns a guess into a certain mark but never a mark into a guess;
        erasing only clears the current mode's mark."""
        cur = self.board[r][c]
        if target == EMPTY:
            ok = cur == self.mode_mark
        elif target == MARK:
            ok = cur in (EMPTY, GUESS)
        else:
            ok = cur == EMPTY
        if ok:
            self._set(r, c, target)

    def _paint_all(self, cells, target=None):
        if self.over:
            return
        self.begin()
        for r, c in cells:
            self._paint(r, c, self.mode_mark if target is None else target)
        self.end()

    def drag_target(self, r, c):
        """Dragging from this cell erases if it already holds the mode's mark, else marks."""
        return EMPTY if self.board[r][c] == self.mode_mark else self.mode_mark

    def drag_paint(self, r, c, target):
        """Paint inside an open transaction: the caller wraps a whole drag in begin/end."""
        if not self.over:
            self._paint(r, c, target)

    def mark_side(self, r, c, dr, dc):
        """Mark the cells of this region beyond (r, c) in one direction, across the whole
        board: dc = 1 → every same-region cell in a column right of c, and so on."""
        cells = [(i, j) for i, j in self.region_cells(self.region(r, c))
                 if (dr and (i - r) * dr > 0) or (dc and (j - c) * dc > 0)]
        self._paint_all(cells)

    def assist(self, r, c):
        """Marks implied by this region's open cells all lying on one row or column:
        the rest of that line, plus any cell touching every open cell (which covers
        runs of 2–3 and the C·C pattern with one marked cell between).
        Returns False (and changes nothing) when the region doesn't qualify."""
        cells = self.region_cells(self.region(r, c))
        if any(self.board[i][j] == CAT for i, j in cells):
            return False
        cand = [(i, j) for i, j in cells if self.board[i][j] in (EMPTY, GUESS)]
        if len(cand) < 2:
            return False
        if len({i for i, _ in cand}) == 1:
            line = [(cand[0][0], j) for j in range(self.n)]
        elif len({j for _, j in cand}) == 1:
            line = [(i, cand[0][1]) for i in range(self.n)]
        else:
            return False
        out = [cell for cell in line if cell not in cand]
        out += [(i, j) for i in range(self.n) for j in range(self.n)
                if (i, j) not in cand
                and all(abs(i - ci) <= 1 and abs(j - cj) <= 1 for ci, cj in cand)]
        self._paint_all(out)
        return True

    def clear_guesses(self):
        cells = [(r, c) for r in range(self.n) for c in range(self.n) if self.board[r][c] == GUESS]
        if self.over or not cells:
            return
        self.begin()
        for r, c in cells:
            self._set(r, c, EMPTY)
        self.end()

    # ── save / restore ───────────────────────────────────────────────────────

    def to_dict(self):
        self.end()
        return {'board': [row[:] for row in self.board], 'hearts': self.hearts,
                'guess_mode': self.guess_mode,
                'undo': [[[list(d) for d in m.diffs], m.cost] for m in self.undo_stack],
                'redo': [[[list(d) for d in m.diffs], m.cost] for m in self.redo_stack]}

    @classmethod
    def from_dict(cls, puzzle, data):
        """Rebuild a saved game, or return None if the save doesn't fit this puzzle."""
        try:
            game = cls(puzzle, hearts=int(data['hearts']))
            board = [[int(v) for v in row] for row in data['board']]
            if len(board) != game.n or any(len(row) != game.n for row in board):
                return None
            if any(not EMPTY <= v <= WRONG for row in board for v in row):
                return None
            game.board = board
            game.guess_mode = bool(data.get('guess_mode'))
            for key, stack in (('undo', game.undo_stack), ('redo', game.redo_stack)):
                for diffs, cost in data.get(key, []):
                    stack.append(Move([tuple(int(v) for v in d) for d in diffs], int(cost)))
            return None if game.over else game
        except (KeyError, TypeError, ValueError):
            return None

    # ── undo / redo ──────────────────────────────────────────────────────────
    # Undo never refunds a heart and redo charges it again, as in the web game.

    def undo(self):
        self.end()
        if self.over or not self.undo_stack:
            return False
        move = self.undo_stack.pop()
        for r, c, prev, _ in move.diffs:
            self.board[r][c] = prev
        self.redo_stack.append(move)
        return True

    def redo(self):
        self.end()
        if self.over or not self.redo_stack:
            return False
        move = self.redo_stack.pop()
        for r, c, _, cur in move.diffs:
            self.board[r][c] = cur
        self.hearts -= move.cost
        self.undo_stack.append(move)
        return True
