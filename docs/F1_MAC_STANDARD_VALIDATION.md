# F1の2修正: Mac ARM64/TCG再受け入れ

2026-10-22 JST。基点main `a8512cb21564d404149436e2337ba24edd3ba98e`。
[Linuxで発見した2修正](F1_LINUX_STANDARD_VALIDATION.md)と同じソースで、Macの標準1 VM通信を確認した。
[機械可読記録](validation/f1-mac-standard-20261022.json)に入力・ソースhash・boot ID・停止証拠を保存。
検証時は未コミットであり、GitHub CIは後続PRの対象コミットで別途確認する。

## 準備と条件

別作業のWarden変更を含まない専用worktreeをmainから作り、今回の2修正・回帰試験・文書だけを移した。
Linux記録の3ファイル（Nix定義・F1 guest・ServiceAccount回帰試験）とSHA-256が一致。
既存専用Colima builderのNix cacheでoffline buildし、旧guestを残した別bundleへ保存した。
F1起動Pythonがguest toplevelのclosureへ含まれることも実依存一覧で確認した。

実行はMac上で直接QEMU TCG/max。Colima内の入れ子VMではない。
専用builder/containerは準備後に元の停止状態へ戻し、他用途Colimaは稼働を継続した。
4 vCPU・16 GiB RAM・32 GiB仮想disk、同時起動1 VM。新規2 VMを順次実行した。
Node300秒・Pod120秒・通信各5秒・2回共有600秒・watchdog660秒と元の期待値は変更しない。
準備前の利用可能RAMは23,961 MiBで、18,432 MiB条件を満たした。追加メモリの確保は不要。

## 結果

| VM | boot ID | 実観測 |
| --- | --- | --- |
| fresh-1 | d701e0c0-e91c-4a46-93b1-56c642f0639f | 1 Node・2 Pod Ready、Service DNS、TCP/UDP nonce一致・受信各1件 |
| fresh-2 | 59731bb0-67a8-4e6f-884f-a10daa21498e | 同じ固定bundle・異なるboot IDで再成功 |

一括passed・終了0。独立verifierで生のguest/VM結果を再検証した。
両VMとも証拠同期・停止前同期・QEMU終了0・reaped true・escalated false。
終了後、両QEMU/controllerの実PID不在を確認した。
QEMU生存時間は各115,402 ms・118,582 ms。workflow全体や一般的な性能の保証には読み替えない。

MacのK3s回帰は84成功/1 skip（Linux procfs専用）、同一ソースのLinux回帰は85成功。
ServiceAccount依存待ちは既存Pod期限内に限定し、APIエラーを待機成功へ変換しない。元manifestは依存成立後に1回だけ適用する。

## 保存先と再利用

raw証拠: `/Volumes/Elecom-1TB/Linux/veil-arena/f1-linux-mac-final-20261022/acceptance/`。
guest: 同じrootの`image/`。runner: `/nix/store/ksppnmk56sm941pjf63sl2bgx1k62bqf-arena-k3s`。
新規の証拠先を指定して再利用できる。既存出力へ上書きしない。
生disk・serial・kubeconfig・tokenは公開記録へ含めない。

Linux初回の起動失敗とServiceAccount競合、旧Mac受け入れ、今回の最終Linux/Mac成功は別々の証拠として保持。
標準1 VMの通信を両対応ホストで確認できたが、修正のレビュー・取り込み・対象コミットCIは別の工程。
既存Keeper/lifecycle証拠と2 VM追加検証TODOを保持。署名タグ・公開は保留。
