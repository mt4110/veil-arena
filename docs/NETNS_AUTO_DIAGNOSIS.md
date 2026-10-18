# netns一括試験の失敗診断を自動表示する

正式入口から試験が失敗したとき、保存先を探して手動診断を実行する手間を減らす。
元の合否と終了コードはRustのworkflowが決め、診断はその後の読み取りだけを担当する。

## 実行順序と失敗時の扱い

1. 既存のランチャーがguestを実行し、証拠を同期保存して所有QEMUを停止・回収する。
2. workflowがランチャーの終了を待ち、従来の基準で集計して `summary.json` を保存する。
3. 失敗時だけ固定Pythonの `diagnose_netns.py` を呼び、最初の問題とログの場所を表示する。
4. 診断の結果にかかわらず、元のworkflow終了コードを返す。

診断用Pythonの不在、診断の異常終了、壊れたJSONは `diagnosis unavailable` として表示する。
診断が5秒以内に終わらない場合は、その診断子プロセスだけを停止してwaitする。
この5秒は後処理の上限であり、ケース・バッチ・VMの既存期限を変更しない。
診断プロセスはVMやネットワーク資源を作らない。
回収の成否は元の停止証拠で判断し、診断の生成成功をQEMU回収成功とは扱わない。

workflowが出力先の作成や集計保存で失敗した場合は、元のエラーと診断不能を表示する。
既存出力先の拒否時に、そこにある過去の証拠を今回の失敗として診断しない。
正常時には診断を呼ばず、従来の短い表示を維持する。

