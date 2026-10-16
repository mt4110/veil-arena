# 次回ソース版リリースの準備

2026-10-16。公開前の作業記録。版番号は **v0.2.0**。
v0.1.0以降に反復・診断の入口、NetEm比較、SSD正式入口を追加したため、
修正だけのpatch版ではなくminor版とした。Rust・controller・両lockfile・Nixを0.2.0へ揃えた。
タグ作成とリリース公開はまだ行っていない。

## 対象と完了条件

版上げ前に確認したmainは `87af46caec6fd6746f70b52f27b731f9be1c62a1`。
PR #11のマージ後で、[mainのCI](https://github.com/mt4110/veil-arena/actions/runs/38896609813)
は成功している。CIは静的・単体検査とNix評価であり、VM起動とは別の証拠である。
このmainを基点に版を揃え、0.2.0のホスト入口と検証済みguest bundleの組合せを確認する。
最終公開対象はPRマージ後のコミットとし、そのmainのCIを確認してから署名タグの承認を得る。

## 版上げ前mainのSSD確認

上記mainから `scripts/prepare-netns-from-guest.sh` を実行し、以前と同じ
`/nix/store/bkjimwbnb05vh60zmwqikwzvkr2m3h7a-arena-netns-prepared` を再利用した。
guest・kernel・依存・ホスト入口の再ビルドは発生せず、試験中のbuild/fetchもない。
正式な `arena-netns-volume` の `daily` で、新規VM 1回・16ケースが期待結果と一致した。
所要時間はworkflowの記録で292,897ms。比較性能や工数削減の測定値とは扱わない。

- NetEm全損失のcount phaseで、qdisc drop増分8、eBPF pass計8/drop計0、受信0。
- NetEm解除後のdrop phaseで、対象4をdrop、対照4は到達。
- 設定不成立はcount phaseの送信前に環境エラー。所有qdiscは解除済み。
- 後続の正常TCケースでcount時4/4到達、限定drop、detach後4/4到達を確認。
- 停止前の証拠同期、QEMU終了コード0・escalationなし・回収済み。
  所有QEMU PIDが残っていないことも確認した。

追加の受け入れVMは今回起動していない。正式入口の既存2 VM受け入れ記録と、
今回のマージ後mainでの再実行を別の証拠として扱う。
新規cold install、Linux x86_64／KVMの現在の16ケース受け入れ、物理SSD抜去は確認していない。

証拠は `/Volumes/Elecom-1TB/Linux/veil-arena/release-readiness-20261016-87af46c`。
`daily-001/summary.json` と `daily-001/daily/vm-stop.json` が全体判定と回収結果。
2,854ファイルのSHA-256を `evidence-manifest.json` に保存し、Finderの `.DS_Store` を除外した。
[公開用の集計](validation/release-readiness-20261016.json)にも対象main、CI、起動入力hash、
証拠manifestのhash、残る条件を記録した。

## 0.2.0ホスト入口の受け入れ

版上げ後に作成した準備出力は
`/nix/store/s90v67j9f7nqg68616abf3913cfwahn3-arena-netns-prepared`。
controller、manifest、launcher、workflow等の7 derivationだけをビルドし、
guest・kernel・参照eBPF・既存の依存cacheを再利用した。
manifestの `host_controller` は0.2.0、再利用guestの `arena_package` は0.1.0である。
今回ソースから新たにビルドした0.2.0 guestの実起動結果とは扱わない。

正式な `arena-netns-volume` の `acceptance` で新規2 VM・各16ケース、計32件が期待結果と一致した。
異なるboot IDと同じ起動入力hashを確認した。両VMで次を照合した。

- NetEmのdrop増分8、eBPF pass 8/drop 0、受信0。
- 設定不成立はcount phaseの送信前に環境エラー。所有qdiscは解除済み。
- 後続TCのcountとdetach後で対象4/対照4が到達し、限定dropでは対象0/対照4が到達。
- 停止前の証拠同期、所有QEMUの正常終了・回収・PID不在。

固定Nix環境の `scripts/check.sh` も成功した。
Rust 10件、controller 4件＋workflow 5件、Python 54件、fmt・Clippy、3 systemのNix評価を含む。
依存グラフ・固定Nix入力・実行コードは変更していない。
GitHubのLinux x86_64 CIは、公開準備PRの最終headのChecksで確認する。
CIはVM内の通信試験やLinux x86_64／KVMの期限超過の解消を証明しない。

