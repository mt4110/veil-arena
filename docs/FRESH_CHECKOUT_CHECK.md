# 新しいチェックアウトで正常・失敗・復帰を確認する

既存の作業ディレクトリの未追跡ファイルやtargetをコピーせず、追跡ソースと準備済み依存だけで、Arenaのオフライン入口を再現する。第三者の試用は保留中で、この確認を第三者・新規端末での導入成功とは扱わない。

## 前提と再利用するもの

- 一般ユーザーで実行する。信頼するArenaとBlackholeのローカルGit履歴、固定Nix環境、Cargo依存を事前に準備する。[依存準備とCargo homeの分離](BLACKHOLE_WORKFLOW.md)を参照。
- 必要なら準備段階で各元リポジトリの`git fetch origin`を行い、調べた`origin/main`の完全なrevisionを控える。ネットワーク取得は以下のオフライン試験と分ける。
- Nix store、Cargo registry/vendor、既存workflow cacheを再利用する。VMは起動しない。新しいソースパスのため再コンパイルは発生し得る。cache再利用をコンパイルの省略と呼ばない。
- 同じworkflow cacheを使う先行試験が終了していることを確認する。lock・過去の証拠・cacheを削除して進めない。

以下はArenaルートで`bash`を起動してから、同じBashセッションで順番に実行する。既定cacheはチェックアウト内の`target/blackhole-workflow`なので、今回は**元のcacheへの絶対パス**を全試験へ渡す。Cargo homeも明示し、Linuxでvendor設定を使う場合は該当する2行を既存のパスへ変更する。未準備の依存は自動取得しない。

## 1. 固定revisionの新しい入力を用意する

```sh
set -euo pipefail
arena_source=$(pwd -P)
blackhole_source=$(cd ../veil-blackhole && pwd -P)
arena_revision=$(git -C "$arena_source" rev-parse refs/remotes/origin/main)
blackhole_revision=$(git -C "$blackhole_source" rev-parse refs/remotes/origin/main)
workflow_cache="$arena_source/target/blackhole-workflow"
arena_cargo_home="${CARGO_HOME:-$HOME/.cargo}"
project_cargo_home="$arena_cargo_home"
mkdir -p "$arena_source/artifacts"
trial_root=$(mktemp -d "$arena_source/artifacts/checkout-check-XXXXXX")
printf 'trial_root=%s\nArena=%s\nBlackhole=%s\n' \
  "$trial_root" "$arena_revision" "$blackhole_revision"

git clone --no-local --no-checkout "$arena_source" "$trial_root/veil-arena"
git -C "$trial_root/veil-arena" fetch "$arena_source" refs/remotes/origin/main
git -C "$trial_root/veil-arena" checkout --detach "$arena_revision"
git clone --no-local --no-checkout "$blackhole_source" "$trial_root/veil-blackhole"
git -C "$trial_root/veil-blackhole" fetch "$blackhole_source" refs/remotes/origin/main
git -C "$trial_root/veil-blackhole" checkout --detach "$blackhole_revision"
git -C "$trial_root/veil-arena" status --short --branch
git -C "$trial_root/veil-blackhole" status --short --branch
```

cloneとfetchはローカルの元リポジトリだけを読む。cloneだけでは元リポジトリの`origin/main`を転送できるとは限らないため、そのrefも明示的に取得する。元の未コミット変更・未追跡ファイルは含めない。新しい入力のstatusに変更があれば、原因を確認してから進める。

## 2. 正常な対象を2回実行する

```sh
cd "$trial_root/veil-arena"
bash scripts/blackhole-workflow.sh \
  --project ../veil-blackhole \
  --cache "$workflow_cache" \
  --arena-cargo-home "$arena_cargo_home" \
  --project-cargo-home "$project_cargo_home" \
  --artifacts "$trial_root/normal" > "$trial_root/normal.log" 2>&1
cat "$trial_root/normal.log"
```

終了0、通常試験2回、全体`passed`を確認する。表示されたsummaryとログを残す。環境エラーなら依存・版・cacheの診断を先に行い、既知の不具合として扱わない。

