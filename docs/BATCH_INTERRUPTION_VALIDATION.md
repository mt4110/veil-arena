# VM内の一括試験のキャンセル・タイムアウト検証

2026-10-11 JST。[試験仕様](BATCH_INTERRUPTION.md)。Linux x86_64／KVMの既存 `ssh pi-node` 環境で、新規2 VMによる受け入れ検証を完了した。

## 確認範囲

実際の一括runnerが正常・経路故障・正常の対照を終え、TC ingressへのattachを確認したcheckpointで中断する。キャンセルは所有batchへのSIGINT、タイムアウトはcheckpoint後500msのcase期限。途中結果の保存、所有helper・namespace・TC program/mapの回収、同じVM内の後続正常TC比較を確認する。

依存版と既存のBPF fixtureを維持する。新しいrunnerに必要なimageを一度準備し、受け入れの2 VMは同じimageを共用する。準備を再実行した同一出力、GC root、runtime storeのNAR hashも照合する。

## 結果

| 項目 | 確認結果 |
|---|---|
| 一括試験 | schema 6・13ケース × 新規2 VM、26ケースが期待結果と一致 |
| キャンセル | 両VMで実際のbatchへSIGINTを送り、実行caseにもSIGINTを伝達。内側batch・network・TCは `cancelled`、終了130を保持 |
| タイムアウト | 両VMでattach確認後500msのcase期限に到達。内側batch・network・TCは `timeout`、終了4を保持 |
| 部分証拠 | 各中断で正常対照、actual ingress filter/program ID、SIGINT対象PID、51段階の完了ログとcleanupを保存。内側の後続6ケースは `not_run` |
| 所有資源 | helper回収、所有namespaceへの参照0、親network不変、TC detach、作成program/map消失を確認 |
| 正常復帰 | 各中断後に新規namespaceで正常TCの5段階を実行。2 VM合計4回、対象UDP drop・対照UDP通過とdetach後の通信を確認 |
| 終了方法 | 4回の中断ともSIGKILLへのescalationなし、出力収集完了。両QEMUは証拠fsync後に終了0・escalationなしで停止 |
| 再利用 | 同じ入力の準備2回で同一出力。2回目のbuild/copyメッセージなし。2 VMでimage/kernel/initrd checksum一致、boot IDは異なる |
| 証拠照合 | 4,591 fileのSHA-256と、実行ソース27 fileのsnapshot・現在のソース一致を確認 |

boot IDは `f6270526-1bb8-437a-a8b1-c5dcad848273` と `84d8f176-db85-4b5c-9916-8dd4b67e8ff4`。

prepared outputは `/nix/store/mc12k5v65596cpcrnxdn0hw2gfw00mgc-arena-netns-prepared`。imageは873,971,712 byte。必要runtime path 752個のうち新規19個をNAR hash照合して追加し、既存のstore copyを上書きしなかった。試験中のbuild/fetchはない。

Rust 1.98.1、Cargo 1.98.0、Python 3.14.7、kernel 6.12.112、Clang 21.1.8、Aya 0.13.1を維持。BPF正常objectのSHA-256は `52f3bdebf614a30cf8155eb1d173aaf9a0a78fe416337afa726e2f47d9702bba`。正常・ロード拒否fixtureのCソースは前回から変更していない。

LinuxではClippy、Rust本体10件・netns10件、controller7件、Python checker18件を通過。macOS ARM64ではfmt、Clippy、Rust本体10件、controller7件、checker18件を通過した。macOSのcontrollerは既知の並列fixture directory衝突を避けて直列実行し、その既存問題は今回修正していない。macOS上ではLinux限定のnetns moduleは実行しない。

追加した検査では、強制停止、出力未完了、別PID、欠落したfilter、正常結果へのすり替え、不完全cleanup、未実行caseの不正な完了表示、別の中断理由を拒否する。

## 証拠

- [独立照合の集計](../artifacts/interruption-v2-validation-1791697191038043000/verification-summary.json)
- [受け入れの集計](../artifacts/interruption-v2-validation-1791697191038043000/remote-evidence/interruption-v2-acceptance-001/summary.json)
- [キャンセルの部分batch結果](../artifacts/interruption-v2-validation-1791697191038043000/remote-evidence/interruption-v2-acceptance-001/daily/netns-batch/batch-cancel-001/interrupted-batch/result.json)
- [タイムアウトの部分batch結果](../artifacts/interruption-v2-validation-1791697191038043000/remote-evidence/interruption-v2-acceptance-001/daily/netns-batch/batch-timeout-001/interrupted-batch/result.json)
- [ローカル検証](../artifacts/interruption-v2-validation-1791697191038043000/local-verification.json)
- [今回のソース変更](../artifacts/interruption-v2-validation-1791697191038043000/task-source-changes.json)

過去の結果、source snapshot、image、cache、GC rootを保持する。artifactにはbuild・runtimeコピー検証・起動・証拠収集・独立照合のスクリプトと元ログを残す。最初のLinux静的検査で発見したunwrapとtest module配置の指摘を修正し、修正前の検査ログも保持する。

## 制約

指定checkpointでの正常なSIGINT処理に限る。任意タイミングの中断、25/45秒の猶予切れ、SIGKILL、VM全体のホスト側キャンセル、OOM、kernel panic、電源断の回収は未検証。VMは証拠fsync後に所有QEMUを停止する方式で、guest正常unmountの保証ではない。Mac ARM64／Linux ARM64のVM実起動、IPv6、TCP限定drop、性能の確認も残る。MVP全体の完成にはしない。

依頼時の指定はGPT-6.1-sol／high。実モデル・推論設定のruntime metadataは未確認、分担なし。今回の入力・キャッシュ入力（入力の内数）・出力tokenは未取得。料金や節約率は未算出。