証拠は `/Volumes/Elecom-1TB/Linux/veil-arena/v0.2.0-preparation-20261016`。
`acceptance-001/summary.json` が集計、各VMの `vm-stop.json` が停止・回収結果。
5,697ファイルを `evidence-manifest.json` のSHA-256で照合した。
[版上げ後の公開用集計](validation/v0.2.0-preparation.json)に、検証した実行入力40ファイルのhash、
起動入力、実行環境、確認範囲を保存した。これらの入力がコミット後も一致することを照合する。

## 過去レビューの確認

マージ済みPR #1〜#11を確認し、未解決マークは次の2件だった。
GitHubのマークは変更していない。

- [PR #1のCargo版比較](https://github.com/mt4110/veil-arena/pull/1#discussion_r4241053782):
  固定Nixpkgsの `pkgs.cargo.version` は `1.98.1`、実行バイナリは `cargo 1.98.0`。
  `flake.nix` はpackage metadataを `versions.rust` と照合し、runnerはバイナリの
  `cargo --version` を `cargo 1.98.0` と照合している。指摘どおりmetadataを `versions.cargo` と比較すると
  現行の固定入力ではassertionが失敗するため、変更しない。
- [PR #2の19段階への文書更新](https://github.com/mt4110/veil-arena/pull/2#discussion_r4241453543):
  `docs/NIX_VM.md` の現行手順は19段階へ更新済み。明示的な過去の18段階の検証結果は保持する。

## 公開物と版の整合

ソース版を継続し、GitHubのソースアーカイブのみを配布する。
VMイメージ・依存バイナリ・生ログ・PCAP・ローカルartifactは同梱しない。
mainの `git archive` でLICENSE、参照eBPFのLICENSE、README、flake.lock、
今回の共通試験fixtureが含まれ、artifactsと.gitが含まれないことを確認した。
自動生成されるGitHubアーカイブそのものの取得検証は、タグ作成後の確認となる。

以下を0.2.0へ揃えた。固定依存版とguestの実行コードは変更していない。

- `Cargo.toml` と `tools/vm-controller/Cargo.toml` のpackage version
- 両方の `Cargo.lock` の自パッケージversion
- `flake.nix` のarenaとcontrollerのversion
- リリースノートの版と、最終公開コミットの証拠

版上げで変わったホストcontrollerと入口は別のNix出力で検証する。
guest bundleは再ビルドせず、既に検証済みの固定入力を再利用する。
これは0.2.0のソースから新しくビルドしたguestの受け入れではなく、
0.2.0のホスト入口と既存guestとの互換性確認である。
再利用bundleの `arena_package` は0.1.0のまま保持する。配布にguest imageは含めない。
現在の16ケースに関するLinux x86_64／KVMの期限超過は、既知の制約として明記して進める方針が承認された。
この環境の受け入れ成功や、期限超過の解消を意味しない。

## 署名タグの準備

Gitの署名形式はSSH。設定された公開鍵のfingerprintは
`SHA256:i9id47kAF+P8PHfopi0n1u7sopVYscp94HVYXONPlk8` で、
既存の `v0.1.0` タグの署名を同じ鍵で検証できた。
準備初回のagent一覧にはこの鍵が含まれていなかった。
秘密鍵の内容は読んでおらず、v0.2.0タグの署名は未実行である。
タグ作成承認後に利用可能な署名経路・必要な認証を最終確認する。鍵やagent設定は変更しない。

以下は**公開承認後の手順案**であり、まだ実行していない。
`<final-commit>` は本PRのマージ後にmainのCIと証拠を照合したコミットへ置き換える。
既存タグがある場合は上書きせず停止する。

```sh
git fetch origin
git tag -s v0.2.0 <final-commit> -m 'Veil Arena v0.2.0'
git verify-tag v0.2.0
git rev-parse 'v0.2.0^{commit}'
# 対象コミット・CI・署名を照合してから
git push origin refs/tags/v0.2.0
gh release create v0.2.0 --verify-tag --title 'Veil Arena v0.2.0' \
  --notes-file docs/releases/v0.2.0-draft.md
```

ノートの内容とタグ上の参照先を最終確認してから最後のコマンドを使う。
ノート内のv0.2.0へのリンクはタグ作成後に有効になるため、公開前に参照先を確認する。
今回の作業はPR作成までとし、マージ・タグ作成・リリース公開は別途承認を得る。
