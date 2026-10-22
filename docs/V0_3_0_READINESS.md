# v0.3.0ソース版の公開準備

2026-10-22 JST。最終ゴールは、他のVeilの変更を繰り返し判断できるVM・仮想ネットワーク基盤。
今回は1 VM標準の到達点を公開可能なソース版へまとめる。機能追加・VM再受け入れ・マージ・署名タグ作成・公開は行わない。

## 基準mainと版番号

基準は `f93888a2fa2c7543fd2b68dfb9b4b699f10abc44`（PR #31マージ）。[対象main CI](https://github.com/mt4110/veil-arena/actions/runs/39698485493)は成功。
最新公開版はv0.2.1。K3s・Keeper接続を含む追加機能を区切るminor版としてv0.3.0を選ぶ。
Rust本体・VM controller・両lockfileの自パッケージ・Nixの2パッケージを0.3.0へ揃える。
依存グラフ、Rust/Python/kernel等の固定版、guest実行コード、資源・期限・期待値は変更しない。

## 標準1 VMの完成条件との照合

| 条件 | 証拠・境界 |
| --- | --- |
| 固定入力・隔離VM・再利用入口 | [F0契約](F0_CONTRACT.md)、[固定入力](contracts/f0-inputs.json)、[F1手順](F1_K3S.md) |
| 両ホストのPod・Service DNS・TCP/UDP、再成功・回収 | [Linux最終F1](F1_LINUX_STANDARD_VALIDATION.md)、[Mac最終F1](F1_MAC_STANDARD_VALIDATION.md)。記録の修正3ファイルは基準mainとhash一致 |
| 実Veilの正常・拒否不成立対照・限定拒否・解除後復帰 | [Keeper記録](KEEPER_F3_VALIDATION.md)、[Linux F3記録](F4_ENTRY_VALIDATION.md)。固定AppArmor smokeのみ |
| 中断・watchdog・部分証拠・回収・正常復帰 | [Linux checkpoint](F4_LIFECYCLE_VALIDATION.md)、[Mac標準記録](validation/f4-standard-one-vm-mac-20261021.json) |
| 本人操作 | [Linux本人4 run](validation/f4-owner-lifecycle-20261021.json)。Mac本人再実行は任意 |
| 最終修正の取り込み | PR #30マージ `db4beac`、[main CI成功](https://github.com/mt4110/veil-arena/actions/runs/39681687718)。引継ぎPR #31とそのmain CIも成功 |

合意した標準1 VMの必須条件は完了。2 VMの追加検証は標準完成の必須条件へ戻さない。
既存文書の「取り込み・CI待ち」は当時の記録として保持し、完成計画と標準判定に今回の最終照合を追記する。
過去の受け入れ証拠を、版上げ後の新規guest成功・cold install・すべての入力の保証へ一般化しない。

## 今回の検証

版整合性、依存不変、既存実VM記録とのhash対応、固定Nix環境のチェック、ソースアーカイブ内容を確認する。
検証結果・ソースhashは[機械可読記録](validation/v0.3.0-preparation-20261022.json)へ保存する。
PRの対象head CIはコミット後に確認する。マージ後main CIと対象SHAの照合は承認後の工程であり、PR CIと混同しない。
今回、新規VM起動・guest再ビルドや保存済みraw証拠の再検証は行わない。既存cache・VM証拠・所有者の未コミット作業を保持する。

## 版上げ後のローカル結果

Mac ARM64の固定Nix環境で、既存Cargo target/cacheを再利用した `scripts/check.sh` は終了0。
fmt・Clippy、Rust本体10件、controller 4件・workflow 5件、診断統合1件（内側15件）、Python discover 169件（Linux procfs専用1件skip）、3 systemのNix定義評価が成功した。
`nix build --offline --no-link .#checks.aarch64-darwin.arena .#checks.aarch64-darwin.vm-controller` も終了0。0.3.0の両パッケージを既存store/cacheからビルドした。
依存グラフはlockfileの自パッケージ版以外で不変。F1受け入れ記録の3ファイルのhashも一致する。
新規文書の参照先と、ステージしたソースアーカイブに両LICENSEが入り、artifacts/.git/target・VM disk/image・PCAPが含まれないことを確認した。
これはタグ前の候補ソース検査であり、GitHubのタグ付きアーカイブは公開承認後に別途検査する。
PR #30・#31の未解決レビューは今回の確認時点で0件。公開準備PR自身の最新CI・レビューは別に確認する。

## 公開範囲と保留条件

[リリースノート案](releases/v0.3.0-draft.md)はv0.2.1からの変更と既知の制約を説明する。
ソース版のみ。LICENSEとreference/LICENSEを含め、VMイメージ・依存バイナリ・artifacts・資格情報は配布しない。
既存のv0.2.1 releaseは追加assetなし。v0.3.0でも手動のバイナリasset追加は予定しない。
新しいソースアーカイブの検査と、タグ作成後にGitHubが生成するアーカイブの検査は別工程とする。

署名方式は既存repo設定のSSH。signingkey設定があることだけを読み取り確認した。
署名実行・署名鍵の利用可能性の実証・v0.3.0タグ作成はまだ行っていない。承認時に対象SHAと署名・検証方法を再確認する。

保持するTODO：Linux資源不足の2 VM統合受け入れ、Macの2 VM統合新規受け入れ、2 VM checkpoint、過去のLinux/KVM期限超過の原因未確定。
物理環境・第三者試用・新PC/SSD変更は今回の公開準備から再開しない。

公開前の残工程は、公開準備PRのレビュー・マージ承認、マージ後mainの対象SHA/CI確認、署名タグ・公開の明示承認、署名検証と生成ソースアーカイブの確認。
既存タグ・リリースを上書きせず、現時点でマージ・署名タグ作成・公開しない。
