# VM内netnsの検証記録

2026-10-10 JST。Linux x86_64の`pi-node`上で専用KVMゲストを3回起動し、正常通信、経路故障の検出、新規VMでの再成功を確認した。今回の終了条件はapp/router/serverの正常経路であり、eBPFの防御能力やMVP全体の完成ではない。[実行手順と試験契約](NETNS_VM.md)を参照する。

## 実起動の結果

| VM run | 期待・実際のlauncher終了コード | 結果 |
| --- | --- | --- |
| netns-normal-001 | 0 / 0 | 51段階成功。双方向ICMP/TCP/UDP、転送停止による両方向ping失敗、転送復旧後のping/TCP/UDP成功 |
| netns-route-fault-001 | 1 / 1 | serverの戻り経路を省いた。両gatewayのping成功後、app→serverのpingが失敗。成功扱いせず終了 |
| netns-normal-002 | 0 / 0 | 新規VMで正常系51段階が再成功 |

正常系は各VMでTCP 4往復、UDP 4往復を実行し、echo応答を送信した有限のpayloadと照合した。routerは2つの/24サブネットの間をL3転送する。各正常VMで、app→router、router→app/server、server→routerの4つのARP neighbor対応も保存ログから確認した。

故障VMは通常の合格条件を弱めず、`031-app-server-ping`の期待終了コード0に対して実際が1だったことで失敗した。返り経路の設定を省いているため、後続の正常通信ケースは実行しない。後片付けと親ネットワークの確認は実行した。

全3回で`helpers_reaped=true`、所有namespaceのプロセス参照なし、親のlink/route不変、cleanupエラーなし。ホストのRustコントローラーも全回で`service_completed`、結果同期成功、同期後停止、QEMU終了コード0、SIGKILLへの強制移行なしを記録した。故障VMのlauncherは試験結果に従い1で終了し、QEMUの0を合格理由にしていない。

## 証拠

- [検証まとめ](../artifacts/netns-validation-1791631250988058000/verification-summary.json)
- [初回正常result](../artifacts/netns-validation-1791631250988058000/remote-evidence/netns-normal-001/netns-results/result.json)
- [故障result](../artifacts/netns-validation-1791631250988058000/remote-evidence/netns-route-fault-001/netns-results/result.json)
- [再成功result](../artifacts/netns-validation-1791631250988058000/remote-evidence/netns-normal-002/netns-results/result.json)
- [初回topology](../artifacts/netns-validation-1791631250988058000/remote-evidence/netns-normal-001/netns-results/topology.json)
- [ARP対応の確認](../artifacts/netns-validation-1791631250988058000/arp-verification.json)
- [実行用Nix storeの照合](../artifacts/netns-validation-1791631250988058000/remote-evidence/netns-v4-runtime-verification.json)
- [Linuxビルダーの試験ソース照合](../artifacts/netns-validation-1791631250988058000/remote-evidence/netns-source-v4-verification.json)
- [LinuxのNix package buildログ](../artifacts/netns-validation-1791631250988058000/remote-evidence/netns-package-build.log)

回収した338ファイルのSHA-256とarchiveのSHA-256をローカルで照合した。Linux側で試験した17ソースファイルも現在のローカルソースと一致した。3回のstore image・kernel・initrdのSHA-256は一致する。経路故障の指定だけをVM manifestとboot cmdlineで区別した。

実行用closureは819 store path。既存の照合済みコピーを再利用し、新たに追加した21 pathはNAR hashを元のNix store記録と照合した。既存コピーは上書きしなかった。NAR hashの一致は配布者の署名確認とは別の証拠であり、署名検査を無効化したコピーではない。準備中の旧イメージやキャッシュも削除せず保持した。

## 環境と補助確認

Rust 1.98.1、Cargo実バイナリ1.98.0、Python 3.14.7、Linuxゲストkernel 6.12.112、QEMU 11.1.1、固定Nixpkgs revisionは継続した。Nix package内でRust runnerの9試験と、netns binaryにも組み込んだ共通process制御の5試験が成功した。5試験は共通コードの再利用であり、独立した5つのネットワークシナリオではない。Linux上のclippyと、結果checkerの7つのPython試験も成功した。

Mac ARM64では通常runnerの9試験、clippy、結果checkerの7試験が成功した。netns binaryはMacで環境エラー3を返し、Linuxビルダーの通常ユーザーによる直接起動も3で拒否した。どちらもnetnsや出力ディレクトリを作成しなかった。Mac ARM64・Linux ARM64・Linux x86_64のVM定義評価と`nix flake check --all-systems --no-build`は成功した。

起動用コンテナは通常ユーザー、外部networkなし、read-only root、全capabilityなし、`/dev/kvm`のみ。ゲストのnetns試験はrootで、ホスト側の特権コンテナやsudo、ネットワーク設定変更は使わない。所有する試験コンテナ3つとNixビルダーは停止済み。証拠・キャッシュ・停止済みコンテナは保全した。

## 未確認事項

Mac ARM64とLinux ARM64のVM実起動、TCGでのnetns実行、IPv6、DNS、PCAP照合、参照eBPFのattach/count/dropは未確認。netnsシナリオ全体の途中キャンセル、VM panic、host電源断も未確認で、共通process制御の単体試験で代替したとは扱わない。

ゲストの通常シャットダウン問題は既存のまま。試験サービス終了後にホストで証拠を同期してQEMUを停止する契約を継続し、guest filesystemのclean unmountを確認したとは報告しない。

現在はM1のLinux x86_64における正常経路が成立し、M2の実Veilプロジェクト接続も既存オフライン試験で成立した状態。M1/M2全体を完了にするには、対応を宣言するホストのVM実起動と参照eBPFの検証が残る。
