# veil-arena

Veilプロジェクトの検証を実行し、結果と証拠を保存するRustのCLIです。最初に、veil-blackholeの既存オフライン回帰試験と人工パケット再生を接続しました。

```sh
bash scripts/blackhole-workflow.sh --project ../veil-blackhole
```

通常ユーザーで実行します。結果は実行ごとの`artifacts/`へ保存します。終了コードは成功0、検証不一致1、引数不正2、環境・保存エラー3、期限超過4、中断130です。

固定Nix環境とCargo依存は準備済みであることが前提です。上の入口は既存cacheを使い、正常試験を既定2回実行して結果を集計します。[反復試験の準備・実行方法](docs/BLACKHOLE_WORKFLOW.md)と、[新しいチェックアウトでの正常・失敗・復帰確認](docs/FRESH_CHECKOUT_CHECK.md)を参照してください。Rust runnerを直接使う手順は以下の実行方法に残しています。

[実行方法と検証範囲](docs/BLACKHOLE_OFFLINE.md)、[検証記録](docs/BLACKHOLE_OFFLINE_VALIDATION.md)、[Nix・Linux VMの構成と手順](docs/NIX_VM.md)を参照してください。Rust 1.98.1・Python 3.14.7を固定し、runnerが実バージョンを照合します。

初期MVP（M0〜M2）の受け入れ検証を完了しました。[マイルストーン](docs/MILESTONES.md)と[公開用の検証集計](docs/validation/v0.1.0.json)に、条件・対応範囲・未検証事項をまとめています。

v0.2.1のソース版に向け、netns一括試験の診断自動表示と、試験用ランチャーのText file busy対策をまとめています。
[リリースノート案](docs/releases/v0.2.1-draft.md)と[公開準備の記録](docs/V0_2_1_READINESS.md)を参照してください。署名タグ・リリース公開は承認後に行います。
以前の16ケースに関するLinux x86_64／KVMの期限超過は既知の制約として残しており、
以前の13ケースの成功を現在の一括試験の成功とは扱いません。

ホスト上のオフラインrunnerはOS sandboxではありません。Nixの開発環境と、外部NICを持たないLinux VMを用意しました。macOS ARM64のNix上のオフライン実行に加え、Linux x86_64／KVMのゲストで「正常版の成功 → 不具合版の検出 → 元コードで再成功」を確認済みです。ゲストの試験サービス終了後、ホストのRustコントローラーが結果をfsyncし、所有するQEMUを停止します。

専用VM内にapp・router・serverのnetnsを作る[正常通信ベースライン](docs/NETNS_VM.md)も追加しました。Linux x86_64／KVMで双方向ICMP/TCP/UDP、router転送停止・回復、経路故障の検出、所有プロセスの回収を確認済みです。[正常・故障・再成功の検証記録](docs/NETNS_VALIDATION.md)を残しています。

```sh
ARENA_VM_ACCELERATOR=kvm bash scripts/prepare-netns.sh
# 返されたstore pathを控える。以後の試験ではビルドしない。
/nix/store/<returned-path>-arena-netns-prepared/bin/arena-netns daily \
  "$PWD/artifacts/daily-001"
# 変更を受け入れる際は、追加の新規VMでも同じ一括試験を行う。
/nix/store/<returned-path>-arena-netns-prepared/bin/arena-netns acceptance \
  "$PWD/artifacts/acceptance-001"
```

[一括実行・キャッシュ再利用の運用](docs/NETNS_WORKFLOW.md)では、正常・戻り経路故障・正常・TC比較・drop無効化・誤ったhook・受信helper停止・BPF直接実行・ロード失敗・一括キャンセル・TC復帰・一括タイムアウト・TC復旧・NetEm全損失比較・設定不成立・正常復帰にTCP drop無効化・TCP SYN比較を加えた18ケースを1回のVM起動で実行し、`summary.json`へ集計します。通信ケースのnetnsは新規作成し、後片付けを確認します。Nix store、依存、読み取り専用VMイメージは再利用し、試験中はビルド・依存取得を行いません。既存の単一ケース用launcherも維持しています。

