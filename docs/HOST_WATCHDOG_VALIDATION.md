# guest runner停止・ホストwatchdogの実起動検証

2026-10-11 JST。[試験仕様](HOST_WATCHDOG.md)。既存 `ssh pi-node` 先のLinux x86_64／KVMで4 VMによる受け入れ検証を完了した。

## 確認範囲

guestの一括runnerを、正常・経路故障・正常を終えTC ingressへのattachを確認した地点でSIGSTOPさせる。guestの終了処理が動かなくても、ホストの試験用watchdog期限で途中証拠を同期し、所有QEMUを停止・回収できるかを確認する。停止VMのguest kernelは応答する状態を維持する。

受け入れは「停止試験VM → 正常再実行VM」を2組、計4 VM。4 VMで同じimage/kernel/initrdを利用し、異なるboot IDを要求する。正常再実行VMでは各13ケースを要求する。

`vm-stop.json` の `checkpoint_elapsed_ms` は、checkpoint検知からQEMU停止完了後までの経過時間。試験用期限は2秒だが、この値を期限検出の遅延や性能測定値として扱わない。

## 結果

| 項目 | 確認結果 |
|---|---|
| 起動順序 | 停止VM → 正常再実行VM → 追加新規停止VM → 正常再実行VM。4 VMすべて期待結果に一致 |
| guest停止 | 両停止VMで一括runnerへのSIGSTOPと `/proc` の `State: T` を確認。caseの親PID、実際のTC ingress filter/program IDも照合 |
| ホスト期限 | 停止checkpoint確認後2秒の試験用期限で `reason: watchdog`、`environment_error`、終了3を記録 |
| 部分証拠 | 正常・経路故障・正常の3ケース、attach前の正常UDP対照、TC checkpoint、停止状態、段階ログ、serial、manifestを保存 |
| 未完了の扱い | guestの完成batch結果・service終了marker・TC cleanupなしを確認。guestの正常終了やcleanup成功に置き換えない |
| 所有QEMU | 4 VMとも停止前・停止後に取得済み証拠を同期、QEMU終了0・回収済み・escalationなし。停止VMのkernelと資源はQEMU終了により回収 |
| 正常再実行 | 2回とも13ケース、合計26ケースが期待結果に一致。TC通過・カウント・限定UDP drop・detach後通信、既存の故障検出・guest中断復帰も維持 |
| 再利用 | 4 VMのimage/kernel/initrd checksum一致、boot IDはすべて異なる。同一入力の準備2回で同一出力、2回目のbuild/copyメッセージなし |
| 独立照合 | 5,486 fileのSHA-256を照合。実行ソース28 fileがsnapshot・現在のソースと一致 |

prepared outputは `/nix/store/l9dkgrbg3k0s3hn7800jl0dij9mwcg7j-arena-netns-prepared`。imageは873,848,832 byte。必要runtime path 752個のうち新規19個をNAR hash照合して追加し、既存コピーを上書きしなかった。試験中のbuild・依存取得はない。

Rust 1.98.1、Cargo 1.98.0、Python 3.14.7、kernel 6.12.112、Clang 21.1.8、Aya 0.13.1を維持。正常・ロード拒否fixtureのCソースと依存lockは前回から変更していない。

LinuxではClippy、Rust本体10件・netns10件、controller9件、Python checker20件を通過した。macOS ARM64ではfmt、Clippy、Rust本体10件、controllerの直列9件、checker20件を通過。macOSの並列fixture生成に残る既存の衝突問題は修正していない。ARM64のMac/TCG・Linux/KVMの定義は評価を通過したが、実起動は確認していない。

追加検査は、無関係な終了3、停止状態以外、期限到達前の記録、QEMU未回収、停止前同期の欠落、escalation、正常終了markerの混入、正常UDP対照の欠落を拒否する。

boot IDは起動順に、`c98e7e77-b16e-44e9-8a4e-f87d76245f7f`、`c0ee6117-7848-4d76-b786-8a89ecc4007b`、`ce8e606f-5ea0-4623-9ef1-8f92c3056583`、`2c7c074a-3620-4b1c-91b4-973176fd63d0`。

## 証拠

- [独立照合の集計](../artifacts/watchdog-v2-validation-1791703825364715000/verification-summary.json)
- [受け入れの集計](../artifacts/watchdog-v2-validation-1791703825364715000/remote-evidence/watchdog-v2-watchdog-acceptance-001/summary.json)
- [最初の停止記録](../artifacts/watchdog-v2-validation-1791703825364715000/remote-evidence/watchdog-v2-watchdog-acceptance-001/daily-watchdog/vm-stop.json)
- [追加新規VMの停止記録](../artifacts/watchdog-v2-validation-1791703825364715000/remote-evidence/watchdog-v2-watchdog-acceptance-001/fresh-watchdog/vm-stop.json)
- [ローカル検証](../artifacts/watchdog-v2-validation-1791703825364715000/local-verification.json)
- [今回のソース変更](../artifacts/watchdog-v2-validation-1791703825364715000/task-source-changes.json)

source snapshot、元ログ、hash manifest、build・runtime照合・起動・収集・独立検証スクリプトを保持する。最初のLinux Clippyで指摘されたstdin読み取り結果の扱いを修正し、指摘を無効化せず再検証した。修正前のログとソースも保持する。過去のartifact・image・cache・GC root・停止containerを削除しない。

## 制約

確認対象はguestの一括runnerだけをSIGSTOPする故障。guest kernel無応答、QEMUの停止不能、ホストのfilesystem I/O停止、OOM、kernel panic、電源断を検証したことにはしない。停止VMでguest namespace・program/mapを個別に解放したとは扱わず、所有QEMUの終了によりVMのkernelと資源を回収する。guestの正常unmountも保証しない。

Mac ARM64／Linux ARM64の定義は評価するが、VM実起動は別の確認項目。IPv6、TCP限定drop、性能なども残る。MVP全体の完成とはしない。

依頼時の指定はGPT-6.1-sol／medium。実モデル・推論設定のruntime metadataは未確認、分担なし。今回の入力・キャッシュ入力（入力の内数）・出力tokenは未取得。料金や節約率は未算出。
