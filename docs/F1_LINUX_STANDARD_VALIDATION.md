# Linux x86_64/KVM: 標準1 VM通信の検証

2026-10-22 JST。基点main `a8512cb21564d404149436e2337ba24edd3ba98e`。
[機械可読記録](validation/f1-linux-standard-20261022.json)に条件・入力・boot ID・各判定・回収を記録する。
**成功はmainに以下の2修正を加えた未コミットソースの結果。未修正mainの受け入れ成功ではない。**
以下は受け入れ検証時点の記録。その時点ではPR・push・署名タグ・公開は行っていなかった。別作業のWarden変更は準備ソースへ含めていない。

## 初回失敗と修正

F1の起動コマンドが `toString guestScript` でパスを文字列化し、参照するPythonソースをguest closureへ収録していなかった。
最初のVMはserviceの起動直後に失敗し、result.jsonなし・環境エラー3。QEMU回収と証拠同期は成功した。
Nixのパス補間へ修正し、起動ソースがguest closureに入ることを実際の依存一覧で確認した。

その修正後は1台目の通信が成功したが、2台目でNamespace作成直後のdefault ServiceAccount未成立によりPod作成がForbiddenになった。
[ServiceAccount controller](https://kubernetes.io/docs/reference/access-authn-authz/service-accounts-admin/)がdefault accountを管理する。
Namespaceを先に作り、既存Pod段階120秒内でaccountの実在・namespace/name・削除中でないことを確認してから元のmanifestを1回適用するよう修正した。
NotFoundだけを期限内で待ち、APIエラー・識別不一致・期限到達は失敗を保持する。無条件のmanifest再適用はしない。
元のfixture bytes・期待値、K3s/kernel/依存の版、CPU/RAM/disk、期限は変更していない。

失敗した `acceptance/`、`acceptance-fixed/` とそれぞれのguest・sourceを保持。
最終版は `image-final/`、`source-final/`、`acceptance-final/` に分離した。

## 最終受け入れ

既存pi-node、非root UID 1000、既存Docker経由のKVM。ホストのgroup/device mode・route・DNS・policyは変更していない。
cap-drop ALL・no-new-privileges・network none、guest自体も `-nic none`。
4 vCPU・16 GiB RAM・32 GiB仮想diskの1 VMを順次2回実行した。
Node 300秒・Pod 120秒・各通信5秒・2回共有600秒・watchdog660秒を維持。

| run | 実観測 | QEMU生存時間 |
| --- | --- | --- |
| fresh-1 | 1 Node/2 Pod Ready、Service DNS、TCP/UDP nonce一致・受信各1件 | 50,133 ms |
| fresh-2 | 同じ固定bundle・異なるboot IDで同じ確認に再成功 | 50,403 ms |

全体はpassed・終了0。上記時間は各controllerのduration_msであり、準備やworkflow全体の所要時間ではない。
両VMで証拠同期・停止前同期・QEMU終了0・reaped true・escalated falseを確認。
保存済み生証拠を固定Pythonの独立verifierで再検証した。
実行containerは停止・終了0・PID 0、専用builderも元の停止状態へ戻した。別用途mainnetは稼働を継続した。

## キャッシュと回帰

guest/runnerは既存専用Nixビルダーでoffline build。既存runtime 335 closure pathsを再利用し、不足した新pathだけを追加した。
既存cache・guest・証拠を上書き・削除していない。準備時のHOME指定不足・read-only directoryコピーの問題は、環境指定とtar転送で修正し、VM試験の失敗と区別した。
起動前確認時は利用可能25,662 MiB・空きdisk262 GiBで、追加メモリの確保は不要だった。

F1回帰19件はMac/Linuxで成功。K3s全体はLinux85成功、Mac84成功/1 skip（Linux procfs専用）。
ServiceAccount遅延・未成立・APIエラー・誤identity/削除中・適用失敗の検出と、元の期限・適用1回を回帰で確認した。

## 証拠と残る境界

生証拠: `pi-node:/home/maakie/veil-arena-validation/f1-standard-20261021/acceptance-final/`。
各fresh-Nのresult.json・nodes/pods/coredns状態・vm-stop.json・vm-manifest.json・qemu-argv.jsonを保持。
run disk・kubeconfig・token・生serialは公開記録へ含めない。
runnerは `/nix/store/xpgaflkw3wylv080wq2rlha0sy5gj2qj-arena-k3s`、guestは `/nix/store/2r1xxy4w4ccl220a208mfsn778w9x1vb-arena-k3s-guest-bundle`。
再実行時は新しい証拠先を使い、失敗先を使い回さない。

Linux F1通信の標準TODOは修正後ソースで確認できた。修正のレビュー・取り込み・対象コミットCIは未実施。
Linux検証時点ではMac実VMを再実行していなかった。その後、同一修正ソースのMac新規2 VMも[受け入れ成功](F1_MAC_STANDARD_VALIDATION.md)。以前のMac証拠へ一般化せず、新しい証拠を分けて保存した。
2 VMの追加検証TODO、新PC・SSD変更、署名タグ・公開は保留。機能追加を標準完成条件へ増やさない。
