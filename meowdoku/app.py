"""Session loop: menus → game → menus, all inside one curses session."""

import curses
import os
import random

from .game_view import GameView
from .levels import LevelError
from .menu import LevelMenu, MainMenu, pack_label
from .model import Game
from .term import Colors, Screen

RANDOM_PACKS = [str(n) for n in range(6, 13)]


class _Blank(Screen):
    def draw(self):
        self.scr.erase()


class App:
    def __init__(self, scr, store, progress, ascii=False):
        self.scr = scr
        self.store = store
        self.progress = progress
        self.ascii = ascii
        self.colors = Colors()
        self.settings = {'auto_mark': True, 'show_coords': False}
        self.blank = _Blank(scr, self.colors)

    # ── screens ──────────────────────────────────────────────────────────────

    def main_menu(self, initial=None):
        while True:
            choice = MainMenu(self.scr, self.colors, self.store, self.progress, initial).run()
            if choice == 'quit':
                return
            kind, pack = choice
            initial = pack or initial
            if kind == 'random':
                result = self.play_random(pack)
            else:
                result = self.level_menu(pack)
            if result == 'quit':
                return

    def level_menu(self, pack):
        while True:
            choice = LevelMenu(self.scr, self.colors, self.store, self.progress, pack).run()
            if choice in ('back', 'quit'):
                return choice
            if choice == 'random':
                result = self.play_random(pack)
            else:
                result = self.play_pack(pack, choice[1])
            if result == 'quit':
                return 'quit'

    # ── playing ──────────────────────────────────────────────────────────────

    def load(self, pack, idx):
        self.blank.busy(f'載入 {pack_label(pack)} 第 {idx} 關…')
        return self.store.load(pack, idx)

    def play_pack(self, pack, idx):
        """Play levels of `pack` in order from `idx`. Returns 'menu' or 'quit'."""
        while True:
            result = self.play_one(pack, idx, f'{pack_label(pack)} #{idx}',
                                   has_next=idx < self.store.count(pack))
            if result == 'next':
                idx += 1
            elif result != 'restart':
                return result

    def play_random(self, pack=None):
        """Random unsolved levels from `pack`, or from any of 6..12 when None."""
        current = None
        while True:
            if current is None:
                p = pack or random.choice(RANDOM_PACKS)
                count = self.store.count(p)
                current = (p, random.choice(self.progress.unsolved(p, count) or range(1, count + 1)))
            p, idx = current
            result = self.play_one(p, idx, f'隨機 · {pack_label(p)} #{idx}', has_next=True)
            if result == 'next':
                current = None
            elif result != 'restart':
                return result

    def play_one(self, pack, idx, title, has_next):
        try:
            puzzle = self.load(pack, idx)
        except LevelError as e:
            self.error(str(e))
            return 'menu'
        self.progress.set_last(pack, idx)
        view = GameView(self.scr, self.colors, Game(puzzle), title, self.settings,
                        on_win=lambda stars: self.progress.record(pack, idx, stars),
                        has_next=has_next, ascii=self.ascii)
        return view.run()

    def play_file(self, puzzle, title):
        while True:
            view = GameView(self.scr, self.colors, Game(puzzle), title, self.settings,
                            has_menu=False, has_next=False, ascii=self.ascii)
            if view.run() != 'restart':
                return

    def error(self, text):
        self.blank.draw()
        self.blank.draw_box([text, '', '按任意鍵繼續'])
        self.scr.refresh()
        self.scr.timeout(-1)
        self.scr.get_wch()


def run(start, store=None, progress=None, ascii=False):
    """start: ('menu',), ('pack', pack), ('play', pack, idx) or ('file', puzzle, title)."""
    os.environ.setdefault('ESCDELAY', '25')

    def main(scr):
        curses.curs_set(0)
        app = App(scr, store, progress, ascii)
        kind = start[0]
        if kind == 'file':
            app.play_file(*start[1:])
            return
        initial = None
        if kind == 'pack':
            initial = start[1]
            if app.level_menu(start[1]) == 'quit':
                return
        elif kind == 'play':
            initial = start[1]
            if app.play_pack(start[1], start[2]) == 'quit':
                return
        else:
            initial = progress.last.get('pack')
        app.main_menu(initial)

    curses.wrapper(main)
