# 起動完了fixtureの公開競合の検証

2026-10-16。基点はPR #10マージ後のmain
`2b2069fe9f1033d8ee55d0f51b6b836fd01e3110`。
[このmainのCI](https://github.com/mt4110/veil-arena/actions/runs/38894666012/job/116952156341)
で、`tc_snapshot_wait_accepts_delayed_publication_but_respects_case_deadline` が
期待値 `ready` に対して空文字を読み、Linuxのnetns単体試験が15成功・1失敗となった。

## 原因と変更

待機側の `Run::ready_within` は起動完了ファイルの存在を確認する。
本番の `save()` は別の `.pending` ファイルへ書き込み、同期・close後に
hard linkで完成したファイルを公開する。一方、失敗した試験の子プロセスは
`sleep 3.2; printf ready > "$1"` と直接書き込んでいた。
リダイレクトによる空ファイル作成後、printfの書き込み前に待機側が走ると、
存在確認を通過した直後の内容確認が空文字になる。

試験fixtureを `src/readiness-publication.sh` へ切り出し、内容を書き終えた
`.pending` を `ln` で公開する形に変更した。Rustの既存試験はこの同じスクリプトを
`include_str!` で使用する。stdinのEOFによる所有子プロセスの正常終了も維持した。
本番の待機処理・保存処理・依存関係は変更していない。

遅延3.2秒、待機予算8秒、ケース期限8秒、内容 `ready` の一致、
ケース期限を過ぎた待機の失敗、子プロセスの正常終了という既存の条件は変更していない。

## 再現方法と結果

新しいPython試験はstdin/stdoutのチェックポイントで公開タイミングを制御する。
旧方式の「ファイルが存在するが空」という区間を保持して観測し、修正後の共通fixtureでは
pendingの内容が完成しても最終パスがまだ存在せず、公開後は `ready` を読めることを確認する。
旧版を無変更で反復して自然発生を捉えた結果ではなく、CIで観測された競合区間を
決定的に再現した結果である。各試験は8秒の期限を持ち、異常終了時も所有子プロセスを回収する。

| 環境 | 検証 | 結果 |
| --- | --- | --- |
| macOS ARM64、固定Nix devShell | `scripts/check.sh` | Rust 10件、controller 4件＋workflow 5件、Python 54件、fmt・Clippy・3 systemのNix評価成功 |
| Linux ARM64、既存Colima builder内、uid 501 | 同じ一括チェック | 上記に加えLinux専用netns Rust 16件成功 |
| Linux ARM64 | CIで失敗した元のRust試験を修正後に10回実行 | 10回成功。期限切れ拒否と子プロセスの正常終了のassertionを含む |
| macOS ARM64 | 新規の公開試験2件を10組実行 | 20件成功 |

Rust 1.98.1、Cargo 1.98.0、Python 3.14.7を両環境で確認した。
Nixは `--offline`、Cargoは `--offline --locked` を使用し、既存の依存・ビルドキャッシュを再利用した。
専用Colima profileとbuilderは検証後に元の停止状態へ戻した。通常利用のdefault profileには変更を加えていない。

再実行は固定Nix環境で以下を使う。Linux専用Rust試験はMacでは実行対象にならない。

```sh
nix --offline develop --command bash scripts/check.sh
nix --offline develop --command python3 -B -m unittest discover -s tests -p test_readiness_publication.py
# Linuxのみ
nix --offline develop --command cargo test --offline --locked --bin veil-arena-netns netns::publication_tests::tc_snapshot_wait_accepts_delayed_publication_but_respects_case_deadline -- --exact
```

## 証拠と確認範囲

ローカル証拠は `artifacts/readiness-publication-DJ9AVj/`。
`verification-summary.json` に対象ソース3ファイルとログのSHA-256、
環境・反復回数・確認範囲を記録した。`ci-failed.log` が取得した失敗CIログ、
`mac-check.log` / `linux-check.log` が一括チェック、
`mac-repeat.log` / `linux-repeat.log` が反復結果である。
ソースのSHA-256は次のとおり。

| ファイル | SHA-256 |
| --- | --- |
| `src/netns.rs` | `666d48cac6003d7bc1431a5d905535004f6d4e94fe2facea6279b923b11cb85b` |
| `src/readiness-publication.sh` | `a397b911b0e8543bfb70389aeb7134e72e9f4e630b98fd5029c18c92af3a1ccb` |
| `tests/test_readiness_publication.py` | `fa354b84a903ff2f49a3624ade3e253b2a2534c2c721129135c3e7ef2319f9af` |

このローカル検証記録の作成時点では、変更を含むGitHubのLinux x86_64 CIはpush前のため未実行。
PR作成後の結果は、PRのChecksで対象headの実行結果を確認する。
Macでの共通fixture検証をLinux専用Rust試験の実行とは扱わない。
shell fixtureが確認するのは公開時の可視性であり、電源断時の永続性ではない。
本番コードを変えない試験fixtureの修正のため、ネットワーク・BPF・新規guest VMの受け入れは再実施していない。
この記録だけでリリース準備完了とは扱わず、署名タグ・リリース公開は保留とする。
