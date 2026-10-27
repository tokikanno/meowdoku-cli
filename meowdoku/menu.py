"""Menus: pack list and level picker."""

import curses

from .term import CTRL_X, ENTER, ESC, MOVES, Screen, text_width

PACK_LABELS = {'hard': '困難', 'bad': '需猜測', 'fav': '收藏'}
FAV = 'fav'
GENERATE = 'generate'
GEN_SIZES = list(range(6, 13))
CONTINUE = 'continue'
QUIT = '離開'


def pack_label(pack):
    return PACK_LABELS.get(pack, f'{pack}×{pack}')


def resume_label(pack, idx):
    return f'隨機出題 {idx}×{idx}' if pack == 'gen' else f'{pack_label(pack)} #{idx}'


class MainMenu(Screen):
    """Returns ('pack', pack), ('random', pack), ('generate', n), ('continue', (pack, idx))
    or 'quit'."""

    def __init__(self, scr, colors, store, progress, initial=None, gen_size=8):
        super().__init__(scr, colors)
        self.store = store
        self.progress = progress
        self.gen_size = gen_size
        last = progress.last
        self.resume = None  # (pack, idx) of the last level, if it was left unfinished
        if ((store.has(last.get('pack')) or last.get('pack') == 'gen')
                and progress.get_save(last['pack'], last.get('level'))):
            self.resume = (last['pack'], last['level'])
        self.items = ([CONTINUE] if self.resume else []) + store.packs + [FAV, GENERATE, QUIT]
        self.sel = 0 if self.resume else self.items.index(initial) if initial in self.items else 0

    def draw(self):
        self.scr.erase()
        h, w = self.scr.getmaxyx()
        rows = []
        for item in self.items:
            if item == CONTINUE:
                rows.append(f'繼續：{resume_label(*self.resume)}')
            elif item == GENERATE:
                rows.append(f'隨機出題：‹ {self.gen_size}×{self.gen_size} ›')
            elif self.store.has(item):
                count = self.store.count(item)
                done = self.progress.cleared(item, self.store.ids(item))
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
            # Blank lines set the continue item and the trailing items apart from the packs.
            first_pack = 1 if self.resume else 0
            gap = (1 if self.resume and i >= 1 else 0) + (1 if i >= first_pack + len(self.store.packs) else 0)
            attr = curses.A_REVERSE if i == self.sel else 0
            self.put(y + 3 + i + gap, x, f'  {row}{" " * (width - 2 - text_width(row))}', attr)
        self.put(y + len(rows) + 5 + (1 if self.resume else 0), x, '↑↓ 選擇 · ←→ 出題大小 · Enter 確定 · r 這個包隨機一關 · q 離開', curses.A_DIM)
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
            elif key in MOVES and item == GENERATE:
                i = GEN_SIZES.index(self.gen_size) + MOVES[key][1]
                self.gen_size = GEN_SIZES[min(max(i, 0), len(GEN_SIZES) - 1)]
            elif key in ENTER:
                if item == QUIT:
                    return 'quit'
                if item == CONTINUE:
                    return ('continue', self.resume)
                if item == GENERATE:
                    return ('generate', self.gen_size)
                if not self.store.count(item):
                    self.show_toast('還沒有收藏：遊戲中按 f 收藏目前的關卡', seconds=2.0)
                    continue
                return ('pack', item)
            elif key == 'r' and self.store.has(item) and self.store.count(item):
                return ('random', item)


class LevelMenu(Screen):
    """Level picker for one pack. Returns ('play', idx), 'random', 'back' or 'quit'."""

    COLS = 10
    ENTRY_W = 7

    def __init__(self, scr, colors, store, progress, pack, initial=None):
        super().__init__(scr, colors)
        self.pack = pack
        self.progress = progress
        self.ids = store.ids(pack)  # level numbers; the favorites' may have gaps
        self.count = len(self.ids)
        if initial is None:
            # Start at the first unsolved level from where the player left off.
            last = progress.last.get('level', 1) if progress.last.get('pack') == pack else 1
            unsolved = progress.unsolved(pack, self.ids)
            initial = next((i for i in unsolved if i >= last), unsolved[0] if unsolved else 1)
        # sel and the grid count positions (1…count); ids[pos - 1] is the level shown.
        self.sel = self.ids.index(initial) + 1 if initial in self.ids else 1
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
        done = self.progress.cleared(self.pack, self.ids)
        self.put(0, x0, f'{pack_label(self.pack)}   已通關 {done} / {self.count}', curses.A_BOLD)
        for gr in range(rows):
            for gc in range(cols):
                pos = (self.top + gr) * cols + gc + 1
                if pos > self.count:
                    break
                idx = self.ids[pos - 1]
                stars = self.progress.get(self.pack, idx)
                attr = self.colors.stars[stars] if stars else 0
                if pos == self.sel:
                    attr = curses.A_REVERSE | (attr & ~curses.A_COLOR)
                self.put(2 + gr, x0 + gc * self.ENTRY_W, f'{idx:>4}', attr)
                if stars:
                    self.put(2 + gr, x0 + gc * self.ENTRY_W + 4, '★' * stars, self.colors.stars[stars])
                elif self.progress.get_save(self.pack, idx):
                    self.put(2 + gr, x0 + gc * self.ENTRY_W + 4, '…', curses.A_DIM)
        if self.typed:
            hint = f'跳到第 {self.typed} 關（Enter 確定 · Esc 取消）'
        else:
            hint = '方向鍵 選擇 · 數字 跳關 · Enter 開始 · r 隨機未通關 · Esc 返回 · … 未完成'
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
                if len(self.typed) < len(str(self.ids[-1])):
                    self.typed += key
            elif key in (curses.KEY_BACKSPACE, '\x7f', '\b'):
                self.typed = self.typed[:-1]
            elif key in ENTER:
                if not self.typed:
                    return ('play', self.ids[self.sel - 1])
                if int(self.typed) in self.ids:
                    self.sel = self.ids.index(int(self.typed)) + 1
                elif self.ids == list(range(1, self.count + 1)):
                    self.show_toast(f'只有第 1～{self.count} 關')
                else:
                    self.show_toast(f'沒有第 {int(self.typed)} 關')
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
