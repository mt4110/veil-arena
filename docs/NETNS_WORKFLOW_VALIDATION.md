# 一括試験とキャッシュ再利用の検証記録

2026-10-10。Linux x86_64の`pi-node`でNixによる準備を行い、KVMの専用ゲストで日常・受け入れモードを確認した。現在の対象はIPv4の正常通信ベースライン。TC/eBPFやMVP全体の完了を示す記録ではない。

## 確認結果

| 確認 | 結果 |
|---|---|
| 同じソースで準備を2回実行 | 同じprepared store path。2回目のログにbuild・fetch記録なし |
| GC root | prepared出力と一致するsymlinkを保持。既存rootを付け替えない |
| daily | 1 VM内で正常0 → 指定した経路故障1 → 正常0。3対照ケースの一括判定は成功0 |
| acceptance | 一括試験に成功後、別の新規VMでも同じ3ケースが成功。計2 VM・6ケース |
| 起動の独立性 | 全3 VMのboot IDは異なる。image/kernel/initrd checksumは同一 |
| ケースの後片付け | 全9ケースでhelper回収、所有namespaceのプロセス参照なし、親link/route不変、cleanup errorなし |
| VMの停止 | 全3 VMでservice終了を確認、出力をfsync後に所有QEMUを停止。強制killなし |
| 証拠 | 回収962ファイルのSHA-256を照合。Linuxへ渡した19ソースファイルと現在の実装も一致 |
| runtime cache | 748必要store pathのうち新規20 pathをNAR hashで照合。既存コピーを保持 |

正常ケースは各51段階、故障ケースは各33段階。故障の唯一の失敗は`031-app-server-ping`、実終了コード1、通常期待値0。両gatewayと後片付けは成功している。故障のresultは`failed`のまま保存し、一括試験がそれを期待した対照として確認する。

準備済み出力は`/nix/store/72xdvbxlnb5srap4h2sgpfcv7y9djq24-arena-netns-prepared`。軽量化したnetns用EROFS imageは865,148,928 bytes。従来のnetns imageは4,001,345,536 bytesで、どちらも保持した。差は主に不要な開発ツールをゲストから外したことによる。Rust 1.98.1、Cargo実バイナリ1.98.0、Python 3.14.7、kernel 6.12.112、QEMU 11.1.1、固定Nixpkgsは継続した。

日常モードの単発記録は37,375 ms、受け入れモードは71,265 ms。4 GiB・2 vCPUのゲスト、KVM、既存cache、起動前checksum・試験・保存・停止を含むworkflow時間。両モードの実行時間帯には重なりがあるため、性能比較や安定した所要時間の保証に使わない。準備時間やトークン削減率は測定していない。

Rustのhost workflow・停止controllerの7テスト、既存arenaの9テスト、Python checkerの9テストをmacOSとLinuxで確認。Linuxのarena clippy、macOSのhost controller clippy、fmtとdiff検査も成功。Nixの全3 systemのflake評価に加え、aarch64-darwin／aarch64-linux向け一括VM定義を評価した。これらのARM64ゲスト実起動は未確認。

## 証拠の場所

- [ハッシュ照合と集計の検証summary](../artifacts/batch-validation-1791640602201105000/verification-summary.json)
- [日常モードの結果](../artifacts/batch-validation-1791640602201105000/remote-evidence/batch-daily-001/summary.json)
- [受け入れモードの結果](../artifacts/batch-validation-1791640602201105000/remote-evidence/batch-acceptance-001/summary.json)
- [準備再利用とimageサイズの記録](../artifacts/batch-validation-1791640602201105000/remote-evidence/batch-cache-proof.json)

証拠はローカルのGit管理対象外。過去のnetns・ブラックホールの結果、依存cache、旧image、停止済みcontainerを保持した。今回のbuilderとruntime containerは停止済み。Docker/Nixのhost権限、host network、署名検証設定は変更していない。試験用runtime containerはnetworkなし、一般ユーザー、既存のKVM device権限、capabilityなし、読み取り専用storeで起動した。guestへ書込み共有したのは当該VMの新規出力先だけ。

## 残る確認

ARM64ゲスト実起動、IPv6、TC/eBPF attach・count・drop、PCAP照合、batch全体のキャンセル・panic・電源断での回収は未確認。VMの停止契約はホストのfsyncと所有QEMU停止で、guestの正常unmountの証明ではない。ブラックホールのオフラインVM内でコンパイルしたtargetを起動間で共有する変更も含めない。

[通常の実行手順](NETNS_WORKFLOW.md)と[従来の単一ケース記録](NETNS_VALIDATION.md)を併せて参照する。
