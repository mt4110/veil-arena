# Macと外部SSDでの専用Linux VM

2026-10-16。外部SSDを再フォーマットせず、既存のARM64 LinuxビルダーとNixキャッシュを再利用してNetEm比較の受け入れを行うためのローカル構成。Ubuntu Serverのインストールではなく、Arenaが固定しているNixOSゲストを使う。

## 保存と実行の境界

今回のSSDは`/Volumes/Elecom-1TB`、APFS、読み書き可能、確認時の空きは約931GiB。`/Volumes/Elecom-1TB/Linux/veil-arena/netem-20261016-155358`だけを今回の新規保存先とする。既存データ、パーティション、権限設定、Nix storeの配置は変更しない。

```text
準備: 既存の専用Colima ARM64ビルダー → 固定Linuxゲストをビルド・試験
実行: Mac上のQEMU → SSD上の読み取り専用guest bundle
                          └─ 新規VM内のapp・router・server netns
結果: SSD上の新規resultsディレクトリ
```

Colimaはゲストの準備と先行確認用。Mac側の受け入れではQEMUが直接Linuxゲストを起動する。既存入口と同じTCGを使い、HVFや入れ子KVMへ黙って変更しない。VMは4GiB・2vCPU・外向きNICなし、ゲストstoreは読み取り専用。MacのHOMEやSSD全体をゲストへ共有せず、各起動の出力先だけを9Pで共有する。

Nix storeと依存キャッシュ、専用Colimaの既存ディスクは元の場所で再利用する。SSDへの全キャッシュ移設ではない。新しいguest bundleとソース記録、起動adapter、結果をSSDへ保存する。Nixによる固定入口も保持するため、検証済みbundleのNix store内のコピーも残る。

## 起動入力の準備

1. 現在のソースを新規ディレクトリへ書き出し、既存ARM64ビルダーで一度だけ準備する。
2. runtime storeコピーのNAR hashを照合し、専用・network noneのruntime containerで16ケースを先行確認する。
3. 成功したVMだけを既存`guest_bundle.py export`で書き出す。Macへコピー後、固定Pythonで`guest_bundle.py check`する。
4. `prepare-netns-from-guest.sh`でMac用の固定Nix入口を準備する。
5. 生成されたNix launcherを基に、boot入力の参照先とmanifestをSSD用へ変更したローカルadapterを保存する。所有QEMUの停止、watchdog、結果checkerは元のコードを維持する。各起動前にSSD上の入力とNix store内の固定bundleをbyte単位で照合する。不一致なら起動しない。

ローカルadapterの生成元・差分の目的・SHA-256はSSD内の`launcher-derivation.json`へ保存する。これは汎用の新しい製品入口ではなく、今回の保存先を検証するためのadapterである。ソース更新時は古いadapterやbundleを上書きせず、新規保存先で準備し直す。

## 反復と受け入れ

準備後はSSD内の`arena-netns`に既存workflowと同じ引数を渡す。`daily`は新規VMを1回、`acceptance`は成功時に追加の新規VMも起動する。どちらも試験中のbuild/fetchをしない。必ず新しい出力先を指定する。

```sh
root=/Volumes/Elecom-1TB/Linux/veil-arena/netem-20261016-155358
"$root/arena-netns" daily "$root/results/daily-002"
"$root/arena-netns" acceptance "$root/results/acceptance-002"
```

SSDはVM停止と証拠保存が完了するまで接続したままにする。終了後も過去のVM結果、bundle、cache、GC rootを自動削除しない。再実行時に準備用Colimaを起動する必要はない。

## 確認状況

Linux ARM64／TCGの先行1 VMで16ケースが成功し、Mac ARM64のnative QEMU／TCGでも追加の新規VMを含む2 VM・各16ケースが成功した。異なるboot IDと同じ起動入力、NetEm drop増分8、eBPF pass 8・drop 0、解除後の限定drop 4、正常復帰4／4を照合した。全3 VMで証拠の停止前同期と所有QEMUの回収を確認した。

既存の結果ディレクトリを指定した再実行は環境エラー3で拒否し、その中の2,849ファイルのハッシュが変わらないことを確認した。今回のSSD使用量は約1.1GiB。準備用Colimaは停止済みで、cacheと過去の証拠は保持している。

照合結果はSSD内の`verification-summary-final.json`、Macの集計は`results/mac-acceptance-001/summary.json`、試験証拠8,539ファイルのmanifestは`evidence-manifest-final.json`。Finderが更新した`.DS_Store`だけを明示的に除外し、元のmanifestと管理ファイルも保持する。試験証拠は最初の照合時から不変だった。[集計記録](validation/netem-mac-ssd-20261016.json)と[NetEmの検証記録](NETEM_COMPARISON_VALIDATION.md)にも結果を残す。Linux x86_64／KVMの期限超過、HVF、物理Pi 5、Ubuntu Serverの導入、IPv6、第三者試用、k8sの成功へ一般化しない。
