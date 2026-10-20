# F2の検証状況（2026-10-20）

最終ゴールは他のVeilシリーズが試験できるVM・仮想ネットワーク基盤。
F2は1 server＋1 agentの専用クラスタ、ノード間通信・限定deny・解除後復帰まで。
Keeperの実行はF3、両対応ホスト・中断/期限超過の最終受け入れと本人操作はF4。

## 条件

基点mainはPR #22のマージコミット`0082ae727dfdbab481e9bd4a925eccbc79bd9c71`。
受け入れ試験時点は未commit・未push。対象コミットのGitHub CIはPRで別途確認し、ローカル検証と区別する。
Mac ARM64／native QEMU TCG、server 4 CPU・16 GiB・32 GiB、agent 2 CPU・4 GiB・32 GiB。
合計6 CPU・20 GiB・64 GiBの仮想run disk。2クラスタは直列に実行する。
固定版とairgap archive・fixture入力を準備時に照合し、試験中にdownloadしない。
ホストbridge・NAT・公開TCP portを作らず、専用UNIX streamでVM同士だけを接続する。

Node 300秒・Pod段階120秒・probe各5秒・一括全体600秒・watchdog660秒を維持。
全体600秒をクラスタごとにリセットせず、追加の新規2 VMと共有する。

## 回帰試験

- 受け入れ時ソースのMac全チェック成功。fmt/clippy、Rust回帰、Python回帰97件、Nix全system評価。
- Linux ARM64でF1＋F2の単体28件成功。F2は15件。
- 同一ノードへの誤配置、boot ID再利用、欠けた対照通信、DNS/nonce/受信件数の矛盾、policy不成立を拒否する。
- ローカル実行エラーを正常denyへ変換しない。固定probeの期待違反は不一致1、破損した証拠は環境エラー。
- 既存出力を保全。agent起動失敗の模擬試験では、先に起動した所有server controllerを回収し、環境エラーを保持する。

## 開発中の失敗と先行証拠

`acceptance-1`では、agentのruntime準備をprobe用5秒で切っていた。
その呼出しをNode段階の残り絶対期限へ直し、通信probeの5秒を維持した。
agent失敗後にserverを不要に待たないよう、片側完了時にguest失敗を検出する。
最初の試験は所有workflowへTERMを送り、両VMの証拠同期・非escalation・QEMU回収を確認した。
raw証拠・旧bundle・cacheは保持している。

`acceptance-2`では新規2 VM×2クラスタの通信・限定deny・解除後復帰が成功し、4台の回収も成立した。
その後、固定probeの期待違反を環境エラーへ寄せていた分類を修正した。
成功条件は変更していないが、先行版の成功を最終ソースの受け入れとは扱わない。

## 最終版の新規クラスタ受け入れ

**F2の初期受け入れ：Mac ARM64／TCGの受け入れ時ソースで、新規2 VM×2クラスタが成功した。**
この実VM記録はレビュー修正前（`42ef42f`）の入力。修正後の検証範囲は末尾へ分けて記す。
最終入口は`/nix/store/w2w1wqn9x4h3s1n6gc8w2vg6k2cmxzmf-arena-k3s-f2/bin/arena-k3s-f2`。
SSD bundleは`Linux/veil-arena/k3s-f2-20261020/images/server-3`と`agent-3`。
raw証拠は同じrootの`acceptance-3/summary.json`と各`fresh-N/server|agent/`に保持した。
[機械可読記録](validation/f2-k3s-20261020.json)に起動入力hash・実Node容量・boot ID・各判定を残す。

| クラスタ | server QEMU生存時間 | agent QEMU生存時間 | 結果 |
| --- | ---: | ---: | --- |
| fresh-1 | 186.579秒 | 185.993秒 | passed / exit 0 |
| fresh-2 | 199.299秒 | 200.878秒 | passed / exit 0 |

