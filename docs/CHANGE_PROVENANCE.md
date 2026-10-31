# 変更の来歴とAI支援の範囲

## 作業開始時点の既存実装

この作業を始めた時点で、`daytrade_runner.py` などの売買コア、リスク制御、テストには多数の未コミット変更がありました。
面接デモが使うコアとその依存・テストを、先に `9912498`（`Preserve existing paper engine baseline for interview demo`）へ保存しました。
このコミットは今回のデモ整備より前から存在したローカル実装の記録です。
過去のコードの著者やAI利用の有無を確定するものではありません。
運用端末の設定、巨大な銘柄一覧、取引履歴、認証、Excelや起動タスク関連の未コミット変更は、この保存には含めていません。

GitHubに既にあった起床・配備スクリプトと開発メモの更新も取り込んでいます。面接デモからこれらを実行しません。

## 今回AI支援で整備した箇所

| 対象 | 今回の変更 |
| --- | --- |
| `demo.py` | 起動ディレクトリーに依存しない、1コマンドの入口 |
| `etf_mobile/interview_demo.py` | 固定CSVの検証、結果の表示用変換、読み取り専用HTTP |
| `etf_mobile/interview_fixtures/` | 利益・損失・見送りの分岐を確認する架空OHLCV、全設定固定 |
| `etf_mobile/daytrade_backtest.py` / `replay_bars` | 既存 `process_bar` をメモリ内で順次適用する補助関数を追加 |
| `etf_mobile/interview_web/` | 計算済みの結果を表示・再生する画面、ローカルCanvasチャート |
| `etf_mobile/test_interview_demo.py` | 再現性、独立した数値照合、先読み・副作用の検証 |
| `scripts/verify_interview_demo.cjs` | 任意のPlaywright画面操作・描画・レスポンシブ検証 |
| `README.md`, `docs/` | 起動、説明手順、制約、検証内容、変更来歴 |
| `.gitignore` | ブラウザ検証の一時ツール・画像をGit対象から除外 |

今回、既存コアの売買ルール・損益式は変更していません。
銘柄フィルター・運用設定の既定値とデモの固定条件は別物です。デモの条件はCSVの隣で管理します。
READMEには作業前から存在した現行PC運用の説明更新も保持しています。

面接では「全コードをAIなしで自作した」「実口座で発注している」「この成績は実相場での収益」と説明しないでください。
AI支援を受けた実装について、自分で設計理由と制約を説明できることを優先します。

## 差分の確認

```powershell
git show --stat 9912498
git log --oneline --all -8
git diff 9912498..HEAD -- demo.py etf_mobile/daytrade_backtest.py etf_mobile/interview_demo.py etf_mobile/interview_fixtures etf_mobile/interview_web etf_mobile/test_interview_demo.py docs scripts/verify_interview_demo.cjs README.md .gitignore
```

初期の保存コミットは原作者の証明ではなく、今回のAI支援整備との時間的な境界を示すためのものです。
