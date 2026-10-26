# v0.4.0の公開確認手順

2026-10-26 JST。v0.3.0以降の追加入口とMac/Linuxの受け入れをまとめるminorソース版。
Rust本体・VM controller・各lockfileの自パッケージ・Nixの2パッケージを0.4.0へ揃える。
依存グラフ、固定lock、guestコード、資源・期限・期待値は変更しない。

## 実VM証拠

[Macの現行統合/lifecycle](MAC_TWO_VM_VALIDATION.md)、[Linux統合](HP_UBUNTU_VALIDATION.md)、
[Linux lifecycle](F2_LIFECYCLE.md)、[Warden policy](WARDEN_MAIN_VALIDATION.md)、
[Warden非ロード](WARDEN_NONLOADING_VALIDATION.md)を個別の固定入力で確認している。
今回の版上げそのものを、新規guest実起動の証拠とはしない。標準1 VMと追加2 VMの区別を維持する。

## 対象コミットと検査

1. PR #38のマージ後main CIを対象SHAで確認する。
2. 0.4.0候補で既存cacheを使い`nix develop --offline --command bash scripts/check.sh`を実行。
   `nix build --offline --no-link .#checks.aarch64-darwin.arena .#checks.aarch64-darwin.vm-controller`で両パッケージも確認する。
3. 両lockfileを旧mainと比較し、自パッケージ版以外の依存不変を確認。Mac受け入れのruntimeソースhashを再照合する。
4. 公開準備PRの対象head CI・レビューを確認してマージ。マージ後mainのCIと対象SHAを別に確認する。
5. 同じコミットへ既存SSH署名設定でannotated tag `v0.4.0`を作成し、`git verify-tag`で検証する。
6. 既存タグを動かさず新規タグだけpush。GitHubが生成するタグのソースアーカイブを検査する。
7. [正式ノート](releases/v0.4.0.md)を使い、draft/prereleaseではないソース版リリースとして公開する。

ソースアーカイブではLICENSEとreference/LICENSEを確認し、artifacts/.git/target・VM画像・disk・PCAP・資格情報を含めない。
検査・CI・署名・公開の結果は作業証拠へ保存し、準備PRのCIをマージ後CIや実VM証拠へ読み替えない。

## 保持する範囲

既存タグ・公開版、stash・別作業・cache・過去の証拠を保持する。
agent側停止注入・panic/電源断・過去Linux netns期限超過の原因調査は追加TODOのまま。
署名タグ・公開は今回のユーザー承認範囲で行い、物理環境・第三者試用・新しい機能へ作業を広げない。

## 版上げ後のローカル確認結果

Mac ARM64の`check.sh`と両Nixパッケージのoffline buildは成功。
両lockfileの依存グラフは自パッケージ版以外で不変、Mac実VM受け入れ記録のruntime hashと一致した。
[機械可読記録](validation/v0.4.0-preparation-20261026.json)はこの候補ソースのローカル結果。
PR CI・マージ後CI・署名・GitHubアーカイブ・公開状態は対象コミットごとに別途照合する。
