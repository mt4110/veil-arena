# M3: 失敗を最初に判別できる表示

2026-10-12 JST。本人が正常コマンドを実行して正常2回のログを共有した一方、保存済み失敗summaryが「どれも成功に見える」と報告した。これを診断表示の問題として扱い、合否条件を変更せず、コンソールの結果案内を改善した。

## 変更

- 正常は`[成功] 通常試験1回目`、不一致は`[失敗] 通常試験1回目`。JSONの`healthy-1`は実行識別子として維持する。
- 失敗時は段階、終了コードと期待値、観測された理由、最大4行・元ログの各240文字までのログ抜粋、stdout／stderrログと詳細報告の場所を表示する。
- beforeの期待した失敗を検出した場合は`[想定した失敗を確認（試験自体は失敗）]`と表示し、正常成功と区別する。
- 環境エラー・期限超過・中断は別ラベル。段階が分からない場合は推測せず、取得できなかったことと外側の実行ログを案内する。準備失敗も段階・終了コード・ログを表示する。

診断情報はsummaryの各runへ追加する補助情報。既存のRust runner、期待値、全体status、終了コード、反復時の識別照合は維持した。既存の証拠を上書きしない。抜粋対象は通常ファイル・2 MiB上限・ファイル名を確認し、最終要素のsymlinkを辿らず、端末制御文字をJSON表現でescapeする。報告やログは信頼するローカル試験の証拠であり、OS sandboxや真正性の保証ではない。

## 確認した結果

Mac ARM64、既存固定Nix環境とcacheを使用。整形・Clippy・Rust試験、Python 40試験、3 systemのNix定義評価が成功。Pythonの追加3試験は失敗表示の段階・期待値・ログ・制御文字、危険なpath／symlink／過大ログの拒否、成功と想定失敗の区別を確認した。表示文言の最終調整後にも全Python 40試験を確認した。

実際の入口で次を確認した。

- 正常Blackhole mainは正常19段階×2回、終了0。
- 既知の不具合を含む保存済みsnapshotは`rust-tests`で終了1。失敗した試験名、実際の`Ok(DnsQuery...)`、期待の`Err(Malformed("DNS reserved Z flag"))`、ログの場所、後続未実行を表示。
- 同じsnapshotをbeforeとして指定すると、想定失敗の確認ラベル→正常2回、終了0。
- 空の準備用Cargo homeを指定すると環境エラー・準備段階・終了コードとログを表示し、終了3。試験失敗に読み替えない。

証拠はGitignoredの`artifacts/m3-failure-display-20261012/`（`checks.log`、`python-verified.log`、`healthy.log`、`failure-final.log`、`expected.log`、`preparation-failure.log`、各summary・Rust報告・ログ）。開発途中のログも保持した。

## Linuxでの追加確認と提出前検証

同日のLinux x86_64上で、既存の専用ビルダー、Nix store、Cargo registry/vendor、workflow cacheを再利用した。全Python 40試験、実際の正常19段階×2回（終了0）、既知のsnapshotの失敗表示と後続停止（終了1）、想定失敗の確認→正常2回（終了0）、準備依存不足の環境エラー表示（終了3）を確認した。

提出前に、ESCに加えてC1制御文字と文字方向を変える制御文字もescapeするよう補強した。日本語は読みやすいまま表示する。最終コードでMacの全チェックとLinuxの全Python 40試験・上記4ケースを再確認した。集計コードと追加試験の2ファイルhashがMac／Linuxで一致した。

追加証拠はGitignoredの`artifacts/m3-display-linux-20261012/`。`mac-final-checks.log`、`linux-check.log`（初回）、`linux-final-check.log`（最終4ケース）、`linux-hashes.log`、`linux-evidence/results/`の各summary・Rust報告・ログを保持した。証拠回収後に所有ビルダーを停止し、`exited`を確認した。cacheと過去の証拠は残している。

Linux ARM64、新規VM、Live取得は今回未実施。本人が改善後の表示だけで診断できるかは未確認。[本人チェック](M3_PERSONAL_CHECK.md)で追加観察する。本記録はPR提出前の結果で、提出後のCI・マージ・公開状態はPRで別途確認する。
