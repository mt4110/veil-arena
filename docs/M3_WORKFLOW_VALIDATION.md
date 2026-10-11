# M3: 準備・反復試験・集計の運用検証

2026-10-11 JST。M3-A〜Cの最初の実開発課題をまとめたローカル検証。Blackholeのfixture読込み修正と、Arenaの1つの入口を同じ変更単位としてレビューする。検証中は小単位のPRを作らず、Blackhole修正とArena運用をそれぞれ1本にまとめて提出する。

後続の[差分レビューと個別手順との比較](M3_SELF_USE_VALIDATION.md)で、段階名・順序とhashの検査を補強した。本記録の最終コード・試験数はその補強前の候補を指す。

## 完成した技術的な範囲

| 区切り | 実装・確認 |
| --- | --- |
| M3-A 準備 | 固定Nix環境、experimental featuresの入口指定、Arena buildとBlackhole実行のCargo homeを分離。offline／locked、既存cacheの再利用、同じcacheの並列使用拒否 |
| M3-B 反復・集計 | 修正前の指定段階と指定試験ログの失敗確認、修正後の正常2回、反復間のソース／バイナリ一致。manifest・journal・summary・各Rust証拠を保存 |
| M3-C 実課題 | 既存のfixture open問題でMac／Linuxの変更前後を確認。時間、外側のコマンド件数、準備失敗・追加診断を記録 |

M3の人的工数削減の受け入れは未完了。手動baseline、レビュー・マージも残る。外部利用者による再現と製品価値の確認は後段であり、今回の技術的受け入れ条件にはしない。k8s等の機能追加へ自動で進む根拠にしない。

## 最終コードの確認

Arena基点`d4a034f`、Blackhole基点`c05b69f`。両方ともローカルの未コミット差分を検証した。固定Rust 1.98.1、Cargo 1.98.0、Python 3.14.7。Mac ARM64とLinux x86_64で実行した。

| 試験 | 結果 |
| --- | --- |
| Mac一括 | beforeの指定試験が失敗→healthy-1／healthy-2の19段階成功。全体終了0 |
| Linux一括 | 同じ順序・19段階の正常2回を確認。全体終了0 |
| 最終補助コード | Macで全Python 34試験成功。Linuxで追加10試験成功。補助スクリプト・入口shell・追加試験の3ファイルhashはMac／Linuxで一致 |
| Blackhole回帰 | 正常runにRust／Python試験を含む。MacはRust 56／Python 4、LinuxはRust 50／Python 4 |
| 依存不足 | Macで空のArena Cargo homeを指定。準備の環境エラー・終了3、suite未実行。依存取得せずログを保持 |
| 誤った期待値 | Macで存在しない失敗ログ文字列を指定。beforeのRust試験自体は失敗したが期待値不一致で終了1、healthy-1／2は`not_run` |
| 復帰 | 上記2つの否定試験後、既存の通常cacheで正常2回・終了0を確認 |
| 中断・期限の分類 | 補助の準備用processを短い期限で停止する単体試験。途中のRust timeout／cancelled結果を環境エラーへ読み替えない分類試験。実workflowの任意時点の中断は今回未検証 |
| 証拠保全 | JSONの排他的保存と0600、project内やsymlink保存先の拒否を単体試験。旧証拠・cacheは削除しない |

単体試験のVM判定fixtureが出すメッセージは、実VMの新規起動の証拠ではない。今回、VM image／kernel／netns／eBPFの変更も新規VM起動も行っていない。Linux ARM64・Windows・Live取得も今回の範囲外。

## 操作と実測

最終の変更前後確認は、各環境で1つのworkflow呼び出しから行った。内部の外側のコマンドはArena build 1回＋Rust runner 3回の計4回。Cargo／Rust試験内部のprocess数や、人が行う操作数ではない。

| 最終run | 準備 | before（期待した失敗） | healthy-1 | healthy-2 | workflow合計 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Mac ARM64 | 142 ms | 1,017 ms | 1,853 ms | 1,370 ms | 4,794 ms |
| Linux x86_64 | 265 ms | 4,092 ms | 5,523 ms | 6,377 ms | 18,185 ms |

workflow合計は各呼び出し・保存・集計を含む。Nix devShellへ入る前の時間は含まない。両方ともwarm cache。初回・別候補版のcold build結果も保存しているが、上記と比較して性能改善率は算出しない。

手動で同じ課題を処理した場合の時間・操作数は未取得。summaryの`manual_baseline=null`、`time_saving_claim=false`を維持する。利用者の操作・理解負担を測定したと装わない。

直前の実課題でNix設定・空commit・誤ったCargo vendor指定の3件の準備診断が必要だった。新しい入口はNix設定とCargo home分離を引き受け、Git commitを自動で作らない。最終のLinux一括では追加の設定修正は不要だった。source snapshotの受け渡しとLinux専用ビルダーの起動・停止は検証準備として別に行っており、1コマンドで自動化したとは呼ばない。

## 証拠と現在地

`artifacts/m3-workflow-20261011/`に以下を保存する。

- `accepted/blackhole-workflow-jht7_rk9/summary.json`: 最終Mac一括
- `linux-evidence-accepted/m3-workflow-20261011-final/blackhole-workflow-1o1vwlen/summary.json`: 最終Linux一括
- `accepted-preparation-error/`: 依存不足の環境エラー
- `accepted-wrong-marker/`: 指定した失敗文字列との不一致
- `recovery/`: 否定試験後の正常2回
- `python-accepted.log`: Macの全Python 34試験
- `mac-accepted-hashes.log`／`linux-accepted-hashes-stop.log`: 3ファイルの同一性と所有Linuxビルダー停止
- `validation-summary.json`: 保存した各候補・最終runの集計。古い候補を最終版へ読み替えない

Linuxでは既存Nix store／Cargo vendor・registry／専用workflow cacheを再利用した。専用ビルダーは停止、入力・出力・cacheを保持した。他のコンテナは操作しない。実課題の原因と修正は[fixture検証記録](M3_FIXTURE_OPEN_VALIDATION.md)、再利用手順は[運用ガイド](BLACKHOLE_WORKFLOW.md)を参照する。

このまとまりはローカルでレビューできる状態。小さな修正ごとのPRは作成せず、M3の技術的な変更をまとめている。本記録はコミット前の検証結果。PR提出後もリリース公開・マージ・タグ変更は別の承認を要する。今後は手順を実際に使い、診断負担と手動baselineを確認してM3の継続・縮小を判断する。
