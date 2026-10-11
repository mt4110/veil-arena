# ブラックホール接続の検証記録

2026年10月10日 JST。対象はローカルのveil-blackholeから作ったsnapshot。Linux VM検証、macOSでのNix導入、初期接続を分けた記録。Live取得、DNS送信、sudo、netns操作は実行していない。

この記録はオフライン接続時点の範囲と試験数を保持する。後続で追加したapp/router/serverの実通信は、[netnsの検証記録](NETNS_VALIDATION.md)で扱う。

## Linux VMでの成功・不具合検出・再実行

SSH接続先`pi-node`で確認した。ホストはLinux 7.0.0-38-generic／x86_64、ゲストは固定kernel 6.12.112。QEMU KVM、2 vCPU、4096 MiB、外部NICなし。ゲスト内でRust 1.98.1、Cargo 1.98.0、Python 3.14.7を照合した。[全体の比較結果](../artifacts/pi-node-1791624441124999000/verification-summary.json)

| シナリオ | 実際の結果 | 証拠 |
|---|---|---|
| 元コードの新規VM | 18段階成功、launcher終了0 | [結果](../artifacts/pi-node-1791624441124999000/remote-evidence/healthy-005/results/blackhole-offline-1791629145271909081-621-0/result.json) |
| 不具合snapshotの新規VM | RustのCLI試験2件で不一致を検出、launcher終了1。後続は未実行 | [結果](../artifacts/pi-node-1791624441124999000/remote-evidence/mutant-002/results/blackhole-offline-1791629248205931688-626-0/result.json) |
| 元コードをもう一度新規VMで実行 | 18段階成功、launcher終了0 | [結果](../artifacts/pi-node-1791624441124999000/remote-evidence/healthy-006/results/blackhole-offline-1791629351295255593-640-0/result.json) |

正常版の各runでは既存Rust試験46件とPython試験4件が通過した。macOSでのRust試験52件とは、OS条件による対象の違いがある。これらの件数は確認範囲の記録であり、実通信や全入力への保証ではない。

不具合は別コピーの`src/main.rs`にある`_ => counts.malformed += 1,`を`+= 0,`へ変更して作った。`invalid_checksum_exits_two_without_counting_query`と`invalid_input_and_options_fail_explicitly`が、期待する終了2に対して実際の終了0を検出した。元ソースは変更していない。初期送信47ファイルと修正・追加後50ファイルのhashを照合し、変更が意図した1か所だけであることも確認した。[ソース照合](../artifacts/pi-node-1791624441124999000/remote-evidence/revision5-source-verification.json)

全3runでソースとバイナリの開始・終了hashが一致した。正常版の2runはソースhash、バイナリhash、boot inputのhashも一致した。各ゲストはtmpfsの新規rootで、前runのCargo targetを共有していない。

終了処理はホストのRustコントローラーが管理する。サービス終了markerを確認し、結果をfsyncしてから所有QEMUへSIGTERMを送る。全3runで`vm-stop.json`の終了理由は`service_completed`、停止前の同期はtrue、QEMU終了0、SIGKILLへの強制移行なしだった。[初回の停止記録](../artifacts/pi-node-1791624441124999000/remote-evidence/healthy-005/vm-stop.json)、[不具合版の停止記録](../artifacts/pi-node-1791624441124999000/remote-evidence/mutant-002/vm-stop.json)、[再実行の停止記録](../artifacts/pi-node-1791624441124999000/remote-evidence/healthy-006/vm-stop.json)

| 基盤の確認 | 結果 |
|---|---|
| Linux x86_64のarena Nix package | buildとRust試験9件が成功 |
| ホスト停止コントローラー | macOS ARM64とLinux x86_64のNix package build・Rust試験4件が成功。サービス完了後の同期と停止、終了0でもサービス未完了なら拒否、watchdog、証拠symlinkの拒否を確認 |
| Pythonによる結果形式判定 | 両OSの固定Nix環境で4試験が通過。環境エラー・timeout・中断を回帰不一致にしない |
| Nix定義 | 3対象systemのflake/checkとVMのderivation評価が成功。ARM64 VMの実起動成功を意味しない |
| 実行用コピー | 元のNix store記録と1,032 pathのNAR hashが一致。ホストコントローラー等の追加5 pathも一致。既存コピーは上書きしていない |
| 証拠の回収 | archive SHA-256と320ファイルのhashがローカルで一致 |

最初のNix copyによる実行領域準備は、公式コンテナの一部store pathがtrusted keyの署名を持たず失敗した。署名チェックを無効化せず、同じホストの既知のビルド出力を直接コピーし、元のstore記録のNAR hashと照合した。コピー後のハッシュ計算は初回に1 pathで60秒を超えた。個別再確認で元とコピーのhashが一致し、準備処理の計算期限を調整して全件照合した。試験の300秒期限と期待値は維持した。

## 検出したVM環境エラーと残る制約

