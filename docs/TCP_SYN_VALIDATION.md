# 固定IPv4 TCP SYNの通過・限定drop・復帰

2026-10-19。基点はmain `a3cd1b049d25bbb48d6b97314529fe526f3df13f`。
Veilの変更の正否を繰り返し判断する基盤として、既存UDP対照にTCPの新規接続対照を追加する。
署名タグ・公開、第三者試用、k8sは保留。Blackholeの実装は変更しない。

## 試験契約

対象は専用VM内のapp `10.77.1.10` → server `10.77.2.10`。
対象ポート39201と対照ポート39202へ、各phaseで1回ずつ新しいTCP接続を試みる。
通過する接続はphase・対象を含む固定payloadをechoし、送信側と受信側で内容を照合する。

参照eBPFはrouterの `rt-app` ingressへattachする。
IPv4・非fragment・固定アドレス・固定宛先ポートの、SYN=1かつACK/RST/FIN=0のpacketを数える。
これはフラグによる固定入力の分類であり、接続状態を追跡するfirewallではない。
TCPヘッダーの最小長・data offset・IP total length・data_endを確認してから参照する。
UDPの既存ポート・mode 0/1/2と6カウンターの構成は維持し、TCPのcount/dropをmode 3/4に分ける。

| phase | 接続・payload | SYNカウンター |
| --- | --- | --- |
| plain / pass | 対象・対照が各1接続・echo成功 | 変化なし |
| count | 対象・対照が各1接続・echo成功 | 各seen >= 1、pass=seen、drop=0 |
| drop | 対象はconnect timeout・受信0、対照は1接続・echo成功 | 対象seen >= 1、drop=seen、pass=0。対照はpass=seen、drop=0 |
| detached | filter消失後、対象・対照が各1接続・echo成功 | mapは解放済み。記録上のゼロ値は新たなmap読取ではない |

SYNの再送はpacket数へ含め、アプリケーションの接続試行数と区別する。
SYNの固定個数は仮定しない。timeoutだけでdrop成功にせず、対象dropと対照成功の証拠を突き合わせる。
connect refused等の別のエラーは期待したtimeoutとして受け入れない。

## 一括試験・故障検出

従来の16ケースの後に、`tc-tcp-drop-disabled-001`（期待終了1）と
`tc-tcp-syn-001`（期待終了0）を追加し、schema 8の18ケースをすべて要求する。
前者はdropをcountへ置き換え、対象が接続できてしまう故障を検出する。
後者では通過・count・drop・detach後の復帰をすべて要求する。

case 90秒、コマンド8秒、batch 540秒、service 600秒、host 660秒・外側670秒は維持する。
BPF_PROG_TEST_RUNは従来70入力へTCPの39入力を追加し、計109入力でerrno・返値・byte列・カウンターを照合する。
短縮TCPヘッダー、data offset／IP長の境界、SYN・SYN+ACK・ACK・RST・FIN・SYN+FINを含む。
これは固定fixtureの直接実行であり、wire上の不正TCP通信の網羅的検証とは分ける。

古い16ケースのbundleは当時の入口・証拠とともに保持する。
現在のchecker/controllerはschema 8と18ケースを要求するため、古い結果を新しい試験の成功へ読み替えない。

## 準備と実行

今回の変更はRust guest runnerと参照eBPFに及ぶため、対応するLinuxビルダーでguestを更新する。
固定kernel・依存cacheは再利用する。準備後の試験中はbuild/fetchしない。
Linux先行VMが成功した結果から既存の `guest_bundle.py export` で書き出し、
Macでは `prepare-netns-from-guest.sh` と `arena-netns-volume` を使う。
[準備と反復](NETNS_WORKFLOW.md)、[SSD入口](MAC_SSD_VM.md)を参照する。

## 検証結果

Linux ARM64／TCGの先行1 VMと、Mac ARM64のnative QEMU／TCG・SSD正式入口の
追加新規VMを含む2 VMで、各18ケースがすべて期待結果と一致した。
故障ケースの実終了1・3を成功0へ書き換えず、期待する失敗として外側で検証している。

| 確認 | 3 VMでの結果 |
| --- | --- |
| plain・pass・count | 対象／対照が各1接続、固定payloadのecho成功 |
| countのSYN増分 | 対象seen 1・pass 1、対照seen 1・pass 1、drop 0 |
| drop | アプリ試行は各1回。対象timeout・受信0、対照接続／echo成功・受信1 |
| dropのSYN増分 | 対象seen 2・drop 2・pass 0、対照seen 1・pass 1・drop 0 |
| detached | filter消失、対象／対照が各1接続・echo成功 |
| drop無効化 | 対象が接続できる故障を実終了1で検出し、指定した原因と完全なcleanupを照合 |
| BPF_PROG_TEST_RUN | 各109入力のbyte列・errno・返値・map増分を照合 |
| 既存試験 | UDP・NetEm・ロード失敗・一括キャンセル／タイムアウト・正常復帰を含む従来16ケースも期待どおり |
| 資源回収 | 3 VMすべて停止前証拠同期・所有QEMU回収成功、escalationなし。TC program／mapはunload、pinなし |
| 起動入力 | 3つのboot IDは別。Mac受け入れ2 VMのboot入力hashは同一。kernel・initrd・store imageの内容hashはLinux先行VMとも一致 |

