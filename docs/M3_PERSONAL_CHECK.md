# 本人によるBlackhole実利用チェック

目的は、既存の入口で実行・失敗診断・再実行ができるかを確認すること。自動試験の件数や実行時間では、本人の理解負担を判定しない。完了までに使った手動操作、詰まった箇所、必要だった説明を記録する。速度競争や試験ではない。

## 1. 正常なmainを実行する

Arenaルートから実行する。依存は事前に準備済みであること。同じcacheを使う他の試験が動いていないことを確認する。

```sh
bash scripts/blackhole-workflow.sh --project ../veil-blackhole
```

`[成功] 通常試験1回目`、`[成功] 通常試験2回目`、`全体結果: passed`、`summary: .../summary.json`が表示される。表示されたパスを開き、全体の`status`と2つの`runs`を探す。JSONはエディタでも読める。ターミナルなら次のように表示する。

```sh
python3 -m json.tool /absolute/path/printed/by/workflow/summary.json
```

正常行では`status`と`runner_status`が両方`passed`になる。分かりにくかったフィールドを記録する。

## 2. 保存済みの失敗を診断する

まず、担当者が準備した不具合再現snapshotを使い、コンソールだけで失敗を診断する。`--project`には案内されたsnapshotのパスを指定する。

```sh
bash scripts/blackhole-workflow.sh --project /absolute/path/to/prepared-snapshot
```

この既知の失敗では終了1が予定どおりの結果。コマンドが成功しなかったことを理由に、正常mainや期待値を変更しない。同じcacheの試験が完了してから次の操作へ進む。

詳細が必要になったときだけ、表示されたログやsummaryを開く。`runs`の`report`はsummaryのあるディレクトリを基準にした相対パス。そのRust報告の`steps`から失敗段階と`stdout_log`／`stderr_log`をたどれる。

今回の診断対象は、修正前のBlackholeコードへDNS予約Zビットの回帰試験だけを追加したsnapshot。既知の不具合再現であり、本番コードを壊して作る失敗ではない。正常mainに誤った変更を加えない。

改善後の入口では、失敗時にコンソールへ`[失敗]`、段階、終了コードと期待値、理由、最大4行のログ抜粋、stdout／stderrログと詳細報告の場所を表示する。まずその表示で診断できるかを確認し、必要ならsummaryへ進む。JSONの`execution.status=completed`はプロセス終了を表し、試験成功ではない。`healthy-1`は保存時の識別子として残る。

次の3点を自分の言葉で説明できるかを記録する。

- どの段階・どの試験が失敗したか。
- 実際の結果と期待値はどう違ったか。
- 詳細を見るならどのログを開くか、後続の試験は実行されたか。

<details>
<summary>診断の手がかりが必要な場合</summary>

今回は`rust-tests`内のZビット回帰試験。正常queryが返る一方、期待値はmalformed。全体は`failed`で、後続runは`not_run`。具体的なassertionの詳細はstdoutログ、Cargoの実行エラーはstderrログで確認できる。分からなければ必要だった説明も記録する。

</details>

## 3. 正常mainを再実行する

最初と同じ正常コマンドをもう一度実行する。cacheや証拠を削除せず、正常2回と新しいsummary保存先を確認する。古い失敗summaryが残っていることも確認する。

同時実行時の`Resource temporarily unavailable`と終了3はcache lockの可能性がある。既存実行の完了を確認してから再実行する。lockファイルを削除して回避しない。準備段階で停止した場合、summaryが作られずコンソールだけにエラーが出ることがある。

## 4. snapshotの準備手順を確認する

[公開された準備手順](BLACKHOLE_WORKFLOW.md#before-snapshotを準備する)を読み、base commit・追加試験・snapshotの保存先を区別できるか確認する。既存snapshotを使った実行の確認だけなら作り直さなくてよい。準備負担も調べる場合は手順どおり新規snapshotを作り、同じsnapshot/cacheで再実行する。

この操作確認は任意の環境構築・権限変更・依存取得を要求しない。Linuxのvendor環境では既存の[Cargo homeの指定](BLACKHOLE_WORKFLOW.md#cargoキャッシュを分ける)を使う。VM起動・通信送信は行わない。

## 返してほしい記録

```text
正常実行・結果確認：できた／詰まった（箇所）
失敗の説明：段階、実際と期待値の違い、後続の状態
再実行：できた／詰まった（箇所）
snapshot準備：実行した／読んだだけ／未確認
追加で必要だったコマンド・説明：
おおよその操作・理解時間：分からなければ未計測
続けて使いたいか、その理由：
```

2026-10-12、本人からコンソールでの失敗診断と正常再実行の成功報告が届いた。[自己利用の受け入れ記録](M3_SELF_USE_ACCEPTANCE.md)を参照する。本人の操作時間・工数削減・維持費は未取得で、製品価値の判定は別に残す。追加観察で詰まったところだけを改善し、新機能や別の不具合探索へ広げない。既存手順の方が扱いやすければ入口を縮める選択も残す。