## 3. 既知の不具合を別snapshotで確認する

DNS予約Zビットを受理していた修正前コードへ、修正時に追加した試験だけを適用する。baseと試験の取得元を別々の固定commitで指定し、以後のBlackhole変更を混ぜない。公開用commitは作らず、baseの履歴と未コミットpatchをrunnerのソース識別に残す。

```sh
base=b620f99c56eb001ad2a6b5ce1f2f889424dfe12d
regression_revision=d5037b326beb9a29f3845ec8561ec1ada95f7cd7
git clone --no-local --no-checkout "$trial_root/veil-blackhole" "$trial_root/before-dns-z"
git -C "$trial_root/before-dns-z" checkout --detach "$base"
git -C "$trial_root/veil-blackhole" diff "$base" "$regression_revision" \
  -- tests/decode.rs > "$trial_root/dns-z-regression.patch"
test -s "$trial_root/dns-z-regression.patch"
cat "$trial_root/dns-z-regression.patch"
# 追加がDNS予約Zビットの回帰試験だけであることを確認してから続ける。
git -C "$trial_root/before-dns-z" apply --check "$trial_root/dns-z-regression.patch"
git -C "$trial_root/before-dns-z" apply "$trial_root/dns-z-regression.patch"
git -C "$trial_root/before-dns-z" diff --stat

set +e
bash scripts/blackhole-workflow.sh \
  --project "$trial_root/before-dns-z" \
  --cache "$workflow_cache" \
  --arena-cargo-home "$arena_cargo_home" \
  --project-cargo-home "$project_cargo_home" \
  --artifacts "$trial_root/failure" > "$trial_root/failure.log" 2>&1
failure_code=$?
set -e
cat "$trial_root/failure.log"
test "$failure_code" -eq 1
```

終了1だけでは再現成功としない。コンソールとsummaryで、`rust-tests`の`dns_reserved_z_is_malformed_while_ad_cd_remain_supported`が失敗し、実際は`Ok(DnsQuery ...)`、期待は`Err(Malformed("DNS reserved Z flag"))`、後続`healthy-2`は`not_run`であることを確認する。`execution.status=completed`は試験成功を意味しない。stdout／stderrとRust詳細報告へ辿れることも確認する。

固定commitがない、patchが異なる、別の段階で失敗した場合は再現未成立として止める。新しい不具合を探したり、正常版や期待値を書き換えたりしない。

## 4. 同じcacheで正常へ復帰する

```sh
bash scripts/blackhole-workflow.sh \
  --project ../veil-blackhole \
  --cache "$workflow_cache" \
  --arena-cargo-home "$arena_cargo_home" \
  --project-cargo-home "$project_cargo_home" \
  --artifacts "$trial_root/recovery" > "$trial_root/recovery.log" 2>&1
cat "$trial_root/recovery.log"
git -C "$trial_root/veil-arena" status --short --branch
git -C "$trial_root/veil-blackhole" status --short --branch
```

再び終了0、正常2回、全体`passed`を確認する。正常と復帰のsummaryで同じcacheと正常対象を使い、各runのソース識別・バイナリhashが一致することを確認する。3つの出力領域と失敗ログを保持し、前の結果を削除して復帰した扱いにしない。

終了後も新しいチェックアウト、snapshot、依存cache、証拠は残す。以後は同じ入力とcacheを再利用できる。これらの削除はこの手順の操作に含まない。

## 記録する範囲

OS/CPU、両revision、patchのhash、Nix/Cargo/workflow cacheの指定、3つのsummaryとログ、失敗した段階・実際と期待・未実行、正常版のソースとバイナリの不変性を記録する。初回の依存取得時間、別マシン、Linux、VM、Live通信、第三者の理解、時間削減・製品価値は、この手順を1回実行しただけでは確認済みにならない。

2026-10-16の実施結果は[新しいチェックアウトの検証記録](FRESH_CHECKOUT_VALIDATION.md)を参照する。