以下も証拠として保全し、合格runへ置き換えていない。

| 状態 | 判定と対応 |
|---|---|
| 初回起動で共有領域のmount失敗 | 試験開始前の環境エラー、終了3。固定NixOSモジュールのLinux既定virtiofsとlauncherの9Pが食い違っていた。9Pへ統一しfsType assertionを追加。診断後、このVMは手動で停止した |
| 修正後のTCGでcold compileが300秒超過 | guest resultはtimeout、QEMUもホスト期限で停止。launcher終了3。KVMを明示選択する実装を追加し、既存ユーザーの権限で同じ試験を実行した |
| KVMで試験成功後に通常shutdownが停止 | guestの18段階は成功でもVM停止が終わらず、ホスト期限でlauncher終了3。通常shutdownの根本原因は未特定。現在の終了契約は、サービス完了・ホストの同期・所有VMの停止とし、上の3runで確認した |

停止コントローラーはホスト用crateに分離したため、この修正でゲストのkernel・tool closureを再ビルドしなかった。既存guest imageを保ったまま、追加したホスト用5 pathだけをコピー・照合した。

ホストへのNixインストール、ホストの権限やネットワーク設定変更、既存サービスの変更は行っていない。準備用ビルダーと試験VMコンテナは全て停止済み。ファイル・コンテナ・キャッシュは削除せず保持している。[Nix・VMガイド](NIX_VM.md)に環境と手順を記載した。

macOSからのVM起動、Linux ARM64、実通信、Linux eBPFのload/attach/drop、VM panic、host停止からの回復、未信頼コードの封じ込めは未検証。Linux VMでのオフライン解析成功を、それらの確認結果とは扱わない。netnsとAyaを含むMVP全体は未完了。

## Nix導入時のmacOS確認（Linux VM検証前）

macOS 27.0.1、arm64、Nix 2.33.1、固定Nixpkgsで確認。実バイナリの版はRust 1.98.1、Cargo 1.98.0、Python 3.14.7。ホストのHomebrew版に合わせた合格判定にはしていない。

| 確認 | 結果 |
|---|---|
| Nix devShellの実version | 固定版に一致 |
| Nix内のfmt、Rust試験、clippy | 通過。Rust試験9件（版不一致の拒否を追加） |
| 回収したVM結果の形式チェック | 当時はPython標準unittestの3試験が通過。合格、回帰、欠落、複数結果、不完全、service失敗、不正JSON・型を確認。後に環境失敗の区分を追加して4試験にした |
| `nix build .#default --no-link` | macOS ARM64のpackage buildと9試験が成功 |
| `nix build .#checks.aarch64-darwin.versions --no-link` | 版宣言の整合性assertionを通過 |
| `nix flake check --all-systems --no-build` | macOS ARM64・Linux ARM64／x86_64の定義評価が成功。Linuxでのbuild成功を意味しない |
| VM定義 | 3ホスト向けderivation評価、生成launcherのbash構文確認が成功。起動は未確認 |

| 接続試験 | 実際の結果 | 証拠 |
|---|---|---|
| 元コード | 18段階成功、終了0 | [結果](../artifacts/nix-validation-20261010/blackhole-offline-1791622699347651000-28944-0/result.json) |
| 初期接続で作った不具合コピー | Rust回帰で不一致を検出、終了1 | [結果](../artifacts/nix-validation-20261010/blackhole-offline-1791623109890654000-33586-0/result.json) |
| 元コードを新規runで再実行 | 18段階成功、終了0 | [結果](../artifacts/nix-validation-20261010/blackhole-offline-1791623141443538000-34328-0/result.json) |
| Nixでビルドしたpackageの実バイナリ | 18段階成功、終了0。Nixの読み取り専用build pathへ書き込まない | [結果](../artifacts/nix-package-validation-20261010/blackhole-offline-1791623299977363000-35740-0/result.json) |
| Pythonのversion表示だけを3.13.0にするshim | Rust/Cargo一致後、Python照合で環境エラー、終了3。build以降は未実行 | [結果](../artifacts/nix-version-mismatch-20261010/blackhole-offline-1791623331105361000-36005-0/result.json) |

Python版不一致の試験は、最初の実行で別runのcache lockと競合し、版照合に到達しなかった。上記はそのrun終了後に逐次実行し、Python段階での拒否を確認した結果。lock競合の証拠も保持している。

この時点ではMacにLinux用Nixビルダーがなく、Linux VMは評価のみだった。その後のLinux x86_64での実確認は上記の記録を参照する。Intel macOSは固定Nixpkgsの対象外。[Nix・VMガイド](NIX_VM.md)

## 初期接続の記録（版照合追加前）

通常ユーザー、macOS 27.0.1、arm64、Rust/Cargo 1.98.1で確認した。以下の17段階・8試験はこの時点の結果で、現在の固定Nix環境とは区別する。

## 自動チェック

