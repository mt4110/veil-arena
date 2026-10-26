# Mac: 現行2 VM統合・lifecycleの最終追加検証

2026-10-26 JST。対象はmain commit `2ce0bc877f9e574380b0ada66d8a6f71555eee1a`。
[機械可読記録](validation/mac-two-vm-final-20261026.json)に固定入力・ソースhash・実停止状態を保存した。
今回の試験ではruntimeの修正を行っていない。

## 実行条件

Mac ARM64、RAM64 GiB。実行前の利用可能/inactive RAMは34687 MiB、内蔵disk空き543.63 GiB。
通常Colimaを停止し、専用Colima `veil-arena-mvp-arm64` の既存Nix storeでguestをoffline準備した。
同じ固定lock・F0 Keeper snapshotを使い、UID501、QEMU11.1.1、guest kernel6.12.112、K3s1.36.4+k3s1を維持。
準備後は専用builder/containerを停止し、Mac上のQEMU TCG/CPU maxで試験した。
Apple Virtualization.frameworkやHVFによるArena実行の証拠ではない。

server4 CPU/16 GiB＋agent2 CPU/4 GiB、各disk32 GiB。同時起動は最大2 VM。
Node300秒・Pod120秒・probe5秒・通常watchdog660秒を変更しない。
エレコムSSDはhp-ubuntuへ移設されMacでは利用できないため、内蔵diskへ新規保存した。
SSD上にしかなかった過去の生証拠は、今回の再照合済み証拠と混同しない。残っている過去の退避分は保持する。

## 結果

| 入口 | 新規VM | 結果 |
| --- | ---: | --- |
| F2→F3統合 | F2 4 VM＋F3 2 VM | 全体終了0。ノード間Pod/Service DNS/TCP/UDP、限定deny・対照通過・解除後復帰。固定Keeperの限定exec拒否・解除後復帰 |
| 2 VM lifecycle | 4クラスタ・8 VM | 中断→正常→期限検出→正常、全体終了0。4クラスタで600秒を共有 |

故障注入点は`f2-nodes-ready-before-pods`。server runnerだけを停止し、agentも所有VMとして回収する。
中断・watchdogケースの実controller終了3を保存し、正常クラスタの両controller終了は0。
watchdogは既定の起動待ち120秒、checkpoint観測後2000 msに対し、実測2117 msで検出した。

独立判定器でF2/F3の生証拠とlifecycleを再照合し、14のboot IDがすべて異なることを確認した。
全14 VMでQEMU終了0・回収済み・強制回収なし・証拠同期成功・所有controller/QEMU PID不在。
bundle seal不変、試験前後のhost link/IPv4 route不変を確認した。
試験後、もともと稼働していた通常Colimaは復帰し、専用builderは停止したまま保持する。

## 準備済み入力で再実行する

同じMac/UIDでstore pathと画像が残っていること、必要なメモリとdisk空きを確認する。
通常Colimaや他アプリの停止は、その用途への影響を確認してから行う。
次は準備済み入力を使う入口で、試験中のdownload/buildはない。

```bash
arena_base=/Users/takemuramasaki/_workspace/veil-arena/artifacts/mac-two-vm-final-20261026-a01
arena_f4=/nix/store/xwlwy3hv9j4wblir1f2g2b4mnzcjhgb1-arena-k3s-f4
arena_f2=/nix/store/vvh8gl9w6rdks8ficyf9s7zclmn5jglv-arena-k3s-f2
arena_out="$arena_base/recheck-integrated-$(date +%Y%m%d-%H%M%S)"
"$arena_f4/bin/arena-k3s-f4" "$arena_base/images/server" "$arena_base/images/agent" "$arena_base/images/keeper" "$arena_out"
arena_exit=$?
printf '終了コード: %s\n証拠: %s/summary.json\n' "$arena_exit" "$arena_out"
# 統合が終了0で、所有VMの回収を確認してから、別途lifecycleを実行する。
# lifecycleは空き258 GiB、統合は194 GiBを要求。既存出力は上書きしない。
if [ "$arena_exit" -eq 0 ]; then
  arena_out="$arena_base/recheck-lifecycle-$(date +%Y%m%d-%H%M%S)"
  "$arena_f2/bin/arena-k3s-f2" "$arena_base/images/server" "$arena_base/images/agent" lifecycle "$arena_out"
  arena_exit=$?
  printf '終了コード: %s\n証拠: %s/summary.json\n' "$arena_exit" "$arena_out"
fi
```

期待する表示は統合`k3s-f4: passed`、lifecycle`k3s-f2 lifecycle: passed`、各終了0。
失敗時は後続試験へ進まず、失敗した段階・`vm-stop.json`・保存済み証拠と所有VM回収を確認する。
生証拠は上記`arena_base`の`integrated/`・`lifecycle/`、独立照合は`final-verification.json`。
画像・生disk・serial・private-control・token・kubeconfigは公開しない。

## 区切り

MacとLinuxの固定条件で、1 VM標準に加え、2 VM統合・server側checkpointの故障/復帰を確認できた。
agent側停止注入・panic/電源断・過去Linux netns期限超過の根本原因は未検証/未確定として残す。
任意のVeil製品や別CNIでの成功を保証せず、新しい必須試験を追加してこの区切りを延ばさない。
タグ・追加公開は、検証記録の取り込みと対象main CIを確認してから別途判断する。
