# F0検証契約：K3s環境と最初のKeeper接続

2026-10-20 JST。基点main `5f0c0a3`。[完成計画](COMPLETION_PLAN.md) のF0。
状態：契約の文書化・入力照合・パッケージ定義評価済み。F1の実装・build・VM起動は未着手。
機械可読な正本は [f0-inputs.json](contracts/f0-inputs.json)。文書と矛盾した場合は実行せず整合させる。

2026-10-21の提供範囲改訂: 標準完成は [1 VM](F4_STANDARD_ONE_VM.md)、2 VMは追加検証。以下の固定入力・資源・権限・期限の契約は変更しない。

## 1. 最初の実Veil試験

Keeperの `tools/m0_exec_check.py` と `experiments/apparmor/m0-exec-check.profile` を選定する。
外部サービスやSQLiteを必要としない、既存のAppArmor exec smoke checkである。
profile名は `veil_keeper_m0_exec_check_20261016`。パスによる自動attachmentはなく、`aa-exec`で明示選択する。
profileは限定したexec拒否を分離して試すための広い許可を持ち、本番用保護profileではない。

| 段階 | 既存checkerの呼び方（guest内） | 必須結果 |
| --- | --- | --- |
| baseline-before | `/usr/bin/python3 /inputs/m0_exec_check.py --expect baseline` | 実label unconfined、true終了0、false終了1、checker終了0 |
| confined | `/usr/bin/aa-exec -p veil_keeper_m0_exec_check_20261016 -- /usr/bin/python3 /inputs/m0_exec_check.py --expect deny-true` | 実labelが対象profileのenforce、trueのexecがEACCESまたはEPERM、false終了1、checker終了0 |
| baseline-after | profile解除後にbaselineを再実行 | 対象profileなし、baseline-beforeと同じ結果 |
| 拒否不成立の対照 | profile未選択で `--expect deny-true` を実行 | checker終了1。環境失敗やtimeoutを代用せず、label／exec結果の不一致を検出 |

単なる非ゼロ終了を拒否成功としない。正しい期待値に一致したchecker終了0を外側で集計する。
Keeperの既存 `experiments/apparmor/m0-trial.py` は監査参考に留め、実行しない。
これはColimaの `/usr` パス、uid 501／gid 1000、SUDO_UID等に依存するためである。
Arenaのguest runnerが上の既存checkerを起動し、policyの追加・解除・証拠保存を所有する。
`tools/m0_preflight.py` はBPF LSM候補の診断を含むため、このAppArmor試験の必須gateには流用しない。

KeeperのHEADだけでは再現できない。今回選んだ2入力は所有者checkoutで未追跡だった。
元checkoutは変更せず、JSONのSHA-256と一致する通常ファイルだけを信頼するsnapshotへコピーする。
symlink、不一致、過大入力（各1 MiB超）、欠落は準備段階の環境エラーで停止する。
profile・checkerを書き換えてNixOSへ合わせず、互換配置をguest側で用意する。

## 2. 権限と互換配置

| 場所 | 権限・許可範囲 |
| --- | --- |
| Mac／Linuxホスト | 通常ユーザーによるNix準備・所有QEMU起動・専用出力への保存。sudo、ホストのpolicy／route／DNS変更は不要 |
| 専用guestの管理処理 | guest内root。securityfsの読取り、指定profileのsyntax確認・add・remove、試験プロセスの停止。対象profileの衝突時は適用しない |
| Keeper checker | guest専用のuid 501／gid 1000の一般ユーザー。unconfinedと明示profileの実labelを確認する |
| K3s管理 | 専用guest内root。クラスタ管理権限はguest内に限定。ホストのkubeconfigを渡さない |

NixOSには既存checkerが要求する `/usr/bin/true`・`false` があると仮定しない。
固定Coreutilsの実ELFをguestの書込み領域へ通常ファイルとして配置し、内容hashを照合する。
実行対象をNix storeへのsymlinkに置き換えるだけでは、profileのパス規則を同じ条件で試せないため不可。
Python／aa-execは固定packageへのguest内互換入口とし、管理処理は固定parserのstore pathを使う。
Keeper checkerのuidと、9P出力の所有者に合わせるhost UIDは別の設定として保存する。

AppArmor有効なkernel、稼働LSM、securityfs、parser、profileのsyntax、実ELFと実labelを事前確認する。
NixOSの `security.apparmor.enable` を新規guestで設定し、既存netns guestやホストのboot設定を変えない。
AppArmor未対応／読取り不能／不足権限は環境エラーとし、BPF LSMや別方式へ黙って切り替えない。

