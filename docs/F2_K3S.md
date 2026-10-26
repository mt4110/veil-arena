# F2追加検証: 2 VMのK3sと限定NetworkPolicy

2026-10-21改訂: [標準完成は1 VM](F4_STANDARD_ONE_VM.md)。本入口・資源・期限・期待値は変更せず追加検証として保持する。

他のVeilが使う仮想クラスタの土台として、server/agent間の通信と限定deny・解除後復帰を判定する。
F3のKeeper実行、F4の両対応ホスト・本人操作、物理環境・第三者試用・署名タグ・公開は含めない。

## 固定入力・資源・隔離

[F0入力表](contracts/f0-inputs.json)の同じNixpkgs・kernel・K3s・Python・QEMU・airgap archiveを使う。
F1のbundleを名前だけ変えて使わず、`f2-server`・`f2-agent`を準備してsealする。
fixtureはArena所有の`tests/k3s/f2_fixture.py`と固定Pythonから作る。試験VMにダウンロード経路はない。

| VM | CPU | RAM | run disk仮想容量 |
| --- | ---: | ---: | ---: |
| server | 4 | 16 GiB | 32 GiB |
| agent | 2 | 4 GiB | 32 GiB |
| 同時起動合計 | 6 | 20 GiB | 64 GiB |

実行前に6 CPU、利用可能/inactive memory 24 GiB、disk空きdaily 66 GiB・acceptance 130 GiBを確認する。
acceptanceは2クラスタを直列に実行し、書込みdiskは4台分保持する。仮想容量と実消費量は分ける。
既存cache・失敗証拠を削除しない。準備用ビルダーは試験前に停止してメモリを戻す。

VMはQEMUのUNIX stream backendで1対1接続する。ホストLANへのbridge・NAT・公開TCPポートは作らない。
専用NICはserver `10.77.10.1/24`、agent `10.77.10.2/24`。Pod `10.42.0.0/16`、Service `10.43.0.0/16`。
Flannel VXLANとK3s内蔵NetworkPolicy controllerを使う。ホストのroute・DNS・権限を変更しない。
UNIX socketは短い専用0700 directoryに置く。QEMUの所有socketが終了時に消えてもdirectoryは保持する。

参加tokenはクラスタごとに生成し、各guestへread-onlyの`/control`で渡す。
`private-control/`とrun diskには秘密が含まれる。出力は0700、ファイルは0600で保持し、生disk・control・serialを公開しない。
JSON集計・API証拠にはtoken・kubeconfig・Secretを取得しない。

## checkpointによる追加の故障検証

[2 VM lifecycle](F2_LIFECYCLE.md)は、serverのNode Ready後・Pod適用前で中断とwatchdogを注入する。
中断→正常→期限検出→正常の4クラスタ・8 VMで600秒を共有し、空き258 GiBを要求する。
Linux x86_64/KVMで両VMの証拠同期・回収と正常復帰を確認済み。[Macでも成功](MAC_TWO_VM_VALIDATION.md)。agent側の停止注入は未検証。

## 準備と実行

```bash
# 同architecture・同じ非root UIDのLinux builder。既存cacheを再利用する。
bash scripts/prepare-k3s.sh f2-server
bash scripts/prepare-k3s.sh f2-agent

# 実行ホスト。
bash scripts/prepare-k3s.sh f2-runner
```

guest bundleは[F1手順](F1_K3S.md)と同じ9ファイルを、新しい0700 directoryへ転送する。
server/agentを混ぜず、directory modeがread-onlyのstore出力をそのまま上書きコピーしない。

```bash
# 準備出力の実在するpathを設定する。REPLACEのまま実行しない。
arena_runner=/nix/store/REPLACE-arena-k3s-f2
arena_server=/Volumes/Elecom-1TB/Linux/veil-arena/REPLACE-server-bundle
arena_agent=/Volumes/Elecom-1TB/Linux/veil-arena/REPLACE-agent-bundle
arena_out=/Volumes/Elecom-1TB/Linux/veil-arena/f2-acceptance-$(date +%Y%m%d-%H%M%S)
"$arena_runner/bin/arena-k3s-f2" "$arena_server" "$arena_agent" acceptance "$arena_out"
arena_exit=$?
printf '終了コード: %s\n証拠: %s\n' "$arena_exit" "$arena_out"
```

`daily`は2 VMの1クラスタ、`acceptance`は追加の新規2 VMを含む2クラスタ。
Node 300秒・Pod段階120秒・exec＋受信ログ取得を合わせたprobe各5秒・一括全体600秒・controller watchdog 660秒は維持する。
各クラスタへ600秒ずつを割り当てず、2クラスタで共有する。失敗したprobeの自動retryや上限延長はしない。

## 判定

