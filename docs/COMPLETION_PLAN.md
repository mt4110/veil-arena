# Veil Arenaの完成計画 F0〜F4

2026-10-20 JST。ユーザーが採用した完成範囲。基点mainは `5f0c0a3`。
最終ゴールは、他のVeilシリーズが試験を始められるVM・仮想ネットワーク・Kubernetes環境の完成。
物理ネットワークは別の `physical-veil-arena` に分ける。

## 完成条件

1. Linux VM＋netnsと、K3sの2ノード環境を、準備・実行・結果確認・停止の入口から利用できる。
2. Linux系の実Veilを最低1つ接続し、正常・意図した不一致・解除後の正常復帰を判定できる。
3. 固定Nix環境、kernel、依存、K3sとOCIイメージを準備時に固定し、試験中はdownload/buildしない。
4. 通常試験・中断・期限超過でも保存可能な証拠を残し、所有VM・プロセスを回収する。次の正常runが成功する。
5. Mac ARM64／TCGとLinux x86_64／KVMの宣言した構成で、新規環境による追加受け入れが成立する。
6. 本人が手順から実行し、期待値との差と証拠の場所を確認できる。F4まで達したら機能開発を一区切りにする。

K3sをKubernetesの実装として採用する。kind／kubeadm版を別途作ることは完成条件に含めない。
CNIはFlannel／VXLANの1種類。通常の参照試験と、実Veilの試験結果は分ける。
既存のBlackholeオフライン入口・netns 18ケース・証拠・旧bundle・cacheは保持する。
第三者試用、支払意思や販売の検証は、環境完成の前提にしない。

## 現在地

VM・netns・参照eBPF・NetEm・証拠保存・watchdog・SSD入口は実装済み。
TCP追加後のARM64受け入れは [記録](TCP_SYN_VALIDATION.md) を参照する。
既存のVMは `-nic none`、試験serviceはPrivateNetworkであり、K3s用VMへそのまま流用しない。
既存の固定版・VM停止・bundle照合を利用し、新しいシナリオを専用構成として追加する。

F1は、F0の互換入口を含め、Mac ARM64／TCGの新規2 VMで[受け入れ完了](K3S_F1_VALIDATION.md)。
F2もMac ARM64／TCGの新規2クラスタで[受け入れ完了](K3S_F2_VALIDATION.md)。
F3の固定Keeper exec smokeも、Mac ARM64／TCGの新規2 VMで[受け入れ完了](KEEPER_F3_VALIDATION.md)。
F4のF2・F3統合入口を[専用手順](F4_ACCEPTANCE.md)へ追加。
Linux x86_64／KVMのF3は新規2 VMで[成功](F4_ENTRY_VALIDATION.md)。統合新規受け入れはホスト資源不足で残件。
両対応ホスト・指定checkpointの中断/期限超過・本人操作を含むF4全体は未完了。
F0は [検証契約](F0_CONTRACT.md) と [入力固定表](contracts/f0-inputs.json) を作成・静的確認した状態。
F0時点は定義評価まで。F1でARM64 packageのbuild・archive取得・実VM起動を確認した。

## マイルストーンと残件

| 段階 | 作業 | 完了証拠 |
| --- | --- | --- |
| F0 検証契約 | Keeperの既存最小exec試験、必要権限、入力hash、固定版、対応ホスト、資源・期限、環境失敗の条件を固定 | F0契約とJSONの一致、既存入力とのhash一致、既存単体試験、F1着手条件の明記 |
| F1 1 VMのK3s | 固定Nixpkgsから専用VMとair-gap入力を準備。1 node、固定fixture Pod、クラスタDNS、TCP／UDP疎通を確認 | downloadなしでNode／Pod Ready、名前解決・通信、証拠同期、所有QEMU回収。別の新規VMでも再成功 |
| F2 2 VMのクラスタ | 1 server VM＋1 agent VMを専用仮想LANへ接続。Podを別nodeへ配置。Service・DNS・NetworkPolicyの対象拒否／対照通過と解除を比較 | node配置・送受信の対応、限定拒否と解除後復帰。node未準備・経路故障を防御成功にしない |
| F3 実Veil接続 | 固定Keeper checkerとprofileでbaseline→限定exec拒否→解除後baseline。実行・判定はArenaが管理 | 実コードの実行結果・実label・errno・回収と再成功。不成立の拒否条件を意図的に検出。Kernel未対応は環境エラー |
| F4 最終受け入れ | 両対応ホストの新規クラスタで一括確認。指定checkpointの中断・期限超過、部分証拠・VM回収・正常再実行。本人操作確認 | 対応表、異なるboot ID・同一起動入力、成功／故障検出／復帰、操作手順と既知の制約 |

