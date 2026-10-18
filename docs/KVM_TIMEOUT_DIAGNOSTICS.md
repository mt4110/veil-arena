# Linux x86_64／KVMの期限超過の切り分け

2026-10-17 JST。基点はv0.2.0のmain `5b26eff49b0a8993dd169fc1dfbf2af5ed30ff32`。
第三者試用とk8sは保留。PR・push前の検証記録であり、根本原因の解消や新しいリリースを意味しない。

## 結論

過去の2件では失敗した段階が異なる。今回、同じprepared image・期限・期待値で
1回だけ再実行したところ16ケースが成功し、期限超過は再現しなかった。
環境が原因だったとも、実装の不具合がなかったとも断定できない。
根拠のない追加反復は行わず、未再現として記録した。

| 実行 | 証拠で確認できること |
| --- | --- |
| 旧 `netem-1pLqF6-v7-acceptance-v1` | `tc-after-cancel-001` が終了3／期待0、93,895ms。ケース詳細は `echo helper readiness timeout/cancellation`。個別の公開待ちとケース全体の期限のどちらに達したかは、旧報告だけでは区別不能 |
| 旧 `netem-1pLqF6-v7-acceptance-v2` | `tc-restored-001` をバッチ監督側がtimeout。直前までの記録されたケース時間の合計505,549ms、対象ケース36,540ms。共有バッチ期限540秒に達し、後続NetEmケースへ未到達 |
| 今回 `kvm-diag-20261017-kIvSzq` | 同じ入力のdaily 1 VM・16ケースが期待結果と一致。workflow 298,898ms、NetEmまで到達し正常復帰。追加のfresh VMは起動していない |

所要時間には子プロセスだけでなくログの同期保存等も含まれ、旧報告はそれらを分離していない。
違う時点の1回同士を比較して性能改善率や原因の証明に使わない。

## 再実行と観測の境界

pi-nodeの専用runtime containerで、既存の
`/nix/store/cyqd86jjqm885ybfz96hhpms97f8y0jb-arena-netns-prepared` を使用した。
旧NetEm v7と同じguest・kernel・参照eBPF・controller・runtime-storeコピーであり、
現在のソースから新たにビルドしたguestの受け入れとは別の結果である。
今回の変更は診断用Pythonと文書のみで、Rust実行コード、Nix定義、依存、期限、期待値は変更していない。
旧失敗と今回の3つのboot入力ハッシュが同じであることを照合した。

従来どおり通常ユーザー、明示的なKVM利用、外向きNICなし、read-only store、
root filesystem read-only、capabilityなし、8GiB／4CPU上限のruntimeを使った。
ケース90秒、個別コマンド8秒、バッチ540秒、guest service・host watchdogも変更していない。
観測用の外側720秒は既存watchdogの外に置いた停止保険で、内側の試験期限を延長しない。

ホストで5秒間隔のvmstatを採取した。初回の起動後平均を除く59サンプルのI/O待ちは4〜41%、
平均19.95%、CPU idleは9〜50%、平均28.05%だった。開始前のI/O pressureも観測した。
利用可能メモリには余裕があったが、これだけで全期間の資源不足を否定しない。
他のプロセスは停止せず、観測用vmstatだけを所有PIDで終了した。

ホストの同期保存probeは専用の新規ファイルへ各4KiBを4回ずつ書いた。
永続ディスク上は11.921〜14.090ms、tmpfs上は0.003〜0.013msだった。
probeはホスト上のPython 3.14.4で採取した観測で、固定Pythonによる試験とは区別する。
guestの9P経由の同期保存時間ではなく、過去の遅延時点の測定でもない。
したがってI/Oは観測した要因の一つで、期限超過の根本原因は未確定のままとする。

## 診断改善と回帰確認

`scripts/diagnose_netns.py` は、VM停止・回収済みのworkflow証拠を読むだけの補助コマンド。
最初の失敗ケース、終了コードの実際と期待、記録された時間、詳細、ログの位置を表示する。
意図した故障検出は失敗として選ばず、監督側timeout・ケース不一致・VM停止の異常を区別する。
欠落は明示し、JSONは1MiBまで、範囲外参照・証拠内のsymlink・非通常ファイルを拒否する。
元の合否を再判定したり、ホスト／実装の原因を推測で確定したりしない。
コマンドの使い方と終了コードは[一括試験の手順](NETNS_WORKFLOW.md#失敗した段階と証拠を調べる)を参照。

回帰試験9件では、期待した故障と実際の失敗の区別、監督側timeout、欠落した子報告、
VM側の停止報告、不正な参照、過大JSON、正常な記録、CLIの終了コード、証拠の不変を確認した。
macOS ARM64では固定Nix環境の `scripts/check.sh` が成功した。
Rust 10件、controller 4件＋workflow 5件、Python 63件、fmt・Clippy、3 systemのNix評価を含む。
Linux x86_64でもキャッシュ済み固定Python 3.14.7でPython 63件が成功した。
LinuxのRustを今回再ビルドしたとは扱わない。

Linuxへの最初の試験入力の転送で、既存の `src/readiness-publication.sh` を含め忘れ、
既存1件が失敗した。最初のarchiveとログを保持し、fixtureを含む入力を別の場所へ配置して
全63件を再確認した。試験の期待値や実装を緩めて通したものではない。

## 証拠と残る確認

ローカル証拠は `artifacts/kvm-timeout-20261017-kIvSzq/`。
`remote-evidence/kvm-diag-20261017-kIvSzq/workflow/summary.json` が今回のVM集計、
各ケースと `vm-stop.json` が合否・後片付け・回収、`host-vmstat.log` と `preflight.json` が観測記録。
`old-evidence-recheck.json` で旧失敗3,887ファイルが元のmanifestと一致した。
Linux側の旧結果・キャッシュ・停止containerと今回の新規結果も保持している。
[公開用集計](validation/kvm-timeout-20261017.json)に、旧失敗と今回の未再現、
診断入力のSHA-256、観測範囲、回帰結果と未確認事項をまとめた。

今回の1 VMでは停止前の証拠同期・所有QEMUの正常終了と回収を確認した。
NetEm drop増分8、eBPF pass 8/drop 0、設定不成立時の送信前エラー、qdisc解除と後続TC正常復帰も照合した。
新しい診断コマンドは旧失敗2件と今回の成功を読み分け、実際の旧証拠を書き換えない。
診断のfixture検証と、旧イメージの実通信試験は別の証拠である。

根本原因の特定、継続して成功するホスト条件、Linux x86_64／KVMの現在のソースによる
追加fresh VMを含む受け入れは未完了。既知の制約を解消したとは記載しない。
新たな失敗が出た場合は、同じイメージと証拠保存先でホスト観測を併記してから、
同期保存・guestの9P経路・ホスト資源のどの層を測る必要があるかを判断する。
