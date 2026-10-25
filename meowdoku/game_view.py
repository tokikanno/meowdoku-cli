"""The game screen: board drawing and key handling. Rules live in model.Game."""

import curses
import itertools
import re
import unicodedata

from .model import CAT, EMPTY, GUESS, HEARTS_MAX, MARK, WRONG
from .term import (CTRL_G, CTRL_R, CTRL_X, ENTER, ESC, MOVES, SHIFT_MOVES, Screen,
                   text_width)

SYMBOLS = {EMPTY: '', MARK: '×', GUESS: '△', CAT: '🐱', WRONG: '╳'}
SYMBOLS_ASCII = {EMPTY: '', MARK: 'x', GUESS: '?', CAT: '@', WRONG: '!'}

# (cell width, cell height, grid lines), largest first; the first that fits wins.
# With grid lines a cell's pitch is one more than its size in each direction.
CELL_SIZES = [(7, 3, True), (3, 1, True), (4, 1, False)]
LABEL_W = 3  # room for the row letters left of the board
CHROME_H = 7  # header + column labels above the board, gap + 4 footer lines below

HELP = [
    ('方向鍵 / hjkl', '移動，到邊界從另一邊出現'),
    ('Space', '標記／取消標記（依模式寫入 {X} 或 {G}）'),
    ('g', '切換標記模式：無貓 {X} ↔ 猜測 {G}'),
    ('Enter', '翻開，翻錯扣一格生命'),
    ('Enter（貓上）', '跳到下一個唯一沒標記的格子並翻開'),
    ('Shift+方向鍵', '拖拽標記：起點沒標記就一路標記，有標記就一路清除'),
    ('v', '連續標記模式：移動時一路標記，再按 v 或 Esc 結束'),
    ('H / L', '同色格子中，目前格子左／右側的全部標記'),
    ('K / J', '同色格子中，目前格子上／下方的全部標記'),
    ('s', '同色剩下的格子都在同一行／列時，標記推得出的格子'),
    ('Ctrl-G', '清除所有猜測 {G}'),
    (':', '跳到指定格子：字母 + 數字，例如 c5'),
    ('a', '切換翻開後自動標記'),
    ('t', '切換座標提示'),
    ('u / Ctrl-R', 'undo / redo'),
    ('r', '重新開始這一關'),
    ('n', '下一關（結束後）'),
    ('Esc / m', '回到選單'),
    ('q', '離開（會先確認）'),
    ('Ctrl-X', '直接離開'),
]


def _box_chars():
    """Box-drawing char for each (up, down, left, right) weight: 0 none, 1 light, 2 heavy."""
    dirs = {'UP': 'u', 'DOWN': 'd', 'LEFT': 'l', 'RIGHT': 'r',
            'VERTICAL': 'ud', 'HORIZONTAL': 'lr'}
    table = {}
    for cp in range(0x2500, 0x2580):
        name = unicodedata.name(chr(cp))[len('BOX DRAWINGS '):]
        if any(w in name for w in ('DOUBLE', 'DASH', 'ARC', 'DIAGONAL')):
            continue
        groups = [g.split() for g in name.split(' AND ')]
        weights = [next((w for w in g if w in ('LIGHT', 'HEAVY')), None) for g in groups]
        default = next(w for w in weights if w)
        key = dict.fromkeys('udlr', 0)
        for g, w in zip(groups, weights):
            for word in g:
                for d in dirs.get(word, ''):
                    key[d] = 1 if (w or default) == 'LIGHT' else 2
        table[key['u'], key['d'], key['l'], key['r']] = chr(cp)
    return table


BOX = _box_chars()

BATCH_SIDES = {'H': (0, -1), 'L': (0, 1), 'K': (-1, 0), 'J': (1, 0)}