Rust/Ayaで[TC ingressの参照eBPF](docs/TC_INGRESS.md)も接続しました。固定IPv4/UDP fixtureで、eBPFなし・pass・count・限定drop・detach後を比較し、送信/受信とkernel counterを照合します。参照packetコードは固定Clangで生成するC objectです。[故障検出の記録](docs/TC_FAULT_VALIDATION.md)で、遮断不成立と環境故障を正しい理由で不合格にできるか確認します。

[固定IPv4 TCPの新規接続比較](docs/TCP_SYN_VALIDATION.md)では、対象ポートの初期SYNだけをdropし、対照接続の成功とdetach後の正常復帰を照合します。SYN再送のpacket数とアプリケーションの接続試行数は分けて記録します。現在の一括試験はschema 8・18ケースを要求するため、古い16ケースのguest bundleを再利用する場合はguestの更新が必要です。

[NetEmとeBPFの損失の区別](docs/NETEM_COMPARISON.md)では、routerの送信側にNetEmの全損失を設定し、TC ingressのpass counter・qdisc drop counter・受信を照合します。設定不成立はcount phaseの送信前に環境エラーとし、解除後の正常到達を確認します。[Mac ARM64と外部SSD](docs/MAC_SSD_VM.md)で新規2 VM・各16ケースの受け入れを確認しました。Linux x86_64／KVMで残る期限超過は別に扱います。[検証記録](docs/NETEM_COMPARISON_VALIDATION.md)を参照してください。

外部SSDの正式入口は`arena-netns-volume`です。マウント状態・固定入力との一致・既存出力を検査し、同じVM起動・集計処理を使います。[運用手順](docs/MAC_SSD_VM.md)と[正式入口の検証記録](docs/VOLUME_ENTRY_VALIDATION.md)を参照してください。

[異常・短縮入力の直接実行](docs/BPF_TEST_RUN.md)では、VM内のBPF_PROG_TEST_RUNで70入力の戻り値・kernel拒否・counterを照合します。wire試験とは別に結果を保存します。

[ロード失敗の検証](docs/BPF_LOAD_FAILURE.md)では、verifierが拒否する専用fixtureを環境エラーとして記録し、正常なTC試験への復帰を確認します。

初版の13ケースについては、Linux ARM64／TCGとmacOS ARM64／TCGで、それぞれ新規2 VMの一括試験を確認しました。Macでは、Linuxで準備した同じゲストbundleを固定Nixツールで起動します。[bundleの準備・反復実行](docs/NIX_VM.md#linuxで準備したゲストをmacで再利用する)を参照してください。IPv6のwire試験、固定fixture以外の異常入力、TCPの限定drop、k8s、kernel panic・host電源断は初版の対応範囲に含めません。

VM内の一括試験は、TC attach後の[キャンセル・タイムアウトと正常試験への復帰](docs/BATCH_INTERRUPTION.md)も検証する。指定checkpoint以外の中断やpanic・電源断の保証は含めない。

ホスト側の停止・復帰検証は[guest runner停止とwatchdog](docs/HOST_WATCHDOG.md)の入口を使います。`watchdog-daily`は停止試験と正常再実行の2 VM、`watchdog-acceptance`は追加の新規VMで同じ組を確認する4 VMです。通常の`daily`/`acceptance`は従来の1/2 VMを維持します。

開発用のチェックは依存の準備後に `nix develop --command bash scripts/check.sh` で実行します。CIはLinuxの静的・単体チェックとNix定義評価を行います。CI成功をVMの実起動確認に置き換えません。

`artifacts/` はGit対象外です。検証文書内のartifactリンクは検証者のローカル証拠を指し、公開リポジトリには含まれません。

Rust・スクリプト・文書は[MIT](LICENSE)、`reference/*.c` の参照eBPFは[GPL-2.0-only](reference/LICENSE)です。初版はソース配布とし、VMイメージや依存バイナリは同梱しません。
