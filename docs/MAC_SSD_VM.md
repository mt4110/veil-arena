# Macと外部SSDでの専用Linux VM

2026-10-16。外部SSDを再フォーマットせず、検証済みゲストとNixキャッシュを再利用する。正式な入口は、bundleから準備したNix出力の`arena-netns-volume`。Arenaが固定しているNixOSゲストを使い、保存先は実行時の引数で指定する。

## 保存と実行の境界

最初に確認したSSDは`/Volumes/Elecom-1TB`、APFS、読み書き可能、空き約931GiBだった。初回の入力と証拠は`/Volumes/Elecom-1TB/Linux/veil-arena/netem-20261016-155358`へ保持している。この名前や日時は入口の実装に固定しない。既存データ、パーティション、権限設定、Nix storeの配置は変更しない。

```text
準備: 既存の専用Colima ARM64ビルダー → 固定Linuxゲストをビルド・試験
実行: Mac上のQEMU → SSD上の読み取り専用guest bundle
                          └─ 新規VM内のapp・router・server netns
結果: SSD上の新規resultsディレクトリ
```

Colimaはゲストの準備と先行確認用。Mac側の受け入れではQEMUが直接Linuxゲストを起動する。既存入口と同じTCGを使い、HVFや入れ子KVMへ黙って変更しない。VMは4GiB・2vCPU・外向きNICなし、ゲストstoreは読み取り専用。MacのHOMEやSSD全体をゲストへ共有せず、各起動の出力先だけを9Pで共有する。

Nix storeと依存キャッシュ、専用Colimaの既存ディスクは元の場所で再利用する。SSDへの全キャッシュ移設ではない。guest bundleと結果をSSDへ保存する。固定入力との照合用に、検証済みbundleのNix store内のコピーも保持する。

## 起動入力の準備

