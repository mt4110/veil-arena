# Warden非ロード試験：2026-10-23の検証記録

Mac ARM64／QEMU TCGの隔離VM1台で、Wardenのサービス同定から終端証拠・QEMU停止まで成功しました。
これは`diagnose --service`の非ロード試験です。通信遮断や本番への導入可否を判定するものではありません。

## 入力と証拠の位置づけ

[公開集計](validation/warden-nonloading-20261023.json)に、
実装・bundle・証拠のSHA256、実CLIの終了コード、boot ID、停止結果を記録しています。
raw stdout/stderr/traceと旧失敗証拠は検証者のローカル`artifacts/`に保持し、VMイメージや個人環境のログは公開しません。
集計ファイルだけを独立した第三者の再実行証拠として扱わないでください。

Arenaはmain `e489def8a89c983d73ae24653b076aab97653dde`に未コミットの非ロード試験を加えた状態で実行しました。
このPRへの整理は最新main `662f949`を基点とし、既にマージ済みの文書更新を維持しています。
公開集計に記録した実装6ファイルは、試験済みsnapshotと今回のPRでbyte一致を確認しています。
PRのCommit/CIと実VM実行時のソース状態は分けて扱います。

Wardenはmain `3a7281648ef7a0e62626c79894222591a51bbbe1`に、サービス同定機能のローカル変更を加えたソースです。
このWarden変更はArenaのPRには含めません。使用したCLI/BPFはNix一体パッケージのhashで識別します。
**公開済みWardenのmainだけを取得すれば、この試験が成立するとは確認していません。**

## 初回失敗と修正

初回は診断サービスが開始直後に失敗し、診断ディレクトリも終端結果も得られませんでした。
外側watchdogが150秒で打ち切り、controllerがQEMUを停止・回収しました。これを不合格として保持しています。

| 問題 | 修正・確認 |
| --- | --- |
| 出力先UIDとゲストrootの不一致が原因候補 | 専用ユーザーをhostUidに一致。実VMでUID501を確認 |
| 終端マーカー位置が不一致 | 記録を同期後、root markerを発行。ホストでも一致を検査 |
| 開始失敗がserialに残らない | journal+consoleへ接続。開始失敗時のマーカーを追加 |

初回の直接のerrnoは得られておらず、書き込み権限が開始失敗の原因だったと断定はしません。
今回の修正で、UID一致と証拠出力が実際に成立したことを確認しました。

## 修正後の実VM結果

ゲストはNixOS/aarch64 Linux、2 vCPU/4 GiB、NICなし、readonly store、証拠用9p共有のみです。
BPF ELFは整合性検査の対象として収録し、ロードしません。Warden CLIのbyteは変更していません。

| ケース | 終了コード | 確認結果 |
| --- | --- | --- |
| running | 1 | 合成サービスを同定し、service_identity=candidate |
| missing | 1 | 未ロードのunitを起動せず、service_identity=unknown |
| protected | 2 | SSH対象を入力段階で拒否。診断stdoutなし |
| mixed | 2 | service/cgroup混在を入力段階で拒否。診断stdoutなし |

通常サービスの終了1は既存loaderの専用sandbox制限による期待値です。
同定に成功しても、`loader_scope=blocked`、`runtime_acceptance=unknown`、`deployment_ready=false`を維持します。
全4ケースの診断プロセスと子プロセスのtraceでBPF syscallは0、socket familyはAF_UNIXだけでした。
このtraceはOS内の全プロセスを対象にしたものではありません。

## 終端・停止と回帰

- root/nestedのservice-resultがsuccessで一致し、terminalのwriter_uid=output_owner_uid=501を確認。
- host summaryはpassed-nonloading、runnerは終了0。
- controllerはservice_completedを検出し、証拠同期後にSIGTERMでQEMUを停止。reapと停止後の両PID不在を確認。強制SIGKILLなし。
- これはcontrollerによる停止です。ゲストOSの自発的なpoweroff完了を確認した結果ではありません。
- 専用Colima builderとbuilder containerは停止。旧bundle、初回失敗証拠、無関係な変更は保全。
- ローカルPythonは全210件が失敗なし（既存1件skip）、うち非ロード関連16件が成功。

開始失敗・書込UID不一致・マーカー不一致・公開順の異常・上書き拒否・symlink拒否はローカル回帰です。
今回のVMでは意図的な開始失敗やwatchdog障害を追加注入していません。
x86_64での今回の試験、実サービス・クラウド運用、BPF attach、通信拒否・解除・撤去は未確認です。

準備・再実行の入口は[非ロード試験の手順](WARDEN_NONLOADING.md#準備と起動の分離)を参照してください。
旧policy bundleと非ロードbundleを混在させず、新しい出力ディレクトリで実行します。
