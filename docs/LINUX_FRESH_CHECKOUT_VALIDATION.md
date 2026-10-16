# マージ後main：Linuxの新しいチェックアウトでの確認

2026-10-16 JST。PR #7マージ後のArena mainを固定し、[新しいチェックアウトの手順](FRESH_CHECKOUT_CHECK.md)をLinuxでも実行した。実行コード・依存・合否判定は変更していない。Blackholeの実機確認と第三者の試用は引き続き保留。

## 基準と環境

| 項目 | 確認した条件 |
| --- | --- |
| Arena | `8bf5691e7daebf84ae6268845b041c0586fd2886`、PR #7マージ後main |
| 正常Blackhole | `db11a84551bb0f0e8fb356a1f84bb01c38d20db6` |
| 失敗snapshot | `b620f99c56eb001ad2a6b5ce1f2f889424dfe12d`に`d5037b326beb9a29f3845ec8561ec1ada95f7cd7`の追加decoder試験だけを適用 |
| patch SHA-256 | `777876f8a2417ba5d16b1698e7fb628138a1c1a54205ce956b47690aea06a7e9`、前回macOSと同じpatch |
| 実行場所 | 既存Linux x86_64の専用ビルダー`veil-arena-builder-1791624441124999000-daemon` |
| ユーザー | UID/GID 1000、試験はrootで実行していない |
| ホストkernel | `7.0.0-38-generic`。以前の専用VMの固定kernelとは別 |
| 固定ツール | Nix 2.33.1、Rust 1.98.1、Cargo実バイナリ1.98.0、Python 3.14.7 |

検証元のGit履歴はローカルbundleで転送し、Linux上で元のcommitを保持するソースを用意した。そのソースからさらにローカルcloneし、手順の新しい入力を作った。bundleのSHA-256は転送前後で一致。未コミット・未追跡ファイルや依存を丸ごと転送していない。SSH/SCPは準備と証拠回収の通信で、試験によるDNS送信やLive取得とは別に扱う。

