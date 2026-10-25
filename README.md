# meowdoku-cli

[Meowdoku](https://github.com/plko/meowdoku/) 的終端機版本。只用 Python 3 標準函式庫（`curses`），不需要安裝任何套件。

```sh
python3 -m meowdoku            # pack 8 第 1 關
python3 -m meowdoku 10 42      # pack 10 第 42 關
python3 -m meowdoku hard 3     # 其他 pack：6～12、hard、bad
python3 -m meowdoku --file level.txt
```

第一次執行時會從原 repo 下載關卡，快取在 `~/.cache/meowdoku/`。

| 參數 | 說明 |
|---|---|
| `--update` | 重新下載關卡清單 |
| `--levels-dir DIR` | 使用本機的 bundle 目錄（例如原 repo 的 `levels/bundle/`），不下載 |
| `--ascii` | 只用 ASCII 符號 |

遊戲中按 `?` 查看熱鍵。規劃與設計決策見 [PLAN.md](PLAN.md)。

## 測試

```sh
python3 -m unittest
```
