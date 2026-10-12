# M3: マージ後mainの手順確認と本人チェック準備

2026-10-12 JST、表示改善前のmain確認記録。Arena main `bffa1dcd8acef304fdb0fba2f82738b285cc363b`、Blackhole main `d5037b326beb9a29f3845ec8561ec1ada95f7cd7`へfast-forwardして確認した。元の未追跡ファイルは保持した。この確認段階は本人向け文書と記録の準備だけで、runner・Blackholeコードは変更していない。後続の表示改善は[別の検証記録](M3_FAILURE_DISPLAY_VALIDATION.md)で扱う。

## 技術側の結果

Mac ARM64の固定Nix環境（Rust 1.98.1、Cargo 1.98.0、Python 3.14.7）と既存Nix／Cargo／workflow cacheを使用した。

- mainの`BLACKHOLE_WORKFLOW.md`からsnapshot準備コマンドをそのまま実行。修正前commit＋追加decoder回帰試験のsnapshotを新規作成した。
- 公開された一括入口で、そのsnapshotの指定失敗→正常mainの19段階×2回が成功。
- 正常mainだけの入口でも正常2回が成功。
- 同じsnapshotを通常の試験対象として実行すると、`rust-tests`のZビット回帰試験で終了1となり、`healthy-2`は`not_run`。この結果を本人の失敗診断用に用意した。
- その後、同じcacheで正常mainを再実行し、正常2回が成功。過去の失敗結果は残った。

担当者が正常試験を先行試験と重ねた際、同じcacheのlockにより終了3と`Resource temporarily unavailable`が返った。先行試験が完了してから正常入口を再実行して成功した。lockを解除・削除せず、最初のログも保持した。並列運用の推奨ではない。

今回はマージ後のMac手順確認。Linuxでのマージ後再実行、新規VM、Live取得は実施していない。Linuxでの修正コードの検証は前回の[M3 DNS検証](M3_DNS_RESERVED_Z_VALIDATION.md)と分ける。自動実行は本人の利用評価を代替しない。

## 本人へ引き渡すもの

[本人による実利用チェック](M3_PERSONAL_CHECK.md)を追加。正常結果の確認、保存済み失敗からの段階・期待値の診断、正常再実行、snapshot準備の理解を確認する。本人の所要時間・追加説明・継続理由は未取得。返答後に詰まった箇所だけを改善する。

## 証拠

Gitignoredの`artifacts/m3-main-use-20261012/`に保存した。

- `preparation.log`、`before-path.txt`: 公開手順の実行とsnapshotパス。
- `batch.log`、`blackhole-workflow-5qmye7zx/`: 指定失敗の確認→正常2回。
- `healthy.log`: 同時使用によるlockエラー。
- `healthy-after-lock.log`、`blackhole-workflow-1nfy154t/`: 正常2回。
- `failure.log`、`blackhole-workflow-wwagd9v1/`: 既知の不具合の失敗・後続停止。
- `recovery.log`、`blackhole-workflow-6yf_rbpd/`: 正常再実行2回。

各workflowのmanifest・events・summary・Rust報告・ログを保持。正常反復間のソースとバイナリの一致を確認した。この確認の完了時点では新しい文書のcommit・push・PRは実施していなかった。提出後の状態はPRで確認する。
