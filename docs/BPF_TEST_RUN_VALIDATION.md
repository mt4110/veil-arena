# BPF_PROG_TEST_RUNの実起動検証

2026-10-11 JST。Linux x86_64／KVM、既存の `ssh pi-node` 先で検証した。[仕様と入力契約](BPF_TEST_RUN.md) を参照する。今回の受け入れは正常な一括試験と追加の新規VMを順に実行し、両方とも成功した。

## 結果

| 項目 | 確認結果 |
|---|---|
| 一括試験 | 9ケース × 新規2 VM、18ケースの期待結果と一致 |
| 直接実行 | 各VMで70入力。34はkernelのEINVAL拒否、36はeBPF実行 |
| 直接実行の合計 | 140回のAPI呼出し、68回の実行前拒否、72回のプログラム実行。wire packet数ではない |
| 正常入力での復帰 | pass/count/dropの対象・対照入力がすべて期待値と一致。最終counter `[3,1,2,2,2,0]` |
| 既存通信試験 | 異常入力の後の `tc-restored-001` で5段階の実通信・counter比較も成功 |
| BPF資源 | 両VMでprogram/map消失確認、pinなし |
| VM | boot IDは異なり、image/kernel/initrd checksumは一致。結果fsync後に所有QEMUを停止、終了0・停止のescalationなし |
| キャッシュ | 同じソースから準備を2回。prepared pathは同一、2回目にbuild/copyメッセージなし。以前のstore copyとGC rootを保持 |
| runtime copy | 必要751 pathのうち新規21 pathをNAR hash照合。既存pathは以前の照合記録と突き合わせ、上書きなし |
| 証拠 | 2,420 fileのSHA-256を確認。実行ソース24 fileをsnapshot・現在のソースと照合 |

boot ID:

- daily: `767cab71-28c8-44c4-a7c5-c944ebd64371`
- fresh: `9f2d1887-7afe-4353-a822-1ce1422126c8`

prepared output: `/nix/store/a7074x6lb2q2zdqmf5dhrza6izhm7sya-arena-netns-prepared`。imageは873,734,144 byte。今回runnerが変わったため新しいimageを作り、2 VMで共用した。試験中のビルド・依存取得は行っていない。

Rust 1.98.1、Cargo 1.98.0、Python 3.14.7、kernel 6.12.112、Clang 21.1.8、Aya 0.13.1を維持した。参照object SHA-256は `fd170b6211671a5ef97f8c27727e1d970794e523c657071fa9a83c341b21766c`。

## ビルド・検証時の問題

LinuxのClippyで固定chunk幅のAPI選択に1件の指摘が出た。`as_chunks::<2>()` に修正した新しいsource snapshotで再検証し、警告抑止は追加していない。最初のログも保存した。

最終Linux検証はClippy（warningsをerrorとして実行）、Rust本体9件・netns8件、controller7件、Python checker14件を通過した。結果チェッカー試験はkernel実行有無・errno・戻り値・counter・出力・入力file・case欠落・map解放証拠の破損を拒否する。

macOS ARM64ではfmtとPython checker14件を通過した。controllerの並列試験でtimestampから作る一時directory名が衝突し、`AlreadyExists` が1件発生した。期待値を変えず直列実行した7件は通過した。Linuxの並列試験は通過しているが、このmacOSのfixture生成の問題は残っている。macOSでのeBPF実行を確認した意味ではない。

## 証拠

- [独立照合の集計](../artifacts/test-run-validation-1791688499791257000/verification-summary.json)
- [VMごとの受け入れ集計](../artifacts/test-run-validation-1791688499791257000/remote-evidence/test-run-v2-acceptance-001/summary.json)
- [dailyの直接実行結果](../artifacts/test-run-validation-1791688499791257000/remote-evidence/test-run-v2-acceptance-001/daily/netns-batch/bpf-test-run-001/result.json)
- [freshの直接実行結果](../artifacts/test-run-validation-1791688499791257000/remote-evidence/test-run-v2-acceptance-001/fresh/netns-batch/bpf-test-run-001/result.json)
- [準備出力の再利用](../artifacts/test-run-validation-1791688499791257000/remote-evidence/test-run-v2-cache-proof.json)
- [今回のコード変更範囲](../artifacts/test-run-validation-1791688499791257000/task-source-changes.json)

artifactにsource archive、転送・ビルド・runtime照合・VM起動・収集・独立照合のスクリプト、元ログ、入力 `.bin`、hash manifestを保持する。以前の結果、image、cache、GC root、停止したcontainerは削除していない。

## 未確認の範囲

今回確認したのは固定kernel・CPU・object・入力の結果。macOS ARM64／Linux ARM64のVM実起動、IPv6、TCP限定drop、任意fuzz、ロード失敗注入、全体キャンセル、VM panic／電源断での回収、性能は未確認。guestの正常unmountまで確認した保証もない。MVP全体を完成扱いにはしない。

依頼時の指定はGPT-6.1-sol／medium。実モデル・推論設定のruntime metadataは未確認、分担なし。今回の入力・キャッシュ入力（入力の内数）・出力tokenは未取得で、料金や節約率は算出していない。
