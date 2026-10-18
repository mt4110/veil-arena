# SSD正式入口のwatchdog-daily検証

2026-10-19 JST。基点はv0.2.1のmain `0876fb68f79ad88382445d3441b151be5b541036`。
最終ゴールはVeilの変更の正否を繰り返し判断できる検証基盤。
今回は正式なSSD入口から、応答しないguest runnerの停止・証拠保存・所有QEMU回収と正常再実行を一続きで確認する。
以下はローカル検証時点の記録であり、その時点ではPR・commit・push・タグ・リリース公開は行っていない。

## 最小変更と再利用

Rustのworkflowはすでに `watchdog-daily` に対応していたが、SSD入口のモード検査が拒否していた。
`environments/vm/offline.nix` のusageと許可モードの2行へ追加した。
入力検査、UID制約、各起動時のbundle再検査、Rust controller、guestコード、checker、期限・期待値は変更しない。
SSD入口の `watchdog-acceptance` は今回追加しない。

既存bundleから準備した入口は
`/nix/store/h2fc020yc30jjflfcl3cngyh15cxjkyn-arena-netns-prepared`。
新たにビルドしたのはSSD wrapperと結合出力の2 derivationだけで、
guest・kernel・0.2.1のcontroller・依存cacheは再利用した。試験中のbuild/fetchもない。
Mac ARM64／native QEMU・TCG／APFS SSD、4GiB・2vCPU、外向きNICなしで実行した。
guest内のArenaは既存の0.1.0であり、現行ソースから新たにビルドしたguestの受け入れとは分ける。

## 生成された入口の回帰確認

実際のNix出力の `arena-netns-volume` で事前検査11件が成功した。VM起動は伴わない。

- 旧入口は `watchdog-daily` を引数エラー2で拒否することを確認。
- 新入口は `daily`／`acceptance`／`watchdog-daily` の各モードで、未マウント先と既存出力を環境エラー3で拒否。
  既存のmarker内容は不変。
- `watchdog-daily` の不正bundleを環境エラー3で拒否し、workflow出力を作らない。
- 未対応の `watchdog-acceptance`、不正モード、引数不足を引数エラー2で拒否。

この拒否試験は実マウントの抜去や同時書換えではなく、固定した不正入力による確認。
元のbundleに書き込まず、専用の新規fixtureとログを保存した。

## 実VMの停止と正常復帰

正式な `arena-netns-volume ... watchdog-daily ...` で計2 VMを実行し、全体は終了0／passed。
workflow記録の所要時間は357,206ms。性能比較や工数削減の測定値には使わない。

| 項目 | 確認結果 |
| --- | --- |
| 停止VM | observerが所有一括runnerへSIGSTOPを送り、`/proc`のState Tとcheckpointを確認 |
| 停止前の進捗 | 正常・経路故障・正常の3ケースが期待どおり。TC ingressへのattachと正常UDP対照を保存 |
| 期限 | 既存のcheckpoint後2,000ms、起動120,000msの試験用期限を維持し、ホストがwatchdog停止を記録 |
| 停止VMの判定 | `environment_error`／終了3を保持。checkerとworkflowの証拠検証が成功した場合のみ、期待した故障検出として外側のrunをpassedとする |
| 未完了証拠 | 完成batch結果、service終了marker、実行中TCのresultとcleanupは存在しない。guest cleanup成功へ置き換えない |
| 所有QEMU | 両VMで停止前・停止後同期、終了0、回収済み、escalationなし、PID不在を確認 |
| 正常再実行 | 別boot IDの新規VMで16ケースすべてが期待結果と一致。NetEm損失とeBPF dropの区別、中断・timeout・NetEm後の正常復帰も確認 |
| 起動入力 | image/kernel/initrdの記録が両VMで一致 |
| 証拠保全 | 3,294ファイルのSHA-256を照合。完了済みworkflow出力への再実行は終了3で拒否し、全証拠が不変 |

`checkpoint_elapsed_ms` は2,096msだが、checkpoint検知後からQEMU停止完了までを含む。
期限検出の遅延やナノ秒単位の性能値として扱わない。
正常VMのホスト期限660秒・外側670秒も変更していない。
停止VMのkernel資源は所有QEMU終了によって回収し、guest内のnamespaceやmapを個別にcleanupしたとは扱わない。

boot IDは停止VMが `794e5ef6-d8a4-491a-87e8-ec56447f17a7`、
正常再実行VMが `111990fb-9f03-4063-8eba-a99ce3c0f447`。

## 回帰試験と証拠

固定Nix環境のMac `scripts/check.sh` が成功した。
Rust本体10件・netns16件・controller4件・workflow5件・診断統合15件・Python65件、
fmt・Clippy・3 systemのNix評価を含む。既存の不正watchdog証拠・回収異常・診断不能時の拒否試験も維持している。
今回、Linuxホストで試験一式やKVMを再実行した結果ではない。

実VM証拠と拒否試験は `/Volumes/Elecom-1TB/Linux/veil-arena/ssd-watchdog-20261019`。
`watchdog-daily-001/summary.json`、各VMの `vm-stop.json`、停止VMの
`watchdog-ready.json`／`watchdog-runner-status.json`／`watchdog-verification.json` が主要証拠。
`preflight/summary.json` は生成された入口の11件の拒否試験を記録する。
`evidence-manifest.json` はworkflow内3,294ファイルを照合し、Finderの `.DS_Store` を除外する。

ホスト側の準備・回帰ログ、検証スクリプトと実行入力snapshotは `artifacts/ssd-watchdog-20261019/` に保持する。
[公開用集計](validation/ssd-watchdog-20261019.json)に実行入力47ファイルのhash、起動入力・証拠manifestのhashと制約を記録した。
元のguest bundle、以前の結果、依存cache、GC root、無関係なプロセスは削除・停止していない。
再実行手順は[SSD運用手順](MAC_SSD_VM.md#runner停止からの復帰確認)を参照。

## 確認範囲

今回は停止VMと正常復帰VMの1組。4 VMの追加受け入れ、Linuxホストでの今回のruntime、
guest kernel無応答、QEMU停止不能、kernel panic、ホストI/O停止・電源断、SSD稼働中抜去・同時書換えは未確認。
Linux x86_64／KVMの過去の期限超過を解消したとは扱わない。
第三者試用とk8sは引き続き保留。今回の記録を未確認環境の成功や未知の攻撃への防御へ一般化しない。