基準mainの[CI run 38849042683](https://github.com/mt4110/veil-arena/actions/runs/38849042683)も、先頭revisionが`8bf5691e7daebf84ae6268845b041c0586fd2886`で成功していることを確認した。これは今回の未commit文書差分へのCIではなく、新しいチェックアウトの実行結果とも分ける。

## 既存cacheと準備上の差

Nix storeと既存Docker imageを再利用し、依存取得・image再生成・cache削除は行っていない。次の3つをすべてのworkflowへ同じ絶対パスで渡した。

| 指定 | 既存の保存先 |
| --- | --- |
| `--cache` | `/tmp/arena-m3-workflow-cache-20261011` |
| `--arena-cargo-home` | `/tmp/arena-home/.cargo` |
| `--project-cargo-home` | `/tmp/arena-short-dns-20261011/blackhole-cargo` |

Blackhole用Cargo homeには、既存vendorを指す`config.toml`がある。Arena用registryと混ぜず、共有configも変更していない。新しいソースパスのため再コンパイルは発生しており、cache再利用をbuildの省略や時間短縮の実測と読み替えない。

このビルダーはUID 1000のpasswd登録がなく、環境のHOMEは`/`だった。通常の`nix --version`が`cannot determine user's home directory`で終了134となった。これはworkflow実行前の準備失敗で、runnerの検証不一致や終了3の記録ではない。最初の失敗を保存し、HOME・ユーザー登録・権限・trusted-usersを変更せず、次のセッション限定設定で固定Nix devShellを起動した。

```sh
# 記録時の専用ディレクトリ。別の環境では専用の書込み先を用意する。
nix_run_root=/tmp/arena-linux-checkout-20261016-2fN3zm
export XDG_CONFIG_HOME="$nix_run_root/nix-config"
export XDG_CACHE_HOME=/tmp/arena-home/.cache
export XDG_DATA_HOME="$nix_run_root/nix-data"
export XDG_STATE_HOME="$nix_run_root/nix-state"
mkdir -p "$XDG_CONFIG_HOME" "$XDG_DATA_HOME" "$XDG_STATE_HOME"
export NIX_CONFIG="${NIX_CONFIG-}"$'\nuse-xdg-base-directories = true'
nix --version
```

上記は今回のUIDだけのコンテナで確認した組合せで、通常のLinuxユーザーへ一律に要求する設定ではない。daemonからは`use-xdg-base-directories`をrestricted settingとして無視する警告が出た。警告は隠さず保存し、信頼ユーザーの追加や権限緩和は行わなかった。クライアントの起動と固定devShellのツール版を確認した後、一般ユーザーのまま既存の`blackhole-workflow.sh`を使った。固定ツールを直接呼ぶ代替試験へ置き換えてはいない。

## 実行と判定

PR #7時点の手順の4つのshell blockを使い、3つのcache変数を上記の既存パスへ置き換えた。Nixのセッション設定だけを前置した実行用scriptを証拠と一緒に残した。追加したLinux用cache例はこの3変数の指定を文書化したものである。

| 操作 | 結果 |
| --- | --- |
| 正常 | 終了0、19段階×2回が成功 |
| 意図した失敗 | 終了1、`rust-tests`の`dns_reserved_z_is_malformed_while_ad_cd_remain_supported`で失敗 |
| 失敗の表示 | 実際は`Ok(DnsQuery ...)`、期待は`Err(Malformed("DNS reserved Z flag"))`。終了101／期待0とstdout・stderr・詳細報告の場所を表示 |
| 後続停止 | Rustの後続段階とworkflowの`healthy-2`が`not_run` |
| 正常への復帰 | 同じcacheと正常対象で終了0、19段階×2回が成功 |
| ソース・バイナリ | 正常・復帰の計4 suiteで識別が一致。各suiteで`source_unchanged`と`binary_unchanged`がtrue |
| 入力 | 正常両checkoutは試験後もGitの変更なし。失敗snapshotは`tests/decode.rs`の追加14行だけ |

Rust報告の19段階の名前・順序・終了状態、ソースとバイナリの識別、各ログの存在を別途検査した。失敗が指定した回帰試験であることをログで照合し、別の失敗や準備エラーを代用していない。

正常版のソース識別は`4a79712e34c0a59316d12696a5ffb83c7ad400015bc08cfbfce0cc9e22ef3c25`。Linuxの試験用バイナリSHA-256は`600ea5bf9cd9da60d701dec72abb16f833d014156507cbea14626180fda393cc`。このバイナリhashがmacOSや別パスの再ビルドでも同じになる保証ではない。

## 手順の改善と確認範囲

新しいチェックアウトのガイドへ、操作1の後にLinux用の3つのcache指定を置き換える例と、試験開始前のNixユーザー環境の確認を追加した。macOSの検証記録には、後続のLinux確認へのリンクを追記した。今回の追加は文書だけで、試験の期待値や安全条件は弱めていない。

変更した4文書のshell構文、相対リンク先、差分の空白検査が成功。実行用scriptが基準mainの手順に3つのcache指定とセッション準備だけを加えた内容であることも照合した。

同じLinuxホストの準備済み依存を使った結果であり、cold install、新規端末、通常ユーザーを持つ別のLinux、Linux ARM64、VM／eBPF実起動、実DNS・sudo、第三者の理解、性能・工数削減を確認したものではない。全単体試験や新規VM受け入れを別途実行したとは扱わない。M4の第三者試用や主力製品への格上げも未完了。

## 証拠と後片付け

ローカルのGit対象外`artifacts/linux-checkout-20261016-2fN3zm/`に保存した。

- `linux-prepare.sh`／`.log`：ソース準備、Nixの準備失敗と固定ツールの版確認
- `linux-procedure.sh`／`.log`：正常・失敗・復帰の実行手順とコンソール
- `linux-verify.sh`／`.log`：ソース・バイナリ・結果・ログの照合
- `evidence/`：3つのworkflowのmanifest・events・summary・Rust報告・ログ、patch、Nixの準備失敗、`verified-evidence.json`
- `local-evidence-index.json`：Linux上の絶対パスに対応する、回収後のsummaryとログの保存先
- `builder-preflight.log`、`builder-stop.log`：ビルダーの開始前状態と終了後状態

Linux側にも新しいcheckout・snapshot・cache・証拠を保持し、既存資源は削除していない。試験後は所有ビルダーを停止し、元の`exited`状態へ戻した。本記録は文書差分のcommit・push・PR提出前に得た結果。提出後のCI・マージ・公開状態はPRで別途確認する。
