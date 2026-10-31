# LMTrade

固定した模擬株価を使い、売買判断・仮想約定・損益計算・判断理由を確認するペーパートレードツールです。
面接では、外部接続のいらないオフラインデモを使用します。楽天口座、Excel、APIキー、npm、pipでの追加インストールは不要です。

## 面接デモの起動

Python 3.10以上と、現在のChrome / Edge / Firefoxを用意してください。
クローンしたフォルダーで実行すると、ブラウザが開きます。

```powershell
git clone https://github.com/kur11208/lmtrade.git
cd lmtrade
python demo.py
```

既にクローンしている場合は `python demo.py` の1コマンドです。
Windowsで `python` が見つからない場合は `py -3 demo.py` を使います。
既定URLは <http://127.0.0.1:8765/>、使用中なら8766〜8774番へ順に切り替わり、実際のURLをターミナルに表示します。
ブラウザを自動で開かない場合は `python demo.py --no-browser`。終了は `Ctrl+C` です。
サーバーはこのPC内からのみ接続できます。自動起動タスクは登録・有効化しません。

画面は計算済みの結果から開きます。「最初から再生」で判断の過程を表示し、
「下抜け後の反転」「レンジ」に切り替えると損失と見送りも確認できます。
各ケースは初期資金10万円の独立評価です。

| 固定ケース | 決済済み取引 | 手数料込み損益 |
| --- | --- | --- |
| 上昇 | 買い・利確1件 | +270.45円 |
| 下抜け後の反転 | 空売り・損切り1件 | -361.48円 |
| レンジ | 0件 | 0円 |

いずれも架空の検証入力に既存売買エンジンを適用した結果で、収益性の検証ではありません。
取引結果の固定値を画面に流し込まず、起動ごとにCSVから計算します。
デモはCSV・設定・画面ファイルだけを読み、実データ・口座情報・既存取引履歴を読み書きしません。
実注文を実行する経路もありません。

## 検証と説明資料

追加ライブラリなしで、再現性・損益・データ分離・HTTPを検証できます。

```powershell
python demo.py --check
python -m unittest discover -s etf_mobile -p test_interview_demo.py -v
```

- [面接の操作手順・5分の紹介・コード説明・想定質問](docs/INTERVIEW.md)
- [データ・約定・損益計算の仕様](docs/DEMO_DESIGN.md)
- [実施した検証と未検証事項](docs/DEMO_VERIFICATION.md)
- [既存実装と今回のAI支援変更の区別](docs/CHANGE_PROVENANCE.md)

## 既存の運用系

以下は楽天RSSやスマホサーバーを使う既存構成です。面接デモの起動には使用しません。

Local paper-trading tools for Japanese ETF/day-trade monitoring.

The current setup is intentionally market-data-only for external broker/RSS integration:

- Rakuten MarketSpeed II RSS bridge reads market data through Excel.
- The PC runs the HTTP dashboard and paper runner against the same local data.
- Phones reach the dashboard through tailnet-only Tailscale Serve access.
- Live order routing is disabled by configuration (`allow_live_order=false`).

## Main Components

- `etf_mobile/daytrade_app.py` - PC-hosted daytrade dashboard and feed endpoint.
- `etf_mobile/daytrade_runner.py` - paper-trading runner.
- `etf_mobile/rakuten_rss_bridge.ps1` - Windows MarketSpeed II RSS bridge.
- `etf_mobile/market_feed_start.ps1` - starts MarketSpeed II and the RSS bridge.
- `etf_mobile/market_feed_stop.ps1` - stops the RSS feed while preserving MarketSpeed II unless a full stop or shutdown is explicit.
- `etf_mobile/phone_wake_pc.sh` - Termux Wake-on-LAN helper for scheduled PC startup.
- `etf_mobile/DAYTRADE_README.md` - detailed operational notes.

## Safety

This repository excludes local secrets, SSH keys, logs, Excel workbooks, SQLite files,
paper trade history, intraday market data, generated deployment archives, and runtime
queues. Review `.gitignore` before making a public repository.

## Basic Checks

```powershell
cd C:\work\lmtrade\etf_mobile
python -m py_compile daytrade_app.py daytrade_runner.py
python -m unittest
```
