# NetEm全損失比較の検証記録

2026-10-16。基点はPR #8マージ後のmain `e011c7f17b22f1ea61fbb0e066b597571d56f4ad`。検証はPR・push前の未コミット作業ツリーで実行し、集計の`runtime_source_sha256`で実装入力を固定した。対象は専用Linux guest内の固定IPv4／UDPである。x86_64／KVMでの失敗を保持し、後続のARM64／TCGで受け入れを確認した。

## 確認状況

**Mac ARM64／TCGと外部APFS SSDの構成で、最終版の新規2 VM・各16ケースの受け入れが成功した。** 同じ実装をLinux ARM64／TCGの先行1 VMでも確認した。Linux x86_64／KVMの期限超過は未解決として残す。以下は当初のx86_64試行であり、成功へ変更しない。

x86_64用の最終準備出力は`/nix/store/cyqd86jjqm885ybfz96hhpms97f8y0jb-arena-netns-prepared`で、ローカルのNix評価とも一致した。

| 最終版の実行 | 結果 | 残った確認 |
| --- | --- | --- |
| `netem-1pLqF6-v7-acceptance-v1` | 既存`tc-after-cancel-001`が約93.9秒で環境エラー。所有資源回収は成功 | NetEm 3ケースへ未到達、fresh段階は未起動 |
| 同じイメージの`acceptance-v2` | 既存12ケースを通過した後、一括540秒の期限に達し`tc-restored-001`がtimeout。workflowは環境エラー3 | NetEm 3ケースへ未到達、fresh段階は未起動 |

タイムアウトや期待値は再実行のために変更しなかった。この2回では最終版のNetEm全損失・設定不成立・正常復帰へ到達していない。途中版のcounter観測や単体fixtureを最終版のwire成功へ読み替えない。期限超過の原因は未確定で、ARM64での成功だけを原因特定やx86_64の合格にしない。

## 外部SSDとARM64での受け入れ

ユーザーの指定した外部SSDをAPFSのまま利用した。[構成と再実行手順](MAC_SSD_VM.md)、[集計記録](validation/netem-mac-ssd-20261016.json)を参照する。準備には既存の専用Colima ARM64 builderを使い、Nix store・Cargo依存・以前のruntimeコピーを再利用した。754 store pathのNAR hashを照合し、変更分22 pathだけを追加した。先行Linux VMの成功後に既存export/check経由でguest bundleをSSDへコピーし、Mac用の固定Nix入口を生成した。

Macでは生成済みNix launcherのboot入力参照先とmanifestをSSD用に変更したローカルadapterを使う。元launcherとadapterのSHA-256・変更点を残し、各起動前にSSDの入力がNix storeの固定bundleと同じbyte列であることを確認する。元のwatchdog・controller・Python checkerは維持した。既存データの削除、フォーマット、cache移設、MacのHOME共有は行っていない。

| 実行 | 結果 | 確認範囲 |
| --- | --- | --- |
| Linux ARM64／TCG、専用Colima内の先行VM | 16ケース成功 | 新しいguestのNetEm比較・設定不成立・正常復帰 |
| Mac ARM64／TCG、SSD、daily VM | 16ケース成功 | SSDからの実起動、停止前の証拠同期、所有QEMU回収 |
| 同じMac・SSD・入力のfresh VM | 16ケース成功 | 別boot ID、同じimage/kernel/initrd、NetEmの再成功 |

全3 VMで、count phaseのeBPF counter増分は`[4,4,0,4,4,0]`、NetEm drop増分は正確に8。NetEm明示解除後の限定dropは`[4,0,4,4,4,0]`で、detach後の受信は対象4・対照4。loss 0%の設定不成立はcount送信前の環境エラー3として保存され、所有qdiscの明示解除と独立した後続TCの正常復帰を確認した。全VMで停止前の証拠同期と所有QEMU回収が成功した。入力checksum、固定版、全16ケースの期待終了コードとcleanupは現行checkerでも再照合した。

