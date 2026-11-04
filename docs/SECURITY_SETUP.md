# LMTrade 安全な初期設定（2026-11-04）

この変更は GitHub 上のソースコードに適用済みですが、**稼働中の Windows PC / Termux には自動反映されません**。特に旧 `~/phone_auto_update.sh` はHTTP経由で取得したシェルスクリプトを無検証で実行します。旧プロセスを停止し、**信頼済みSSH接続で新しいスクリプトを配布**してください。

## 変更点

- `/feed/bars` は**32文字以上の一致する認証トークン**を必要とし、未設定なら常に401になります。トークンの比較には一定時間比較を使用します。
- `daytrade_app.py` と `app_pc_multi.py` は標準で `127.0.0.1` のみ待受します。
- 更新用アーカイブは、Gitにコミット済みの `etf_mobile` から `git archive` で生成します（ローカルのDB・ログ・秘密鍵は含みません）。
- 配布元で **HMAC-SHA256署名**したマニフェストを検証し、アーカイブSHA-256と安全な展開パスも確認します。**HTTPで配布された更新スクリプトは実行しません**。
- 更新サーバーは明示したLAN側IPのみで起動します。

## 1. PC側で2つの独立した秘密鍵を生成

PowerShellで以下を実行してください（秘密鍵の内容はGitやチャットに貼らないでください）。

```powershell
$dir = Join-Path $env:USERPROFILE ".config\etf_mobile"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
try {
    foreach ($file in @("feed_token", "update_hmac.key")) {
        $bytes = New-Object byte[] 32
        $rng.GetBytes($bytes)
        $secret = [Convert]::ToBase64String($bytes)
        [System.IO.File]::WriteAllText(
            (Join-Path $dir $file),
            $secret,
            [System.Text.UTF8Encoding]::new($false)
        )
    }
} finally { $rng.Dispose() }
```

ファイルは**自分だけが読み取れるACL**に設定してください（環境によってユーザーフォルダの継承権限が異なります）。既存の資格情報を別サービスで使い回さないでください。PC側のRSSブリッジは `feed_token`、更新公開スクリプトは `update_hmac.key` を自動読込します。環境変数 `DAYTRADE_FEED_TOKEN` / `LMTRADE_UPDATE_HMAC_KEY` でも指定できます。

## 2. 初回だけ信頼できるSSHでスマートフォンに導入

端末のSSHホスト鍵の指紋を確認し、PC側を最新の `main` に更新してから実施してください。

```powershell
# ここは実際のTermuxユーザー名・スマートフォンのLAN IPに置換
$phone = "TERMUX_USER@PHONE_LAN_IP"
$dir = Join-Path $env:USERPROFILE ".config\etf_mobile"
ssh -p 8022 $phone "umask 077; mkdir -p ~/.config/etf_mobile"
scp -P 8022 (Join-Path $dir "feed_token") "${phone}:~/.config/etf_mobile/feed_token"
scp -P 8022 (Join-Path $dir "update_hmac.key") "${phone}:~/.config/etf_mobile/update_hmac.key"
ssh -p 8022 $phone "chmod 700 ~/.config/etf_mobile && chmod 600 ~/.config/etf_mobile/*"

# 既存の自動更新プロセスを停止し、信頼済みSSHで最新ソースを配布
ssh -p 8022 $phone "pkill -f '[p]hone_auto_update.sh' 2>/dev/null || true"
.\etf_mobile\deploy_to_phone.ps1 -Restart
scp -P 8022 .\etf_mobile\phone_auto_update.sh "${phone}:~/phone_auto_update.sh"
```

SSH接続できない場合、**HTTPからスクリプトをダウンロードして実行する方法は使わず**、先にSSH設定を復旧させてください。スマートフォン上で旧スクリプトをそのまま再起動しないでください。

## 3. 外部アクセスが必要な場合だけスマートフォン側でLAN待受を明示

通常は `127.0.0.1` のまま利用してください。PCからスマートフォンへ価格データを送信する場合のみ、**信頼できるLAN限定**で以下を設定し、監視サービスを再起動します。

```sh
export DAYTRADE_HOST=0.0.0.0
# UIをLANから見たい場合だけ:
# export ETF_APP_HOST=0.0.0.0
cd ~/etf_mobile
nohup sh watchdog_supervisor.sh >> logs/supervisor_launcher.log 2>&1 &
```

設定は起動したプロセスにのみ継承されます。Termux:Boot等で自動起動する場合、その**非公開の起動設定**にも同様の変数を追加してください。LAN待受は自宅/信頼できるネットワーク以外で使わず、外部公開・ルーターのポート転送はしないでください。HTTPにはTLSがないため、信頼できないネットワークではVPNまたはSSHトンネルを利用してください。

## 4. 署名済み更新を配布し、スマートフォン側で監視

PC側で更新対象のコードをコミット・pushし、作業ツリーを更新した状態から、次を実行します。**未コミットのローカルコードは配布されません。**

```powershell
.\phone_deploy\publish_phone_update.ps1
$env:LMTRADE_UPDATE_BIND_HOST = "PC_LAN_IP"
.\etf_mobile\serve_phone_update.cmd
```

スマートフォン側で（別のTermuxセッション）：

```sh
nohup sh ~/phone_auto_update.sh http://PC_LAN_IP:8765 30 >> ~/phone_auto_update_launcher.log 2>&1 &
```

署名検証エラー・アーカイブ破損・秘密鍵未設定時は、更新されずログへ記録します。ログは `~/etf_mobile/logs/phone_auto_update.log` を確認してください。

## 5. PCから送信される市場データ

PCとスマートフォンで同じ `feed_token` を設定した後、PC上で以下を起動できます。

```powershell
.\etf_mobile\market_feed_start.ps1
```

`rakuten_rss_bridge.ps1` へ送る秘密鍵は環境変数経由で渡し、**コマンドライン引数やログには出しません**。電話側の `DAYTRADE_HOST` をループバックのままにした場合はLAN通信できません。逆に許可した場合は、`/status.json` や画面も同じLANから閲覧可能になるため、端末・ネットワーク側でもアクセス制限してください。

## 6. 運用上の注意

- ソースだけの修正では、すでに実行中の旧スクリプトや公開ポートは直りません。PC・Termux側で再起動が必要です。
- キーを過去に公開した疑いがあれば、再発行して両端末で交換してください。
- URLやIPの例は固定値ではありません。ご自身のローカル環境で確認してください。
- 問題が起きた場合は**旧版の無認証自動更新に戻さず**、SSHによる手動配備に切り替えてください。