上記は同時起動する各QEMUのcontroller実測であり、時間を合算してworkflow時間とは呼ばない。
一括全体600秒を延長せず終了0。4台のboot IDはすべて異なり、各役割の入力hashはクラスタ間で同一。
両クラスタともclient/controlをagent node、serverをserver nodeへ配置した。
Pod IP直接とService DNS経由の正常TCP/UDP、client TCPの限定deny、control TCP/client UDPの通過、
policy解除後のTCP/UDP復帰を確認。deny時は実際にECONNREFUSED（Linux errno 111）で、対象nonceの受信件数0。
allow時はecho一致・受信件数1。policy API specと実iptablesルールの成立/解除を別途保存した。

全4 VMで証拠同期・同期後停止・QEMU exit 0・reaped・非escalationが成立した。
終了後の所有QEMU/controller PIDが残っていないことも確認した。
当時の最終bundleとprepared runnerが受け入れ時runtimeソースに一致すること、各base diskが34359738368 bytesであることを確認した。
専用builderは既存Nix store/Cargo cacheを保持してStoppedへ戻した。100 GiBの内部disk空きは約6.1 GiB。
次の追加build前に容量判断が必要だが、今回の準備と受け入れは完了した。

## 保持する境界

実Veilの防御を認定する試験ではなく、K3s内蔵NetworkPolicy controllerの参照試験。
TCPの拒否は必ずしも無応答dropではなく、観測された接続拒否も含む。
Linux x86_64/KVMの実クラスタ、F3のKeeper、F4の本人操作は未実施。
受け入れ試験時点ではコミット・push・PRは未実施。取り込みはPRで判断する。
物理環境・第三者試用・署名タグ・公開は対象外のままとする。

[実行手順](F2_K3S.md)を参照する。

## PR #23レビュー修正の回帰

2026-10-20。Node 300秒・Pod段階120秒・probe全体5秒・共有workflow600秒・watchdog660秒、通信の期待値は変更していない。

| 指摘 | 修正と確認 |
| --- | --- |
| exec基盤障害を不一致1にする | fixtureが期待違反を検知した場合にだけ、schema・種別・action・protocol・host・nonce付きの構造化証拠を出す。非zeroだけ、壊れた証拠、別nonce、API不通・Pod消失・exec断は環境エラー3にする |
| logs APIがprobe期限を越える | exec前に1つの絶対probe deadlineを決め、受信ログ取得も共有。exec後に5秒を再設定しない。遅れて返った応答も成功にしない |
| run summaryがguest失敗を失う | monitorと完了後verificationの両経路で、伝播前にrole・status・exit・stage・detailをcluster/nodeへ保存。回収失敗は環境エラー3を優先し、元のguest failureも別欄へ保持 |

- Mac全チェック成功：fmt/clippy、Rust回帰、Python回帰111件、Nix全system評価。
- MacのF2対象回帰29件成功。Linux ARM64はF1＋F2＋レビュー回帰42件成功。
- 実子プロセスで、exec相当の部分が完了した後にログ取得相当の子を停止させ、同じ絶対5秒で打切り・所有子回収を確認。次の正常子実行も成功。
- 決定的な時刻試験で、ログ取得が残り3秒を受け取ること、全体期限が先ならそちらを守ること、トラフィック再送やAPIエラーの再試行がないことを確認。
- server/agent双方、guest終了1/3/4/130、monitor/verification両経路について、上位・cluster・nodeの失敗情報と所有controller回収を確認。
- 保存済み`acceptance-3`の2クラスタは現行verifierで再判定して成功した。旧証拠・bundle・cacheは保持した。

今回、修正後guest／fixtureを新たなVMで起動する試験は行っていない。
構造化失敗証拠を追加したため、旧prepared bundleを修正後fixtureの実行証拠として扱わない。
実VM初期受け入れのhashは変更せず、レビュー後のsource hashと検証範囲をJSONの`review_followup`へ分ける。
Linux x86_64のGitHub CIは対象コミットごとにPRで確認する。Linux x86_64/KVMの実クラスタ受け入れとは区別する。
専用builderは既存cacheを再利用して停止へ戻し、容量変更やcache削除は行っていない。

## PR #23追加レビュー3件の回帰