今回のSSD上の実行ソースは、文書を除く39入力のSHA-256が現在の作業ツリーと一致した。MacのRust 10件・fmt・Clippyも最終ソースで再確認した。既存出力を指定した再実行は環境エラー3で拒否し、元の2,849ファイルのハッシュは不変だった。Mac受け入れ時には準備用Colimaを停止し、既存のdefault Colimaは変更していない。

証拠は`/Volumes/Elecom-1TB/Linux/veil-arena/netem-20261016-155358`へ保存した。`verification-summary-final.json`が照合結果、`results/mac-acceptance-001/summary.json`が受け入れ集計、`evidence-manifest-final.json`が試験結果8,539ファイルのSHA-256。manifest自体のSHA-256は`99df21afd8be1583780296af1507412d9b26e4953b1366ad6283f56a8b46779a`。初回manifestの8,545ファイルにはFinderの`.DS_Store`が6個含まれ、そのうち2個が後から更新された。元manifestを保持し、Finder管理ファイル6個だけを最終照合対象から除外した。試験結果8,539ファイルは初回manifestのハッシュとすべて一致した。元の失敗証拠も保持したままである。

## 実装と診断

[仕様と手順](NETEM_COMPARISON.md)に、観測点、5 phaseの送受信・counter、設定不成立の診断と所有qdiscの明示解除を記載する。バッチ契約はschema 7・16ケース。Rust guestとホスト側Python checkerの両方で判定する。

初回のVMでは、新ケース追加後の入れ子中断試験が13ケースを返したのに、既存のguest照合が10件固定だったため環境エラーになった。中断・回収そのものの証拠は残っていたが合格とはせず、同じゲストのcase契約から期待するID・順序・状態を選ぶ方式へ修正した。欠落や別IDを拒否する回帰試験を追加した。16ケースと入れ子試験を収めるバッチ全体の期限は360秒から540秒へ更新し、個別通信90秒・個別コマンド8秒、guest service600秒・host watchdog670秒は維持した。

次のVMでは通常TCの送受信・BPF counterはすべて一致したが、`tc-cleanup.json.pending`のまま最終名がなく環境エラーになった。TC agentにも一般helperの2秒終了猶予が適用され、同期保存中に強制終了できる実装だったため、TC agentだけ8秒まで正常終了を待つよう変更した。3秒遅延した子プロセスの出力完了と正常回収を確認する回帰試験を追加した。保存途中ファイルをcleanup成功として扱う変更はしていない。同期保存の遅延原因自体は特定していない。

途中のNixパッケージビルドでは既存のsignal cleanup試験が失敗した。最初は同じソース・期待値・期限のままNixの並行build数1・core数2へ制限した再実行で成功したが、後のビルドで再発した。調査の結果、終了要求ログの同期保存前に取得した時刻をSIGINT送信後の猶予計算へ使っており、保存が2秒を超えると送信直後に強制終了し得ると分かった。猶予の開始時刻を、ログ同期とSIGINT送信が完了した後へ移した。既存のcancel／timeout両方で、終了コード130／4、部分出力とcleanup出力、強制終了なしを引き続き要求する。最終ソースのパッケージ検査は別に記録する。

3回目のVMでは通常TCの保存・回収は成功したが、既存BPF直接実行試験が`direct test deadline`で停止した。70入力の個別同期保存も20秒の実行期限に含まれ、保存途中（約60入力）の状態だった。入力証拠の準備を上限90秒へ分け、全入力保存後のBPF実行ループに従来の20秒期限を適用した。準備・実行時間を結果へ追加し、バッチ540秒・全70入力の検証・同期保存・BPF回収は維持した。準備も含めた従来の20秒を満たした、という意味ではない。

後のVMでは既存13ケースを通過し、NetEmの設定100%、eBPF pass 8、qdisc drop増分8、明示解除まで確認したが、後続のTC状態JSON公開待ちが3秒を超えて環境エラーになった。最終名のJSONは後から保存されていたが、ケース全体は合格へ変更していない。TC agentの起動・状態公開待ちを個別TCコマンドと同じ8秒へそろえ、ケース90秒・バッチ540秒と一般echo helperの起動確認3秒は維持した。3.2秒後の公開を受け付け、ケース期限は越えず、子を正常回収する回帰試験を追加した。

