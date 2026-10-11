# M3: Blackholeのfixture読込み境界を実開発で検証

2026-10-11 JST。M3の最初の1件。機能追加ではなく、実コードにあるfixtureの事前確認とopenの間の問題を修正した。M3全体の完了・公開を意味しない。

## 実課題と変更

Blackholeは通常ファイルであることを`symlink_metadata`で確認し、その後`File::open`していた。この間に最終パス要素がsymlinkへ置き換わると別のファイルを開き、FIFOへ置き換わるとwriter待ちでFDの種類確認に到達しない余地があった。

open部分を小さな関数に取り出し、事前確認後にsymlinkへ置き換える決定的な試験を追加した。修正前のopen動作はこの試験に失敗した。単に不具合を注入した対照試験ではなく、既存動作の再現である。ただし実際の同時プロセスを使う競合試験ではなく、確認とopenの間を分けた関数境界の試験である。

macOS／Linuxのopenで`O_NOFOLLOW | O_NONBLOCK`を指定し、FDの通常ファイル確認は保持する。既存の固定版libc 0.2.190をUnix対象に広げ、依存版とCargo.lockは変更しない。正常ファイル・確認後のsymlink・確認後のwriterなしFIFOを検証する。FIFOは合成入力だけに作り、試験ディレクトリは削除せず保持する。

親ディレクトリのsymlink、通常ファイル同士の差し替えや内容変更、deviceへ差し替えられた場合のopen自体の副作用は防がない。通常のローカルfixtureを扱う範囲の修正であり、未知のファイル用sandboxではない。非Unixの追加保証も行わない。

## 結果

基点はArena `d4a034f`、Blackhole `c05b69f`（両PR #2のマージ後）。試験対象は未コミットの修正ソース。BlackholeのLive取得・送信は実行しない。

| 確認 | 結果 |
| --- | --- |
| macOS ARM64: 修正前動作＋symlink回帰試験 | 単体試験失敗。Arenaも`rust-tests: failed`で終了1、後続は`not_run`。不一致を成功扱いにしない |
| macOS ARM64: 修正後 | Blackhole Rust 56／Python 4、Arena 19段階成功。最終ソースでfmt／Clippyも成功 |
| Linux x86_64: 修正後 | Blackhole Rust 50／Python 4、fmt／Clippy成功。Arena 19段階が2回成功 |
| ソース識別 | 正常runの開始・終了ソースhashとバイナリhashは一致。MacとLinuxのBlackhole `src/main.rs`／`Cargo.toml`、Arena `src/main.rs`の3ファイルhashも一致 |
| 再利用 | 既存Nix store、Cargo vendor／registryとtargetを使用。VM新規起動なし。既存Linuxビルダーを停止し、証拠とcacheを保持 |

固定環境はRust 1.98.1／Cargo 1.98.0／Python 3.14.7。Linux側のsnapshot Git HEADは元リポジトリのHEADとは別なので、変更コードの識別にはファイルhashと各run manifestを使う。Linux ARM64、Windows、Live captureは今回未検証。

Linux準備は3回失敗した。Nix experimental features未指定、snapshotの空commit、Blackhole専用vendorをArenaのbuildにも使った指定誤り。全ログを保存し、設定を直した後の成功と区別する。試験失敗の隠蔽や依存のオンライン取得は行っていない。

## 反復利用と継続判断

実行は既存の1コマンドを利用する。

```sh
nix develop --command cargo run --offline --locked -- blackhole-offline \
  --project ../veil-blackhole --artifacts artifacts --timeout-seconds 300
```

新しい試験はBlackholeの`cargo test`に入るため、Arena側の新しい段階やfixture adapterは不要。既存の19段階に自動で含まれ、分類・正常再生・checksum検証も続けて確認できる。

最終Mac runは2,873 ms。Linuxの最初の正常runは21,583 ms、再実行は3,046 ms。これはbuildを含む各runの実測時間であり、手動手順との比較や速度改善率ではない。準備・診断時間の連続計測、手動操作の基準値は未取得。準備時の3件の追加診断は維持費として残す。

今回、実際の変更前後を既存Arenaで判定できた。修正ごとにPRを分けず、[準備・反復・集計の運用](BLACKHOLE_WORKFLOW.md)も含めてM3のまとまりでレビューする。その後も必要な開発変更で反復利用し、手動確認の基準値・診断負担を記録する。k8s／Scapy／NetEmの導入理由はこの結果だけでは成立しない。

証拠はGitignoredの`artifacts/m3-fixture-open-20261011/`。`before.log`、`before-arena/`、`final/`、`linux-evidence/`、`summary.json`と準備ログを保持する。本記録はコミット前の検証結果。後続でM3のまとまりとしてPRへ提出する。マージ・リリース公開・タグ変更は、この検証では実施していない。
