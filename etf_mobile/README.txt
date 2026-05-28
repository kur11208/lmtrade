このパッケージは、PC版の本物データを使ってスマホ(Termux)で表示するための最小版です。

含まれるもの
- app_pc_multi.py
- runner_pc_multi.py
- data/<symbol>/paper_state_atr.json
- data/<symbol>/paper_history.csv
- data/<symbol>/paper_settings.json
- data/<symbol>/paper_live_config.json
- data/<symbol>/ohlc.csv

起動手順(Termux)
1. unzip して作業ディレクトリに入る
2. 必要なら pip install jpholiday
3. ターミナル1で:
   python app_pc_multi.py
4. ターミナル2で:
   python runner_pc_multi.py
5. PCブラウザから:
   http://スマホIP:8000/

安定運用コマンド(Termux)

起動:
cd ~/etf_mobile
nohup sh watchdog_supervisor.sh >> logs/supervisor_launcher.log 2>&1 &

確認:
ps -ef | grep -E 'watchdog|app_pc_multi|runner_pc_multi' | grep -v grep
curl -I http://127.0.0.1:8000/
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/
tail -n 50 logs/watchdog.log
tail -n 20 logs/app.log
tail -n 20 logs/runner.log

ロジックテスト:
python -m py_compile app_pc_multi.py runner_pc_multi.py test_runner_logic.py backtest_synthetic_100y.py backtest_real_ohlc.py
python test_runner_logic.py

100年合成データ成績テスト:
python backtest_synthetic_100y.py

実OHLC成績テスト:
python backtest_real_ohlc.py

候補銘柄OHLC取得:
python fetch_candidate_ohlc.py
python fetch_candidate_ohlc.py --update-symbols

銘柄別パラメータ最適化:
python optimize_strategy.py
python optimize_strategy.py --apply

全候補デモ観察モード:
python set_demo_mode.py all-on --max-position 0.2
python set_demo_mode.py status

最適化採択状態へ戻す:
python set_demo_mode.py optimized

スマホサーバー移行(Termux)

PC側で作った etf_mobile_termux_YYYYMMDD_HHMMSS.tar.gz をスマホの Download に置いた場合:
pkg update
pkg install python
termux-setup-storage
cd ~
tar -xzf /sdcard/Download/etf_mobile_termux_YYYYMMDD_HHMMSS.tar.gz
cd ~/etf_mobile
mkdir -p logs
python -m py_compile app_pc_multi.py runner_pc_multi.py
python set_demo_mode.py status
nohup sh watchdog_supervisor.sh >> logs/supervisor_launcher.log 2>&1 &

スマホ側確認:
curl -I http://127.0.0.1:8000/
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/
ip addr show wlan0
tail -n 50 logs/watchdog.log
tail -n 20 logs/runner.log

PCや別スマホのブラウザから見る:
http://スマホIP:8000/

PCからスマホへ自動反映(SSH)

初回だけスマホのTermuxで:
pkg install openssh
passwd
whoami
sshd

PC側PowerShellで、whoami の結果を ETF_PHONE_USER に入れる:
$env:ETF_PHONE_HOST='192.168.0.62'
$env:ETF_PHONE_USER='u0_aXXX'
.\deploy_to_phone.ps1 -Restart

PC側の変更を監視して自動反映:
.\deploy_to_phone.ps1 -Restart -Watch

補足:
- Termux の sshd は通常 8022 番です
- 通常反映ではスマホ側で増える paper_state_atr.json / paper_history.csv / mobile_runner_state.json / logs は上書きしません
- 初回丸ごとコピーしたい場合だけ .\deploy_to_phone.ps1 -IncludeLiveData -Restart を使います
- パスワード入力を省きたい場合はPCの公開鍵をスマホの ~/.ssh/authorized_keys に登録します

停止:
pkill -f watchdog_supervisor.sh
pkill -f watchdog_etf.sh
pkill -f app_pc_multi.py
pkill -f runner_pc_multi.py

補足
- JSON/CSV の BOM を読めるようにしてあります
- グラフは paper_history.csv をそのまま使います
- 画面には paper_history.csv から計算した戦績期間、履歴件数、履歴損益、最大DDも表示します
- runner_pc_multi.py は移動平均、リターン、ATR率でルール判定し、保存形式は PC版互換です
- max_position_w が未設定なら従来どおり最大100%、最適化後は安定重視で採択銘柄も最大40%投入に抑えています
- runner は ohlc.csv に利用可能な日付があれば価格/ATRに ohlc.csv を使い、無い場合だけダミー価格にフォールバックします
- 損益計算は current_w_today と手数料/スリッページを反映し、ATRストップに当たった場合はCASH化します
- watchdog_etf.sh は app.log / runner.log が 5MB を超えたら .1 に退避してから空にします
- app health failed 時は watchdog.log にHTTPエラー、PID、8000番ポート状況、直近app.logを残します
- app_pc_multi.py は GET と HEAD に対応しているため、curl -I http://127.0.0.1:8000/ でも 200 が返ります
- runner health は enabled な全銘柄の last_runner_at を確認します
- watchdog は app/runner を python -u で起動するため、ログが遅れて出にくくなっています
- data/candidate_universe.json に追加検証候補を置き、fetch_candidate_ohlc.py で Yahoo Finance chart API から調整後OHLCを取得できます
- test_runner_logic.py は一時ディレクトリに5銘柄それぞれ100年分の合成OHLCを作り、既存dataを変更せずにrunnerロジックを検証します
- backtest_synthetic_100y.py は一時ディレクトリで候補銘柄それぞれ100年分の合成OHLCを作り、損益、CAGR、最大DD、取引回数、勝率、ストップ回数を集計します
- backtest_real_ohlc.py は実 ohlc.csv を一時ディレクトリにコピーして同じ成績とデータ品質フラグを集計します
- optimize_strategy.py は train<=2018-12-31 / test>=2019-01-01 で銘柄別に探索し、採用条件を満たさない銘柄は strategy_enabled=false にします
- optimize_strategy.py のスコアは CAGR - 0.7*最大DD絶対値 を基本に、過大な投入比率、過大な売買回数、過大な露出へペナルティを入れています
- 現在の最適化反映後は 2556/1540/1328/2558/2631/2559/1478/1399 が strategy_enabled=true、その他候補は false です
- 1306/1475/1655/1489 は取得データに大きな日次ジャンプや長期下落フラグが出たため不採用です
- 1591/1476/1550 は実データではプラスですが、安定重視スコアが採択基準に届かなかったため不採用です
- デモで全部観察したい場合は set_demo_mode.py all-on --max-position 0.2 を使います。全21銘柄を strategy_enabled=true にし、最大投入比率を20%に抑えます
- 最適化採択状態へ戻したい場合は set_demo_mode.py optimized を使います。logs/optimization_results.json の採択設定に戻します
