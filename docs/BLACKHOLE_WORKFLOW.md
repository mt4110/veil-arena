# Blackholeの反復試験を1つの入口から実行する

M3の日常利用向け。既存のRust `blackhole-offline` runnerを使い、準備・反復・証拠集計をまとめる。Blackholeのソース変更やbaseline snapshotの作成は自動で行わない。PR作成・公開・VM起動・cache削除も行わない。

## 日常の入口

```sh
bash scripts/blackhole-workflow.sh --project ../veil-blackhole
```

固定Nix環境でArenaをoffline buildし、Blackholeの19段階試験を既定2回実行する。2回とも正常、19段階の名前と順序が一致し、有効なソース／バイナリhashがあることを要求する。開始・終了のソースとバイナリが不変、反復間のソースとバイナリも同じであることを確認する。

Nixのexperimental featuresは入口で指定する。flakeの更新とNix/Cargoのネットワーク取得は行わない。必要なNix storeとCargo依存は事前に用意する。不足した依存を自動取得して試験を続けない。

## Cargoキャッシュを分ける

macOSの通常利用は既存の`CARGO_HOME`（未指定なら`~/.cargo`）を使う。LinuxでBlackhole用vendorを使う場合、Arenaのbuild用registryと混ぜない。

```sh
bash scripts/blackhole-workflow.sh \
  --project /path/to/veil-blackhole \
  --arena-cargo-home /path/to/arena-cargo-home \
  --project-cargo-home /path/to/blackhole-cargo-home \
  --cache /path/to/reusable-workflow-cache \
  --artifacts /path/to/artifacts
```

`--project-cargo-home`は事前に用意したCargo configとvendorを指す。共有configを書き換えない。cacheにはrunnerのtargetと、Rust runnerが管理するcheckout別targetを保持する。同じworkflow cacheの同時使用はlockで拒否する。cache/outputはBlackholeチェックアウト外に置き、symlinkを含む保存先は拒否する。

既定cacheはArenaの`target/blackhole-workflow/`、保存先は`artifacts/`。cold buildの時間とwarm runの時間は区別する。既存の別入口のtargetやVM imageは削除・再配置しない。

## 変更前後を確認する

事前に、修正前の動作と新しい回帰試験を含む信頼するGitチェックアウトを用意する。元の古い版に回帰試験がない場合、その成功を「不具合検出」と呼ばない。今回の保存済み再現用snapshotでは次のように実行した。

```sh
bash scripts/blackhole-workflow.sh \
  --project ../veil-blackhole \
  --before artifacts/m3-fixture-open-20261011/before-project \
  --before-failure rust-tests \
  --before-contains 'test fixture_open_tests::symlink_replacement_after_preflight_is_rejected ... FAILED'
```

beforeは指定段階で失敗し、指定した回帰試験のログが含まれることを要求する。引数エラー・環境エラー・別の試験失敗を代用しない。その後に修正後の2回を確認する。想定外の結果で停止し、後続はsummaryの`not_run`に残す。失敗を黙って再試行しない。

`--repeat`は1〜5、`--timeout-seconds`は1〜3600（既定300）。期限は準備と各Rust suiteごと。suiteの外側ではRustの終了猶予に5秒を加える。SIGTERM／Ctrl-Cと期限超過では、補助スクリプトが起動したprocess groupの停止を試みる。Rust自身による子process管理・2 MiBログ上限は既存のまま。prepのログには同じサイズ上限を設けていない。SIGKILL・host停止などで最終summaryがない場合は、eventsと途中のログを完了結果に読み替えない。

## 集計を読む

```text
artifacts/blackhole-workflow-<unique>/
  manifest.json              入力、期待する失敗、期限、Cargoキャッシュの指定
  events.jsonl               準備・各suiteの開始と結果
  summary.json               全体判定、各suiteの実結果と期待値への一致、時間
  preparation.*.log          Arenaのoffline buildログ
  before/                    修正前のRust runner証拠（指定した場合）
  healthy-1/, healthy-2/     正常試験のRust runner証拠
  before.*.log, healthy-*.*.log
```

各runは新規領域へ保存し、既存の証拠を上書き・削除しない。JSONとログは0600、runディレクトリは0700。

beforeの`runner_status=failed`は実際の試験失敗。行の`status=passed`は指定した失敗を検出できたという評価であり、Blackholeの試験成功ではない。通常runはどちらも`passed`になる。`report`は各Rust結果への相対パス。

全体の終了コードは0=期待値一致、1=不一致、3=環境エラー、4=期限超過、130=中断。引数不正は2。環境エラーでsuiteを開始しなかった場合は、正常試験が0件だったこともsummaryに残る。

`duration_ms`はworkflowの測定時間、各runの時間はRustが測定したもの。`automated_command_count`は準備とRust runner呼び出しの外側の件数であり、Rust/Cargo内部の全process数や人の操作数ではない。手動baselineは未取得なら`null`とする。時間短縮や人的工数削減をこれだけから算出しない。

対象は信頼するローカルのArenaとBlackhole。build scriptやテストコードをOS sandboxへ閉じ込める機能ではない。Live取得・送信・OSネットワーク変更は入口の機能に含めない。

実課題の修正結果は[M3のfixture検証](M3_FIXTURE_OPEN_VALIDATION.md)、この入口自体の検証は[M3運用検証](M3_WORKFLOW_VALIDATION.md)、後続の差分レビューと個別手順の比較は[M3実利用検証](M3_SELF_USE_VALIDATION.md)を参照する。
