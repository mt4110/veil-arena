# NetEmの全損失と参照eBPFのdropを区別する

対象は専用VM内の固定IPv4/UDP。既存のTC比較と同じ8個（対象port 39101へ4個、対照port 39102へ4個）を使い、正常対照、NetEm全損失、限定したeBPF drop、解除後の正常到達を比較する。任意の実ネットワーク障害の自動診断ではない。

## 観測点と期待値

```text
app/app0 → router/rt-app TC ingress → router/rt-server root NetEm → server/server0
```

NetEmはeBPFの後ろに置く。eBPFが通過を数えた後の損失と、eBPF自身のdropを区別する。phase名・port・sequenceを含むpayloadを受信側で照合し、受信helperのreadinessを確認してから送信する。

| phase | 設定 | 対象／対照の受信 | counterの期待 |
| --- | --- | --- | --- |
| plain | 両方なし | 4／4 | eBPFなし |
| pass | eBPF pass、NetEmなし | 4／4 | eBPF counter不変 |
| count | eBPF count、NetEm loss 100% | 0／0 | eBPFは対象4・対照4をpass、drop 0。NetEmのdrop増分8 |
| drop | NetEm解除、eBPF限定drop | 0／4 | eBPFの対象drop 4、対照pass 4 |
| detached | eBPFも解除 | 4／4 | NetEmの解除readbackとBPF解放を確認 |

NetEmの設定は`tc qdisc add ... root handle 7a00: netem loss 100%`、照合は`tc -j -s qdisc show`。JSONの`loss-random.loss`は比率で、100%は`1.0`として照合する。qdiscのkind・handle・rootを確認し、設定コマンドの終了0だけで送信を始めない。仕様の参考は[NetEm manual](https://man7.org/linux/man-pages/man8/tc-netem.8.html)と[iproute2のJSON出力](https://github.com/iproute2/iproute2/blob/v7.1.0/tc/q_netem.c)。実際の出力は固定ゲストのログに残す。

測定前に、所有routerの`rt-server`だけIPv6を無効化し、router→serverとserver→routerのIPv4 neighborを実際のveth MACへ`PERMANENT`で固定する。設定値・MAC・両neighborの状態をreadbackし、`fixture_control`へ保存する。無効化は[Linux 6.12のinterface単位の設定](https://www.kernel.org/doc/html/v6.12/networking/ip-sysctl.html)、neighbor固定は[ip-neighbour](https://www.man7.org/linux/man-pages/man8/ip-neighbour.8.html)に従う。これはARP探索・IPv6の背景通信を避ける固定fixture用の前提で、ホスト設定や他のnamespaceを変更しない。補助設定は所有namespaceの終了時に回収し、後続の独立した正常ケースは新規namespaceで通常構成を使う。

これは8個の固定UDPで、背景通信を含めたdrop counter増分も8であることを要求する限定試験。余分な損失を勝手に対象パケットとみなさず、不一致は合格にしない。部分的な確率損失やTCP再送は扱わない。

## 設定不成立の対照

`netem-unconfigured`では、同じ所有qdiscにloss 0%を設定する。コマンドは成功しても、要求した100%とはreadbackが異なるため、count phaseの送信前に`NetEm loss configuration differs`・環境エラー3で停止する。plain／passの正常対照は残し、NetEmの設定不成立を通信損失の成功へ読み替えない。

この内側の結果は`environment_error`のまま保存する。一括試験の合格には、指定した診断、未送信、所有NetEmの解除、BPFとnetns/helperの回収がすべて必要。その後の独立した正常TCケースも必須とする。

## 所有と後片付け

NetEmを追加する前は、所有routerのrt-serverに未知のroot qdiscがないことを確認する。解除は同じ所有namespace内で、kind・handle・rootを再確認したqdiscだけを対象にする。解除後もreadbackを保存し、未知のqdiscを削除・置換して続行しない。元のホストinterfaceや共有ネットワークには触れない。

中途失敗でも解除を試み、解除の証拠が不足したら環境エラーとする。namespaceの寿命終了による回収だけを、明示したqdisc解除の成功として扱わない。既存の中断・watchdog試験は維持するが、NetEm操作の任意時点のキャンセル・kernel panic・電源断の新しい保証は含めない。

## 一括実行と証拠

準備・実行は[既存の運用入口](NETNS_WORKFLOW.md)を使う。ゲストコードとmodule一覧が変わるため準備は一度更新し、その後は返された同じpreparedイメージを使う。Nix store・依存cacheと以前のprepared出力を保持し、試験ごとにbuild/fetchしない。受け入れは`acceptance`で追加の新規VMも起動し、boot IDの相違と同じimage/kernel/initrdを照合する。

batch schema 7は16ケース。追加の3つは次の順序。

1. `tc-netem-loss-001`：上表の5 phase、環境解除と正常到達。
2. `tc-netem-unconfigured-001`：意図した設定不成立の診断、送信前停止、所有資源の回収。
3. `tc-after-netem-001`：既存の通常TC比較を新しい所有netnsで再実行。

内側の中断試験の対象一覧は同じゲストのcase契約から選び、追加ケースも未実行の末尾として照合する。以前の10件という固定値は使わない。16ケースと2つの入れ子試験を扱うため、一括の期限は540秒とする。個別通信ケース90秒・個別コマンド8秒・中断checkpointの判定条件は維持し、guest serviceの600秒とホスト側QEMU watchdogの670秒も維持する。

終了要求ログの同期保存とSIGINT送信が完了してから、子プロセスの終了猶予を計測する。送信前の同期時間で猶予を使い切らない。

TC agentの状態JSON公開待ちと起動確認は、個別TCコマンドと同じ最大8秒とし、ケース全体の90秒期限も超えない。一般のecho helperの起動確認は3秒のままとする。

TC agentの終了時は、BPF解放とcleanup JSONの同期保存を終えるため最大8秒待ち、その後も終了しなければ所有プロセスを停止する。通常helperの2秒猶予とは分ける。保存途中の`.pending`だけではcleanup成功としない。

既存のBPF直接実行ケースでは、70入力の同期保存を準備段階（上限90秒）へ分け、保存後の実行ループに20秒の期限を適用する。準備・実行の時間を別々に結果へ残す。全体はバッチの540秒期限で制限され、入力保存、全70件の戻り値・counter、BPF解放の要求は維持する。

各TCケースの`tc-result.json`にphaseごとの送受信・eBPF counterとNetEm snapshotを保存する。`netem-setup.json`は設定確認時点、`netem-cleanup.json`は解除の証拠。段階ログには実コマンドのstdout/stderrが残る。ホスト側Python checkerも、qdiscの識別、loss、counter、受信、段階の順序とcleanupを独立して照合する。

checkerの単体試験は、不正handle、counter不足、eBPF dropとの混同、未成立設定での送信、解除不成立、解除後の受信不足、helper回収不足を拒否する。単体fixtureの成功と実VMの成功は別に記録する。
