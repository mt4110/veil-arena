# netns試験の準備と反復運用

重い準備を試験から分離する。Nixの依存・ビルド出力・読み取り専用VMイメージを保持し、日常試験は1回のVM起動内で13ケースを実行する。変更を受け入れる際には、同じイメージを使った追加の新規VMでも一括試験を確認する。動いているVMや前のケースのnetnsを引き継ぐ方式ではない。

2026-10-10に3ケースの両モードと準備出力の再利用を確認し、2026-10-11に[TC ingress比較](TC_INGRESS.md)を4つ目のケースとして追加し、その後3つのTC故障とTC復旧を追加した。[検証記録](NETNS_WORKFLOW_VALIDATION.md)に結果と未確認項目を残す。

以前の4ケースは[TC検証記録](TC_INGRESS_VALIDATION.md)、以前の8ケースは[故障検出の検証記録](TC_FAULT_VALIDATION.md)、以前の9ケースは[BPF直接実行の検証記録](BPF_TEST_RUN_VALIDATION.md)、以前の10ケースは[ロード失敗の検証記録](BPF_LOAD_FAILURE_VALIDATION.md)、現在の13ケースは[中断・復帰の検証記録](BATCH_INTERRUPTION_VALIDATION.md)を参照する。

## 再利用と新規作成

| 対象 | 運用 |
|---|---|
| Nix依存、Rust/Cargo依存、ビルド出力 | Nix storeで再利用。試験コマンドからNix/Cargoやネットワーク取得を呼ばない |
| kernel、initrd、EROFS store image | 不変のstore pathを再利用。各起動でSHA-256を保存する |
| 準備済みlauncher | `artifacts/prepared-netns/<store-basename>`のGC rootで保持。過去のrootを付け替えない |
| VM | dailyは新規1回、acceptanceは新規2回。各VMのrootはtmpfs、storeは読み取り専用 |
| app・router・serverのnetns | 通信ケースごとに新規作成。所有helper回収と親ネットワーク不変を確認して次へ進む |
| 結果 | 新規出力先へ保存。過去の結果・image・依存cache・GC rootを自動削除しない |

ケースは「正常通信 → 戻り経路故障 → 正常通信 → TC比較 → drop無効化 → 誤ったhook → 受信helper停止 → BPF直接実行 → ロード失敗 → 一括キャンセル → TC復帰 → 一括タイムアウト → TC復旧」の固定13個。故障ケースは単なる非ゼロ終了では合格にせず、両gatewayへの成功、指定したapp/server pingの失敗、後片付け成功を照合する。想定外の失敗は後続ケースを止め、未実行を記録する。各ケースの検証内容は[正常通信ベースライン](NETNS_VM.md)を参照する。

## コマンド

対応するLinuxビルダーで一度準備する。ビルドと起動のホストUIDは一致させる。Linuxで既にKVM利用権限がある場合の例：

```sh
ARENA_VM_ACCELERATOR=kvm bash scripts/prepare-netns.sh
```

返された`/nix/store/<hash>-arena-netns-prepared`を控える。以下は返された実際のpathを使う。試験のたびに準備コマンドを呼ぶ必要はない。

```sh
/nix/store/<hash>-arena-netns-prepared/bin/arena-netns daily \
  "$PWD/artifacts/daily-001"

/nix/store/<hash>-arena-netns-prepared/bin/arena-netns acceptance \
  "$PWD/artifacts/acceptance-001"
```

KVMを使わない対応環境では準備時に`ARENA_VM_ACCELERATOR=tcg`とする。macOSは対応CPUのLinuxビルダーが必要。CPU系統・accelerator・UIDは準備出力に固定し、試験時に黙って変更しない。

`daily`は1 VM・13ケース。`acceptance`はまず同じ一括試験を行い、成功した場合だけ追加VMで同じ13ケースを行う。追加VMのboot IDが異なり、image/kernel/initrdのchecksumが同じであることも集計時に照合する。最初が失敗した場合は追加起動しない。これは同じ条件の再試験であり、違うCPUやkernel上での互換性検証ではない。

ソースやVM定義を変えたら準備を再実行し、新しく返されたlauncherを使う。同じ入力なら既存出力を再利用する。ゲストrunnerを変えた場合はゲストimageが変わる。ホスト専用controller・集計コードだけの変更はゲストimageを変える依存ではない。依存版を変える場合はlockと版契約を明示して更新し、受け入れ試験を行う。

## 今回のpi-nodeで再実行する場合

`pi-node`にはhost Nixを追加せず、既存のDocker builderで準備した。したがってhost上で直接`/nix/store/...`を起動する例とは区別する。確認済みのruntime-storeコピーを読み取り専用で使い、次のコマンドを`ssh pi-node`先で実行できる。`mode`は日常用の`daily`、変更受け入れ用の`acceptance`から選ぶ。