1. 正しい版の2 NodeがReady。server Podはserver node、client/control Podはagent nodeに固定。
2. 実Pod配置・固定image configとの実参照の対応を確認。
3. 別ノードのPod IP直接とService DNS経由でTCP/UDPを送り、nonce echoと受信件数1を確認。受信記録はecho前にflushし、公開遅延は同じprobe期限内のログ読取pollで待つ。
4. server Podだけを選ぶIngress policyを適用。UDPをallowし、TCPはcontrol labelだけをallowする。
5. APIのpolicy specと実ルールの公開を確認した上で、clientのTCPをPod IP/Service両方でdeny。
   同じ宛先へcontrol TCPとclient UDPが通り、拒否nonceがserverログに現れないことを確認する。
6. policyを削除し、実ルール解除後にPod IP/ServiceのTCP/UDPがすべて復帰。
7. 両VMの証拠同期・非escalation・所有QEMU回収、追加クラスタの別boot ID・同一入力を確認。

NetworkPolicyはPodへのIngressだけを対象にする。nodeホスト自体や任意のVeil防御機能の認定ではない。
ルール出版/解除の待機は各5秒以内。通信試験の再試行へ置き換えない。
受信ログの公開待ちは同じprobe期限を共有し、トラフィックを再送しない。deny中に遅れて受信証拠が現れた場合も失敗にする。

正常表示は`k3s-f2 acceptance: passed — .../summary.json`、exit 0。
失敗時は最初の`Failure:`とsummary/detail、各nodeのresultのstage/detailを確認する。
環境不成立は3、検証不一致は1、全体期限は4、中断は130。回収失敗は環境エラーとして保持する。

## 証拠

- `summary.json`: 各クラスタの結果・4台のboot ID・各controller終了と回収。
- `fresh-N/server|agent/vm-manifest.json`, `qemu-argv.json`: 固定入力と所有資源・実argv。
- 各nodeの`result.json`, `vm-stop.json`, `service-result.txt`: guest判定とhostの回収判定を分離。
- serverの`cluster-nodes.json`, `arena-f2-pods.json`, `kube-system-pods.json`: Node/Podの実状態。
- `service-state.json`, `policy-state.json`, `policy-rules-before|after|removed.txt`: DNS宛先・policy成立/解除。
- agentの`fixture-image-state.json`: agentに読み込まれた固定config。

F1のMac ARM64成功やCI評価を、F2実VMの成功やLinux x86_64/KVMの受け入れへ読み替えない。

参照: [QEMU UNIX stream backend](https://www.qemu.org/docs/master/system/qemu-manpage.html)、
[K3s NetworkPolicy controller](https://docs.k3s.io/networking/networking-services)。

失敗したexecの終了コードだけではfixture実行を証明しない。期待違反はfixture由来の構造化証拠と
呼出しaction/protocol/host/nonceの一致がある場合だけ不一致1とし、exec基盤障害や証拠欠損は環境エラー3にする。
失敗時のsummaryにはcluster/node単位のstatus・exit_code・stage・detailも残す。
レビュー修正後の検証範囲と旧prepared bundleとの区別は[検証記録](K3S_F2_VALIDATION.md)を参照する。

LAN socket起動待ち中を含むhost中断は130、共有期限超過は4としてrunにもstage/detailを保存する。
回収失敗があれば3を優先し、元のhost_abortは保持する。

通常のホスト側エラー（disk準備・controller起動・LAN待ち・実行・検証）もrunのstatus/exit_code/stage/detailへ保存する。
回収失敗はstage=cleanup・終了3を優先し、回収前の原因はhost_failureへ保持する。

## probeの受信観測と期限超過の診断

Pod Ready確認後、固定server Podのcontainer IDをCRIでinspectし、稼働中のcontainer・Pod UID・
`/var/log/pods/arena-f2_server_<UID>/server/0.log`の一致を要求する。
probe中は同じログ実体を直接読み、`kubectl logs`を繰り返し起動しない。
Kubernetesの[CRIログ仕様とnode側保存](https://kubernetes.io/docs/concepts/cluster-administration/logging/)に沿う、
この専用server VMの固定fixtureだけを対象とする。任意Pod・別runtime・別logPathへのfallbackはない。

execと受信観測の絶対5秒は共有したまま。送信は1回、allowの受信nonceは1件、denyは0件、
遅延公開・重複・対照通信の判定を維持する。欠損・不正CRI/JSON・不完全行・過大ログ・
inode切替・履歴の切詰め/書換えは環境エラー3。部分証拠を受信0件へ読み替えない。
`receiver-log-source.json`にPod/container・path・inode、`receiver-log.cri`に観測した固定fixtureログを保持する。

コマンド期限超過時は`last-command-timeout.json`を確認する。失敗stage・argv・起動時budget・
実行時間・失敗時の残り時間・workflow残り時間を保存する。stdinとコマンド出力は保存しない。
コマンド起動前に期限切れなら起動せず記録し、遅れて成功応答が返っても成功へ読み替えない。
workflow期限超過4と、短い段階期限による環境エラー3は区別する。