新しいguestが必要な場合は、対応Linuxビルダーで準備・一括確認し、成功したVMだけを既存`guest_bundle.py export`で書き出す。[bundleの準備](NIX_VM.md#linuxで準備したゲストをmacで再利用する)を参照する。検証済みbundleが残っていれば、ゲストのビルドやColimaの起動は不要。

```sh
# リポジトリのルートから、既存bundleを再利用してホスト側の入口を準備する。
volume=/Volumes/Elecom-1TB
root="$volume/Linux/veil-arena/netem-20261016-155358"
bundle="$root/images/guest-bundle"
prepared=$(bash scripts/prepare-netns-from-guest.sh "$bundle")
```

準備の出力はGC rootで保持する。同じ入力と依存ならキャッシュを再利用する。旧入口や証拠を付け替えたり削除したりしない。通常の`arena-netns`は引き続きNix store内のイメージを使う。`arena-netns-volume`は同じlauncher・watchdog・controller・checkerへ、検証した外部bundleの参照を渡す。生成済みshellの書換えやローカルadapterの生成は不要。

## 反復と受け入れ

`arena-netns-volume VOLUME_ROOT GUEST_BUNDLE daily|acceptance|watchdog-daily NEW_OUTPUT_DIRECTORY`を使う。`daily`は新規VMを1回、`acceptance`は成功時に追加の新規VMも起動する。`watchdog-daily`はrunner停止試験VMと、その証拠検証成功後の正常再実行VMの計2回を起動する。いずれも試験中のbuild/fetchをしない。引数は絶対パスとし、出力先の親ディレクトリは同じvolume内に既に存在するものを指定する。bundle自体の中へ結果を保存しない。

```sh
"$prepared/bin/arena-netns-volume" "$volume" "$bundle" daily \
  "$root/results/daily-$(date +%Y%m%d-%H%M%S)"

"$prepared/bin/arena-netns-volume" "$volume" "$bundle" acceptance \
  "$root/results/acceptance-$(date +%Y%m%d-%H%M%S)"
```

入口はvolumeのマウント状態と同一filesystem内の保存範囲を確認し、固定Nix bundleのmetadata・kernel paramsと一致すること、boot入力のSHA-256を照合する。自己申告のhashだけを書き換えた別bundleも受け入れない。既存の出力・dangling symlink・未マウントのディレクトリは環境エラー3で拒否し、検査失敗時にはworkflowの出力を作らない。入力パスは各VM起動時にも再検査する。

### runner停止からの復帰確認

このモードに対応したソースから、同じ `prepare-netns-from-guest.sh` でホスト側入口を準備する。既存の古いimmutableな入口へは追加されない。検証済みguest bundle・kernel・依存cacheは再利用できる。

```sh
"$prepared/bin/arena-netns-volume" "$volume" "$bundle" watchdog-daily \
  "$root/results/watchdog-daily-$(date +%Y%m%d-%H%M%S)"
```

[既存watchdog試験](HOST_WATCHDOG.md)と同じcheckpointで所有guest runnerをSIGSTOPし、停止状態を確認後、ホストの既存試験用期限で所有QEMUを停止する。意図したVM終了3を、証拠同期・回収・停止状態・未完了guest結果の検証が揃った場合だけ期待どおりと扱う。その後、別のboot ID・同一起動入力の新規VMで正常16ケースを実行する。停止試験の証拠が不十分なら後続VMは起動しない。

全体の `summary.json` がpassed／終了0でも、`daily-watchdog/vm-stop.json` の環境エラー3は意図した停止として保持する。停止VMは完成batch結果やguest cleanup成功を生成せず、VM内資源は所有QEMU終了によって回収する。正常再実行の証拠は `daily/` に分けて保存する。失敗時には既存の自動診断と元の終了コードが維持される。

SSD入口は `watchdog-acceptance` をまだ受け付けない。4 VMの追加受け入れが必要な場合は別途対象・実行条件を決める。kernel panic、guest kernel無応答、ホストI/O停止、SSD抜去・同時書換えを再現する試験ではない。

SSDはVM停止と証拠保存が完了するまで接続したままにする。稼働中の抜去・第三者による同時書換えを安全に継続できる保証ではない。終了後も過去のVM結果、bundle、cache、GC rootを自動削除しない。再実行時に準備用Colimaを起動する必要はない。

## 初回ローカルadapterでの確認

Linux ARM64／TCGの先行1 VMで16ケースが成功し、Mac ARM64のnative QEMU／TCGでも追加の新規VMを含む2 VM・各16ケースが成功した。異なるboot IDと同じ起動入力、NetEm drop増分8、eBPF pass 8・drop 0、解除後の限定drop 4、正常復帰4／4を照合した。全3 VMで証拠の停止前同期と所有QEMUの回収を確認した。

既存の結果ディレクトリを指定した再実行は環境エラー3で拒否し、その中の2,849ファイルのハッシュが変わらないことを確認した。今回のSSD使用量は約1.1GiB。準備用Colimaは停止済みで、cacheと過去の証拠は保持している。

照合結果はSSD内の`verification-summary-final.json`、Macの集計は`results/mac-acceptance-001/summary.json`、試験証拠8,539ファイルのmanifestは`evidence-manifest-final.json`。Finderが更新した`.DS_Store`だけを明示的に除外し、元のmanifestと管理ファイルも保持する。試験証拠は最初の照合時から不変だった。[集計記録](validation/netem-mac-ssd-20261016.json)と[NetEmの検証記録](NETEM_COMPARISON_VALIDATION.md)にも結果を残す。Linux x86_64／KVMの期限超過、HVF、物理Pi 5、Ubuntu Serverの導入、IPv6、第三者試用、k8sの成功へ一般化しない。

## 正式入口の確認

正式入口で入力不一致・未マウント先・既存出力など6条件をVM起動前に拒否し、その後の新規2 VMで各16ケースの成功を確認した。新しいソースコピーからの準備も同じ出力を再利用した。[正式入口の検証記録](VOLUME_ENTRY_VALIDATION.md)と[集計](validation/volume-entry-20261016.json)を参照する。物理SSDの抜去は行わず、存在しない／未マウントのディレクトリで未接続時の拒否を確認した。

## SSD入口のrunner停止試験

SSD入口に `watchdog-daily` を追加し、Mac ARM64／TCGで停止VMと正常復帰VMの1組を確認した。
意図した環境エラー3・未完了guest結果を保持し、停止前の証拠同期・所有QEMU回収の検証後、
別boot ID・同一起動入力の新規VMで正常16ケースが期待結果と一致した。
[確認範囲と証拠](SSD_WATCHDOG_VALIDATION.md)を参照する。既存のguest・依存cacheを再利用し、
Linuxホストや4 VMの追加受け入れへは一般化しない。
