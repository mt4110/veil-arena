# Veil Arenaの完成計画 F0〜F4

2026-10-20 JST策定、2026-10-21 JSTに完成範囲を改訂。基点mainは `5f0c0a3`。
改訂時mainは `7cf98e2`。ユーザー承認により1 VMを標準、2 VMを追加検証へ変更した。
最終ゴールは、他のVeilシリーズが試験を始められるVM・仮想ネットワーク・Kubernetes環境の完成。
物理ネットワークは別の `physical-veil-arena` に分ける。

## 完成条件

1. Linux VM＋netnsと、K3sの1ノード（1 VM）環境を、準備・実行・結果確認・停止の入口から利用できる。
2. Linux系の実Veilを最低1つ接続し、正常・意図した不一致・解除後の正常復帰を判定できる。
3. 固定Nix環境、kernel、依存、K3sとOCIイメージを準備時に固定し、試験中はdownload/buildしない。
4. 通常試験・中断・期限超過でも保存可能な証拠を残し、所有VM・プロセスを回収する。次の正常runが成功する。
5. Mac ARM64／TCGとLinux x86_64／KVMの標準1 VM構成で、新規環境による正常試験とcheckpoint中断・期限検出・回収・正常復帰が成立する。2 VMのノード間通信は追加検証であり、標準完成の必須条件にしない。
6. 本人が標準手順から実行し、期待値との差と証拠の場所を確認できる。Linuxの本人4 run成功で操作確認済み。Mac向けにも固定入力と証拠先を示すが、本人再実行を新たな必須条件にはしない。標準1 VMのF4まで達したら機能開発を一区切りにする。

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
2026-10-21にMacの標準1 VMは現行runnerで通信2回・Keeper正常2回・checkpoint 4 runが[成功](validation/f4-standard-one-vm-mac-20261021.json)。Linux本人4 runの成功と分けて記録する。旧F4の2 VM・統合受け入れは未検証の追加検証として保持する。
F0は [検証契約](F0_CONTRACT.md) と [入力固定表](contracts/f0-inputs.json) を作成・静的確認した状態。
F0時点は定義評価まで。F1でARM64 packageのbuild・archive取得・実VM起動を確認した。

## マイルストーンと残件

| 段階 | 作業 | 完了証拠 |
| --- | --- | --- |
| F0 検証契約 | Keeperの既存最小exec試験、必要権限、入力hash、固定版、対応ホスト、資源・期限、環境失敗の条件を固定 | F0契約とJSONの一致、既存入力とのhash一致、既存単体試験、F1着手条件の明記 |
| F1 1 VMのK3s | 固定Nixpkgsから専用VMとair-gap入力を準備。1 node、固定fixture Pod、クラスタDNS、TCP／UDP疎通を確認 | downloadなしでNode／Pod Ready、名前解決・通信、証拠同期、所有QEMU回収。別の新規VMでも再成功 |
| F2 2 VMのクラスタ（追加検証） | 1 server VM＋1 agent VMを専用仮想LANへ接続。Podを別nodeへ配置。Service・DNS・NetworkPolicyの対象拒否／対照通過と解除を比較 | node配置・送受信の対応、限定拒否と解除後復帰。node未準備・経路故障を防御成功にしない |
| F3 実Veil接続 | 固定Keeper checkerとprofileでbaseline→限定exec拒否→解除後baseline。実行・判定はArenaが管理 | 実コードの実行結果・実label・errno・回収と再成功。不成立の拒否条件を意図的に検出。Kernel未対応は環境エラー |
| F4 標準1 VMの最終受け入れ | 両対応ホストで1 VMを順次起動し、正常・checkpoint中断・期限検出・部分証拠・VM回収・正常復帰を確認。本人操作確認 | 対応表、異なるboot ID・同一起動入力、成功／故障検出／復帰、操作手順と既知の制約。旧F2・F3統合入口は追加検証として維持 |

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
標準1 VMのF4は未完了。Mac技術確認とLinux Keeper/lifecycle本人実行は成功したが、Linux x86_64/KVMのF1 Pod・Service/DNS・TCP/UDPは未検証で標準TODOに残る。次は今回の提供範囲改訂・証拠・手順のレビューと取り込み判断。その後、既存1 VM条件でLinux F1通信を確認してから標準完成を判断する。本人操作はLinuxの成功記録を利用し、追加機能を終了条件に増やさない。詳細は [標準1 VMの完了判定](F4_STANDARD_ONE_VM.md)。2 VMの実装・生証拠・未検証TODOは追加検証として保持し、成功へ読み替えない。

F1の専用入口と運用契約は [F1_K3S.md](F1_K3S.md)。実装・実行結果の判定はF0と分けて記録する。

F2の専用入口・運用契約は [F2_K3S.md](F2_K3S.md)。Linux x86_64／KVMの実クラスタ受け入れはF4に残る。

## F4の標準1 VM残件と追加検証TODO（2026-10-21）

追加機器の購入・T340の起動を前提にせず、既存Linux／KVMの1 VMから進める。
[1 VM lifecycle手順](F4_LIFECYCLE.md)で、Node Ready後・Keeper開始前のcheckpointを使う。
中断→新規正常VM→watchdog期限検出→新規正常VMを一括実行し、部分証拠と所有QEMU回収を判定する。
この1 VM単位はLinux x86_64／KVMで[受け入れ成功](F4_LIFECYCLE_VALIDATION.md)。
1 VMの結果を2 VM側や両ホストの最終受け入れへ一般化しない。

| TODO | 状態・着手条件 |
| --- | --- |
| Linux x86_64の新規F2・F4統合受け入れ（追加検証） | 資源不足で未完了。pi-nodeは4 CPU、既存F2条件は6 CPU以上。条件を緩めず、満たす実行経路が得られた時点で再開 |
| Macの新規F2・F3統合受け入れ（追加検証） | 2026-10-21の現行main preflightもRAM不足で終了3・新規VM 0台。直前観測24,178 MiB、必要24,576 MiB。3 bundleのhash照合は成功。[記録](validation/f4-mac-main-preflight-20261021.json)。旧runnerは2 moduleがmainと異なるため次回準備・照合する。余力がある時点で再開し、他プロセス停止や条件緩和はしない |
| Linux x86_64/KVMの標準F1通信 | 未検証。1 VMでPod Ready・Service DNS・TCP/UDP・証拠同期・回収・新規VM再成功を確認するまで標準F4は未完了。Linux Keeper/lifecycle成功では代用しない |
| Macの標準1 VM受け入れ | [成功](validation/f4-standard-one-vm-mac-20261021.json)。現行runnerで通信2回・Keeper正常2回・新checkpoint bundleの4 runを実行。全8 VMが異なるboot ID、同期・正常回収。旧guest・証拠は保持 |
| 2 VM側のcheckpoint受け入れ（追加検証） | 未完了。既存実装・証拠を保持し、標準1 VMの成功とは別に記録 |
| 本人操作確認 | 既存pi-nodeで本人の4 run実行が成功。一括終了0・異なる4 boot ID・中断/期限検出後の正常復帰・証拠同期・全所有QEMU回収を[確認](validation/f4-owner-lifecycle-20261021.json)。Mac側と2 VM側へは一般化しない |

既存キャッシュ・証拠・diskは保持する。新規ハードウェアがないことを理由に未検証を合格扱いにせず、
条件が揃うまで上記TODOを残す。対象外の物理環境・第三者試用・署名タグ・公開は再開しない。