SYN 2個は今回の観測値であり固定期待値ではない。再送を接続2回と数えず、対象1回・対照1回の試行と分けている。
Mac受け入れ2 VMのworkflowは641,700 ms（約10分42秒）。起動前照合・試験・保存・停止を含む今回1回の記録で、性能保証や改善率ではない。

Mac／Linux ARM64で `scripts/check.sh` のfmt・Clippy・Rust／Python試験・Nix全3 systemの定義評価が成功した。
Linuxのnetns Rust試験は18件、Pythonは67件、host診断integrationは15件。Macのnetns実行部はLinux限定のため、Mac単体試験をeBPF実行証拠とは扱わない。
Linuxへ渡したsnapshotと現行のruntime・build・test入力48ファイルのhash一致を確認した。

[公開用集計](validation/tcp-syn-20261019.json)に入力hash・各VMの結果・制約を保存した。
ローカルの生証拠は次に保持する（Git管理対象外）。

- SSD：`/Volumes/Elecom-1TB/Linux/veil-arena/tcp-syn-20261019/`。bundleとMac受け入れ証拠6,454ファイル。
- Mac受け入れsummary：SSD保存先の `results/acceptance-v3/summary.json`。
- [Linux先行summary](../artifacts/tcp-syn-20261019/linux-evidence/seed-v3/summary.json)と生証拠3,225ファイル。
- [集計](../artifacts/tcp-syn-20261019/verification-summary-v3.json)、[SSD manifest](../artifacts/tcp-syn-20261019/ssd-evidence-manifest-v3.json)、[Linux manifest](../artifacts/tcp-syn-20261019/linux-evidence-manifest-v3.json)。各manifestは通常ファイルをSHA-256で記録し、Finderの `.DS_Store` のみ除外した。
- 初回失敗・ビルド／回帰ログ：`artifacts/tcp-syn-20261019/` とその `linux-evidence/seed-v2/`。

今回起動した専用builderとColima profileは元の停止状態へ戻した。default Colimaや他のプロセスは停止せず、旧bundle・証拠・依存cacheを保持した。
上記のローカル検証を終了した時点では、コミット・push・PR作成前だった。署名タグ・リリース公開は引き続き保留する。

準備済みのMac入口から繰り返す場合の例（新しい出力先を指定する）：

```sh
prepared=/nix/store/ixakiqgrd7xckx2bwc3sksmqkkq37mzs-arena-netns-prepared
volume=/Volumes/Elecom-1TB
root="$volume/Linux/veil-arena/tcp-syn-20261019"
"$prepared/bin/arena-netns-volume" "$volume" "$root/images/guest-bundle" daily \
  "$root/results/tcp-self-use-$(date +%Y%m%d-%H%M%S)"
```

終了0と `daily: passed`、`summary.json` の18ケース一致、
`daily/netns-batch/tc-tcp-syn-001/tc-result.json` のphase別接続・カウンターを確認する。
実行中はSSDを接続したままにする。異なるcheckoutやguestへこのimmutableな準備出力を付け替えない。


## 初期失敗と修正

初回の単体試験では、batch拡張後も中断時の残りケース数を13とした期待値と、
checkerの一部に残るschema 7が不一致を起こした。18ケースの契約に合わせて更新し、
期限や安全条件を弱めずMac／Linuxで再検証した。初期ログは削除していない。

最初の更新guest（v2）では、既存16ケースは成功したがTCP追加のattachment確認で環境エラーとなった。
`tc -j filter show` はheader行とprogram行の2行を返すが、新TCP側が1行だけと仮定していたためである。
既存UDPの判定を共通化し、対象program IDがちょうど1つ、direct-action・handle・priorityが一致し、
別programが混在しないことを引き続き要求した。header行の許容とID・priority・direct-action・重複の拒否を
Rust回帰試験へ追加した。失敗VMの証拠・後片付け結果も保持し、修正後の別guest（v3）で確認した。

## 範囲

固定IPv4の新規接続と初期SYNに限る。確立済み接続の遮断、IPv6、任意の攻撃入力、
性能・最大接続数、実BlackholeのLive遮断を検証するものではない。
Linux x86_64／KVMの過去の期限超過の根本原因は未確定のまま保持し、今回の18ケース実起動も未確認。
新guestの `watchdog-daily` 実VM停止・復帰の組は再実行しておらず、過去の16ケースguestでの本人確認と区別する。
