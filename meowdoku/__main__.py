"""python3 -m meowdoku [pack] [level]"""

import argparse
import sys
from pathlib import Path

from . import ui
from .levels import LevelError, LevelStore, parse_level
from .model import Game


def main(argv=None):
    p = argparse.ArgumentParser(prog='meowdoku', description='Meowdoku 終端機版')
    p.add_argument('pack', nargs='?', default='8', help='關卡包：6～12、hard、bad（預設 8）')
    p.add_argument('level', nargs='?', type=int, default=1, help='第幾關，從 1 開始（預設 1）')
    p.add_argument('--file', type=Path, help='直接玩單一關卡檔（level_*.txt 格式）')
    p.add_argument('--levels-dir', type=Path, help='使用本機的 bundle 目錄，不下載')
    p.add_argument('--update', action='store_true', help='重新下載關卡清單')
    p.add_argument('--ascii', action='store_true', help='只用 ASCII 符號繪製')
    args = p.parse_args(argv)

    try:
        if args.file:
            puzzle = parse_level(args.file.read_text(encoding='utf-8'))
            title = args.file.name

            def restart():
                return Game(puzzle), title

            next_level = None
        else:
            store = LevelStore(args.levels_dir, update=args.update)
            pos = {'pack': args.pack, 'idx': args.level}

            def load(idx):
                return Game(store.load(pos['pack'], idx)), f'meowdoku {pos["pack"]} #{idx}'

            def restart():
                return load(pos['idx'])

            def next_level():
                if pos['idx'] >= store.count(pos['pack']):
                    return None
                pos['idx'] += 1
                return load(pos['idx'])

        game, title = restart()
    except (LevelError, OSError) as e:
        sys.exit(f'meowdoku: {e}')

    try:
        ui.run(game, title, next_level=next_level, restart=restart, ascii=args.ascii)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
