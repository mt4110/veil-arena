# hp-ubuntu: Linux x86_64/KVMの確認済み構成と運用

2026-10-26 JST。固定ソース `4b2291052c22f518aab04797e0070d67c98b94c4`。
標準完成は引き続き1 VM。本書は、資源不足で保留していたLinuxの2 VM追加検証を新ホストで確認する記録。
この構成での実績を再利用候補として示すもので、最低必要量・他のUbuntu/CPU/CNIへの適合認定ではない。

## ホストと固定条件

| 項目 | 確認した構成 |
| --- | --- |
| 接続・実行者 | `ssh hp-ubuntu`、通常ユーザー`take`、UID/GID 1000 |
| CPU | Intel Core i7-9700、8コア/8論理CPU、VT-x/EPT |
| RAM | 約61.6 GiB（64 GB搭載）。実行前に利用可能24 GiB以上を確認 |
| OS/kernel | Ubuntu 26.04.1 LTS、ホストkernel 7.0.0-38-generic |
| disk | ESD-EPK 1 TBのext4/LVM。root LV 100→400 GiB、VG未割当約528 GiB |
| 実行経路 | ネイティブQEMU 11.1.1/KVM、CPU `host`。Docker・ホストK3sは使わない |
| Nix | 2.33.1 multi-user。sandbox/署名検査有効、trusted userはrootのみ |
| 固定guest | kernel 6.12.112、K3s 1.36.4+k3s1、Python 3.14.7、Flannel/VXLAN。F0のlock/hashを維持 |

ホスト準備は別途承認を得て実施。LVM metadataをLinuxとMacへ退避しhash照合後にext4を拡張、`take`を既存`kvm` groupへ追加した。
これを試験中の権限変更や、データ本体のバックアップと混同しない。BitLockerの他disk・物理LAN・ホストAppArmor policyは変更していない。
Nixはroot所有daemon、build userと標準shell hookを持つ永続的な準備基盤。毎試験で導入・撤去しない。
初回installerは展開umaskによる新規storeの読取権限で停止した。標準権限を補正して導入を完了し、storeの内容検証を確認した。失敗ログも保持。

F2はserver 4 CPU/16 GiBとagent 2 CPU/4 GiB、各run diskは32 GiB。同時最大6 CPU/20 GiB。
F3は4 CPU/16 GiB/32 GiBの1 VMを順次実行する。統合入口は6論理CPU、利用可能24 GiB、出力先空き194 GiB以上を要求する。
F2の2クラスタで600秒を共有し、続くF3の2 VMでも600秒を共有する。Node300秒・Pod120秒・probe5秒・Keeper command20秒・通常watchdog660秒と期待値は変更しない。

## 準備とF2単独

Macの未コミット変更を含めず固定commitを`/home/take/veil-arena-lab/sources/arena-4b2291052c22/`へ配置した。
KeeperはF0契約で選んだ2ファイルのみをhash照合して入力へ保存。元checkoutを変更せず、公式署名付きcacheを使ってbundle3種・runner3種を準備した。
全6生成物に準備scriptのGC rootがあり、オフライン再利用を確認。K3sのローカル回帰85件、Keeper回帰15件は成功したが、VM実起動とは別の証拠。

F2単独は新規2クラスタ・計4 VMで成功。一括終了0。
両クラスタで別ノードPod配置、Pod IP直接・Service DNS経由のTCP/UDP、nonce/受信件数、限定deny・対照通過・解除後復帰を照合した。
4 boot IDは異なり、全QEMU/controller PID不在、QEMU終了0/reaped true/非escalation/停止前同期を確認。host link/main routeも前後一致。
単独F2の証拠は`hp-ubuntu:/home/take/veil-arena-lab/runs/f2-acceptance-20261026-a01/`。後続統合試験と別の結果として保持する。

## F2→F3統合受け入れ

[機械可読記録](validation/hp-ubuntu-f4-20261026.json)のとおり、専用統合入口で新規F2 2クラスタ→F3 2 VMが成功し、一括終了0。
先行したF2単独の成功を代用せず、新規6 VMの生証拠を各工程の判定器で再照合した。

