# eBPFロード失敗の実起動検証

2026-10-11 JST。既存の `ssh pi-node` 先のLinux x86_64／KVMで、新規2 VMによる受け入れ試験を実施した。[試験仕様](BPF_LOAD_FAILURE.md)を参照する。

## 結果

| 項目 | 確認結果 |
|---|---|
| 一括試験 | 10ケース × 新規2 VM、20ケースが期待結果と一致 |
| ロード故障 | 両VMでELF解析・map作成後、BPF_PROG_LOADがEINVAL（22）と `invalid func unknown#999999` を返した |
| 元の故障結果 | `environment_error`・終了3を保持。診断と後片付けの一致を一括試験側で期待故障の検出として評価 |
| 誤った遮断成功の防止 | program FD未取得、未attach・未送信、前後のmap counterは0 |
| 所有資源 | 作成した2 mapの消失を確認。ロード前後のprogram ID集合が一致。systemdなど既存のprogramには触れない |
| 復帰 | 同じVM内の後続 `tc-restored-001` で新規netns・正常objectを使い、通過・カウント・対象drop・対照通過・detach後の通信を確認 |
| 既存試験 | 正常通信、経路故障、TC故障、70入力のBPF直接実行も維持 |
| VM | boot IDは異なり、image/kernel/initrd checksumは一致。証拠fsync後に所有QEMUを停止、終了0・escalationなし |
| 再利用 | 準備を2回実行して同じ出力。2回目に追加build/copyメッセージなし。2 VMで同じimageを利用、試験中はbuild/fetchなし |
| 証拠照合 | 2,432 fileのSHA-256を照合。実行ソース26 fileをsnapshot・現在のソースと照合 |

boot IDは `9e896440-1736-4264-85cc-37580ac1b680` と `f729add6-ea34-41ee-b2ca-aa9e6bf15f92`。

prepared outputは `/nix/store/mixcq8qy27sj02ijizpb98sppxlfg6s6-arena-netns-prepared`。今回runner・fixtureの変更に必要なimageを準備し、両VMで共用した。imageは873,762,816 byte。必要runtime path 752個のうち、新規21個をNAR hash照合して追加し、以前のstore copyを上書きしなかった。

Rust 1.98.1・Cargo 1.98.0・Python 3.14.7・kernel 6.12.112・Clang 21.1.8・Aya 0.13.1を維持。拒否fixture objectのSHA-256は `3a227e0b7338ff5c0ffcf1c15df7dbcabdb9e576a5067db95463d35dea092aa0`。正常な参照Cソースは前回から変更していない。試験したobjectのhashは準備証拠に保存し、ソースの一致をbinaryの同一性と同一視しない。

## ローカル・Linuxの検証

LinuxではClippy（warningsをerrorとして扱う）、Rust本体9件・netns9件、controller7件、Python checker16件を通過した。チェッカーの試験では、権限不足・別診断・予期しない成功・未解放map・counter変化・program集合変化・保存ログ不一致・壊れたELFを拒否する。

macOS ARM64ではfmt、Python checker16件、controllerの直列7件を通過した。前回確認したtimestamp由来の並列fixture directory衝突を避けるため、今回のmacOS controller試験は直列実行した。期待値は変えていない。Linuxは並列試験を通過している。macOSの並列fixture生成問題は今回修正していない。

## 証拠

- [独立照合の集計](../artifacts/load-failure-validation-1791694091787561000/verification-summary.json)
- [受け入れの集計](../artifacts/load-failure-validation-1791694091787561000/remote-evidence/load-failure-acceptance-001/summary.json)
- [dailyのロード故障結果](../artifacts/load-failure-validation-1791694091787561000/remote-evidence/load-failure-acceptance-001/daily/netns-batch/bpf-load-failure-001/result.json)
- [freshのロード故障結果](../artifacts/load-failure-validation-1791694091787561000/remote-evidence/load-failure-acceptance-001/fresh/netns-batch/bpf-load-failure-001/result.json)
- [準備とcache再利用の証拠](../artifacts/load-failure-validation-1791694091787561000/remote-evidence/load-failure-cache-proof.json)
- [今回のコード変更範囲](../artifacts/load-failure-validation-1791694091787561000/task-source-changes.json)

artifactに元ログ、fixture、verifier診断、source archive、hash manifest、転送・build・runtime照合・起動・収集・独立照合スクリプトを保持する。以前の結果・image・依存cache・GC root・停止containerは削除していない。

## 残る範囲

確認したのは固定の範囲外helper拒否と後続の正常試験。あらゆるロード障害、OOM、任意kernel/driverは未検証。ARM64 VM実起動、IPv6、TCP限定drop、全体キャンセル、VM panic・電源断での回収、guestの正常unmount確認、性能も残る。MVP全体を完成扱いにはしない。

依頼時の指定はGPT-6.1-sol／medium。実モデル・推論設定のruntime metadataは未確認、分担なし。今回の入力・キャッシュ入力（入力の内数）・出力tokenは未取得、料金や節約率は未算出。
