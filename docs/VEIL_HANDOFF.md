# 他のVeilシリーズへの引継ぎプロンプト

2026-10-22。1 VM標準でArenaの機能開発を一区切りにし、対象Veilの開発で既存基盤を利用するための引継ぎ。
以下のブロックを対象プロジェクトのチャットへ貼り付ける。記載は基準コミット時点の確認結果であり、再利用時は実在pathと現在のソース・入力の対応を確認する。

```text
このチャットでは、対象のVeilプロジェクトの開発を主目的にしてください。
veil-arenaは、変更の正否を繰り返し判断するための既存の検証基盤として利用します。

■ ゴールと現在地

最終ゴールは、対象Veilの具体的な変更について、正常動作・意図した拒否や不一致・解除後の復帰を、証拠に基づいて判断できることです。

veil-arenaは「1 VM標準」で機能開発を一区切りにしました。
基準mainは db4beac71b67751c6f9f23e4fb65ac7eff66a358 です。
PR #30の取り込みと、このマージコミットのGitHub CI成功まで確認済みです。

Arena側の追加機能を自動的に開発したり、過去のロードマップを再開したりしないでください。
対象Veilの具体的な試験要求に既存入口では対応できないと判明した場合だけ、不足と最小の対応案を示してください。

■ 最初に確認すること

1. 適用されるAGENTS.md等と、対象Veilの現在の仕様・既存試験を読む。
2. 対象VeilとArenaのbranch・HEAD・未コミット変更を確認する。
3. 今回変更する振る舞いを1つに絞り、期待結果・失敗条件・確認方法を明確にする。
4. オフライン試験、netns試験、K3s試験のうち必要十分な入口を選ぶ。
5. 実装成功・単体試験成功・実VM成功・本人操作確認を分けて報告する。

ローカルのArena候補：
/Users/takemuramasaki/_workspace/veil-arena

別ホストでは実在pathを確認してください。
ローカルcheckoutには別作業の未コミット変更があり得ます。mainと同一だと仮定せず、既存変更を保全してください。

■ 標準構成と確認済み範囲

標準は同時起動1 VMのK3s 1ノード環境です。
K3sをKubernetes実装として採用しています。kind/kubeadm別環境は必須ではありません。

対応実行経路：
・Mac ARM64：Mac上で直接起動するQEMU/TCG
・Linux x86_64：KVM。既存pi-nodeでは専用Docker経由の実行経路を確認済み
・Linux ARM64のguest準備経路と、試験の実行対応ホストは区別する
・Colimaはguest準備用ビルダー。Arena試験VMとは別物

確認済み：
・両対応ホストの新規1 VMでNode/Pod Ready、Service DNS、TCP/UDPのnonceと受信件数の照合、正常停止
・同じ固定入力で別の新規VMを起動し、再成功
・固定KeeperのAppArmor exec smoke：正常→拒否不成立の対照→限定exec拒否→profile解除→正常復帰
・両ホストの指定checkpointで、中断→新規正常VM→watchdog期限検出→新規正常VM
・部分証拠保存、証拠同期、所有QEMU回収
・Linuxで本人による上記4 runの実行と表示・証拠先の確認
・既存Blackholeオフライン入口
・netns一括試験18ケース、参照TC ingress eBPF、固定IPv4 TCP/UDP、NetEm全損失との比較
・VM内BPF_PROG_TEST_RUNによる固定70入力の検証

netns各版の対応ホスト・成功記録は専用文書を確認してください。上記を一律に両ホストの最新bundle成功へ一般化しないでください。
これらは固定fixtureと指定checkpointに対する確認です。
任意の攻撃、全Veil機能、本番保護、全タイミングの中断、kernel panicや電源断の保証ではありません。
Keeper接続はVM内の固定AppArmor試験です。Pod配備、station-api・SQLite・独自BPF LSMの検証へ一般化しないでください。
参照eBPFの成功も、対象VeilのeBPF成功とは分けてください。

■ 試験の入口

以下は入口の形です。runner/bundleは現在のホスト・UID・CPU系統・ソースに対応する実在pathを確認してから設定してください。
arena_outは毎回新しい証拠先にします。以下の変数を未設定のまま実行しないでください。

【Blackholeオフライン試験：Arenaのcheckoutで実行】
bash scripts/blackhole-workflow.sh --project /実在path/veil-blackhole
この入口を他のVeilへそのまま流用できるとは仮定しないでください。

【K3sのPod・DNS・TCP/UDP】
"$arena_runner/bin/arena-k3s" "$arena_bundle" daily "$arena_out"

【固定Keeper】
"$arena_runner/bin/arena-keeper" "$arena_bundle" daily "$arena_out"

【Keeperのcheckpoint中断・期限検出・復帰】
"$arena_runner/bin/arena-keeper" "$arena_bundle" lifecycle "$arena_out"

dailyは新規1 VMです。
acceptanceは同時に2 VMを起動する意味ではなく、新規1 VMを順次2回実行します。
lifecycleは新規1 VMを順次4回実行します。

【netns一括試験】
"$arena_prepared/bin/arena-netns" daily "$arena_out"
変更の受け入れ時は対応するacceptance入口で追加の新規VMも確認します。
外部SSDの正式入口はarena-netns-volumeです。使う場合は専用手順を確認してください。
古い16ケースbundleを現行18ケースの対応bundleとして扱わないでください。

新しい対象コードを試す際は、そのコードが実際に試験入力へ入っていることをhash等で確認してください。
既存の固定bundleを起動するだけでは、新しい対象Veilの変更を検証したことにはなりません。

■ 準備・資源・期限

既存Nix store、依存キャッシュ、固定base imageを再利用してください。
準備と試験を分け、試験中にdownload/buildしないでください。

必要な場合の準備入口（Arenaのcheckoutで実行）：
・bash scripts/prepare-k3s.sh guest
・bash scripts/prepare-k3s.sh runner
・bash scripts/prepare-keeper.sh guest /実在path/veil-keeper
・bash scripts/prepare-keeper.sh runner

guestは対応するLinux builderで準備します。
Keeper準備はF0で固定した2ファイルのhashを要求します。
対象変更でhashが変わった場合は不一致を黙って回避せず、比較対象と入力更新方法を明示してください。
既存snapshotやbundleを上書きしないでください。

K3s/Keeperの現行固定条件：
・試験VM：4 vCPU、16 GiB RAM、32 GiB仮想disk
・ホスト利用可能/inactive RAM：18 GiB以上
・disk事前条件：daily 34 GiB、acceptance 66 GiB、lifecycle 130 GiB以上
・workflow：全runで600秒共有
・Node Ready：300秒
・F1 Pod Ready：120秒、通信probe：各5秒
・Keeper parser/checker：各20秒
・通常VM watchdog：660秒
・lifecycleの故障注入期限は専用手順に従う

これらは現行契約であり、最低必要資源を測定した値ではありません。netnsの条件とは分けてください。
試験を通すために期限・期待値・資源の事前条件を緩めないでください。
RAM不足なら必要な余力をすぐ伝えてください。本人が利用PCを切り替えられます。
他用途のプロセスやColimaを勝手に停止しないでください。

■ 結果の確認

コンソールのpassed表示だけでは判断しないでください。
・summary.json：一括結果、各run、終了コード、故障注入の独立検証
・各runのresult.json：stage/detail、guest判定、boot ID
・vm-stop.json：qemu_reaped、qemu_exit_code、escalated、evidence_synced
・Keeperのkeeper-result.json：実label、errno、checker結果、profile解除・一覧復元
・checkpoint-state.json等：実停止状態、対象PID、Keeper未開始
・通信：送信/受信nonce、件数、Node/Pod/imageの対応

回収失敗・同期失敗を、想定した拒否・中断・期限超過で隠さないでください。
lifecycle全体の終了0は、故障注入が正しく検出され、その後の正常復帰まで成立した場合の成功です。
故障run自身のcontroller終了3とは区別します。
各実行には新しい証拠ディレクトリを使ってください。
古い正常・失敗証拠、旧guest、cache、run diskは保持してください。
生disk、serial、kubeconfig、token等は公開しないでください。

■ 責務の境界

対象Veil：
・製品の機能、検知・拒否ポリシー、入力分類、カウント等の仕様
・製品コードの修正と、その変更に必要な回帰試験
・何を期待結果とするかの定義

Arena：
・固定入力、隔離環境、試験の起動と期限管理
・観測とアサーション、結果集計
・証拠保存、所有資源回収、再実行可能性

環境故障を製品の防御成功と判定しないでください。
Arenaへ接続するためだけに対象Veilへ無関係な機能を追加しないでください。
両リポジトリの変更が必要なら、それぞれの理由と範囲を分けてください。

■ 保持する追加検証TODO

以下は未完了のまま保持し、1 VM標準の完成条件へ戻さないでください。
・Linux x86_64/KVMの新規2 VM・F2/F3統合受け入れ：既存ホストのCPU資源不足
・Macの新規2 VM・F2/F3統合受け入れ：事前確認時のRAM不足
・2 VM構成のcheckpoint中断・期限超過・回収・復帰
・過去のLinux/KVM期限超過の根本原因：未確定

2 VMの実装・旧証拠・未検証TODOを削除せず、具体的なノード間試験要求が出た場合に再開を提案してください。
1 VM成功を2 VM成功へ読み替えないでください。

■ 対象外・承認境界

・物理ネットワークは別のphysical-veil-arena
・第三者試用、複数CNI、IPv6クラスタ、HA、kind/kubeadm別実装は今回の標準範囲外
・新PC導入、SSDフォーマットや移行は、この引継ぎによって再開しない
・署名タグ作成、リリース公開は引き続き保留
・コミット、push、PR、マージ、公開は、その時点の明示された承認範囲に従う
・削除や破壊的上書きは対象を示して承認を得る

まず対象Veilの現状を確認し、既存Arenaで検証できる具体的な変更を1つ選んでください。
実作業の前に、そのチャットで適用されるモデル選択手順に従ってください。
今回の引継ぎを、無期限の自動開発や追加機能の包括承認として扱わないでください。

■ 正本として読む資料（Arena内）

docs/COMPLETION_PLAN.md
docs/F4_STANDARD_ONE_VM.md
docs/F0_CONTRACT.md
docs/contracts/f0-inputs.json
docs/F1_K3S.md
docs/F3_KEEPER.md
docs/F4_LIFECYCLE.md
docs/F1_LINUX_STANDARD_VALIDATION.md
docs/F1_MAC_STANDARD_VALIDATION.md
docs/NETNS_WORKFLOW.md
docs/BLACKHOLE_WORKFLOW.md
docs/MAC_SSD_VM.md

一部文書には検証当時の「取り込み・CI待ち」が残っています。
PR #30の取り込みとmain db4beacのCI成功は確認済みですが、古い実VM記録を新しいソースの成功へ自動的に一般化しないでください。
```
