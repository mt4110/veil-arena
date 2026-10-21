# 標準1 VMの完了判定（2026-10-21改訂）

他のVeilを持ち込めるVM・仮想ネットワーク環境を主目的とし、1 VMのK3sを標準とする。
2 VMを削除せず、ノード間通信が必要な利用者向けの追加検証として提供する。
これは合格条件の緩和ではなく、提供範囲の変更。過去の未実行・資源不足はそのまま記録する。

## 標準で確認すること

- 既存F1の1 nodeでPod・Service/DNS・TCP/UDP通信を確認。netns・NetEm・参照eBPF入口も保持する。
- 固定Keeperの正常・拒否不成立対照・限定exec拒否・解除後復帰を判定する。
- Mac ARM64/TCGとLinux x86_64/KVMそれぞれで、新規VMによる成功を確認する。
- checkpoint中断→新規正常VM→watchdog→新規正常VMの4 runで、部分証拠・同期・所有QEMU回収を判定する。
- 本人が標準手順を実行し、表示と保存先を確認する。Linux本人実行は成功済み。Mac本人再実行は任意で、新たな完了条件として追加しない。

正常2回は `arena-keeper BUNDLE acceptance NEW_OUTPUT`、故障と復帰4回は
`arena-keeper CHECKPOINT_BUNDLE lifecycle NEW_OUTPUT` を使う。同時起動は常に1 VM。
F3試験の成功だけでDNS/TCP/UDPの再検証済みとは扱わず、F1通信の証拠と分ける。

## 資源とビルドの区別

現行固定契約は4 vCPU・16 GiB RAM・32 GiB仮想disk、ホスト空き/inactive RAMは18 GiB以上。
正常2回のdisk事前条件は66 GiB、lifecycle 4回は130 GiB。workflow 600秒を共有する。
Node 300秒・Keeper command 20秒・通常watchdog 660秒と期待値は変えない。

16 GiBは2026-10-20の承認済み資源設定で、1 nodeの最低必要量を測定した値ではない。
専用Colimaビルダーの12 GiBは別の設定。試験時は準備済みkernel・guest・依存を再利用し、コンパイルしない。
Nix storeの依存共有や動的リンクはguest全体の実行RAMの代用にならない。
小容量化は別の計測付き変更として扱い、今回は固定入力・RAM条件を書き換えない。

## ColimaとMacの試験VMの区別

ColimaはLinux用guestを準備する専用ビルダーとして利用する。今回のK3s試験VMはColima内の入れ子VMではなく、Mac上で直接起動するQEMU/TCG。
準備に使用した `veil-arena-mvp-arm64` だけを元の停止状態へ戻してから試験し、他用途のColimaは停止しない。
Colima全体の停止は試験の条件ではない。必要なホストRAMが不足した場合は、他プロセスを勝手に停止せず事前確認で止める。

今回確認したColimaのdefault/専用builderは `vmType: vz` で、AppleのVirtualization.frameworkを使用する。
これは試験VMのTCGとは別の実行経路。Appleの[Virtualization.framework](https://developer.apple.com/documentation/virtualization/creating-and-running-a-linux-virtual-machine)と、
QEMUの[Hypervisor.frameworkアクセラレーター（HVF）](https://www.qemu.org/docs/master/system/introduction.html)も区別する。
既存QEMUバイナリの `-accel help` ではHVF/TCGが列挙されたが、ArenaのHVF実起動・証拠保存・停止回収は未検証。
本改訂ではTCGから黙って切り替えず、性能改善やRAM削減も主張しない。方式変更は別途判断し、標準完成の条件へ追加しない。
ColimaのVZ設定は[公式設定資料](https://colima.run/docs/configuration/)を参照。

## 追加検証として残すもの

F2の2 VM・別ノードPod・Service/DNS・Flannel/VXLAN・限定NetworkPolicyと解除を保持する。
旧 `arena-k3s-f4` はF2とF3を統合する追加検証入口として維持し、6 CPU・24 GiB条件を変更しない。
Linux資源不足、Mac統合事前停止、2 VM checkpointの未検証は残す。
kind/kubeadm別実装、複数CNI、IPv6、HA、物理環境は標準完成へ追加しない。

## Macの今回の到達点

[検証記録](validation/f4-standard-one-vm-mac-20261021.json)のとおり、現行main `7cf98e2`に対応するrunnerで以下が成功した。

- 既存F1 guestの新規2 VMでNode/Pod Ready、Service DNS経由のTCP/UDP、nonceと受信件数の照合。
- 既存Keeper guestの新規2 VMで正常・拒否不成立対照・限定拒否・解除後復帰。
- 新しいcheckpoint対応guestを専用builderのcacheからオフライン準備し、中断→正常→watchdog→正常の4 run。
- 計8 VMのboot IDはすべて異なり、証拠同期・QEMU終了0・reaped・非escalationを確認。
- 通信とcheckpointの生証拠を現行の独立判定器でも再検証した。

旧guestは削除せず、専用builder/containerは元の停止状態へ戻した。他用途のColima環境は稼働を継続。
Linux本人4 runの成功は別の[記録](validation/f4-owner-lifecycle-20261021.json)へ保持する。
次は文書・証拠のレビューと取り込み判断。標準範囲を越える機能追加は再開しない。

Linux x86_64/KVMのF1通信は[2026-10-22の修正後ソースで新規2 VM成功](F1_LINUX_STANDARD_VALIDATION.md)。
Linux本人4 runとは別に、Pod・Service/DNS・TCP/UDP・証拠同期・回収・再成功の証拠を確認した。
同じ2修正のMac新規2 VMも[再受け入れ成功](F1_MAC_STANDARD_VALIDATION.md)。未修正mainの成功ではなく、今回の2修正のレビュー・取り込み・対象コミットCIを残す。以前のMac証拠へ一般化せず、新しい記録を用いる。
以前のLinux未実施の[F1記録](K3S_F1_VALIDATION.md)と失敗証拠は履歴として保持する。2 VM追加検証のTODOは別のまま。
Macの本人再実行は任意で、Linux本人実行済みの操作条件を重複して必須にしない。

## Macで同じ入力を再利用する入口

```bash
arena_runner=/nix/store/16hbra48x4n3dzn050vg1s3acx895a3r-arena-keeper
arena_bundle=/Volumes/Elecom-1TB/Linux/veil-arena/f4-standard-one-vm-20261021/image
arena_out="/Volumes/Elecom-1TB/Linux/veil-arena/f4-one-vm-self-use-$(date +%Y%m%d-%H%M%S)-$$"
printf '証拠の予定先: %s\n' "$arena_out"
"$arena_runner/bin/arena-keeper" "$arena_bundle" lifecycle "$arena_out"
arena_exit=$?
printf '終了コード: %s\n証拠: %s/summary.json\n' "$arena_exit" "$arena_out"
```

対応は上記の固定入力のみ。期待は `k3s-f3 lifecycle: passed` と一括終了0。
`fresh-1`と`fresh-3`は検証済み故障注入としてcontroller終了3、その後の正常runが終了0となる。
同時起動は1 VM。4 runで600秒を共有し、事前確認が失敗した場合は開始しない。

旧bundle・cache・生証拠は保全し、Mac 1 VM成功を2 VMやLinux追加検証の成功へ一般化しない。
新PC・SSD変更、第三者試用、署名タグ・公開は保留。
