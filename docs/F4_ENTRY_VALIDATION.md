# F4統合入口・Linux/KVM経路の検証記録

2026-10-21。基点main `cb274f4`（PR #25取り込み後）。コミット・push・PR前の作業。
機械可読記録は [f4-entry-20261021.json](validation/f4-entry-20261021.json)。
最終ゴールは他のVeilから利用できるVM・仮想ネットワーク・Kubernetes検証環境。
今回は両ホストの実行経路とF2・F3統合入口を整備する単位で、F4全体の完了ではない。

## 実装

- [F4入口](F4_ACCEPTANCE.md)で、既存のF2 acceptanceとF3 acceptanceを順次実行する。
- F2の新規2クラスタとF3の新規2 VMで使う書込みdisk・証拠はすべて別。既存guest・Nix／Cargo cacheは再利用する。
- 各工程600秒と従来のVM資源・期待値を維持。自動再試行・期限延長なし。
- Linux x86_64をKVM／host CPUへ明示接続。Mac ARM64は従来どおりTCG／max。
- KVM APIと空VM作成を非root UIDで事前確認。失敗時のTCG fallbackやホスト権限変更はしない。
- 工程別終了コードと証拠を残し、元の独立判定器で生証拠も再検証。途中失敗で次工程を始めない。

## Linux x86_64／KVM: F3新規2 VM成功

`pi-node`の専用Docker実行経路を利用。通常SSHユーザーから直接の`/dev/kvm`アクセスは不成立で、
既存のDocker経路からdeviceを渡し、コンテナ内UID 1000とdeviceの実groupで確認した。
ホストのgroup所属、device mode、AppArmor、routeは変更していない。
実行containerは非root・cap-drop ALL・no-new-privileges・network none。既存の別用途containerは停止していない。

固定guestを既存専用Nix builderで準備し、runtime storeは既存335 closure pathsのうち不足3 pathのみ追加した。
既存キャッシュを置換・削除せず、専用builderを元の停止状態へ戻してから試験した。
同一x86_64 guest bundle、異なるboot IDの新規2 VMで以下が成功した。

| VM | boot ID | QEMU実行時間 | 結果 |
| --- | --- | --- | --- |
| fresh-1 | 7038bc93-4039-44b3-a495-8d5085a37991 | 27,848 ms | passed |
| fresh-2 | 6ff12fe7-471f-4ac1-a4f5-63645f8449a7 | 23,645 ms | passed |

時間は各`vm-stop.json.duration_ms`であり、準備・入力hash照合や全体所要時間を含めない。
各VMでReady Node、baseline、拒否不成立のnegative control、enforce中のtrue exec拒否（実errno 13）、
false終了1、解除後baseline、元profile一覧への復帰を既存F3判定器が確認した。
両VMのQEMU終了0・reaped true・escalated false・evidence同期 true・controller終了0。

生証拠は`pi-node:/home/maakie/veil-arena-validation/f4-20261021/keeper-acceptance/`。
公開記録へdisk・serial・kubeconfig・tokenを持ち込まない。

## 統合新規受け入れは資源不足で残件

Linuxは4論理CPUであり、固定F2の6 CPU以上を満たさない。F2実VMは開始していない。
Macは14論理CPUだが、統合入口の事前確認で利用可能／inactive RAM 24 GiBに届かず終了3。
停止直後の観測は22,615 MiB。新規VM・出力summaryは作成されていない。
他プロセス停止、CPU判定の偽装、RAM条件の緩和は行っていない。

Macの過去F2新規2クラスタとF3新規2 VMの保存済み証拠は、今回のF4再検証関数で成功。
これは保存済み証拠への判定器互換性確認であり、今回の新規F4受け入れへ読み替えない。

## 回帰と残件

Mac固定Nix環境でRustのfmt／clippy／tests、Python 155試験、全systemのNix定義評価が成功。
Linux x86_64では固定Python 3.14.7によるPython 155試験が成功。CIやLinux Rust実行とは区別する。
新規試験はホスト経路／KVM拒否・fd回収、工程順序、終了コード、raw evidence不正、工程間キャンセル、資源不足・既存出力拒否を扱う。
このダミー試験を、K3sの実checkpointキャンセル受け入れ成功には数えない。

残るF4は、資源が揃ったホストでの新規F2・F3統合成功、指定checkpoint中断／期限超過と回収・正常再実行、本人操作。
過去のLinux/KVM期限超過の根本原因は未確定のまま保持する。
物理環境・第三者試用・署名タグ・公開は対象外。

その後の1 VM checkpoint中断・watchdog期限検出・正常復帰は[別の検証記録](F4_LIFECYCLE_VALIDATION.md)へ記載する。本書の統合受け入れ資源不足はそのまま残件。
