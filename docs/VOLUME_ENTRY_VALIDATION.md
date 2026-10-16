# 外部volumeの正式入口の検証記録

2026-10-16。基点はPR #9マージ後のmain `0df5ded783b9d1ce3d551254415c1a125f56fbf8`。[このmainのCI](https://github.com/mt4110/veil-arena/actions/runs/38876150864)も成功を確認した。本変更は未コミット作業ツリーで検証し、実行に使ったホスト側入力6ファイルのSHA-256を[集計](validation/volume-entry-20261016.json)に保存した。後続のPR準備で、これらの入力と試験証拠を再照合する。マージ・公開は別の承認段階とする。

## 目的と変更の境界

初回の[SSD検証](NETEM_COMPARISON_VALIDATION.md)では、生成したNix launcherを書き換えたローカルadapterを使った。今回は`arena-netns-volume VOLUME_ROOT GUEST_BUNDLE daily|acceptance NEW_OUTPUT_DIRECTORY`をbundleから準備するNix出力へ含め、[再実行手順](MAC_SSD_VM.md)を正式入口へ変更した。

VM起動・watchdog・Rust controller/workflow・Python checkerは共通のまま、`scripts/volume_guest.py`で外部volumeの入力を検査し、同じlauncherへ渡す。通常の`arena-netns`のmanifestとcontrollerは変更前と一致した。Rustソース、guest、kernel、参照eBPF、固定依存版は変更していない。

## 拒否の確認

以下は単体fixtureだけでなく、実際に生成した`arena-netns-volume`で確認した。すべてVM起動前の環境エラー3で、workflowの新規出力は作られなかった。

| 条件 | 確認した結果 |
| --- | --- |
| 存在しないvolume | `guest volume is not mounted` |
| 接続先に残った空ディレクトリを模した非マウント先 | 同じ診断で拒否。実際のSSDは抜去していない |
| metadataは元のまま、同じサイズの異なるImage | `boot input hash/size differs: Image` |
| 既存出力ディレクトリ | `output path already exists`。既存証拠を保持 |
| 出力先がdangling symlink | 同じ診断で拒否。symlinkも保持 |
| 出力が選択volumeの外 | `output path is outside the selected volume` |

改変用Imageは新規の試験fixtureだけに作成した。元のstore image/initrdは読み取り用hardlinkで再利用し、内容・権限は変更していない。元のNetEm試験証拠8,539ファイルも、以前のmanifestのSHA-256とすべて一致した。

単体8件では、上記に加えて、自己整合した別bundleのmetadataでも固定Nix入力と異なれば拒否すること、検査中のマウント消失、bundle内への出力、symlinkによる保存範囲外参照、空白を含むパス、Macの`/tmp`のcanonical pathを確認した。

## 再利用と受け入れ

初回のSSDにある検証済みguest bundleをそのまま使用した。Nixで新規に作ったのはホスト側5出力のみで、guestの再ビルド・cache削除・Colima起動は行っていない。

別ディレクトリへ現在のソースをコピーして新しいGit管理下に置き、同じ準備コマンドを実行したところ、同じprepared出力を返した。これは既存依存を持つ同じMacでのソース配置独立性の確認であり、新規端末へのcold installではない。

準備出力は`/nix/store/bkjimwbnb05vh60zmwqikwzvkr2m3h7a-arena-netns-prepared`。この出力の正式コマンドから、新規の受け入れ出力先を指定した。

| 実行 | 結果 |
| --- | --- |
| 正式入口・daily VM | 16ケース成功、停止前の証拠同期と所有QEMU回収成功 |
| 同じ入力・追加fresh VM | 16ケース成功、別boot ID、同じ起動入力、同じ回収成功 |

拒否試験の後にも正常に実行できた。NetEmのdrop増分8とeBPF pass 8/drop 0、NetEm解除後の対象drop 4、設定不成立の環境エラーと所有qdisc解除、後続の正常TCを両VMで照合した。起動入力3ファイルのSHA-256は初回のbundleと一致し、実際のVM manifestにもSSD上のパスと選択volumeが記録された。

## 静的検証と証拠

固定Nix環境の`scripts/check.sh`で、Rust 10件、controller 4件＋workflow 5件、Python 52件、fmt・Clippy、3 systemのNix定義評価が成功した。新しいNix shell launcherはビルド時のsyntax/ShellCheckも成功した。

証拠は`/Volumes/Elecom-1TB/Linux/veil-arena/volume-entry-20261016-185409`。`verification-summary.json`が最終照合、`results/acceptance-001/summary.json`が受け入れ集計、`rejections.json`と各ログが拒否試験、`source-copy-prepared.path`が別ソース配置からの準備結果である。

試験証拠5,692ファイルを`evidence-manifest.json`へ保存し、Finderの`.DS_Store`だけを対象から除外した。manifestのSHA-256は`4eb383a0d6dae1f4d1e928f6e76068591d1be9106ad8491f0a8e7c0b1558df58`。古い証拠・ローカルadapter・GC rootは保持している。

## 未確認事項

実行確認はMac ARM64／native QEMU・TCGとAPFS SSDに限る。Linux上の実マウント先からの実行、物理SSDの稼働中抜去、同時書換え、HVF、Pi 5実機、IPv6を保証しない。以前のLinux x86_64／KVMの期限超過は未解決のまま残す。第三者試用とk8sは引き続き保留である。