syntax確認は `apparmor_parser --skip-kernel-load --skip-cache /inputs/m0-exec-check.profile`（`-Q -K`）で行い、kernelへloadしない。
syntax確認前後のprofile名・mode一覧が一致し、対象profileが不在であることを要求する。
`--skip-cache` だけでは非loadにはならない。parserの既定動作はaddであるため、syntax成功を所有証拠にしない。
その後に `apparmor_parser --skip-cache --add /inputs/m0-exec-check.profile` を明示実行し、成功した時点だけを所有開始とする。
replace、profile全集合のreload、killallは使わない。
根拠は [AppArmor 5.0 parserの仕様](https://www.apparmor.net/man/5.0/apparmor_parser/)。
追加成功の所有証拠があるprofileだけを解除し、追加失敗時は他者profileを解除しない。
解除にも `apparmor_parser --skip-cache --remove /inputs/m0-exec-check.profile` を用い、完全なprofile定義を渡す。
3つの呼出しは同じread-only snapshotを使い、各呼出し前に固定hashを照合する。
標準入力はDEVNULL、各コマンドは20秒上限。入力省略によるstdin待ちを許さない。
JSONのargv先頭は論理名であり、準備時に固定Nix packageのparser絶対パスへ解決してmanifestへ保存する。

変更前後のprofile名・mode一覧、対象profileの不在、所有子プロセス回収を確認する。
一覧hash一致は他のpolicy本体すべての一致を証明するものではない。
F0ではこれらを実行しない。F3の試験時に専用VM・対象profile・回収範囲を明示する。

## 3. 固定版

| 対象 | 採用値・由来 |
| --- | --- |
| Nixpkgs | 既存flake.lockの `8edc0c72e3a38faf5434e40d1f19431125fe4b30` を維持 |
| Rust／Cargo実バイナリ／Python | 1.98.1／1.98.0／3.14.7 |
| guest kernel／QEMU | 6.12.112／11.1.1 |
| K3s | 固定Nixpkgsの `pkgs.k3s`：1.36.4+k3s1 |
| containerd／Flannel | 同じK3s recipeの2.3.4-k3s1.36／v0.28.4。別版を混在させない |
| AppArmor／Coreutils | 同じNixpkgsの5.0.2／9.11 |

両Linux CPU系統でK3s・AppArmor・airgap archiveのderivationをoffline評価した。
評価成功はpackage build・取得・kernel適用・クラスタ成功ではない。
Keeperの過去のUbuntu 24.04／kernel 6.8／parser 4.0.1での成功を、新構成へ一般化しない。

公式airgap archiveはamd64／arm64をJSONのURL・hashへ固定する。今回はdownloadしていない。
F1の準備で実ファイルのhashとCPUを照合し、kernel/initrd/store/base diskのhashも生成する。
DNS／TCP／UDP fixtureのOCI imageは、同じ固定PythonとArena所有の小さなclient/serverからF1で生成する。
生成したscript・OCI manifest/configのdigestを実物から記録し、`imagePullPolicy: Never`で使う。
未生成のdigestを捏造したり、tagだけ／latest／外部registryへのfallbackを許したりしない。
準備manifestにこれらが揃うまではVM試験を開始しない。再実行時は同じimmutable入力を再利用する。

## 4. 対応ホストと今回の読取り確認

| ホスト | guest・実行方式 | 契約と現状 |
| --- | --- | --- |
| Mac ARM64 | aarch64-linux／native QEMU TCG | F1の最初の実行先。実機はDarwin arm64・物理RAM 64 GiB。Linux ARM64ビルダーでguestを準備しbundleを利用。K3s実起動は未確認 |
| Linux x86_64 | x86_64-linux／KVM | F4必須の受け入れ先。pi-nodeはLinux x86_64、KVM device・Dockerあり。SSHユーザーは直接KVM読書き不可。専用runtimeのdevice／補助groupアクセスを起動前に確認する |
| Linux ARM64 | aarch64-linux／TCG | package準備・先行確認の経路。今回の完成必須ホストには追加しない。必要時は対応表へ別途追加 |

pi-nodeのdeviceはmode 660・group 993を観測したが、番号を実装に固定しない。
従来の専用runtime経路で実行時のdevice groupを取得し、必要なdeviceだけを渡す。ホストの権限を変更しない。
アクセスできなければそのホストの実行を止める。TCGへの自動切替や、MacだけでF4完了とはしない。
Intel Mac、Mac HVF、Linux ARM64 KVM、物理Piは今回の対応宣言外。

## 5. F1の最小実装契約

新しいK3s用VM構成・manifest・checkerを追加する。既存netns 18ケース用launcher/checkerの名前を付け替えない。
Nix定義・準備入口・Rust controllerの既存の役割を使い、汎用plugin frameworkは作らない。

- 1 VM・1 server node、Flannel VXLAN。CoreDNSを使い、Traefik／ServiceLB／local-path等の不要addonは無効にする。
- 外向きNICなし。guest内部dummy linkに固定node IP、外へ転送しない既定経路を設定する。ホストへのNAT／bridgeなし。
- Pod CIDRは10.42.0.0/16、Service CIDRは10.43.0.0/16。node側は10.77.10.0/24を予約し、既存netnsの10.77.1/2と分ける。
- Nix store／base imageは不変。runごとに書込みdiskを新規作成し、K3s DB・containerd・kubeconfig・tokenを分離する。証拠へ秘密をコピーしない。
- 新規fixture Podはserverとclientの役割を固定し、Service名の解決、nonce付きTCP／UDP echoを照合する。外部名の解決を試さない。
- Node Ready／Pod ReadyをAPIの状態から期限付き確認し、固定sleepのみで送信を始めない。

| F1ケース | 完了条件 |
| --- | --- |
| 固定入力と起動前検査 | hash・CPU・空き資源・新規出力先を確認。不一致は起動前に拒否 |
| 1 node／固定Pod | 同一runのNode Ready・対象Pod Ready・実node配置・image IDが一致 |
| DNS・TCP・UDP | Service名が解決し、対象runの固定入力・nonce・echo・件数が送受信で一致 |
| 停止・新規VM | 証拠保存と所有QEMU回収後、別boot ID・同一入力の新規VMで同じ結果 |
| 収集不能・起動不成立 | passedにしない。reason・期限・取得できた証拠・回収状態を保存 |

初回の工程上限は2 vCPU・6 GiB RAM・run disk仮想容量24 GiBだった（JSONの`initial_f1_limits`に保持）。
2026-10-20のユーザー承認で、現行は4 vCPU・16 GiB RAM・run disk仮想容量32 GiBへ変更した
（JSONの`f1_limits`）。Node待機300秒・Pod待機120秒・
通信probe各5秒・workflow全体600秒・host watchdog660秒とする。F1の最初の試行を有限にする設計値であり、実測SLOではない。
起動前にhost余力と実disk空きを確認する。期限は共有の絶対deadlineで制限し、各待機で再設定しない。
期限内に成立しなければ証拠を残して終了し、自動延長・retryは追加しない。既存18ケースの期限は変更しない。

F2の承認資源はserver 4 CPU・16 GiB・32 GiB、agent 2 CPU・4 GiB・32 GiB、同時合計6 CPU・20 GiB・64 GiB（`f2_limits`）。
Node／Pod／probe／workflow／watchdogの期限はF1から変更しない。追加の新規2 VMを含むacceptanceも、2クラスタでworkflow全体600秒を共有する。

結果は成功0、不一致1、引数不正2、環境・保存・回収エラー3、workflow期限超過4、中断130を基本にする。
Node／Podが準備できない場合は環境エラー3、host watchdogでのVM停止も既存同様3として別理由を残す。
nonce不一致等の検証失敗を環境成功へ読み替えない。独立したguest判定とVM lifecycleを両方要求する。
run出力は新規作成のみ。終了後の証拠・cache・diskを自動削除しない。停止対象は所有QEMU／子だけ。

## 6. F0で実施した確認とF1開始条件

実施：Keeper入力のコード読取りとhash照合、既存preflight判断ロジック10件（固定Python 3.14.7）、
両CPUのpackage定義評価、Mac／pi-nodeのOS・deviceアクセスの読取り確認。
単体試験はkernel policyやK3sを動かした証拠ではない。SSHでsudo・profile操作・VM起動はしていない。

F0の決定事項は揃っており、次はF1の実装に進める。F1の試験開始には、以下を準備工程で実際に満たすことが必要。

1. 固定packageのbuildと全image取得・hash確認。互換ELF配置もmanifestへ記録。
2. 新guestでのcgroup v2、overlay／bridge／VXLAN等のkernel要件と、AppArmor機能の確認。
3. 新規run disk・出力先・秘密の扱い・所有資源の回収処理と、上限内のhost資源。
4. readiness・DNS／TCP／UDP・VM停止を判定するcontroller／checker。欠落を成功にしない単体回帰。

F1ではKeeper profileをloadしない。Keeper接続はF3であり、station-api／SQLite・Pi試験・独自BPF LSM・本番認定は対象外。
ローカル確認ログは `artifacts/f0-contract-20261020/`。JSONにないartifact digest・実測成功は未確認のまま保持する。
一次資料：[K3s air-gap](https://docs.k3s.io/installation/airgap)、[必要条件](https://docs.k3s.io/installation/requirements)、
[Flannel](https://docs.k3s.io/networking/basic-network-options)。NixOSのAppArmorとpackage recipeは固定NixpkgsのファイルhashをJSONへ記録した。
