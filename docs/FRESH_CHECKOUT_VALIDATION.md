# 現行mainと新しいチェックアウトの検証

2026-10-16 JST。対象はArenaのオフライン入口の再現性。Blackholeの実機確認と第三者の試用は保留したまま、準備済み依存を使う新しいソース配置で確認した。

## 基準と入力

| 対象 | revision・条件 |
| --- | --- |
| Arena | `69c10b2556bc3efe57e5ae7bff54121155ea5793`、PR #6マージ後の`origin/main` |
| 正常Blackhole | `db11a84551bb0f0e8fb356a1f84bb01c38d20db6`、PR #5マージ後の`origin/main` |
| 失敗snapshot | `b620f99c56eb001ad2a6b5ce1f2f889424dfe12d`へ`d5037b326beb9a29f3845ec8561ec1ada95f7cd7`の追加decoder試験だけを適用 |
| patch SHA-256 | `777876f8a2417ba5d16b1698e7fb628138a1c1a54205ce956b47690aea06a7e9` |
| 実行環境 | macOS ARM64、固定Nix devShell、Rust 1.98.1／Cargo実バイナリ1.98.0／Python 3.14.7 |

両元リポジトリのremoteを取得し、検証したmainを固定した。元ディレクトリからローカルcloneし、未コミット・未追跡ファイルをコピーしていない。新しい正常両checkoutは試験後もGitの変更なし。失敗snapshotの差分は`tests/decode.rs`の追加14行だけで、元のBlackholeソースは変更していない。新しい公開用commitも作っていない。

Nix store、既存の`~/.cargo`、元Arenaの`target/blackhole-workflow/`を使った。workflow cacheと両Cargo homeは全試験で同じ絶対パス。Nix/Cargoの試験はoffline／lockedで、依存取得やcache削除は行っていない。新しいソースパスに対応するコンパイルは発生しており、すべてのbuildが省略されたという結果ではない。

## 結果

| 操作 | 判定と証拠 |
| --- | --- |
| 正常 | 終了0、19段階×2回が`passed` |
| 既知の失敗 | 終了1、`rust-tests`のZビット回帰試験が失敗。実際は`Ok(DnsQuery ...)`、期待は`Err(Malformed("DNS reserved Z flag"))`。終了コード101／期待0とstdout・stderr・詳細報告の場所を表示 |
| 失敗後の停止 | Rustの後続段階とworkflowの`healthy-2`が`not_run`。別の環境エラーを不具合再現と読み替えていない |
| 正常への復帰 | 同じcache・正常対象で終了0、19段階×2回が`passed` |
| 識別の照合 | 正常・復帰の計4 suiteで同じソース識別とバイナリhash。各suiteの`source_unchanged`／`binary_unchanged`がtrue |
| 証拠保存 | 正常・失敗・復帰の出力は別領域。以前の起動待ち検証summaryも残っていることを確認 |

正常版バイナリのSHA-256は`19a7917c8fb3cb8e819a9cb268591f405f132ec7e31d35760607a8be164caa15`、runnerが計算したソース識別は`4a79712e34c0a59316d12696a5ffb83c7ad400015bc08cfbfce0cc9e22ef3c25`。release buildとは異なるオフライン試験用バイナリであり、他環境の再ビルドで同じ値になるという保証ではない。

## 改善と範囲

runnerの不具合は見つからず、実行コード・合否判定・依存は変更しなかった。READMEの最初の入口を、既存の反復・集計用workflowへ揃えた。新しいチェックアウトでは既定cacheが別の場所になるため、cacheとCargo homeの引継ぎを明示した[再実行手順](FRESH_CHECKOUT_CHECK.md)を追加した。既知の失敗はbaseと試験取得元を別々に固定し、以後のBlackhole変更に引きずられないようにした。

最初の準備では、ローカルcloneに元リポジトリの`origin/main`が転送されておらず、Arenaのcheckoutが停止した。試験は未開始。このログも保持し、手順には必要refのローカルfetchを明記した。元のbranch、ソース、cacheを壊して解決したものではない。

整備後の文書から4つのshell blockをそのまま取り出し、別の新しいチェックアウトで全手順を実行して終了0を確認した。こちらも正常2回→同じ回帰試験の失敗と後続停止→正常2回が成立した。各summary、正常版のソース・バイナリ不変性、patch hash、入力checkoutに変更がないことを再確認した。文書6ファイルのshell構文、相対リンク先、差分の空白検査も確認した。実行コードに変更がないため、別途の全単体試験やVM試験の再実行は行っていない。

Blackholeを再開する際の経緯・役割・未確認事項・次の目標は[専用引継ぎプロンプト](BLACKHOLE_HANDOFF.md)へ分離した。このArenaタスクはBlackholeの実機試験を必要条件にしない。

今回の結果は同じMacの準備済み依存を使った検証。Linuxでの新しい再実行、cold install、新規端末、VM／eBPF実起動、実DNS、実sudo、第三者の理解、人的工数や収益性は未検証。以前のVM受け入れ・Linux検証の結果を今回の実施結果に混ぜない。M4の第三者試用も未実施であり、主力製品への格上げは未判定。

PR #7マージ後mainを使ったLinuxの確認は、後続の[追加検証記録](LINUX_FRESH_CHECKOUT_VALIDATION.md)に分けて残す。上記はmacOS実施時点の範囲である。

## ローカル証拠

Git対象外の`artifacts/fresh-checkout-20261016-XOB5sd/`へ次を保存した。公開リポジトリには同梱しない。

- `normal.log`、`normal/blackhole-workflow-b1utlfkr/summary.json`
- `failure.log`、`failure.exit`、`failure/blackhole-workflow-nm2zflqe/summary.json`
- `recovery.log`、`recovery/blackhole-workflow-9bp__bgt/summary.json`
- 各manifest・events・Rust結果と段階別ログ
- `dns-z-regression.patch`、clone／checkoutの準備ログ、`verified-evidence.json`
- 新しい正常両checkoutと失敗snapshot

文書どおりの再確認は`artifacts/checkout-check-6GGFf5/`へ保存した。元の証拠領域に`documented-procedure.sh`、`documented-procedure.log`、`documented-evidence.json`も残した。

本記録はPR・push前の結果。外部CIやマージ・公開の完了を意味しない。
