"""Menus: pack list and level picker."""

import curses

from .term import CTRL_X, ENTER, ESC, MOVES, Screen, text_width

PACK_LABELS = {'hard': '困難', 'bad': '需猜測'}
RANDOM_ANY = '隨機（6～12 任意大小）'
QUIT = '離開'


def pack_label(pack):
    return PACK_LABELS.get(pack, f'{pack}×{pack}')


class MainMenu(Screen):
    """Returns ('pack', pack), ('random', pack or None) or 'quit'."""

    def __init__(self, scr, colors, store, progress, initial=None):
        super().__init__(scr, colors)
        self.store = store
        self.progress = progress
        self.items = store.packs + [RANDOM_ANY, QUIT]
        self.sel = self.items.index(initial) if initial in self.items else 0

    def draw(self):
        self.scr.erase()
        h, w = self.scr.getmaxyx()
        rows = []
        for item in self.items:
            if item in self.store.manifest:
                count = self.store.count(item)
                done = self.progress.cleared(item)
                label = pack_label(item)
                rows.append(f'{label}{" " * (10 - text_width(label))}{done:>5} / {count}')
            else:
                rows.append(item)
        width = max(text_width(r) for r in rows) + 4
        x = max(0, (w - width) // 2)
        y = max(0, (h - len(rows) - 6) // 2)
        self.put(y, x, 'MEOWDOKU', curses.A_BOLD)
        self.put(y + 1, x, '已通關 / 關卡數', curses.A_DIM)
        for i, row in enumerate(rows):
            gap = 1 if i >= len(self.store.packs) else 0  # blank line before the extra items
            attr = curses.A_REVERSE if i == self.sel else 0
            self.put(y + 3 + i + gap, x, f'  {row}{" " * (width - 2 - text_width(row))}', attr)
        self.put(y + len(rows) + 5, x, '↑↓ 選擇 · Enter 確定 · r 這個包隨機一關 · q 離開', curses.A_DIM)
        if self.toast:
            self.draw_box([self.toast[0]])

    def run(self):
        while True:
            self.draw()
            self.scr.refresh()
            key = self.read_key()
            if key is None:
                continue
            item = self.items[self.sel]
            if key in (CTRL_X, 'q'):
                return 'quit'
            if key in MOVES and MOVES[key][1] == 0:
                self.sel = (self.sel + MOVES[key][0]) % len(self.items)
            elif key in ENTER:
                if item == QUIT:
                    return 'quit'
                if item == RANDOM_ANY:
                    return ('random', None)
                return ('pack', item)
            elif key == 'r' and item in self.store.manifest:
                return ('random', item)


class LevelMenu(Screen):
    """Level picker for one pack. Returns ('play', idx), 'random', 'back' or 'quit'."""

    COLS = 10
    ENTRY_W = 7

    def __init__(self, scr, colors, store, progress, pack, initial=None):
        super().__init__(scr, colors)
        self.pack = pack
        self.progress = progress
        self.count = store.count(pack)
        if initial is None:
            # Start at the first unsolved level from where the player left off.
            last = progress.last.get('level', 1) if progress.last.get('pack') == pack else 1
            unsolved = progress.unsolved(pack, self.count)
            initial = next((i for i in unsolved if i >= last), unsolved[0] if unsolved else 1)
        self.sel = initial
        self.typed = ''  # digits typed so far, for jumping to a level number
        self.top = 0  # first visible grid row

    def grid(self):
        h, w = self.scr.getmaxyx()
        cols = max(1, min(self.COLS, (w - 2) // self.ENTRY_W))
        rows = max(1, h - 5)
        return cols, rows

    def draw(self):
        self.scr.erase()
        h, w = self.scr.getmaxyx()
        cols, rows = self.grid()
        x0 = max(0, (w - cols * self.ENTRY_W) // 2)
        r_sel = (self.sel - 1) // cols
        self.top = min(max(self.top, r_sel - rows + 1), r_sel)
        done = self.progress.cleared(self.pack)
        self.put(0, x0, f'{pack_label(self.pack)}   已通關 {done} / {self.count}', curses.A_BOLD)
        for gr in range(rows):
            for gc in range(cols):
                idx = (self.top + gr) * cols + gc + 1
                if idx > self.count:
                    break
                stars = self.progress.get(self.pack, idx)
                attr = self.colors.stars[stars] if stars else 0
                if idx == self.sel:
                    attr = curses.A_REVERSE | (attr & ~curses.A_COLOR)
                mark = '★' * stars + ' ' * (3 - stars) if stars else '   '
                self.put(2 + gr, x0 + gc * self.ENTRY_W, f'{idx:>4}', attr)
                if stars:
                    self.put(2 + gr, x0 + gc * self.ENTRY_W + 4, mark, self.colors.stars[stars])
        if self.typed:
            hint = f'跳到第 {self.typed} 關（Enter 確定 · Esc 取消）'
        else:
            hint = '方向鍵 選擇 · 數字 跳關 · Enter 開始 · r 隨機未通關 · Esc 返回'
        self.put(h - 2, x0, hint, curses.A_DIM)
        if self.toast:
            self.draw_box([self.toast[0]])

    def run(self):
        while True:
            self.draw()
            self.scr.refresh()
            key = self.read_key()
            if key is None:
                continue
            cols, rows = self.grid()
            if key == CTRL_X:
                return 'quit'
            if isinstance(key, str) and key.isdigit():
                if len(self.typed) < len(str(self.count)):
                    self.typed += key
            elif key in (curses.KEY_BACKSPACE, '\x7f', '\b'):
                self.typed = self.typed[:-1]
            elif key in ENTER:
                if not self.typed:
                    return ('play', self.sel)
                if 1 <= int(self.typed) <= self.count:
                    self.sel = int(self.typed)
                else:
                    self.show_toast(f'只有第 1～{self.count} 關')
                self.typed = ''
            elif key == ESC and self.typed:
                self.typed = ''
            elif key in (ESC, 'q'):
                return 'back'
            elif key == 'r':
                return 'random'
            elif key in MOVES:
                dr, dc = MOVES[key]
                self.move(self.sel + dr * cols + dc)
            elif key == curses.KEY_NPAGE:
                self.move(self.sel + rows * cols)
            elif key == curses.KEY_PPAGE:
                self.move(self.sel - rows * cols)
            elif key == curses.KEY_HOME:
                self.sel = 1
            elif key == curses.KEY_END:
                self.sel = self.count

    def move(self, idx):
        self.sel = min(max(idx, 1), self.count)
