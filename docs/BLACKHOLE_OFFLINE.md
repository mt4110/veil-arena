# ブラックホールのオフライン検証

veil-arenaから、信頼するローカルのveil-blackholeチェックアウトに対して、既存のRust/Python回帰試験と人工入力の再生を実行する。runnerが終了コードと出力を照合し、実行ごとの結果とログを保存する。ブラックホールのソースやfixtureは編集しない。

これはM2の実プロジェクト接続の最初の実装である。[Nixと専用Linux VM](NIX_VM.md)を追加し、Linux x86_64／KVMゲストで成功、不具合検出、新しいVMでの再成功を確認した。[VM内のnetns正常通信](NETNS_VM.md)は別のシナリオで扱う。Linux eBPFの検証は未実装で、MVP全体の完成を意味しない。

## 実行方法

Rust 1.98.1、Cargo 1.98.0、Python 3.14.7、Git、C compilerを利用する。Nixの固定環境を使う。macOSのビルドにはSDKも必要。依存がローカルのCargoキャッシュにない場合、offline buildは環境エラーで停止する。準備と試験の手順は[Nixガイド](NIX_VM.md)を参照する。

```sh
cd /path/to/veil-arena
nix develop --command cargo run --offline --locked -- blackhole-offline \
  --project ../veil-blackhole
```

ビルド済みのrunnerでは、保存先と期限も指定できる。

```sh
nix develop --command ./target/debug/veil-arena blackhole-offline \
  --project ../veil-blackhole \
  --artifacts artifacts \
  --timeout-seconds 300
```

`--project`は必須。GitのルートとRust package名を確認する。保存先の既定は現在の作業ディレクトリの`artifacts/`。ブラックホールのチェックアウト内とsymlinkの保存先ルートは拒否する。各runを排他的に作り、既存結果を上書きしない。

期限は個別コマンドではなく、ソース確認から最終確認までの全実行に適用する。既定300秒、範囲1〜3600秒。停止・出力回収には追加で最大2秒待つ場合がある。Ctrl-C、SIGTERM、SIGHUPは中断として扱う。

## 検証内容

| 段階 | 実行と期待値 |
| --- | --- |
| 環境識別 | rustc/Cargo/Pythonの実versionを固定版と照合し、Gitルート、HEAD、追跡ファイルの変更状態を記録 |
| ビルド | `cargo build --offline --locked --bin veil-blackhole`。失敗は環境エラー |
| Rust回帰 | `cargo test --offline --locked`。既存試験をそのまま実行 |
| Python回帰 | Python 3.14.7の`python3 -B -m unittest discover -s tests -p 'test_*.py'`。既存のモックによるオフライン試験 |
| 通常再生 | 人工IPv4、IPv6、DNS単体、Darwin BPFの2レコード。終了コード0とquery・エラー件数を照合 |
| チェックサム | 人工IPv4、IPv6、IPv6拡張ヘッダー。終了コード0、検証有効表示、UDP checksumのvalid件数を照合 |
| 不正入力の拒否 | 不正UDP長と不正UDP checksum。終了コード2に加え、offline表示と`queries=0 unsupported=0 malformed=1 truncated=0`を要求 |
| 短縮DNSの分類 | Arenaが用意する11-byte DNSヘッダー。終了コード2と`queries=0 unsupported=0 malformed=1 truncated=0`を要求。Blackhole側への新fixture追加を必須にしない |

計19段階（v0.1.0は18段階）。終了コード2だけでは拒否成功としない。fixture読込みや引数のエラーも同じ終了コードになり得るため、期待する集計出力が必要になる。最初の不一致・環境エラー・期限超過・中断で後続を停止し、未実行は`not_run`として残す。失敗を黙って再試行しない。

VMの結果判定器も19段階を要求する。過去のv0.1.0の18段階報告を再確認する場合は、そのタグに含まれる判定器を使う。

## 結果と終了コード

