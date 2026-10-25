"""meowdoku [pack [level]]"""

import argparse
import sys
from pathlib import Path

from . import app
from .generator import parse_seed_code
from .levels import LevelError, LevelStore, Progress, parse_level


def main(argv=None):
    p = argparse.ArgumentParser(prog='meowdoku', description='Meowdoku 終端機版。不加參數會進入選單。')
    p.add_argument('pack', nargs='?', help='關卡包：6～12、hard、bad、fav（收藏）；只給 pack 會進入選關畫面')
    p.add_argument('level', nargs='?', type=int, help='第幾關，從 1 開始')
    p.add_argument('--file', type=Path, help='直接玩單一關卡檔（level_*.txt 格式）')
    p.add_argument('--random', type=int, metavar='N', choices=range(6, 13),
                   help='現場產生 N×N 的關卡（6～12）')
    p.add_argument('--seed', metavar='CODE',
                   help='用關卡代碼重現隨機出的關卡，例如 12-5f3a9c21（標題列會顯示代碼）')
    p.add_argument('--levels-dir', type=Path, help='使用本機的 bundle 目錄，不下載')
    p.add_argument('--update', action='store_true', help='重新下載關卡清單')
    p.add_argument('--ascii', action='store_true', help='只用 ASCII 符號繪製')
    args = p.parse_args(argv)

    store = progress = None
    try:
        if args.file:
            start = ('file', parse_level(args.file.read_text(encoding='utf-8')), args.file.name)
        else:
            store = LevelStore(args.levels_dir, update=args.update)
            progress = Progress()
            if args.seed:
                n, seed = parse_seed_code(args.seed)
                start = ('generate', n, seed)
            elif args.random:
                start = ('generate', args.random)
            elif args.pack is None:
                start = ('menu',)
            elif not store.has(args.pack):
                raise LevelError(f'沒有 pack「{args.pack}」，可用的有：{", ".join(store.packs)}、fav')
            elif not store.count(args.pack):
                raise LevelError('還沒有收藏任何關卡：遊戲中按 f 收藏')
            elif args.level is None:
                start = ('pack', args.pack)
            elif not 1 <= args.level <= store.count(args.pack):
                raise LevelError(f'pack {args.pack} 只有第 1～{store.count(args.pack)} 關')
            else:
                start = ('play', args.pack, args.level)
    except (LevelError, OSError, ValueError) as e:
        sys.exit(f'meowdoku: {e}')

    try:
        app.run(start, store, progress, ascii=args.ascii)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
