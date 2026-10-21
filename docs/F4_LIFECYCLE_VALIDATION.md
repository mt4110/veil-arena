# F4の1 VM checkpoint中断・watchdog期限検出・正常復帰

2026-10-21。基点main `cb274f4`。前回のF4入口の未コミット変更を保持した上で追加した。
手順は [F4_LIFECYCLE.md](F4_LIFECYCLE.md)、機械可読記録は [f4-checkpoint-20261021.json](validation/f4-checkpoint-20261021.json)。
以下はPR準備前の検証時点の記録。実行時点では未コミットで、push・PR・公開は行っていなかった。

## 今回の成功範囲

既存`pi-node`（Linux x86_64、4論理CPU）で1 VMずつKVM起動した。
固定guestは4 CPU・16 GiB RAM・32 GiB仮想disk。全4 runで既存600秒workflowを共有した。
固定Nix／Cargo cache・kernel・K3s・Keeper入力を再利用し、checkpoint対応guestは別の新規bundleとして準備した。
古いbundle・準備途中の生成物・証拠は削除していない。

| run | 実際の結果 | 次の確認 |
| --- | --- | --- |
| fresh-1 | Node Ready後・Keeper開始前で停止状態Tを実証。所有controllerのTERMでreason cancelled、controller終了3 | fresh-2の通常試験に復帰 |
| fresh-2 | Node Ready、Keeper baseline／negative control／限定拒否／解除後baselineが成功。controller終了0 | 同じ固定入力で次の故障注入 |
| fresh-3 | 同じcheckpointで停止。既存watchdog fixtureが観測後2,971 msで期限検出。reason watchdog、controller終了3 | fresh-4の通常試験に復帰 |
| fresh-4 | fresh-2と同じ通常試験が成功。controller終了0 | 一括終了0 |

watchdog fixtureの期限は既存の起動待ち120秒・checkpoint観測後2,000 ms。
通常のNode 300秒・Keeper command 20秒・workflow 600秒・VM watchdog 660秒は変更していない。
故障注入が終わった後に通常期限へ戻し、期待値を弱めたり再試行したりしていない。

4 VMのboot IDは異なり、bundle hashと起動入力は一致した。
全runでQEMU終了0、reaped true、escalated false、evidence_synced true、停止前の同期 true。
部分guest結果はenvironment_error／終了3のまま保持し、成功結果に書き換えていない。
上位だけが、想定故障の独立検証とその後の正常復帰を確認して終了0を返す。
fresh-1の意味上の中断分類130と、実controller終了3は別欄で保存した。

## 隔離と証拠

実行は既存専用Docker経路。非root UID 1000、cap-drop ALL、no-new-privileges、network none。
ホストのgroup／device権限・AppArmor・routeは変更していない。
専用builderは元の停止状態へ戻してからVMを起動した。別用途の`mainnet`コンテナは稼働を継続した。
実行containerは終了0で停止し、VM回収は各controllerのChild reaping記録で確認する。

生証拠：`pi-node:/home/maakie/veil-arena-validation/f4-checkpoint-20261021/lifecycle/`。
各runの`result.json`・`cluster-nodes.json`・`vm-stop.json`を保持する。
fixtureでは`checkpoint-state.json`・`watchdog-ready.json`から実停止状態・boot ID・PID・Keeper未開始を照合する。
正常runでは`keeper-result.json`の実label・errno・解除と一覧復帰を既存F3判定器で検証した。
disk・serial・kubeconfig・tokenは公開記録へコピーしない。

## 回帰と残件

Macの固定Nix環境でRust fmt／clippy／testsと全systemのNix定義評価が成功。
最終Python回帰はMac 163試験（procfsの1件対象外）、Linux x86_64 163試験（全件実行）が成功した。
checkpoint証拠不正・profile残存・回収／同期失敗・期限未到達を想定成功としない回帰を追加した。
600秒を次runで再設定しないこと、壊れたcheckpoint証拠で所有controllerを回収し後続VMを開始しないことも確認した。
Linuxのダミー子プロセス試験では、実際のfork／SIGSTOP／proc状態確認と所有子回収を実行する。
Macではこのprocfs試験を対象外とし、VM実起動の成功へ読み替えない。

資源不足のLinux F2・統合新規受け入れ、Mac統合受け入れ、2 VM側と残る対応ホストのcheckpoint試験、本人操作は
[完成計画のTODO](COMPLETION_PLAN.md)に残す。追加購入を前提にせず、条件が満たされた時点で再開する。
今回の1 VM成功をF4全体や、過去のLinux/KVM期限超過の原因解明とは扱わない。
物理環境・第三者試用・署名タグ・公開は対象外。
