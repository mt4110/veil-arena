# 固定ツールチェーンとLinux VM

2026-10-11。MVPの必須要件に、Nixによる依存固定と専用Linux VMによるkernel境界を含める。macOS ARM64のNix上のオフライン実行と、Linux x86_64／KVMゲストでの成功・不具合検出・新規VMでの再成功を確認済み。[app/router/serverのnetns正常通信](NETNS_VM.md)と[TC ingressの固定UDP比較](TC_INGRESS.md)も追加した。初版の完成判定と未検証事項は[マイルストーン](MILESTONES.md#v010の受け入れ判定)と[受け入れ集計](validation/v0.1.0.json)を参照する。

## バージョン契約

| 対象 | 固定値 | 固定方法 |
|---|---|---|
| Rust | 1.98.1 | rust-toolchain.toml、Cargo.toml、flake.nix、runnerの照合 |
| Cargo実バイナリ | 1.98.0 | 固定NixpkgsのRustパッケージ、runnerの照合 |
| Python | 3.14.7 | .python-version、flake.nix、runnerの照合 |
| Linux kernel | 6.12.112 | 固定NixpkgsのlinuxPackages_6_12、VM定義のassertion |
| QEMU | 11.1.1 | 固定Nixpkgsのqemu |
| Aya | 0.13.1 | Linux用Cargo依存とCargo.lock |
| Clang | 21.1.8 | 固定Nixpkgsのclang-unwrappedとassertion。参照objectの準備のみ |
| Nixpkgs | 8edc0c72e3a38faf5434e40d1f19431125fe4b30 | 固定release URLとflake.lockのnarHash |

NixpkgsのRustパッケージ属性のversionは1.98.1だが、同梱Cargoの`--version`は1.98.0だった。runnerは実バイナリの値を照合する。Homebrew等のCargo 1.98.1をそのまま使うと環境エラーになる。

初回のNix取得・Cargo依存取得はネットワークを使う準備段階。試験の`--offline --locked`とは別に扱う。NixのlockはkernelやCPUの挙動がどの機械でも同じになる保証ではない。[Nixのflakeとlock](https://nix.dev/manual/nix/stable/command-ref/new-cli/nix3-flake)、[NixOS VMモジュール](https://github.com/NixOS/nixpkgs/blob/8edc0c72e3a38faf5434e40d1f19431125fe4b30/nixos/modules/virtualisation/qemu-vm.nix)

## 対応と確認状態

| ホスト | 開発shell・runner | Linuxゲスト | 現在の確認 |
|---|---|---|---|
| macOS ARM64 | 確認済み | aarch64-linux | オフライン18段階が成功。検証済みbundle＋固定Nix入口で新規2 VM各13ケースを確認 |
| Linux ARM64 | 確認済み | aarch64-linux | 専用Colima VM内のLinux ARM64でbuild・TCG boot・新規2 VM各13ケースを確認。KVMとbare metalは未確認 |
| Linux x86_64 | 確認済み | x86_64-linux | Docker内のNixでshell/package build、KVMのVM実起動と18段階の成功・不具合検出・再成功を確認 |
| Intel macOS | 対象外 | — | このNixpkgsがx86_64-darwinを対象外にしているため対応を宣言しない |

macOSでNixOSのゲストclosureを作るには、対応するLinuxビルダーが必要。`pi-node`はLinux x86_64である。ARM64の準備には、共有Colimaと別の専用プロファイルを用意し、HOMEの共有・SSH agent転送を無効にした。Linux依存をMacのNix storeへ取り込まず、検証済みゲストのboot入力をbundleとして再利用できる。

2026-10-16の追加確認では、[Mac ARM64と外部APFS SSD](MAC_SSD_VM.md)でNetEmを含む16ケースを新規2 VMで確認した。上表の初版13ケースと区別する。Linux x86_64／KVMの追加16ケースの最終受け入れは期限超過で未完了のまま残す。

### Linuxで準備したゲストをMacで再利用する

信頼するソースからLinux ARM64で準備し、一括試験を通過したVMの結果を指定する。ゲストのUIDはMacの実行ユーザーと一致する必要がある。今回の専用LinuxビルダーとMacはUID 501で揃えた。

```sh
# Linux側。PASSED_VMはacceptance/dailyなどの、13ケースを完了したVM結果。
nix develop --command python3 scripts/guest_bundle.py export PASSED_VM NEW_BUNDLE
# NEW_BUNDLEをMacへコピーし、固定Nix環境でハッシュと契約を確認する。
nix develop --command python3 scripts/guest_bundle.py check NEW_BUNDLE
bash scripts/prepare-netns-from-guest.sh NEW_BUNDLE
# 返されたimmutable store pathを使う。起動中のビルド・依存取得はない。
/nix/store/<returned-path>-arena-netns-prepared/bin/arena-netns acceptance \
  "$PWD/artifacts/mac-acceptance-001"
```

store image・kernel・initrd・kernel params・元manifestのハッシュを照合し、ホストとゲストのCPU系統、UID、固定版の不一致を拒否する。bundleもホストlauncherもNix storeへ保持し、準備出力ごとにGC rootを作る。既存rootを付け替えたり削除したりしない。通常のNix署名検証は変更しない。bundleのハッシュは同一入力の確認であり、出所やコードの安全性を証明するものではない。任意の未信頼bundleを受け入れる用途は対象外。

## ホスト上のオフライン検証

Nixと`nix-command`・`flakes`を利用可能にする。今回確認したNixは2.33.1。リポジトリのルートで実行する。

```sh
# 初回のみ。Cargo.lockに記録された依存を取得する準備段階。
nix develop --command cargo fetch --locked
nix develop --command cargo fetch --locked --manifest-path ../veil-blackhole/Cargo.toml

# 試験中は依存取得とlock更新をしない。
nix develop --command cargo run --offline --locked -- blackhole-offline \
  --project ../veil-blackhole

nix develop --command cargo test --offline --locked
nix develop --command cargo clippy --offline --locked --all-targets -- -D warnings
nix develop --command cargo test --offline --locked --manifest-path tools/vm-controller/Cargo.toml
nix develop --command cargo clippy --offline --locked --manifest-path tools/vm-controller/Cargo.toml --all-targets -- -D warnings
nix develop --command python3 -B -m unittest discover -s tests -p 'test_*.py'
nix flake check --all-systems --no-build --no-update-lock-file
```

最後のコマンドは他OS向け定義の評価であり、他OS上での実行試験ではない。Nix package自体のビルドは`nix build .#default --no-link`で行う。初回は依存取得を伴う。ローカルの未追跡ファイルをNix flakeへ渡すにはGitが認識している必要がある。

runnerはRust・Cargo・Pythonのバージョンを試験前に照合する。版が違えば後続試験を実行せず環境エラーになる。必要な版と照合出力はmanifestとログに保存する。

ビルドキャッシュは実行時の作業ディレクトリの`target/blackhole-offline/<checkout-path-sha256>/`。Nixでビルドした実行ファイルが、ビルド時の読み取り専用ソースへ書き込まないようにした。書込み可能なarena作業ディレクトリから起動する。

## Linux VMの準備と起動

対応CPUのLinuxビルダーと十分な空き容量が必要。ゲスト内のRustコンパイルを含むため、初回ビルドには時間とダウンロードがかかる。Linux x86_64／KVMで以下のbuild・起動の流れを確認した。ビルダーの設定はこのスクリプトでは行わない。

```sh
# 起動せず、固定したVM用closureとホスト用launcherをビルドする。
ARENA_VM_ACCELERATOR=tcg bash scripts/build-offline-vm.sh ../veil-blackhole

# Linuxで、現在のユーザーが/dev/kvmを既に利用できる場合。
# ホストの権限設定はこのスクリプトで変更しない。
ARENA_VM_ACCELERATOR=kvm bash scripts/build-offline-vm.sh ../veil-blackhole

# 前のコマンドが返したstore pathをそのまま指定する。
/nix/store/<returned-path>-arena-vm-offline/bin/arena-vm-offline \
  "$PWD/artifacts/vm-offline-001"
```

出力先は新しいディレクトリが必須。既存の場所は拒否する。起動にはrootを使わない。結果の9P shareを0700で扱うため、ビルド時のホストUIDと実行時のUIDを一致させる。CPUはホストと同じ系統のゲストを選び、2 vCPU、メモリ4096 MiBで起動する。既定はTCGで、LinuxではKVMを明示選択できる。KVMはhost CPUを使い、TCGのmax CPUとは別の実行条件としてmanifestに記録する。KVMの失敗時にTCGへ黙って切り替えない。未知のacceleratorとmacOSでのKVM指定は定義評価で拒否する。

VMへ入れるブラックホールのソースは、src/tests/scripts/examplesとCargo manifest/lock・build.rs・toolchainのsnapshot。`.git`、artifact、target、HOME、Docker socketを渡さない。許可したソースディレクトリ内の任意ファイルの秘密情報を自動判定する仕組みはなく、信頼する入力を確認して使う。ゲスト内にsnapshot用のローカルGit commitを作る。ゲストのHEADは元checkoutのHEADではない。Nix source store pathとrunnerのファイルhashで実際に試験したsnapshotを識別する。

Cargo依存は対象のCargo.lockからNixで準備してclosureへ収める。ゲスト内では依存取得をせず、同じRust runnerで19段階を実行する。ゲストの試験判定とホストのQEMU生存期間の制御はRustで、呼出しは固定shell wrapper、回収した結果の形式チェックは標準ライブラリだけのPython補助を使う。pytestは導入しない。ホスト専用のRustコントローラーは`tools/vm-controller`に分離し、変更のたびにゲストのツールイメージを作り直す必要がない構成にした。

## VMの境界と結果

netnsの通常運用は[一括試験](NETNS_WORKFLOW.md)に移した。準備で生成したNix store出力をGC rootで保持し、試験コマンドは固定launcherだけを使う。netns用ゲストには実行に必要なiproute2等とビルド済みRust runnerを入れ、Rust/Python開発ツールは入れない。Rust・Cargoのビルド版とホストcheckerのPython版の固定は継続する。ブラックホールのオフラインVMには引き続き開発ツールを収め、ゲスト起動ごとに`/work`でコンパイルする。このコンパイル済みtargetをVM間で共有する変更は今回含めない。

- 外部NICなし、SSHなし。試験サービスにもPrivateNetworkを設定する。
- ゲストのrootはtmpfs、Nix storeはclosureだけを収めた読み取り専用EROFSイメージ。ホストのNix store全体はmountしない。
- ホストとの共有は新規の結果ディレクトリのみ。ホストworkspaceはmountしない。
- `/out`はホストOSにかかわらず9Pで定義する。固定NixOSモジュールのLinux既定値virtiofsを明示的に上書きし、launcherの9P接続と一致させる。定義にはfsTypeのassertionを入れる。
- オフライン試験期限300秒、netns単一ケース期限90秒、一括ケース実行期限540秒、サービス期限600秒。QEMU起動後のホストコントローラーは660秒、外側のwatchdogは670秒と停止猶予5秒。各起動前にイメージchecksumを計算する。一括workflowにはlauncherごとに900秒の上限もある。serialログにもファイルサイズ上限を設定する。
- ホストのRustコントローラーが試験サービスの終了markerを確認し、出力をfsyncしてから所有するQEMUへSIGTERMを送る。停止後にserialと停止理由も同期する。SIGKILLへの強制移行、期限超過、同期失敗、サービス未完了は環境エラー。ホストのファイルを削除する後処理はない。
- QEMUの終了コード0だけでは合格にしない。ホスト制御の完了とservice終了を照合する。オフラインは19段階とsource/binary不変、netnsは51段階の通信・経路・後片付けをそれぞれ確認する。

保存対象は`vm-manifest.json`、`boot-inputs.sha256`（store image・kernel・initrd）、`serial.log`、`vm-stop.json`、`qemu-exit-code.txt`、`guest-kernel.txt`、`guest-cmdline.txt`、`source-store-path.txt`、`service-result.txt`、`results/<run-id>/`。起動失敗・panic・期限超過でguest resultを回収できなければ環境エラー。出力は保全する。

VM launcherの終了コードは0=全試験成功、1=試験不一致、2=引数不正、3=起動・回収・環境エラー。VM内の既存runnerは詳細な終了コードをresultとログへ残す。

## Linux実起動で確認した状態と制約

`pi-node`のホストkernelは7.0.0-38-generic、ゲストは6.12.112。既存ユーザーにKVMの利用権限があり、権限設定を変更せず利用した。ホストにNixをインストールせず、公式Nix 2.33.1のamd64イメージをdigestで固定した専用Dockerビルダーで準備した。ビルダーは非特権、入力はread-only、HOMEやSSH鍵・Docker socketはmountしていない。公式イメージ既定のNix `sandbox=false`を使っているため、準備段階も信頼するソースだけを対象にする。[公式Nix Dockerイメージ](https://hub.docker.com/r/nixos/nix/)

起動側は外部networkなし、通常ユーザー、capabilityなし、read-only root、`/dev/kvm`のみを渡す専用コンテナで動かした。実行用にコピーした1,032 store pathは元のNAR hashと全件照合した。追加したコントローラー等5 pathも照合し、既存コピーを上書きしなかった。転送した証拠320ファイルとarchiveのSHA-256もローカルで確認した。[検証記録](BLACKHOLE_OFFLINE_VALIDATION.md)

初回のVM起動では、Linux既定のvirtiofsとlauncherの9Pが食い違い、mountに失敗した。9Pへ統一して修正した。その後TCGでは初回コンパイルが300秒を超えた。KVMでは同じ試験期限と期待値で18段階を通過した。

ゲストの通常シャットダウンは、試験成功後にstoreのunmount周辺で停止が進まず、ホストの期限に達した。原因の特定と通常シャットダウンの修復は未完了。現在は読み取り専用イメージと使い捨てrootを前提に、試験サービス終了、ホストでの結果同期、ホストによるVM停止を終了契約とする。`vm-stop.json`に`service_completed`、同期の成否、停止コードと強制移行の有無を記録する。通常シャットダウン成功やguest filesystemのclean unmountを確認したとは扱わない。

Linux側の初回コピーには時間と保存容量を要した。既存Nix環境での普段の導入負担、Macからの起動、利用者による再現は別途検証する。今回の専用ビルダーとVMコンテナは停止済みで、キャッシュ・証拠・検証領域は削除せず保持している。

`mkOfflineVm`と`build-offline-vm.sh`は人工入力のオフライン試験用として維持する。`mkNetnsVm`と`build-netns-vm.sh`は、ブラックホールのソースを渡さずに正常通信を試験する別の入口。共通のreadonly guest store・外部NICなし・結果共有・ホスト停止契約を再利用し、netnsの設定操作はゲスト内だけで行う。オフラインサービスは通常ユーザー、netnsサービスはゲストrootで動く。

netnsの実行方法・寿命は[netnsガイド](NETNS_VM.md)、Aya attach・限定drop・解除後の復帰は[TC ingress](TC_INGRESS.md)を参照する。固定IPv4/UDPの参照試験であり、実BlackholeのLinux遮断能力を確認した結果とは扱わない。

停止試験の明示入口では、guest batchのSIGSTOP確認後2秒・checkpoint未到達時120秒の試験用期限を使う。通常の660秒・外側670秒のwatchdogは維持する。[ホストwatchdogの試験契約](HOST_WATCHDOG.md)を参照する。
