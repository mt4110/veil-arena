# v0.2.1候補の公開前確認

2026-10-19 JST。最終ゴールは、Veilの変更の正否を繰り返し判断できる検証基盤。
今回は取り込んだ診断機能と試験fixture修正を照合し、公開前の残項目を明確にする。
タグ作成・リリース公開・第三者試用・k8sは行わない。

## mainとレビュー

確認したmainは `dbe0edc76f9005cd6b4c995f8ceb019626ed4df8`。
PR #15のmerge commitと一致し、[mainのCI](https://github.com/mt4110/veil-arena/actions/runs/39140468729)は同じコミットで成功した。
ログ上でRust 10件・netns 16件・controller 4件・workflow 5件、診断統合15件、Python 65件と、
fmt・Clippy・Nix評価の成功を確認した。これはVM実起動とは別の証拠である。

PR #1〜#15のレビュースレッドを確認し、#3〜#15の未解決マークは0件だった。
過去の未解決マークは次の2件。GitHub上のマークは変更しない。

- [PR #1のCargo比較](https://github.com/mt4110/veil-arena/pull/1#discussion_r4241053782)：
  固定Nix入力で再評価するとpackage metadataは1.98.1、固定shellの実行バイナリは1.98.0。
  現行の `pkgs.cargo.version == versions.rust` とrunnerのバイナリ版期待値は整合しており、
  提案どおりmetadataを `versions.cargo` と比較すると一致しない。今回の公開阻害要因とは判断しない。
- [PR #2の段階数](https://github.com/mt4110/veil-arena/pull/2#discussion_r4241453543)：
  現行 `docs/NIX_VM.md` の実行・検査契約は19段階へ更新済み。過去の18段階の記録は保持する。

## 版上げ前mainのSSD入口

既存bundleを `scripts/prepare-netns-from-guest.sh` に渡し、
`/nix/store/parw0xwnn6gir83x8igjfb0zbz05hrg8-arena-netns-prepared` を準備した。
既存のguest・kernel・controller・依存を再利用し、ホスト入口とmanifest等5 derivationのみをビルドした。
準備と試験を分け、試験中のbuild/fetchは行っていない。ColimaやLinux側builderの起動も不要だった。

Mac ARM64／native QEMU・TCG、4GiB・2vCPU、外向きNICなし、APFS SSD上で
正式な `arena-netns-volume ... daily ...` を実行した。
新規VM 1回・16ケースの終了コードがすべて期待値と一致し、workflowは終了0／passed。
所要時間291,184msは今回1回の記録であり、性能改善や工数削減の測定値ではない。

- NetEm全損失：qdisc drop 8、eBPF pass 8／drop 0、受信0。
- NetEm解除後：対象4をeBPFでdrop、対照4が到達。
- 設定不成立：countの受信報告を作る前に設定不一致を検出。
- 中断・timeout・NetEm後のTC試験：count時とdetach後に対象4／対照4が到達。
- netnsケースのhelper回収・namespace参照消失・親ネットワーク不変、BPFケースのunload、
  中断ケースのcleanup検証が成功。
- 停止前の証拠同期、QEMU終了0、escalationなし、回収済み・所有PID不在を確認。
- 正常時の自動診断表示は発生せず、手動の読み取り専用診断でもpassed／問題なし／証拠欠落なし。

証拠は `/Volumes/Elecom-1TB/Linux/veil-arena/v0.2.1-readiness-20261019`。
`daily-001/summary.json` と `daily-001/daily/vm-stop.json` が全体判定と回収結果。
新しい結果の2,847ファイルをSHA-256 manifestで照合し、Finderの `.DS_Store` を除外した。
以前の結果、guest bundle、GC root、依存キャッシュは削除・付け替えしていない。
[公開用集計](validation/v0.2.1-readiness-20261019.json)に対象コミット、入力・manifestのhash、残項目を記録した。

補助照合スクリプトの初回実行では、全ケースに同じcleanup形式があると仮定したため、
BPFケースでKeyErrorになった。VM試験自体の失敗ではない。
netns・BPF・中断の各報告形式に応じた照合へ修正して再確認し、試験報告と期待値は変更していない。

## 公開内容との照合と残る条件

[ノート案](releases/v0.2.1-draft.md)はv0.2.0以降の診断追加、レビューでの誤分類修正、
ETXTBSY対策を含める。ソース配布方針とMIT／参照eBPF GPL-2.0-onlyを継続する。
今回のmainの `git archive` にLICENSE、参照eBPFのLICENSE、固定fixture、診断スクリプトが含まれ、
artifactsと.gitが含まれないことを確認した。

Linux x86_64／KVMの期限超過の根本原因は未確定で、現在のソースによる追加fresh VMを含む受け入れも未完了。
今回のMac試験を、その解消の証拠にはしない。
再利用guest内のArenaは0.1.0、ホストcontrollerと現行mainのpackage versionは0.2.0であり、
v0.2.1の最終ソースや新規ビルドguestを受け入れたという記録ではない。
今回の異常系診断はmain CIのダミーfixture回帰で確認し、実VMの異常停止は追加注入していない。
追加2 VMのacceptance、cold install、IPv6 wire、HVF、SSD稼働中抜去・同時書換えも今回の対象外。

版上げ前の確認では新たな公開阻害要因は検出しなかった。
このmainでの成功と、以下の0.2.1へ版を揃えた後の確認は別の証拠として扱う。
マージ・署名タグ・公開はそれぞれ承認後とする。

## 0.2.1へ版を揃えた後の確認

公開準備枝でRust・controller・両lockfileの自パッケージ・Nixの版を0.2.1へ揃えた。
lockfileの依存グラフ、固定ツール、guest実行コードは変更していない。
Macの固定Nix環境で `scripts/check.sh` が成功した。
Rust 10件・netns 16件・controller 4件・workflow 5件、診断統合15件、Python 65件と、
fmt・Clippy・3 systemのNix評価を含む。

既存bundleから準備した入口は
`/nix/store/if444bq5bf38clai6bq1rwpn4rg5f416-arena-netns-prepared`。
0.2.1のcontroller、lockfile更新に伴うvendor-dir、manifestと入口等の計7 derivationをビルドし、
Nixパッケージ内のcontroller 4件・workflow 5件も成功した。
guest・kernel・既存の依存cacheは再利用した。試験中のbuild/fetchはない。

版上げ後の正式SSD入口のdailyで、新規VM 1回・16ケースすべてが期待結果と一致した。
所要時間288,019ms。NetEm drop 8、eBPF pass 8／drop 0、受信0、解除後の対象drop 4／対照到達4、
設定不成立の送信前検出と、後続count／detach後の対象4／対照4到達を照合した。
ケースごとの資源回収、停止前の証拠同期、QEMU終了0、escalationなし、回収とPID不在も確認した。
正常時のコンソールはpassedの1行で、手動診断も問題なし・証拠欠落なしだった。

証拠は `/Volumes/Elecom-1TB/Linux/veil-arena/v0.2.1-preparation-20261019`。
2,847ファイルのSHA-256をmanifestで照合した。以前のmain確認と今回を合計2 VMのacceptanceとは扱わず、
それぞれのソース・入口によるdaily 1 VMの確認として記録する。
[版上げ後の公開用集計](validation/v0.2.1-preparation-20261019.json)には実行・ビルド・試験入力47ファイルのhashを保存した。
コミット後にも照合し、PR headのGitHub CI結果はPR本文に記録する。
今回の結果を新規ビルドguest、Linux x86_64／KVMの期限超過解消、第三者試用やk8sの成功へ一般化しない。

公開前に残す確認は、本PRのレビュー・取り込み後の対象コミットとmain CIの照合、
同コミットへの署名タグとソース版公開の承認である。
ソース版のGitHubアーカイブそのものはタグ作成後に検査する。既存タグ・リリースの上書きは行わない。
