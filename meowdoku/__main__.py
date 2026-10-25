"""meowdoku [pack [level]]"""

import argparse
import sys
from pathlib import Path

from . import app
from .levels import LevelError, LevelStore, Progress, parse_level


def main(argv=None):
    p = argparse.ArgumentParser(prog='meowdoku', description='Meowdoku 終端機版。不加參數會進入選單。')
    p.add_argument('pack', nargs='?', help='關卡包：6～12、hard、bad；只給 pack 會進入選關畫面')
    p.add_argument('level', nargs='?', type=int, help='第幾關，從 1 開始')
    p.add_argument('--file', type=Path, help='直接玩單一關卡檔（level_*.txt 格式）')
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
            if args.pack is None:
                start = ('menu',)
            elif args.pack not in store.manifest:
                raise LevelError(f'沒有 pack「{args.pack}」，可用的有：{", ".join(store.packs)}')
            elif args.level is None:
                start = ('pack', args.pack)
            elif not 1 <= args.level <= store.count(args.pack):
                raise LevelError(f'pack {args.pack} 只有第 1～{store.count(args.pack)} 關')
            else:
                start = ('play', args.pack, args.level)
    except (LevelError, OSError) as e:
        sys.exit(f'meowdoku: {e}')

    try:
        app.run(start, store, progress, ascii=args.ascii)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