2026-10-20。基点は`49a2f31`。修正前に追加回帰で公開待ちの誤失敗・deny受信証拠の見逃し、
LAN起動待ち中の中断コード、host abortのrun summary不整合を再現した。

- TCP/UDPともfixtureが受信記録をflushしてからechoする。CRI側の公開遅延も考慮し、正常なログsnapshotを既存probe deadline内でpollする。
- 通信は1回だけ送信する。allowは受信nonce 1件の公開を待ち、重複や欠損を成功にしない。
  denyは同じ期限まで観測し、遅れて受信nonceが現れたら失敗。API障害・期限後応答も成功にしない。
- LAN socket待ち・socket準備と中断の競合・両VM実行中で、SIGINT/SIGTERM/SIGHUPをcancelled/130として保存する。
  全体600秒到達はtimeout/4。ローカルLAN起動上限5秒との取り違えを防ぐ。
- host abortをrunのstatus/exit_code/stage/detailにも保存する。回収失敗は環境エラー3を優先し、元のhost_abortを別欄に保持する。
- Node 300秒・Pod 120秒・probe 5秒・共有workflow600秒・watchdog660秒と通信の期待値は変更していない。

Mac全チェックは成功（Python回帰120件）。F2対象38件、Linux ARM64のF1/F2/レビュー回帰51件が成功した。
所有controllerの回収、旧受け入れ2クラスタの現行verifierによる再判定も確認した。
実子プロセスの期限・回収・次の正常起動は前回の回帰と合わせて再実行した。
キャッシュと旧証拠を保持し、専用builderは停止へ戻した。容量変更・削除は行っていない。

今回も新しいguest/fixtureの実VM再受け入れは行っていない。4 VMの受け入れは従来入力の履歴として保持し、
今回のsource hashと回帰範囲はJSONの`review_followup_round2`へ分けて記録する。

## PR #23通常ホストエラーの回帰

2026-10-20。基点`d145773`。追加された未解決レビュー1件について、修正前にrunのexit_code・原因欠落を再現した。

- qemu-img失敗、agent Popen失敗、LAN socketのローカル5秒期限切れ、controller異常終了、cluster検証・最終入力検証の失敗を環境エラー3としてrunにも保存する。
- 起動・LAN・実行・検証のstage/detailを保持し、回収が成功した場合は上位summaryとの一致を確認した。
- 回収が不成立ならstage=cleanup・環境エラー3を優先し、元の通常ホストエラーはhost_failureに保持する。
- 所有controllerだけを回収し、次の正常なmock実行が成功することを確認した。実VMの新規起動試験ではない。
- 既存の中断130・全体期限4・guest失敗の回帰を維持。期限・通信の期待値、依存関係は変更していない。

Mac全チェック成功（Python123件、F2集中回帰41件）。既存キャッシュを再利用したLinux ARM64のF1/F2/レビュー回帰54件も成功した。
専用builderとcontainerは停止へ戻し、旧bundle・受け入れ証拠・キャッシュを保持した。
GitHub Linux x86_64 CIはpush後の対象コミットで確認する。新規VM再受け入れ・Linux x86_64/KVM実クラスタ受け入れは今回実施していない。

## マージ後mainの新規VM再受け入れ（2026-10-21）

基点`cb021ad`から、レビュー修正後のguest/fixtureを含む両bundleとrunnerを準備した。
専用builderは明示承認された256 GiBへ拡張し、キャッシュ・旧bundle・旧証拠を保持した。
試験前にbuilder/containerを停止し、既存資源・期限・期待値のまま新規VMを起動した。

結果はfresh-1のdeny段階で環境エラー3。Node Ready・別ノードPod配置と正常TCP/UDP（Pod IP/Service DNS）は成功。
policy APIと実ルール公開の証拠は存在するが、その後の通信probeで`command exceeded absolute deadline`になった。
fresh-2は未起動。server/agentとも証拠同期・同期後停止・QEMU exit 0・reaped・非escalationを確認し、所有PID残存なし。
agentのcontroller終了3は、server失敗後の所有停止によるcancelledであり、正常なクラスタ成功とは扱わない。