F3の最小試験はKeeperの既存AppArmor exec smokeであり、station-apiの全M0や本番保護を認定しない。
Keeper試験はVM内の一般ユーザーから行い、Podへの移植は求めない。Kubernetes側の通信は別の固定fixtureで検証する。
各Veilの機能完成を待つことや、Veil側の新機能をArenaの都合で開発することはしない。

## 再利用と境界

- 準備済みのkernel／Nix・Cargo cacheと固定base imageを再利用する。K3s／AppArmorを追加したguestは新しい入力としてbuild・照合する。
- 各runのK3s DB・containerd・証明書・join token・書込みdiskは独立させる。前runのクラスタ状態を引き継がない。
- F1は外向きNICなしの1 VM。内部dummy linkと外部へ到達しない既定経路でK3sの経路要件を満たす。F2だけVM間専用仮想LANを追加する。
- ホストLANとのbridge、NAT、ポート公開を作らない。ホストのDNS・route・AppArmorを変更しない。
- 既存netnsのcase／batch期限・期待値は変更しない。K3s初期試験の上限はF0契約で別に定め、失敗時に自動延長・自動再試行しない。
- CIの単体・定義評価、VM実起動、本人操作確認は別々の証拠にする。

## 対象外と停止条件

物理NIC・switch・Pi本体の構築、kind／kubeadm版、複数CNI、HA、cloud模倣、IPv6クラスタ、
実マルウェア・任意攻撃の網羅、性能保証、GUI、第三者試用、販売・公開は対象外。
MacのBlackhole LiveをLinuxへ移植せず、Keeperの独自BPF LSM engineも作らない。

既存Linux x86_64／KVMの期限超過は根本原因未確定のまま保持する。F4の成功を古い失敗の原因解明へ読み替えない。
F1の入力・権限・資源が揃わない、期限内に起動しない、外部通信を必要とする場合は証拠を残して止める。
本書の上限を超える調査や方式変更は、根拠と変更範囲を示して判断する。
F4の後は保守と、他のVeilから持ち込まれる具体的な試験要求へ移る。課題の発見だけで終了条件を増やさない。

## 参照と次の実装単位

K3sは [Kubernetesディストリビューション](https://docs.k3s.io/) であり、
[air-gap手順](https://docs.k3s.io/installation/airgap)、[必要条件](https://docs.k3s.io/installation/requirements)、
[Flannel仕様](https://docs.k3s.io/networking/basic-network-options) を採用根拠とする。
現在の [M0〜M5文書](MILESTONES.md) は履歴として保持し、本書を今後の完成判断に使う。

F1〜F3は完了。F3は[専用入口](F3_KEEPER.md)で固定Keeper checker/profileを実行し、拒否不成立の対照も判定する。
次はF4。対応ホストの最終受け入れ・指定checkpointの中断/期限超過・正常再実行・本人操作を別途着手判断し、機能範囲を増やさない。

F1の専用入口と運用契約は [F1_K3S.md](F1_K3S.md)。実装・実行結果の判定はF0と分けて記録する。

F2の専用入口・運用契約は [F2_K3S.md](F2_K3S.md)。Linux x86_64／KVMの実クラスタ受け入れはF4に残る。

## F4の実行可能な範囲と資源不足TODO（2026-10-21）

追加機器の購入・T340の起動を前提にせず、既存Linux／KVMの1 VMから進める。
[1 VM lifecycle手順](F4_LIFECYCLE.md)で、Node Ready後・Keeper開始前のcheckpointを使う。
中断→新規正常VM→watchdog期限検出→新規正常VMを一括実行し、部分証拠と所有QEMU回収を判定する。
この1 VM単位はLinux x86_64／KVMで[受け入れ成功](F4_LIFECYCLE_VALIDATION.md)。
1 VMの結果を2 VM側や両ホストの最終受け入れへ一般化しない。

| TODO | 状態・着手条件 |
| --- | --- |
| Linux x86_64の新規F2・F4統合受け入れ | 資源不足で未完了。pi-nodeは4 CPU、既存F2条件は6 CPU以上。条件を緩めず、満たす実行経路が得られた時点で再開 |
| Macの新規F2・F3統合受け入れ | 前回は利用可能/inactive RAM約22.1 GiBで24 GiB条件に届かず事前停止。余力がある時点で再開。他プロセス停止や条件緩和はしない |
| 2 VM側・残る対応ホストのcheckpoint受け入れ | 未完了。1 VMの限定検証とは別に記録 |
| 本人操作確認 | [既存pi-nodeの手順](F4_LIFECYCLE_SELF_USE.md)を準備し、起動前確認まで成功。本人による4 run実行・表示と証拠の確認は未実施 |

既存キャッシュ・証拠・diskは保持する。新規ハードウェアがないことを理由に未検証を合格扱いにせず、
条件が揃うまで上記TODOを残す。対象外の物理環境・第三者試用・署名タグ・公開は再開しない。