```text
artifacts/blackhole-offline-<time>-<pid>-<suffix>/
  manifest.json          環境、コマンド、期待値、開始時のソースhash
  events.jsonl           開始・各段階・終了の追記記録
  result.json            全体と各段階の判定、終了理由、終了時のソースhash
  inputs/                再生に使った人工fixtureのコピー
  <step>.stdout.log      各コマンドの標準出力
  <step>.stderr.log      各コマンドの標準エラー
```

| CLI終了コード | 意味 |
| --- | --- |
| 0 | 全段階が期待値に一致 |
| 1 | テスト・再生結果の不一致 |
| 2 | runnerへの引数不正 |
| 3 | 環境、起動、入力、保存、ログ上限等のエラー |
| 4 | 全実行の期限超過 |
| 130 | 中断 |

段階の判定は`passed`、`failed`、`environment_error`、`timeout`、`cancelled`を区別する。ログ上限は段階ごとの標準出力・標準エラーそれぞれ2 MiB。超過は`output_limit`として記録し、対象process groupを停止する。CLIは終了コード3を返す。runディレクトリは新規作成時0700、JSON・fixtureは0600で作る。成果物はGit対象外で、runnerに削除機能はない。

Rust/C/Pythonのコード、fixture、Cargo manifest/lock、toolchain等の対象ファイルをSHA-256で識別する。開始・終了の一覧とhashが変われば、結果全体を環境エラーにする。HEADだけで未コミットコードの検証を説明しない。Gitの状態表示は追跡ファイルが対象で、対象ディレクトリ内の未追跡コードはhash一覧にも含める。

再生用fixtureは開始時にコピーし、そのコピーを使う。実行バイナリのhashも別に残す。build cacheは実行時の作業ディレクトリの`target/blackhole-offline/<checkout-path-hash>/`に置き、チェックアウトの実パスごとに分ける。同じ作業ディレクトリ・チェックアウトを使うarenaの同時実行はadvisory lockで拒否する。lockファイルは削除せず、FDを閉じて解除する。

## 実行範囲と制約

通常ユーザーで動作し、root実行は拒否する。runnerはLive capture、sudo、DNS送信、VM/netnsの構築、ホストのネットワーク変更、削除コマンドを呼び出さない。Cargoには`--offline --locked`を指定し、依存の取得やlock更新を行わない。

対象のbuild script・テストコードはホスト上で実行するため、信頼するローカルのチェックアウトに限る。これはOS sandboxではなく、対象コードによるファイルアクセスや通信をOSレベルで禁止する機能ではない。未知のリポジトリや実マルウェアの実行に使わない。

fixture名とCLI集計形式が変われば、adapterの期待値もレビューする。期待値変更で既存の安全条件を弱めない。ソースhashの開始・終了比較は変更検出であり、全コードを不変snapshotにした保証ではない。検証中の編集と、arena以外からの同じcacheへの書込みは避ける。

SIGKILLやhost停止では最終JSONを保存できない場合があり、残ったjournalを完了結果として扱わない。別sessionへ逃げた子孫プロセスを完全に封じる仕組みでもない。artifact保存先そのものが使えない場合は、stderrと終了コード3で報告する。

## 開発時の確認

```sh
cargo fmt --all -- --check
cargo test --offline --locked
cargo clippy --offline --locked --all-targets -- -D warnings
```

runnerの試験は、異常終了・signal・起動失敗・期限・実行中の中断・出力上限・子孫のパイプ保持・拒否の誤判定・既存成果物の保全を確認する。一時ファイルと結果は自動削除しない。

直接依存は、JSON記録にserde_json、入力識別にsha2、signal処理にctrlc、Unixのprocess group/非blocking I/Oとlockにlibcを使用する。版はCargo.tomlとCargo.lockで固定し、いずれもMITまたはApache-2.0を選べるライセンスを確認した。脆弱性確認の範囲は[検証記録](BLACKHOLE_OFFLINE_VALIDATION.md)に記載する。
