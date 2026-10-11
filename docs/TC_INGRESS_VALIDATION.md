# TC ingress一括試験の検証記録

2026-10-11。Linux x86_64の`pi-node`、専用KVM guest、kernel 6.12.112で参照eBPFを実際にロード・接続し、送受信とkernel mapの値を確認した。固定IPv4/UDP・1方向の参照試験であり、MVP全体や本番防御機能の完了ではない。

## 確認した結果

dailyは1 VM・4ケース、acceptanceは2 VM・8ケースで成功した。既存の正常通信・戻り経路故障・正常通信の3ケースに、TC ingressケースを追加している。TCケースでは既存ベースラインを通した後、5段階を実行する。

| TC段階 | 指定フローの受信 | 対照フローの受信 | 指定 seen/pass/drop差分 | 対照 seen/pass/drop差分 |
|---|---:|---:|---|---|
| eBPFなし | 4 | 4 | 未接続 | 未接続 |
| pass | 4 | 4 | 0/0/0 | 0/0/0 |
| count | 4 | 4 | 4/4/0 | 4/4/0 |
| 限定drop | 0 | 4 | 4/0/4 | 4/4/0 |
| detach後 | 4 | 4 | 未接続 | 未接続 |

この表の結果を、成功した3 VMすべてで確認した。受信側はpayload・送信元・連番を照合する。指定フローが受信ゼロでも、対照到達とkernel drop counterが一致しなければ合格にしない。接続済み3モードは実際のper-CPU map値を全CPU分合計した差分。未接続段階にはkernel mapがない。

- TC filterのprogram IDをAya load結果と照合。`rt-app` ingress、clsact/netlink、priority 49152、handle 1、direct-action。
- 全TCケースでfilter解除、owned programの消失、map ID検索のENOENT、pinなしを確認。解除後最大1秒まで実際の解放を観測する。
- 全12ケースで所有helper回収、所有namespaceのプロセス参照なし、親link/route不変、cleanup errorなし。
- 全3成功VMでguest service終了・出力fsync・所有QEMU停止を確認。強制killなし。
- 3 VMのboot IDは異なり、image/kernel/initrd checksumは同一。
- 同じ入力での準備再実行は同じprepared出力。2回目のログにbuild・fetch記録なし。GC rootを保持。
- 回収1,900ファイルのSHA-256、Linuxへ渡した21ソースファイル、現在の実装を照合。必要runtime pathは751、新規コピー42 pathのNAR hashを確認。既存コピーを保持。

prepared出力は`/nix/store/j2i1jfrxr322cjzzq340p3v72b8j77kq-arena-netns-prepared`。参照object SHA-256は`0ebb4f6e507a089e26edf0387061398d3e51120894cbd202bb994558caa7a409`で、全3成功VMのload記録と一致した。imageは873,680,896 bytes。固定Clang 21.1.8、Aya 0.13.1を使い、Rust 1.98.1・Cargo実バイナリ1.98.0・Python 3.14.7・QEMU 11.1.1・固定Nixpkgsを継続した。

dailyの単発workflow記録は63,330 ms、acceptanceは103,303 ms。各guestは4 GiB・2 vCPU。checksum・起動・試験・保存・停止を含む。この単発値を性能比較、安定した所要時間、token削減率には使わない。

## 失敗記録と修正

初回の実起動`tc-v4-daily-001`は環境エラー3として保存した。TCのJSONは階層headerとprogram項目を別に出し、IDは`options.prog.id`にあった。実際の形式へ照合処理を修正し、direct-actionとhandleも確認した。また、解除直後のprogram/map残存を観測したため、上限1秒で実際の消失を再確認する処理を追加した。期限を待っただけで合格にせず、解放が確認できなければ環境エラーを継続する。

修正後の別ソースsnapshot・別imageでdailyとacceptanceを実行した。最初の失敗、修正前のソース・ビルド記録、旧image/cacheは保持している。初回の失敗を成功結果で上書きしていない。

## その他の検証と制約

Linuxでarena clippy、Rustの既存9テスト・netnsの6テスト・host controllerの7テスト、Python checkerの11テストが成功。checkerでは対照欠落、kernel count欠落、drop対象の到達、段階欠落、誤hook、cleanup不完全を合格にしないことを確認した。macOSでhost controllerのtest/clippyを確認。Nix全3 systemのflake評価と、Mac ARM64／Linux ARM64向けbatch VM定義の評価も成功した。ARM64実起動の確認ではない。

Cargo.lockのregistry 49 packageをOSV APIで照会し、この照会ではadvisoryの一致がなかった。脆弱性不存在の証明ではなく、[ローカルの照会記録](../artifacts/tc-validation-1791650409269587000/osv-proof.json)へ範囲を残した。AyaのlicenseはMIT OR Apache-2.0。公開・releaseは行っていない。

ARM64 guest実起動、IPv6、TCP drop、異常packet、PCAP照合、他filterとの競合、全体キャンセル/panic/電源断、通信中のルール更新、性能測定は未確認。host fsync/stopはguestの正常unmountの証明ではない。参照packetコードはC/Clang、制御・ロード・判定はRust/Ayaである。

## 証拠

- [全体のハッシュ照合・TC比較集計](../artifacts/tc-validation-1791650409269587000/verification-summary.json)
- [daily結果](../artifacts/tc-validation-1791650409269587000/remote-evidence/tc-v5-daily-001/summary.json)
- [acceptance結果](../artifacts/tc-validation-1791650409269587000/remote-evidence/tc-v5-acceptance-001/summary.json)
- [追加VMのTC送受信・counter・cleanup](../artifacts/tc-validation-1791650409269587000/remote-evidence/tc-v5-acceptance-001/fresh/netns-batch/tc-ingress-001/tc-result.json)
- [初回失敗の結果](../artifacts/tc-validation-1791650409269587000/remote-evidence/tc-v4-daily-001/summary.json)

証拠はGit管理対象外。今回のbuilder/runtime containerは停止し、削除せず保持した。host権限・ネットワーク・署名検証設定は変更していない。実行手順は[TC参照の仕様](TC_INGRESS.md)と[一括運用](NETNS_WORKFLOW.md)を参照する。
