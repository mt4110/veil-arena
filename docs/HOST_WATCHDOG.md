# guest runner停止とホストwatchdogの検証

guest内の一括runnerが終了処理を実行できないとき、ホストが期限超過を検出し、取得済みの証拠を保存して所有QEMUを停止できるかを確認する。guest kernelは応答する状態を維持する。kernel panic、ホスト電源断、ホストのfilesystem I/O停止を再現する試験ではない。

## 停止地点と判定

停止試験VMは、通常と同じ読み取り専用imageを使い、追加boot parameter `arena.watchdog=1` で明示的に選ぶ。guestでは実際の一括runnerが正常通信・経路故障・正常通信を終え、TC ingressへのattachとattach前の正常UDP対照を確認する。

親の一括runnerに寿命を束縛したobserverが、実行caseの親PIDとcheckpointを確認して、所有する親runnerへSIGSTOPを送る。`/proc/<pid>/status` の `State: T` を確認し、部分進捗・停止状態・case PID・実際のprogram/map ID・filter情報を保存する。guestの終了処理、正常終了marker、完成したbatch結果は待たない。

ホストcontrollerは停止状態のcheckpointを確認すると、2秒後を試験用期限にする。checkpointが出ない場合の起動期限は120秒。通常のホスト期限660秒と外側670秒のwatchdogは維持する。今回の試験用期限は同じcontrollerの期限検出経路を通すためのもの。

期限超過時は、取得済みの通常結果・途中ログ・manifest・serialログをfile/directory fsyncしてから、所有している未回収のQEMUへSIGTERMを送る。5秒の猶予を超えた場合は所有QEMUへ強制停止を試みるが、今回の受け入れではescalationなし・QEMU終了0・回収済みを要求する。停止後にも残った証拠をfsyncする。

`vm-stop.json` schema 2に、`reason: watchdog`、`status: environment_error`、終了3、停止対象PID、QEMU回収、停止前同期、checkpoint確認、試験用期限と経過時間を記録する。guestの結果は未完了のまま残す。正常な通信や遮断の成功に置き換えない。

この停止VMではguestのnamespace・program/mapを個別に解放するcleanupは実行しない。所有QEMUの終了によって、そのVM内のkernelと資源を回収する。停止前のguest cleanup成功を示す記録を生成しない。

## 再実行と受け入れ

準備は[既存の入口](NETNS_WORKFLOW.md)を使う。必要なホスト入口・guestを準備した後、以下のコマンドからbuild・Nix・依存取得は呼ばない。ホスト入口だけの変更なら検証済みguestを再利用できる。

```sh
/nix/store/<returned-path>-arena-netns-prepared/bin/arena-netns watchdog-daily \
  "$PWD/artifacts/watchdog-daily-001"

/nix/store/<returned-path>-arena-netns-prepared/bin/arena-netns watchdog-acceptance \
  "$PWD/artifacts/watchdog-acceptance-001"
```

| モード | 起動順序 |
|---|---|
| `daily` / `acceptance` | 従来の正常一括試験。1 VM / 2 VM、各16ケース |
| `watchdog-daily` | 停止試験VM → 新規の正常再実行VM。計2 VM |
| `watchdog-acceptance` | 停止試験VM → 正常再実行VM → 追加の新規停止試験VM → 正常再実行VM。計4 VM |

起動した全VM（`watchdog-daily`は2、`watchdog-acceptance`は4）のboot IDがすべて異なり、image/kernel/initrd checksumが一致することを照合する。停止試験は停止状態・部分対照・host期限・証拠同期・QEMU回収を確認できた場合だけ、期待する環境エラーの検出として外側の集計へ合格を記録する。単なる終了3では合格にしない。予期しない結果の場合は後続VMを起動しない。

正常再実行VMはそれぞれ現在の16ケースを実行し、通過・カウント・限定UDP drop・対照通過・detach後の通信、異常入力、ロード故障、guestキャンセル・タイムアウトからの復帰も確認する。guest kernelの応答喪失やQEMUの停止不能を確認したことにはしない。

SSD上の既存bundleを使う場合は、[SSD正式入口の手順](MAC_SSD_VM.md#runner停止からの復帰確認)から `arena-netns-volume ... watchdog-daily ...` を実行できる。こちらは停止と正常再実行の2 VMに限定する。

## 証拠

停止VMの出力には `watchdog-ready.json`、`watchdog-runner-status.json`、`netns-batch/batch-progress.json`、TC checkpointと正常UDP対照、完了した各caseの結果、`vm-stop.json`、`serial.log`、manifestを残す。完成したguest batch結果・service終了marker・TC cleanupは存在しないことを確認する。host checker通過後には `watchdog-verification.json` を新規作成し、期待する環境エラーと未完了guest結果を明記する。

以前のartifact・image・依存cache・GC root・停止containerは削除しない。実起動の確認結果は[検証記録](HOST_WATCHDOG_VALIDATION.md)へ保存する。
