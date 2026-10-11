# VM内の一括試験の中断と復帰

VM内で実際の一括runnerを起動し、正常通信・経路故障・正常通信を終えた後、TC ingressへのattachを確認して中断する。対象は固定の `pass-attached-before-send` checkpoint。kernel panic、電源断、任意のタイミングの異常終了を保証する試験ではない。

## 中断の仕組み

- キャンセル：監督プロセスがcheckpointを確認し、所有している未回収の一括runnerへSIGINTを送る。一括runnerは実行中の所有caseへSIGINTを送り、caseの終了処理を待つ。
- タイムアウト：一括runnerは実行中のcaseのcheckpointを確認し、500ms後を期限にする。期限到達時に理由を `timeout` として保存してから、所有caseへSIGINTを送る。
- SIGINTは所有子プロセスに送る。cleanupに必要なnamespace・TC helperを一斉に停止させない。caseには25秒、監督プロセスには45秒の終了猶予を設け、超過すれば所有process groupを強制停止する。強制停止や不完全な証拠を合格にしない。

正常な外側の一括試験はschema 6・13ケース。`batch-cancel-001` と `batch-timeout-001` は各々、別の一括runnerを同じVM内で起動する。内側は元の10ケースの契約を維持し、4番目のTC比較で中断する。後続6ケースは `not_run` として保存する。追加VMや依存buildはこの内側の試験から呼ばない。

キャンセル後は `tc-after-cancel-001`、タイムアウト後は `tc-restored-001` を実行する。各々、新規のapp/router/server namespaceとTC program/mapで、正常通信、通過、カウント、対象drop、対照通過、detach後の通信を再確認する。

## 保存する証拠と判定

中断された内側のbatch、network、TC結果は `cancelled`・終了130、または `timeout`・終了4のまま保持する。その中断と資源回収を正しく確認できた場合だけ、外側の中断検証caseを `passed` とする。

```text
netns-batch/batch-cancel-001/             timeoutはbatch-timeout-001
  result.json                           中断検証の判定
  interrupted-batch.stdout.log / .stderr.log
  interrupted-batch/
    result.json                         cancelled/timeout、未実行の後続case
    supervisor-request.json             キャンセル時のSIGINT対象と理由
    tc-ingress-001/
      interrupt-request.json            実行caseへ送ったSIGINT対象と理由
      tc-interruption-ready.json         実際のprogram/map ID、filter、case PID
      result.json / tc-result.json       中断理由、部分結果、cleanup
      tc-plain-received.json             attach前の正常対照
      *.stdout.log / *.stderr.log        完了した各段階のログ
```

Rustのguest判定に加え、host checkerは中断理由、終了コード、signal終了なし、出力完了、escalationなし、対象PID、実際のingress filterとprogram ID、正常対照、完了した51段階の順序、namespace参照消失、親ネットワーク不変、TC detachとprogram/map消失を照合する。checkpoint前の対照、途中報告、SIGINT記録が欠けた場合は環境エラーとする。中断されたTC比較を正常な5段階の比較として扱わない。

報告はfile fsync後に最終名へ原子的に公開し、`.pending`も残す。既存の出力先、過去の証拠、image、依存cache、GC rootを削除しない。所有process・namespace・BPF資源の解放が試験の後片付け対象。

## 実行

[通常の準備とdaily/acceptance](NETNS_WORKFLOW.md)の入口を使う。日常は準備済みimageを使った1 VM・13ケース、変更の受け入れは同じimageを利用する追加新規VMを含む2 VM・26ケース。ソース変更時だけ新しいimageを準備し、同じ入力の2回目の準備では既存出力を再利用する。

[実起動の検証記録](BATCH_INTERRUPTION_VALIDATION.md)に確認結果を記録する。Mac ARM64／Linux ARM64のVM実起動、IPv6、VM全体のホスト側キャンセル、猶予を使い切る停止、SIGKILL、OOM、panic、電源断は別の確認項目。今回の確認はguest内の指定checkpointでの中断・復帰に限る。