| 工程 | 今回の実観測 |
| --- | --- |
| F2新規2クラスタ | 両方で2 Node Ready、別ノードPod、Pod IP/Service DNS経由のTCP/UDP、nonceと受信件数、限定deny・対照通過・解除後復帰 |
| F3新規2 VM | 両方でbaseline true=0/false=1、拒否不成立対照のchecker=1、対象enforce labelでtrueのexecがEACCES/errno13、false=1、解除後baseline再成功 |
| profile回収 | 両F3で対象profile除去、元のprofile一覧へ完全復元 |
| VM回収 | 全6 VMがQEMU終了0・reaped・非escalation・停止前同期、記録されたQEMU/controller PID不在 |
| 不変条件 | 6 boot IDはすべて異なる。全bundleのhash・入力は前後一致。host link/main routeも前後一致 |

F2のQEMU生存時間は第1組server114,243/agent113,137 ms、第2組server108,156/agent110,042 ms。
F3は32,113/32,103 ms。各controllerのduration_msであり、準備・統合全体の性能保証ではない。
試験後の空きdiskは約337.9 GiB。期限・資源条件の変更、失敗の自動retry、試験中のbuild/fetchはない。

生証拠は`hp-ubuntu:/home/take/veil-arena-lab/runs/f4-integrated-20261026-a01/`、preflightと独立照合は同labの`setup/f4-integrated-20261026-a01/`。
Mac側の選定コピーは`artifacts/hp-ubuntu-f4-integrated-20261026-a01/`。ディスク・serial・kubeconfig・join tokenはコピー/公開対象から外す。
今回の文書・集計は未コミットで、PR・push・対象head CIは未実施。実VM結果と後続の文書レビューを分ける。

## 保存先・再実行

Linux側の基点は`/home/take/veil-arena-lab/`。

- `sources/`: 固定source。各sourceの`artifacts/prepared-k3s/`に不変生成物のGC root。
- `inputs/`: hash確認済みの選定入力。
- `profiles/f4-prepared.env`: 当ホスト用の実在するrunner/bundle path。sourceしてもVMは起動しない。
- `setup/`: ホスト準備、preflight、launcher、独立照合の記録。
- `runs/`: 試験ごとの新規証拠・書込みdisk。既存出力を再利用しない。
- `/nix/store`: 依存・不変イメージのcache。試験中はdownload/buildしない。

当ホストの準備済み入力を再利用し、**実行承認後**に次の形で統合試験を行う。

```bash
ssh hp-ubuntu 'bash -s' <<'ARENA_F4'
set -euo pipefail
. /home/take/veil-arena-lab/profiles/f4-prepared.env
arena_out="$arena_base/runs/f4-acceptance-$(TZ=Asia/Tokyo date +%Y%m%d-%H%M%S)-$$"
printf '証拠の予定先: %s\n' "$arena_out"
if "$arena_f4_runner/bin/arena-k3s-f4" \
  "$arena_server" "$arena_agent" "$arena_keeper" "$arena_out"; then
  arena_exit=0
else
  arena_exit=$?
fi
printf '終了コード: %s\n証拠: %s/summary.json\n' "$arena_exit" "$arena_out"
exit "$arena_exit"
ARENA_F4
```

期待は`k3s-f4: passed`・終了0。上位summaryだけでなく、F2/F3のsummary、6 VMのresult/vm-stop、Keeperの実label/errno/解除を確認する。
準備済みpathが消えている・hash/UID/CPU系統が違う・資源不足なら止める。旧入力へ黙って置換しない。
失敗時は新しいrunを自動開始せず、まず失敗stageと証拠保存・所有QEMU回収を確認する。
中断時もrunnerの終了処理を待つ。`killall`・他作業の停止・host route変更は使わない。

## 後片付けと残る範囲

所有VMを停止・回収し、共通image/cacheと証拠を保持するのが通常終了。ホスト上に試験用クラスタを残さない。
runごとにK3s DB・証明書・join token・書込みdiskを分離する。証拠・cacheの自動削除やGC timerは設定しない。
容量整理は対象と復元方法を示して別途承認を得る。生disk/serial/kubeconfig/tokenを公開記録へ含めない。

後続の未コミット実装で、[Linuxの2 VM checkpoint中断/期限検出/復帰](F2_LIFECYCLE.md)も成功した。上記固定sourceの統合結果とは別のsource hash・証拠として記録する。
Macの現行F2/F3統合新規受け入れ・2 VM lifecycle、agent側停止注入は追加検証TODOとして保持する。
過去のpi-node資源不足は当時の事実で、過去のLinux netns期限超過の根本原因も未確定のまま。
この試験は固定KeeperのAppArmor exec smokeであり、Keeper全機能・Pod配備・独自BPF LSM・本番保護を保証しない。
標準1 VMの完成条件、物理環境の別管理、第三者試用・追加リリースの保留は変更しない。