現行guestはタイムアウトしたargvを保存しないため、execとlogsのどちらが期限を超えたかは未確定。
限定したダミー時刻確認では、健康なlogs応答が0.33秒かかる場合、残り0.32秒で次のAPIを起動して
環境エラー3になる経路を再現した。これは実VMの失敗原因の断定ではない。
期限を延ばす、失敗を成功へ変える、根拠なくVMを再試行する変更は行っていない。

合意したF2成功ゲートを満たさないため、Keeperの入力照合・最小接続は未着手で停止した。
次はtimeout argv/残りbudgetの証拠を残し、同じ5秒内でのreceiver観測方式を検証する。
記録：[f2-main-20261021.json](validation/f2-main-20261021.json)。raw証拠はSSDの
`Linux/veil-arena/f2-main-20261021/acceptance/`、ダミー再現はローカル`artifacts/f3-preparation-20261021/`に保持する。

## 受信観測修正後の新規VM再受け入れ（2026-10-21）

`cb021ad`を基点とする未commitの修正。F2成功後のKeeper接続を前提に、まず受信観測と期限超過診断だけを変更した。
失敗時のargv・budget・経過時間・残り時間・workflow残り時間を`last-command-timeout.json`へ保存する。
起動前期限切れと遅延応答も検出し、短い期限の環境エラー3／全体期限超過4を維持した。
stdinとプロセス出力はこの診断へ保存しない。実子のtimeout・回収・次の正常実行も回帰確認した。

受信観測は、Readyなserver Podのcontainer IDをCRI inspectし、稼働state・Pod UID・固定logPathとの一致を要求する。
同じ受信ログをVM内で直接読み、probe末尾に`kubectl logs`プロセスを再起動する経路をなくした。
元の絶対5秒・1回の送信・allowの受信nonce 1件／denyの0件と、既存の末尾待機条件は変更していない。
欠損・不正形式・不完全行・過大ログ・inode切替・履歴書換えを成功へ読み替えない。
旧失敗のタイムアウトargvは過去の証拠から復元できず、当時のexec/logsのどちらが超過したかは未確定のまま。

Mac全チェック成功（Python131件、F2対象49件）。Linux ARM64のF1/F2/レビュー回帰62件成功。
キャッシュを保持した256 GiBの専用builderで新bundleを準備し、SSDの新規directoryへ転送した。
試験前にbuilder/containerを停止し、server 4 CPU/16 GiB/32 GiB、agent 2 CPU/4 GiB/32 GiBの資源を維持した。
Node 300秒・Pod 120秒・probe 5秒・共有workflow600秒・watchdog660秒、期待値は変更していない。

**修正後の新規2 VM×2クラスタが成功し、一括終了0。** 両クラスタでReady・別ノードPod配置・
Pod IP/Service DNS経由のTCP/UDP・client TCP限定deny・control TCP/client UDP通過・解除後復帰を確認。
全4 boot IDは異なり、同じ役割の固定入力は一致。保存したCRIログを別途再集計し、24 probeの受信件数と一致した。
全4 VMの証拠同期・同期後停止・QEMU exit 0・reaped・非escalationと、終了後の所有PID不在を確認。

| クラスタ | server QEMU生存時間 | agent QEMU生存時間 |
| --- | ---: | ---: |
| fresh-1 | 167.192秒 | 167.047秒 |
| fresh-2 | 171.534秒 | 170.846秒 |

各QEMUのcontroller実測であり、並行する時間を合算してworkflow時間とは扱わない。
機械可読記録：[f2-cri-20261021.json](validation/f2-cri-20261021.json)。
raw証拠・新bundle・source pinはSSDの`Linux/veil-arena/f2-cri-20261021/`へ保持した。
旧失敗・旧bundle・cacheは削除していない。新規VMの根拠のない自動retryは追加していない。

Keeperの選定2入力はF0のSHA-256と一致したが、profile適用・Keeper実行は今回未実施。
Linux x86_64/KVM実クラスタ受け入れはF4に残る。この受け入れ実施時点ではPR/pushは未実施。物理環境・第三者試用・署名タグ・公開は行っていない。