class GameView(Screen):
    """Plays one level. run() returns 'quit', 'menu', 'next' or 'restart'."""

    def __init__(self, scr, colors, game, title, settings, on_win=None, has_menu=True,
                 has_next=True, ascii=False):
        super().__init__(scr, colors)
        self.game = game
        self.title = title
        self.settings = settings  # shared across levels: auto_mark, show_coords
        game.auto_mark = settings.get('auto_mark', True)
        self.on_win = on_win
        self.has_menu = has_menu
        self.has_next = has_next
        self.symbols = SYMBOLS_ASCII if ascii else SYMBOLS
        self.hearts = ('*', '.') if ascii else ('♥', '♡')
        self.cur = (0, 0)
        self.modal = None  # None | 'help' | 'quit' | 'menu' | 'restart' | 'over'
        self.drag = None  # {'target': state painted, 'sticky': True for v mode}
        self.prompt = None  # text typed after ':', while jumping
        self.result = None

    # ── drawing ──────────────────────────────────────────────────────────────

    def layout(self):
        h, w = self.scr.getmaxyx()
        n = self.game.n
        for cw, ch, grid in CELL_SIZES:
            g = 1 if grid else 0
            bw = LABEL_W + n * (cw + g) + g
            bh = CHROME_H + n * (ch + g) + g
            if bw <= w and bh <= h:
                return cw, ch, grid, max(0, (w - bw) // 2), max(0, (h - bh) // 2)
        return None

    def draw(self):
        self.scr.erase()
        lay = self.layout()
        if lay is None:
            n = self.game.n
            cw, ch, _ = CELL_SIZES[-1]
            self.put(0, 0, f'終端機太小，至少需要 {LABEL_W + n * cw}×{CHROME_H + n * ch}')
            return
        cw, ch, grid, x0, y0 = lay
        g = 1 if grid else 0
        bx, by = x0 + LABEL_W, y0 + 2  # top-left of the board (of its frame, with a grid)
        self.draw_header(y0, x0)
        if self.settings.get('show_coords') or self.prompt is not None:
            self.draw_coords(bx + g, by + g, cw + g, ch + g, cw, ch)
        n = self.game.n
        for r in range(n):
            for c in range(n):
                self.draw_cell(r, c, bx + g + c * (cw + g), by + g + r * (ch + g), cw, ch,
                               brackets=not grid)
        if grid:
            self.draw_grid(bx, by, cw, ch)
        self.draw_footer(by + n * (ch + g) + g + 1, x0)
        self.draw_overlay()

    def draw_overlay(self):
        if self.modal == 'help':
            marks = {'X': self.symbols[MARK], 'G': self.symbols[GUESS]}
            rows = [k + ' ' * (16 - text_width(k)) + v.format(**marks) for k, v in HELP]
            if len(rows) + 6 > self.scr.getmaxyx()[0]:
                # Too short for one column: put the second half beside the first.
                half = (len(rows) + 1) // 2
                left, right = rows[:half], rows[half:] + ['']
                width = max(text_width(t) for t in left) + 3
                rows = [a + ' ' * (width - text_width(a)) + b for a, b in zip(left, right)]
            self.draw_box(['操作說明', ''] + rows + ['', '按任意鍵關閉'], center=False)
        elif self.modal == 'quit':
            self.draw_box(['離開？(y/n)'])
        elif self.modal == 'menu':
            self.draw_box(['放棄這一關，回到選單？(y/n)'])
        elif self.modal == 'restart':
            self.draw_box(['放棄目前的進度，重新開始？(y/n)'])
        elif self.modal == 'over':
            self.draw_box(self.over_lines())
        elif self.toast:
            self.draw_box([self.toast[0]])

    def draw_header(self, y, x):
        g = self.game
        hearts = self.hearts[0] * g.hearts + self.hearts[1] * (HEARTS_MAX - g.hearts)
        mode = f'猜測 {self.symbols[GUESS]}' if g.guess_mode else f'無貓 {self.symbols[MARK]}'
        for text, attr in ((f'{self.title}  {hearts}  ', curses.A_BOLD),
                           (f' {mode} ', curses.A_REVERSE if g.guess_mode else curses.A_BOLD),
                           (f'  自動標記:{"開" if g.auto_mark else "關"}', 0),
                           (self.drag_label(), curses.A_REVERSE)):
            self.put(y, x, text, attr)
            x += text_width(text)

    def draw_coords(self, x, y, pitch_x, pitch_y, cw, ch):
        for c in range(self.game.n):
            label = str(c + 1)
            self.put(y - 2 if pitch_y > ch else y - 1, x + c * pitch_x + (cw - len(label)) // 2,
                     label, curses.A_DIM)
        for r in range(self.game.n):
            self.put(y + r * pitch_y + (ch - 1) // 2, x - LABEL_W - (pitch_x - cw),
                     chr(ord('A') + r), curses.A_DIM)

    def draw_cell(self, r, c, x, y, cw, ch, brackets):
        g = self.game
        st = g.board[r][c]
        attr = self.colors.region(g.region(r, c), dim=st == MARK)
        for dy in range(ch):
            self.put(y + dy, x, ' ' * cw, attr)
        sym = self.symbols[st]
        if not sym and not self.colors.rich:
            sym = chr(ord('a') + g.region(r, c))
        if sym:
            self.put(y + (ch - 1) // 2, x + (cw - text_width(sym)) // 2, sym, attr | curses.A_BOLD)
        if brackets and (r, c) == self.cur:
            for dy in range(ch):
                self.put(y + dy, x, '[', self.colors.cursor)
                self.put(y + dy, x + cw - 1, ']', self.colors.cursor)

    def draw_grid(self, bx, by, cw, ch):
        """Outline each region; inside a region the grid gap takes the region's color."""
        n, reg = self.game.n, self.game.region

        def h_edge(lr, c):  # horizontal line lr (0..n) above row lr, over column c
            return lr in (0, n) or reg(lr - 1, c) != reg(lr, c)

        def v_edge(r, lc):  # vertical line lc (0..n) left of column lc, beside row r
            return lc in (0, n) or reg(r, lc - 1) != reg(r, lc)

        def junction(lr, lc):  # (up, down, left, right) edges meeting at a grid point
            return (int(lr > 0 and v_edge(lr - 1, lc)), int(lr < n and v_edge(lr, lc)),
                    int(lc > 0 and h_edge(lr, lc - 1)), int(lc < n and h_edge(lr, lc)))

        def fill(*cells):
            # A gap inside a region is dimmed when every cell it touches is settled
            # (marked or a cat), so a cat cell stays a clean bright block.
            dim = all(self.game.board[r][c] in (MARK, CAT) for r, c in cells)
            return self.colors.region(reg(*cells[0]), dim=dim)

        def h_line(lr, c, attr, edge):
            self.put(by + lr * (ch + 1), bx + 1 + c * (cw + 1), ('─' if edge else ' ') * cw, attr)

        def v_line(r, lc, attr, edge):
            for dy in range(ch):
                self.put(by + 1 + r * (ch + 1) + dy, bx + lc * (cw + 1), '│' if edge else ' ', attr)

        for lr, c in itertools.product(range(n + 1), range(n)):
            edge = h_edge(lr, c)
            h_line(lr, c, 0 if edge else fill((lr - 1, c), (lr, c)), edge)
        for r, lc in itertools.product(range(n), range(n + 1)):
            edge = v_edge(r, lc)
            v_line(r, lc, 0 if edge else fill((r, lc - 1), (r, lc)), edge)
        for lr, lc in itertools.product(range(n + 1), range(n + 1)):
            key = junction(lr, lc)
            # No edge at a grid point means all four cells around it share a region.
            if any(key):
                self.put(by + lr * (ch + 1), bx + lc * (cw + 1), BOX[key], 0)
            else:
                cells = [(lr - 1, lc - 1), (lr - 1, lc), (lr, lc - 1), (lr, lc)]
                self.put(by + lr * (ch + 1), bx + lc * (cw + 1), ' ', fill(*cells))

        # The cursor is a full frame around its cell, merged into the region edges it meets.
        r0, c0 = self.cur
        cursor = self.colors.cursor
        h_line(r0, c0, cursor, True)
        h_line(r0 + 1, c0, cursor, True)
        v_line(r0, c0, cursor, True)
        v_line(r0, c0 + 1, cursor, True)
        for dr, dc, extra in ((0, 0, (0, 1, 0, 1)), (0, 1, (0, 1, 1, 0)),
                              (1, 0, (1, 0, 0, 1)), (1, 1, (1, 0, 1, 0))):
            lr, lc = r0 + dr, c0 + dc
            key = tuple(max(a, b) for a, b in zip(junction(lr, lc), extra))
            self.put(by + lr * (ch + 1), bx + lc * (cw + 1), BOX[key], cursor)

    def draw_footer(self, y, x):
        g = self.game
        if g.won:
            self.put(y, x, '過關！  ' + self.over_keys(), curses.A_BOLD)
        elif g.lost:
            self.put(y, x, '生命用完了  ' + self.over_keys(), curses.A_BOLD)
        elif self.prompt is not None:
            self.put(y, x, f'跳到：{self.prompt}_   字母選行、數字選列，例如 c5 · Enter 確定 · Esc 取消',
                     curses.A_BOLD)
        else:
            self.put(y, x, f'貓 {g.cats}/{g.n}')
        self.put(y + 1, x, 'Space 標記  Enter 翻開  g 模式  u/^R 復原/重做', curses.A_DIM)
        self.put(y + 2, x, 'Shift+方向 拖拽  v 連續標記  HJKL 同色批次  s 輔助  : 跳轉',
                 curses.A_DIM)
        self.put(y + 3, x, f'r 重來  {"Esc 選單  " if self.has_menu else ""}? 說明  ^X 離開',
                 curses.A_DIM)

    def drag_label(self):
        if not self.drag:
            return ''
        what = '清除' if self.drag['target'] == EMPTY else '標記'
        return f'  {"連續" if self.drag["sticky"] else "拖拽"}{what} '

    def over_keys(self):
        keys = ['n 下一關'] if self.has_next else []
        keys += ['r 重玩'] + (['m 選單'] if self.has_menu else []) + ['q 離開']
        return ' · '.join(keys)

    def over_lines(self):
        g = self.game
        head = ('過關！' + '★' * g.hearts + '☆' * (HEARTS_MAX - g.hearts)) if g.won else '生命用完了'
        return [head, '', self.over_keys(), '其他鍵：關閉這個視窗']

    # ── input ────────────────────────────────────────────────────────────────

    def run(self):
        while self.result is None:
            self.draw()
            self.scr.refresh()
            key = self.read_key()
            if key is None:
                continue
            if key == CTRL_X:
                return 'quit'
            if self.modal:
                self.handle_modal(key)
            else:
                self.handle_key(key)
        return self.result

    def handle_modal(self, key):
        modal, self.modal = self.modal, None
        yes = key in ('y', 'Y')
        if modal == 'quit' and yes:
            self.result = 'quit'
        elif modal in ('menu', 'restart') and yes:
            self.result = modal
        elif modal == 'over' and key in ('n', 'r', 'm', 'q'):
            self.handle_key(key)

    def handle_key(self, key):
        g = self.game
        if self.prompt is not None:
            self.handle_prompt(key)
            return
        if key in SHIFT_MOVES:
            self.drag_move(SHIFT_MOVES[key], sticky=False)
            return
        if self.drag and self.drag['sticky']:
            if key in MOVES:
                self.drag_move(MOVES[key], sticky=True)
                return
            if key in ('v', ESC):
                self.end_drag()
                return
        self.end_drag()  # any other key ends a drag, closing its undo step
        r, c = self.cur
        if key in MOVES:
            dr, dc = MOVES[key]
            self.cur = ((r + dr) % g.n, (c + dc) % g.n)  # wraps around at the edges
        elif key == ' ':
            g.toggle_mark(r, c)
        elif key in ENTER:
            if g.board[r][c] == CAT:
                # On a cat, Enter moves on to the next cell that must be a cat.
                cell = g.forced_cell()
                if cell and not g.over:  # nothing to do is silent, by design
                    self.cur = cell
                    self.reveal(*cell)
            else:
                self.reveal(r, c)
        elif key == 'v':
            self.start_drag(sticky=True)
        elif key in BATCH_SIDES:
            g.mark_side(r, c, *BATCH_SIDES[key])
        elif key == 's':
            if not g.over and not g.assist(r, c):
                self.show_toast('s 不適用：這個顏色剩下的格子不在同一行或同一列')
        elif key == CTRL_G:
            g.clear_guesses()
        elif key == ':':
            self.prompt = ''
        elif key == 'g':
            g.guess_mode = not g.guess_mode
        elif key == 'a':
            g.auto_mark = self.settings['auto_mark'] = not g.auto_mark
        elif key == 't':
            self.settings['show_coords'] = not self.settings.get('show_coords')
        elif key == 'u':
            if not g.undo():
                self.show_toast('沒有可以復原的步驟')
        elif key == CTRL_R:
            if not g.redo():
                self.show_toast('沒有可以重做的步驟')
        elif key == 'n' and g.over:
            if self.has_next:
                self.result = 'next'
            else:
                self.show_toast('沒有下一關')
        elif key == 'r':
            self.leave('restart')
        elif key in ('m', ESC) and self.has_menu:
            self.leave('menu')
        elif key == '?':
            self.modal = 'help'
        elif key == 'q':
            self.modal = 'quit'

    # ── drag ─────────────────────────────────────────────────────────────────
    # A drag is one open transaction in the model, so the whole stroke undoes at once.

    def start_drag(self, sticky):
        g = self.game
        if g.over:
            return
        r, c = self.cur
        g.begin()
        self.drag = {'target': g.drag_target(r, c), 'sticky': sticky}
        g.drag_paint(r, c, self.drag['target'])

    def drag_move(self, delta, sticky):
        if self.drag is None or self.drag['sticky'] != sticky:
            self.end_drag()
            self.start_drag(sticky)
        n = self.game.n
        self.cur = ((self.cur[0] + delta[0]) % n, (self.cur[1] + delta[1]) % n)
        if self.drag:
            self.game.drag_paint(*self.cur, self.drag['target'])

    def end_drag(self):
        if self.drag:
            self.game.end()
            self.drag = None

    # ── jump ─────────────────────────────────────────────────────────────────

    def handle_prompt(self, key):
        if key in ENTER:
            text, self.prompt = self.prompt, None
            m = re.fullmatch(r'\s*([a-zA-Z])\s*(\d+)\s*', text)
            n = self.game.n
            if m and ord(m[1].upper()) - ord('A') < n and 1 <= int(m[2]) <= n:
                self.cur = (ord(m[1].upper()) - ord('A'), int(m[2]) - 1)
            elif text.strip():
                last = chr(ord('A') + n - 1)
                self.show_toast(f'座標要是 A～{last} 加上 1～{n}，例如 c5')
        elif key == ESC:
            self.prompt = None
        elif key in (curses.KEY_BACKSPACE, '\x7f', '\b'):
            self.prompt = self.prompt[:-1]
        elif isinstance(key, str) and (key.isalnum() or key == ' ') and len(self.prompt) < 4:
            self.prompt += key

    def leave(self, result):
        """Leave the level, asking first if that would throw away progress."""
        if self.game.over or not self.game.undo_stack:
            self.result = result
        else:
            self.modal = result

    def reveal(self, r, c):
        g = self.game
        result = g.reveal(r, c)
        if result == 'wrong' and not g.lost:
            self.show_toast(f'翻錯了！剩 {g.hearts} 格生命')
        if result and g.over:
            self.modal = 'over'
            if g.won and self.on_win:
                self.on_win(g.hearts)