| 確認 | 結果 |
| --- | --- |
| `cargo fmt --all -- --check` | 通過 |
| `cargo test --offline --locked` | runnerの8試験が通過 |
| `cargo clippy --offline --locked --all-targets -- -D warnings` | 通過 |
| `cargo build --offline --locked` | 通過 |

runnerの試験で、非0終了とsignal、起動失敗、事前中断、実行中の中断、期限超過、子孫がパイプを保持する場合、2 MiBの出力上限、拒否結果の誤判定、同じ名前候補での既存結果保全を確認した。時間だけによる一時ディレクトリ名が衝突したため、試験用の連番とrunディレクトリの排他的割当を追加した。既存ファイルの削除や上書きで回避していない。

## 実プロジェクトとの接続

初期接続版のrunnerで17段階がすべて成功した。既存のブラックホールのRust試験52件、Pythonのモック試験4件が通過し、人工IPv4/IPv6/DNS/BPF入力、チェックサム検証、不正UDP長・checksumの拒否を確認した。これらの件数は確認範囲の記録であり、品質や全入力への保証ではない。

| シナリオ | CLI結果 | 証拠 |
| --- | --- | --- |
| 不具合を入れた隔離コピー | `failed`、終了コード1。Rust回帰で2件の失敗を検出し、後続を停止 | [結果](../artifacts/verification/blackhole-offline-1791621550591677000-16467-0/result.json) |
| コピーの試験後に元コードを実行 | `passed`、終了コード0。17段階成功 | [結果](../artifacts/verification/blackhole-offline-1791621558103926000-17833-0/result.json) |
| 元コードをもう一度実行 | `passed`、終了コード0。17段階成功。別runとして保存 | [結果](../artifacts/verification/blackhole-offline-1791621559374565000-17953-0/result.json) |

全3runで、対象ソースの開始・終了hashと、build後・終了時のバイナリhashが一致した。[比較結果](../artifacts/verification-summary-20261010.json)にも対象cacheと結果への参照を保存した。ログとfixtureは各結果JSONと同じディレクトリにある。これらのartifactはローカル保存で、Git管理や公開の対象ではない。

不具合は、ローカルcloneの`src/main.rs`でmalformed件数の加算を1から0へ変更して作った。元のブラックホールは変更していない。元の作業ツリーには開始時から未追跡の`.DS_Store`があり、終了時もその状態を維持した。cloneと失敗時の証拠も削除していない。

隔離コピーによる試験中、チェックアウト間で共通のtarget directoryを使うと、別コピーのバイナリを再利用し、元コードまで失敗する問題を検出した。cacheをcanonical checkout pathのhashごとに分けて修正し、上記の「不具合コピー → 元コード → 元コード」を再確認した。異なるコピーのcacheは別、元コードの繰り返しでは同じcacheを利用している。

## CLIの異常系

| シナリオ | 確認した結果 | 証拠 |
| --- | --- | --- |
| rustcを30秒待つ試験用shimにし、期限1秒を指定 | `timeout`、終了コード4。後続は`not_run` | [結果](../artifacts/verification/blackhole-offline-1791621211707468000-12404-0/result.json) |
| PATHに必要なコマンドがない | `environment_error`、終了コード3。起動失敗の理由を保存 | [結果](../artifacts/verification/blackhole-offline-1791621212780177000-12413-0/result.json) |
| build段階でrunnerへSIGINT | `cancelled`、終了コード130。結果JSONを保存 | [結果](../artifacts/verification/blackhole-offline-1791621212879234000-12415-0/result.json) |

この3つのCLI試験はcache分離修正前に実施した。修正後の停止・出力管理は8つのrunner試験で再確認した。shimとPATH変更は試験プロセスの環境だけに適用し、通常環境には設定していない。

## 依存と未検証事項

直接依存の版とライセンスはローカルの各crate manifestで確認した。serde_json 1.0.150、sha2 0.10.9、libc 0.2.190はMIT OR Apache-2.0、ctrlc 3.5.2はMIT/Apache-2.0。JSON escaping、SHA-256、signal処理、Unix APIのために使用し、版と推移依存をCargo.lockで固定した。

RustSecの公開情報も照合した。sha2の既知の[RUSTSEC-2021-0100](https://rustsec.org/advisories/RUSTSEC-2021-0100.html)は0.9.7に関するもので、今回の0.10.9はその該当版ではない。ほかの直接依存のpackageページは取得できず、cargo-auditもこの環境で利用できなかったため、lock全体の最新advisory照合は未実施。検索結果だけで「脆弱性なし」とは判断していない。

初期接続の時点ではLinux、Windows、実通信、Linux eBPFのload/attach/drop、VM隔離、ホスト停止からの回復、未信頼コードの封じ込めは未検証だった。Linux VMとsnapshotの後続実装・確認は上記を参照する。ホスト上の通常runnerは検証中の編集を完全に防ぐ仕組みを持たず、不変hashの事後照合で判断する。