```sh
root=/home/maakie/veil-arena-validation/run-1791624441124999000
prepared=/nix/store/l9dkgrbg3k0s3hn7800jl0dij9mwcg7j-arena-netns-prepared
mode=daily
run="netns-${mode}-$(date +%s)"
group=$(stat -c '%g' /dev/kvm)
docker run --name "veil-arena-$run" --user 1000:1000 --group-add "$group" \
  --device /dev/kvm --network none --cap-drop ALL --security-opt no-new-privileges \
  --read-only --tmpfs /tmp:rw,nosuid,noexec,size=64m \
  --memory 8g --cpus 4 --pids-limit 1024 --env HOME=/tmp --env PYTHONNOUSERSITE=1 \
  --mount "type=bind,src=$root/results/kvm-runtime-copy/nix/store,dst=/nix/store,readonly" \
  --mount "type=bind,src=$root/results,dst=/out" \
  --entrypoint "$prepared/bin/arena-netns" \
  nixos/nix@sha256:65397cf0c10c60415c5e98ce4c639f1abed1abd256ef88f455028500a204c43b \
  "$mode" "/out/$run"
```

集計はhostの`$root/results/$run/summary.json`へ残る。`--rm`を指定せず、停止したcontainerも保持する。出力先やcontainer名が既存と衝突すれば上書きせず失敗する。このprepared pathとコピーは今回確認したx86_64／KVM／UID 1000の構成に限る。ソース変更後はbuilderで準備し直し、必要な新規store pathのコピーとNAR hash照合を行ってから新しいprepared pathを使う。今回の転送・照合手順は検証artifactに保存している。

## 集計と失敗

標準出力は合否と`summary.json`の場所のみ。詳細は保存する。

```text
artifacts/daily-001/
  summary.json                    全体、各VM、13ケースの合否・期待/実終了コード
  prepared-manifest.json          準備済み構成
  daily.launcher.log              起動とcheckerの詳細
  daily/
    vm-manifest.json              実際の起動構成
    boot-inputs.sha256             image/kernel/initrd
    guest-boot-id.txt              起動識別子
    serial.log / vm-stop.json      ゲストログとホストの停止・同期記録
    netns-batch/result.json        13ケースの集計
    netns-batch/<case>/            段階ログ、topology、cleanup、result
```

acceptanceでは`fresh/`と`fresh.launcher.log`も残る。正常ケースは51段階、経路故障は33段階、正常TCは59段階と5モードの送信/受信/counter照合。BPF直接実行は70入力のerrno・戻り値・counter・保存byte列を照合する。ロード失敗ケースはverifierの特定診断・未送信・map解放を照合する。TC故障は原因ごとに打ち切り位置と診断を固定する。これらは構築・通信・診断・後片付けの段階数で、攻撃パターン数ではない。元のケース結果はdrop無効化で`failed`、誤ったhookと受信helper停止で`environment_error`のまま保存し、その故障を正しく検出した対照試験として一括結果に`passed`を記録する。

終了コードは0=全体成功、1=期待値との不一致、2=引数不正、3=環境・証拠・停止エラー。既存出力先を再使用しない。受け入れで片方のVMが失敗した場合も、最初のログと結果を保存する。batch schema 6は13ケースをすべて要求する。故障ケースは期待した終了コードと原因・通信記録・解放を照合し、想定外の同じ終了コードを合格にしない。終了コード0でも証拠が不完全なら合格にしない。boot IDや入力checksumが一致条件を満たさない場合も環境エラーにする。

guest JSONは完成・file fsync後に最終名へ原子的に公開する。`.pending`も保持し、既存報告を上書きしない。

一括制御・集計はRust、VM起動は固定shell wrapper、証拠の形式チェックは固定Pythonの標準ライブラリ。pytestは追加しない。日常試験のVM停止条件は従来通り、guest service終了を確認し、ホストが出力をfsyncして所有QEMUを停止する。guestの正常unmountまで確認した保証ではない。

この運用はまずnetns試験に適用する。ブラックホールの人工入力試験は既存の入口を維持し、オフラインVM内のコンパイル済みtarget共有は含めない。ARM64ゲスト実起動、IPv6、TCのTCP drop・固定fixture以外の異常入力、VM panicや電源断での回収は別の確認項目として残す。

VM内の一括試験を実際に中断する[キャンセル・タイムアウト試験](BATCH_INTERRUPTION.md)も含める。内側の中断結果は`cancelled`/130・`timeout`/4のまま保存し、資源回収と後続TC復帰を別に判定する。通常の一括結果は13ケースすべての期待結果一致が必要。

guestの終了処理に頼れない停止は[ホストwatchdog試験](HOST_WATCHDOG.md)で検証する。`watchdog-daily`は停止VM→正常再実行VMの2起動、`watchdog-acceptance`は同じ組を追加新規VMでも確認する4起動。同じprepared imageを使い、通常の`daily`/`acceptance`と分けて選べる。