TC待ち時間をそろえたVMでは、eBPFが指定8個をpassする一方、qdisc drop増分が9だったため試験失敗になった。追加1個の種類は未特定のまま保存し、`8以上`へ条件を弱めなかった。測定対象interfaceのIPv6無効化とrouter・server間のpermanent IPv4 neighborを、実際のveth MACとreadback付きで設定する固定fixtureの前提を加えた。補助設定は所有namespace内だけで、namespace終了時に回収する。通常ケースや独立した復帰ケースの構成は変更しない。

背景通信の前提を加えた最初の一括実行は、NetEmへ到達する前の既存`tc-after-cancel-001`が約93.9秒で環境エラーとなり停止した。個別ケース90秒の期限、所有helper・namespace回収、親network不変の記録は維持した。このケースが遅れた原因は未確定で、合格へ読み替えたり期限を延長したりしていない。同じ最終イメージと暖機済みcacheで、ソース・期待値を変えずに1度だけ再実行する方針とした。

## 再利用と隔離

既存のNix store、Cargo／Python／Nixキャッシュと以前のpreparedイメージは保持した。準備はネットワーク取得なしで行い、試験時はprepared launcherだけを使う。外向きNICなしの専用VMと、root filesystem read-only・capabilityなし・network noneのruntime containerを使う。hostの共有interface、権限設定、他containerは変更しない。第三者試用とk8sは対象外。

## 静的・単体検証

最終ソースでmacOSのfmt・Clippy・Rust 10件が成功した（guest専用binaryの実行はLinuxのみ）。LinuxではClippy、Rust 10件＋guest専用16件、host controller 4件＋workflow 5件、Python 44件が成功した。Nixのreleaseパッケージ内でもRust 10件＋guest専用16件が成功した。checkerはqdisc所有識別、counter不足・誤分類、設定不成立時の送信、解除・正常復帰・helper回収の不足を拒否する。

受け入れ用のruntime copyは752 store pathのNAR hashを既存の検証記録または今回の実測と照合した。変更分18 pathだけを追加し、所有コピーの1,094ファイル・225ディレクトリをVM起動前に同期した。cache・旧prepared出力は残し、準備用builderを停止してから実行した。これで共有ホストのI/O性能や一定の所要時間を保証するものではない。

## 制約

追加したNetEmケースはARM64／TCGで実起動を確認した。Linux x86_64／KVMでの最終受け入れ、HVF、物理Pi 5、部分損失、遅延・重複・順序逆転、TCP再送、IPv6、任意時点の中断、kernel panic・電源断は今回の成功範囲に含めない。実Blackholeの通信遮断や製品価値・性能を証明する試験でもない。

## 完了条件と残る確認

同じ最終ソースの16ケースが1 VMで期待どおりに完了し、同じimage/kernel/initrdの追加の新規VMでも成功することを要求した。NetEmの正確なdrop増分8、eBPFの指定8個のpassとdrop 0、NetEm解除後の限定eBPF drop 4、最終到達4／4、設定不成立のcount送信前エラー3、明示したqdisc解除とBPF／helper／namespace回収、独立した通常TC成功を照合した。この条件は今回のMac ARM64／TCG・SSD構成で成立した。x86_64／KVMの期限超過の切り分けは別に残る。

検証完了時点ではPR作成・push・マージ・公開は実施していなかった。後続のPR準備では実装入力と試験証拠を再照合する。マージ・公開は別の承認段階とする。

## 証拠の保存確認

当初のx86_64試行の証拠11,915ファイルを回収し、manifestのSHA-256と照合した。実行ソースと現在のソースの一致、2回の最終試行がcheckerでも環境エラー3になること、所有QEMUの回収と停止前の証拠同期、所有containerの停止を確認した。[当時の照合結果](../artifacts/netem-validation-20261016-1pLqF6/verification-summary.json)は`acceptance_incomplete`のまま保持し、今回のARM64成功で書き換えない。artifactsはGit管理対象外。

回収archiveのSHA-256: `3cd7999804a93d31d90e342399adae79364fce88ddc4abc42150ea9dc620da0b`。
