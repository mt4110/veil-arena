# TC故障検出の検証記録

2026-10-11 JST。3種類のTC故障と正常TCへの復旧を8ケースの一括試験へ追加し、Linux x86_64／KVMで確認した。修正版の受け入れは2台の新規VM、各8ケースで成功した。結果の集計・証拠照合は[verification-summary.json](../artifacts/tc-fault-validation-1791666474231399000/verification-summary.json)に保存した。これは固定IPv4/UDPの参照eBPFと検証基盤の確認で、MVP全体の完成ではない。

## 受け入れ結果

| ケース | 元ケースの判定・終了コード | 確認した内容 |
|---|---|---|
| normal-001 | passed / 0 | 双方向ICMP/TCP/UDP、転送停止と回復 |
| route-fault-001 | failed / 1 | gateway成功、指定した戻り経路欠損によるping失敗 |
| normal-002 | passed / 0 | 正常経路へ復帰 |
| tc-ingress-001 | passed / 0 | plain/pass/count/drop/detachedの5段階 |
| tc-drop-disabled-001 | failed / 1 | drop段階を実際のMODE=1へ変更。指定・対照とも4件到達し、両counter差分seen/pass/drop=4/4/0。通常のdrop期待値が不一致になる |
| tc-wrong-hook-001 | environment_error / 3 | 同じprogramをegressへ接続。ingressが空、egressのprogram ID・direct-action・handleを観測し、通常のattach確認が失敗する |
| tc-receiver-stopped-001 | environment_error / 3 | bind readiness後、送信前の受信helperをsignal 9で停止・回収。通常の生存確認が失敗し、drop送信・受信結果を生成しない |
| tc-restored-001 | passed / 0 | 故障指定なしの5段階へ復帰。限定dropとdetach後の正常到達を再確認 |

故障を注入した元ケースの不合格を保持し、原因と証拠が一致した場合だけ、故障検出ケースとして一括結果へpassedを記録する。単なる終了コード1/3、無関係なエラー、空の受信記録では合格にしない。現物の故障結果は通常TCのcheckerから拒否され、detailを無関係なエラーへ変更したin-memory検証も拒否された。

各VMで8ケースがすべて想定結果と一致した。異なるboot IDと同じimage/kernel/initrdのSHA-256を照合した。program/mapの解放、所有helperの回収、owned namespaceへの参照ゼロ、親ネットワーク不変も全ケースで確認した。guest service完了後にhostが証拠をfsyncし、所有QEMUを停止した。guestの正常unmount完了を保証するものではない。

- [受け入れ集計](../artifacts/tc-fault-validation-1791666474231399000/remote-evidence/tc-fault-v3-acceptance-001/summary.json)
- [最初のVMの8ケース](../artifacts/tc-fault-validation-1791666474231399000/remote-evidence/tc-fault-v3-acceptance-001/daily/netns-batch/result.json)
- [追加新規VMの8ケース](../artifacts/tc-fault-validation-1791666474231399000/remote-evidence/tc-fault-v3-acceptance-001/fresh/netns-batch/result.json)
- [追加VMのdrop無効化](../artifacts/tc-fault-validation-1791666474231399000/remote-evidence/tc-fault-v3-acceptance-001/fresh/netns-batch/tc-drop-disabled-001/tc-result.json)
- [追加VMの誤ったhook](../artifacts/tc-fault-validation-1791666474231399000/remote-evidence/tc-fault-v3-acceptance-001/fresh/netns-batch/tc-wrong-hook-001/tc-result.json)
- [追加VMの受信helper停止](../artifacts/tc-fault-validation-1791666474231399000/remote-evidence/tc-fault-v3-acceptance-001/fresh/netns-batch/tc-receiver-stopped-001/tc-result.json)
- [追加VMのTC復旧](../artifacts/tc-fault-validation-1791666474231399000/remote-evidence/tc-fault-v3-acceptance-001/fresh/netns-batch/tc-restored-001/tc-result.json)

## 初回の受け入れ失敗と修正

