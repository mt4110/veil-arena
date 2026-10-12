# M3: DNS予約Zビットの実開発検証

2026-10-11〜12 JSTのPR準備前の検証記録。Arena main `ca2b126`、Blackhole main `b620f99`を取得してから、それぞれの作業ブランチで検証した。両PR #3のマージを確認済み。以下は検証時点の結果で、提出後のCI・マージ・公開状態は各PRで別途確認する。

## 実課題と修正

Blackholeの既存DNS解析は、予約Zビット（flagsの0x0040）が立った30-byte queryを正常に受理していた。追加したdecoder回帰試験で、期待するmalformedに対して正常queryが返ることを再現した。人工fixtureは既存の`dns-query-a.hex`のflagsだけを0x0100から0x0140へ変えたもの。

[RFC 1035 section 4.1.1](https://www.rfc-editor.org/rfc/rfc1035.html#section-4.1.1)と、確認時点の[IANA DNS Header Flags](https://www.iana.org/assignments/dns-parameters#dns-header-flags)を照合した。現在も予約のbit 9だけをmalformedとし、割り当て済みAD/CD（0x0020/0x0010）は維持する。AD/CDの4組合せを正常対照とし、それぞれにZを加えると拒否することを試験した。

Blackholeの共通`decode_query`へ限定した条件を追加。DNS単体replayのCLI試験では終了2、`queries=0 unsupported=0 malformed=1 truncated=0`を要求した。予約ビットは入力形式の不正であり、攻撃者判定・通信drop・DNSSEC検証ではない。

## 試験結果

Mac ARM64、固定Nix環境（Rust 1.98.1、Cargo 1.98.0、Python 3.14.7）。既存Nix store／Cargo依存／workflow build cacheを再利用した。Arenaのrunner・集計コードは変更していない。

- 追加decoder試験は修正前に失敗した。
- Blackholeの整形チェック、Clippy、Rust 58試験、Python 4試験が修正後に成功した。
- 既存の一括入口が、修正前コード＋追加decoder試験の隔離snapshotで指定失敗を確認し、その後、修正後の正常19段階を2回完了した。各正常runのソース／バイナリ不変と、反復間の一致を既存入口で照合した。
- CLI試験の初稿で終了コードを誤って1としたため最初の一括試験は停止した。既存仕様の解析不正コード2へ訂正し、再実行した。最初の失敗ログ・summaryは保持し、成功結果で置き換えていない。

この10月11日の時点ではLinux実行、新規VM、Live取得は未検証だった。Linuxの追加確認は次節に記録する。DNS単体のCLI経路と共通decoderを確認したもので、wire上の遮断能力の証拠ではない。

## Linux検証とsnapshot手順の確認（2026-10-12 JST）

`ssh pi-node`のLinux x86_64上で、既存の専用Nixビルダー`veil-arena-builder-1791624441124999000-daemon`を再利用した。Nix store、Arena用Cargo registry、Blackhole用vendor、workflow cacheを保持し、依存の取得・更新なしで確認した。Rust 1.98.1、Cargo 1.98.0、Python 3.14.7。新しいsnapshotのパスには初回コンパイルが発生したため、cache再利用を全build省略と表現しない。

- LinuxのBlackhole整形チェック・Clippy・Rust 52試験・Python 4試験が成功。Macの58試験との差はmacOS専用試験であり、Linuxで未実行の分を合格扱いにしない。
- 修正前commitの`git archive`へ追加decoder試験だけを適用したsnapshotで、指定したRust試験の失敗を検出。その後、修正後の正常19段階を2回完了した。
- 同じsnapshot・cache・コマンドを使った再実行でも、失敗検出→正常2回が成功。各回のソース／バイナリ不変と、正常反復間の一致を確認した。
- `src/dns.rs`、`tests/decode.rs`、`tests/cli.rs`、`tests/fixtures/dns-reserved-z.hex`のhashがMacの現行変更とLinuxの試験対象で一致した。

snapshot専用の実装は追加せず、既存Git機能を使う[準備・再実行手順](BLACKHOLE_WORKFLOW.md#before-snapshotを準備する)を整えた。初稿ではGit初期化前の`git apply`が親のArenaリポジトリを参照し、snapshotの試験ファイルを変更しなかった。回帰試験のないbeforeが成功してしまい、一括入口は不具合再現の不一致として停止した。誤ったsnapshotと結果を保全したまま、新しいsnapshotを作り、Git初期化・baseのindex登録・patch適用・試験ファイルの差分確認の順に修正した。

修正後は文書の準備コマンドをそのまま実行し、snapshotに回帰試験が入ることを確認した。Macでも修正した準備方式のsnapshotで失敗検出→正常2回が成功。元のcheckout・過去の証拠を上書きせず、再実行ごとの保存先は新規になる。準備中に止まったsnapshotの削除を復旧手順へ含めない。

追加の証拠はGitignoredの`artifacts/m3-dns-z-linux-20261012/`。`preparation-guide.log`、`mac-prepared-snapshot.log`（失敗）、`mac-corrected.log`（成功）、`linux-check.log`（Blackhole単体成功と未適用snapshotの不一致）、`linux-rerun.log`、`linux-repeat.log`、`linux-hashes.log`、`linux-evidence/results/`の失敗1組・成功2組のmanifest／events／summary／Rust報告／ログを保持した。

Linuxの証拠をMacへ回収してから所有ビルダーを停止し、`exited`を確認した。cache・snapshot・証拠は残し、無関係なコンテナは操作していない。Linux ARM64・新規VM・Live取得は今回未検証。検証完了時点ではcommit・push・PRは未実施だった。本人による操作時間や理解負担の観察も未取得のまま。

## 日常利用で分かったこと

新しいArena機能を作らず、既存の`rust-tests`段階と`--before-contains`で実際の解析変更を検証できた。正常側のCLI試験の誤りも一括入口が不一致として停止した。before snapshotは手動で用意する必要があり、この準備負担は残っている。

本人の操作時間・理解負担・手動診断時間は未取得。自動実行の完了を人的工数削減やM3全体の製品価値確認へ読み替えない。今後の本人利用で、次を記録する。

| 観察項目 | 状態 |
| --- | --- |
| 入口と引数を本人が選べたか | 未取得 |
| before snapshotの準備で詰まった点 | 未取得 |
| summaryから失敗理由を説明できたか | 未取得 |
| 手動で追加した確認・所要時間 | 未取得 |
| 続けて使う理由／使わない理由 | 未取得 |

## 保存した証拠

Gitignoredの`artifacts/m3-dns-z-20261011/`に、修正前の`before.log`と`before-project/`、最初の失敗を含む`blackhole-checks.log`／`batch.log`、訂正後の`blackhole-checks-corrected.log`／`batch-corrected.log`、一括試験ごとのmanifest・events・summary・Rust報告・ログを保存した。成功runは`blackhole-workflow-2xvdl527/summary.json`。小単位のPRは作成していない。