既存のimmutableなprepared pathは更新されない。正式入口の更新方法は
[netns一括試験の手順](NETNS_WORKFLOW.md#失敗した段階と証拠を調べる)を参照。
低水準のRustバイナリを直接呼ぶ従来の3引数形式も維持する。
正式入口は追加の `--diagnose PYTHON SCRIPT` で固定の診断実装を指定する。

## 回帰確認の範囲

`cargo test --offline --locked --manifest-path tools/vm-controller/Cargo.toml`
から、ビルドされた実workflowバイナリを使う統合試験を実行する。
固定Pythonとダミーランチャーを使用し、sudo・BPF・ネットワークは使わない。

- 正常時は診断を実行しない。
- ケース不一致と監督側timeoutを区別し、実際／期待の終了コードとログの場所を表示する。
- VM停止の異常、欠落した子報告、壊れたJSONを扱う。
- 診断用Pythonの不在、異常終了、診断timeoutでも元の終了1／3を維持する。
- ランチャーとその所有ダミー子の回収後に診断を開始する。
- 診断timeoutの子プロセスが残らず、その後の正常実行が成功する。
- 証拠の内容・更新時刻・ファイル一覧を変えない。
- 既存出力先を上書きせず、その古い証拠も診断しない。

これはホスト側の制御・診断接続の確認であり、ダミーの停止記録を実QEMUの回収証明には使わない。
実guestの結果とは分けて検証記録へ記載する。
Linux x86_64／KVMの既知の期限超過は、今回の診断接続では根本原因が確定・解消したとは扱わない。
第三者試用とk8sは対象外。

## 2026-10-18の検証記録

Mac ARM64とLinux x86_64の固定Nix環境（Rust 1.98.1／Python 3.14.7）で
既存の `scripts/check.sh` が成功した。Linuxのみのnetns単体試験16件も成功した。
統合試験に回収順序の確認を追加した最終版は、両環境で8件すべて成功した。
MacでNixビルドした配布用controllerでも、ケース不一致・timeout・正常復帰を確認した。
Linuxの既存依存キャッシュを使用し、一時起動した専用builderを元の停止状態に戻した。
他の稼働containerの起動時刻は変わっていない。

着手・再確認時のGitHub APIではPR #13は `OPEN` で、origin/mainは
`5b26eff49b0a8993dd169fc1dfbf2af5ed30ff32` のままだった。
今回の作業枝 `feat/netns-auto-diagnosis` は診断コードを含む
`773c1235bf2ef7a66cb31b050d178d6b2eba6202` を基点とする。マージ操作は行っていない。
これはPR #13マージ後のmainで確認したという記録ではない。

ログと試験入力は `artifacts/auto-diagnosis-20261018-dctcri9e/` に保持する。
最初のsource archiveと7件版の全体チェックのログも残し、回収順序の確認を加えた
最終8件版の統合試験ログを別名で保持した。期限・期待値は緩めていない。

PR準備時にCIのbuild containerがrootで動くことを確認したため、統合試験の入口に限り、
root起動時は補助groupを空にしてUID/GID 65534へ降格する処理を追加した。
本番workflowのroot拒否は維持する。最終の試験コードはMac通常ユーザー、Linux通常ユーザー、
Linux root起動からの降格のそれぞれで8件成功した。実行コードとguestに追加変更はない。

SSDの既存guest bundleから新しい正式入口を準備し、Mac ARM64／TCGの新規VMで
dailyの16ケースが期待結果と一致した。正常時のコンソール出力は1行で、診断は呼ばれなかった。
`vm-stop.json` は証拠の停止前同期、QEMU終了0・回収済み、escalationなしを示した。
Linuxの実KVM VMで追加の受け入れをした結果ではない。
[公開用の検証集計](validation/netns-auto-diagnosis-20261018.json)にソースのhashと確認範囲を残した。

## watchdogの意図した停止と検証失敗の区別

PR #14のレビュー対応では、watchdog fixtureの検証報告が欠けたときに、
意図した停止を `vm_lifecycle_failure` と表示する問題を修正した。
`watchdog-daily`／`watchdog-acceptance` の両方で修正前の誤分類を再現した。

workflowは検証前から `watchdog_fixture` と `expected_exit_code` をrunへ記録する。
診断はfixture・期待終了3・実際の終了3・watchdog停止の記録が揃う場合、
`watchdog_evidence_verification_failure` としてworkflowの検証エラーと証拠の場所を表示する。
中断fixtureに完成したバッチ結果を要求せず、検証報告・停止時点の進捗などの欠落を示す。
これは停止証拠を合格へ再判定するものではなく、元の終了3を維持する。
予定外の停止理由はVM停止の異常として残し、検証済みfixtureと正常再実行は成功のまま扱う。

追加の3試験を含む統合試験11件で、両watchdogモードの報告欠落、予定外の停止、
正しく検証されたwatchdogと後続正常VMの制御を確認する。すべてダミーランチャーでの確認であり、
実VMのwatchdog試験を再実行した結果とは区別する。期限・期待値・資源回収処理は変更しない。

Mac／Linuxの固定環境でチェック一式が成功し、Linuxのroot起動からの降格でも11件成功した。
[レビュー対応の検証集計](validation/watchdog-diagnosis-review-20261018.json)に再現・ソースhash・確認範囲を残した。

## 証拠検証後の受け入れ条件違反

続くレビュー対応では、`fresh-watchdog` の個別証拠が正しくても、boot IDの重複や
入力hash不一致で受け入れに失敗した場合の診断を区別した。
修正前は、前のboot IDとの重複・以前のboot IDの再使用・入力hash不一致の3条件とも
watchdog証拠の検証失敗と誤分類していた。

runへ `evidence_verification`（`not_started`／`pending`／`passed`）を記録し、
証拠検証が成功した直後、VM間の受け入れ条件を調べる前に `passed` とする。
この状態で全体が失敗した場合、診断は `acceptance_invariant_failure` として
workflowの条件違反とboot ID／起動入力の証拠を示す。証拠検証成功だけではrunを合格にしない。
検証報告欠落は引き続き `watchdog_evidence_verification_failure`、予定外の停止はVM異常のまま。

統合試験13件には上記3条件の拒否と、4つの異なるboot ID・同一起動入力での
`watchdog-acceptance` 成功を含む。実VMの再起動ではなくダミーランチャーによる制御の確認で、
元の終了3、期限、期待値、VM回収処理は維持する。

Mac／Linuxのチェック一式とLinuxのroot起動からの降格で、統合試験13件が成功した。
[追加レビューの検証集計](validation/watchdog-acceptance-review-20261018.json)に確認範囲を記録した。

## watchdog回収異常の優先表示

停止報告の `status`／`reason` が意図したwatchdog停止でも、回収・同期が正常とは限らない。
診断の上書き前に、watchdog停止のschema、終了状態、QEMU回収・終了コード、escalation、
停止前／後の証拠同期、同期エラー、fixture・checkpointの各項目を確認する。
必要な項目が欠ける場合やJSON型が違う場合も、正常な停止として扱わない。

異常な項目があれば `vm_lifecycle_failure` と項目名を優先表示し、証拠検証失敗や
受け入れ条件違反の表示では上書きしない。停止報告自体がなければ証拠不足を示す。
個別証拠の検証段階が `passed` と記録されていても、この停止報告の異常を優先する。
正常な停止・回収の記録が揃う場合に限り、検証失敗と受け入れ条件違反を区別する。

修正前には6種類の回収・同期異常を両watchdogモードで誤分類することを再現した。
統合試験14件にそれらの異常を追加し、元の終了3と後続VMを起動しないことを確認する。
診断の単体試験では停止報告の12項目について欠落／型違い／不正な値を、検証段階の前後で確認する。
これらはダミーの証拠による診断試験であり、実QEMUを未回収にする試験は行わない。

Mac／Linuxのチェック一式、最終Python試験64件、Linux root起動からの統合試験14件が成功した。
[回収異常の検証集計](validation/watchdog-lifecycle-review-20261018.json)に再現と確認範囲を残した。

## 通常VMの停止異常と部分的なバッチ証拠

PR #13へのレビュー対応では、通常VMの停止異常を検出済みの場合、バッチ報告を読まずに
`vm_lifecycle_failure` を保持するようにした。修正前は壊れた部分報告で診断不能となり、
有効な報告内のケース失敗・timeoutでは停止異常の表示が上書きされていた。
停止報告とランチャー／serialログへの参照は残し、証拠・元の終了コードは変更しない。

統合試験15件には上記3種類の子証拠との組み合わせを含む。
単体試験では、検証前後のマーカーと、壊れた報告・失敗ケース・timeout・欠落した報告の
組み合わせでも停止異常を保持し、バッチ報告を欠落一覧へも追加しないことを確認する。
初回Mac試験では追加したパス比較が `/tmp` と `/private/tmp` の別名を区別してしまったため、
参照先を正規化した完全一致へ修正した。初回ログを保持し、分類の期待値は変更していない。
今回は診断とダミー試験のみの変更で、guestやVM回収コード、期限、受け入れ条件は変更しない。

Macの最終チェック一式、Linuxの同条件での再確認、root起動からの統合試験15件が成功した。
Linux初回は既存Rust fixtureの起動が `Text file busy (os error 26)` で失敗した。
summaryとfixtureを保持し、期限・期待値・並列設定を変えず1回再確認すると成功したが、
起動エラーの原因は未確定であり、本修正で解消したとは扱わない。
[通常VMの検証集計](validation/ordinary-lifecycle-review-20261018.json)に範囲と制約を記録した。