修正前のdailyは1 VM・8ケースで成功し、初回acceptanceの最初のVMも成功した。しかし追加VMの最後の正常TCケースが`EOF while parsing a value at line 10 column 0`で環境エラーになり、受け入れ全体を不合格にした。[初回の不合格](../artifacts/tc-fault-validation-1791666474231399000/remote-evidence/tc-fault-v2-acceptance-001/summary.json)と[TC結果](../artifacts/tc-fault-validation-1791666474231399000/remote-evidence/tc-fault-v2-acceptance-001/fresh/netns-batch/tc-restored-001/tc-result.json)を保持した。再実行でこの結果を上書きしていない。

失敗はcount前スナップショットの読取り時で、保存された同じJSONは後から完全な内容として読めた。既存の保存処理は最終名を作ってから逐次書き込みを行い、readiness確認はファイルの出現を見ていた。この途中公開の経路を修正し、新規`.pending`へ全内容を書いてfile fsyncした後、hard linkで最終名を原子的に公開する方式にした。既存名は上書きせず、pendingも保持する。共有出力のhard link非対応はエラーにし、途中公開へのfallbackは設けない。任意のfilesystemや電源断に対する保証へは広げない。

同時に読み出す回帰テストと、既存結果の保全テストを追加した。修正版は新しいimageと出力先で受け入れを再実行し、2 VM・16ケースで成功した。現在の2 VMで212組のpending/公開済みJSONの一致と完全なJSON形式を確認した。[公開の照合証拠](../artifacts/tc-fault-validation-1791666474231399000/publication-proof.json)を参照する。追加のdaily VMは同じケースを重複させるため起動しなかった。

## 再利用、固定版、検証範囲

Rust 1.98.1、Cargo実体1.98.0、Python 3.14.7、Aya 0.13.1、Clang 21.1.8、guest kernel 6.12.112の既存固定版を維持した。依存追加はない。参照C objectは以前と同じSHA-256で、faultは固定されたMODE・hook・所有helper寿命へ注入する。

既存Linux builderのNix/Cargo cacheを再利用した。同じ入力で2回準備して同じstore pathを返し、2回目に追加build/fetchのメッセージがないこととGC rootを照合した。試験中にNix/Cargo・依存取得を呼ばない。ソース変更によるimage更新は準備時だけに行った。旧image・cache・source snapshot・結果・停止containerは保持し、builderと今回のruntime containerは停止済み。

現在のprepared pathは`/nix/store/bc6v2kvmn7w20bkq30bdz7d9nm0fqc4m-arena-netns-prepared`。imageは873,705,472 bytes。試験コマンドとpi-nodeの実行方法は[運用手順](NETNS_WORKFLOW.md)を参照する。

LinuxのRustテストはrunner 9件・netns 7件、controller 7件、Python checker 13件が成功し、Linux clippyも成功した。macOSのcontroller 7件・clippy、Rust format、差分・文書リンク・shell例の構文も確認した。ARM64のMac/Linux VM定義は評価したが、実起動はしていない。

5348個の保存した証拠ファイルのSHA-256と23個の試験対象ソースを照合した。必要なruntime store pathは751個で、最終修正版で新規17個をNAR hash照合して追加し、既存コピーを保全した。[準備再利用](../artifacts/tc-fault-validation-1791666474231399000/remote-evidence/tc-fault-v3-cache-proof.json)と[runtime照合](../artifacts/tc-fault-validation-1791666474231399000/remote-evidence/tc-fault-v3-runtime-verification.json)を残した。受け入れ所要197,316 msは今回1回の観測で、性能比較や費用・トークン削減の根拠ではない。

残る条件はMac ARM64／Linux ARM64のVM実起動、IPv6、TCP限定drop、異常・短縮packet、実際のロード失敗、任意の時点のhelper停止、全体キャンセル、VM panic/電源断時の回収、PCAP照合、他filterとの競合。MVP全体を完成扱いにしない。[マイルストーン](MILESTONES.md)を参照する。
