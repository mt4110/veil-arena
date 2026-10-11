# TC ingress参照eBPFの比較試験

2026-10-11。専用VM内のrouter側`rt-app` ingressへ小さな参照eBPFを接続し、通過・カウント・指定フローのdropを比較する。これはarenaの試験用参照であり、veil-blackholeのLinux防御機能や未知の攻撃への防御能力を示すものではない。

Linux x86_64／KVMでdailyとacceptanceを実起動確認済み。[検証記録と証拠](TC_INGRESS_VALIDATION.md)へ、初回の失敗を含めて保存した。

## 入力と期待値

指定フローはIPv4/UDP、送信元`10.77.1.10:39100`、宛先`10.77.2.10:39101`、payload先頭`VTC1`。対照フローは宛先portだけを39102へ変える。各段階でそれぞれ4 datagramを送り、段階・種別・連番を含むpayloadと送信元を受信側で照合する。duplicateや別payloadは成功にしない。

| 段階 | 指定フローの受信 | 対照フローの受信 | 指定フローのcounter差分 seen/pass/drop | 対照フローのcounter差分 seen/pass/drop |
|---|---:|---:|---|---|
| plain: eBPFなし | 4 | 4 | 未接続 | 未接続 |
| pass: 接続済み、計数なし | 4 | 4 | 0/0/0 | 0/0/0 |
| count: 接続済み、計数あり | 4 | 4 | 4/4/0 | 4/4/0 |
| drop: 指定フローだけ遮断 | 0 | 4 | 4/0/4 | 4/4/0 |
| detached: 接続解除後 | 4 | 4 | 未接続 | 未接続 |

plain/detachedの保存形式ではmodeをnull、counterをzeroにする。これは未接続段階の形式用で、kernel counterの実測値ではない。接続済みのpass/count/dropは実際のmap読取りである。

受信helperは両portのbind完了を保存してから送信を開始する。送信完了後に500 msの受信観測を行い、受信記録とBPF mapのbefore/after差分を照合する。有限の観測窓であり、任意の遅延下で永久に届かないことを保証するものではない。dropは受信ゼロに加え、対照の到達とkernelの正確なdrop数を必須にする。

ARPや背景通信を指定フローの件数へ混ぜない。今回のC参照はVLAN、IPv6、fragment、異常入力を防御する実装ではなく、対象外は通過する。IPv4 IHLとpacket境界を確認するが、本番用の完全なparserとして扱わない。限定ルールはこの1方向のfixtureに固定し、外部IPやhost NICは指定しない。

## 実装と固定版

制御・ロード・map読取り・判定・資源管理はRust。Linux依存にAya 0.13.1を追加し、Cargo.lockで間接依存も固定する。AyaはMIT OR Apache-2.0。TC ingressは`clsact`とnetlink attachを明示し、TCXへの自動選択を使わない。既存filterがないowned namespaceでpriority 49152、handle 1を使用し、`tc -j filter`のprogram IDをAyaのload結果と照合する。[Aya 0.13.1のTC API](https://docs.rs/aya/0.13.1/aya/programs/tc/struct.SchedClassifier.html)

参照packetコードは[reference/tc_ingress.c](../reference/tc_ingress.c)。固定Clang 21.1.8で`bpfel` objectを生成し、Rust/Ayaがロードする。固定Rust 1.98.1を維持するための最初の実装で、RustのeBPF target・nightly toolchainは導入していない。Rust/Cargo/Python/kernelの既存固定版は継続する。コンパイラとheaderは準備で使い、objectだけをguest closureへ収める。debug source pathとversion文字列がコンパイラclosureをguestへ引き込まないようにしている。

MODEはu32のArray、STATSは6要素u64のPerCpuArray。全CPUの値を合計し、モードごとの差分を見る。map更新は送信を止めた段階境界で行う。この試験は通信中のルール世代切替や並行更新の原子性を検証しない。[AyaのPerCpuArray](https://docs.rs/aya/0.13.1/aya/maps/array/struct.PerCpuArray.html)

## 一括実行と証拠

[通常の準備・daily・acceptance入口](NETNS_WORKFLOW.md)を使う。既存の正常・経路故障・正常にTC比較、3種類のTC故障、BPF直接実行・ロード失敗、一括キャンセル・タイムアウトと各中断後のTC復帰を追加し、dailyは1 VM・13ケース、acceptanceは2 VM・26ケースとなる。通信ケースごとにnamespaceは新規作成する。直接実行ケースではinterfaceへのattachを行わない。TCケースは既存の双方向ICMP/TCP/UDP・転送停止/回復のベースラインを先に検証してから、5段階を実行する。

TCケースに以下を残す。

- `tc-result.json`: attachment、5段階の送信/受信・counter差分、cleanup、判定。
- `tc-ready.json`: program/map ID、object SHA-256、interface、hook、attach方式。
- `tc-<mode>-before.json` / `tc-<mode>-after.json`: kernel mapの値。
- `tc-<phase>-receiver-ready.json` / `tc-<phase>-received.json`: bindのreadinessと照合済み連番。
- 段階別のsender stdout、TC filterのbefore/attached/detached JSON、agent/helperログ。
- `tc-cleanup.json`: detachと、owned program/mapがkernelから解放されたこと。

programやmapをbpffsへpinしない。agent終了時にowned linkを解除してFDを閉じ、program一覧とmap ID検索で、解除後最大1秒まで実際の解放を確認する。期限まで残存すればエラーにする。qdiscはowned router namespaceの寿命に従う。既存filterやhost資源を削除する処理はない。namespace helperの回収と親ネットワーク不変の確認も従来通り行う。

batch resultはschema 6へ変更し、13ケースすべてを必須にする。host checkerも5段階の順序、送信payload、受信連番、counter差分、hook情報、cleanupを照合する。旧3/4ケースのprepared launcherは保持するが、新しいTC試験を含むとは扱わない。ソース変更後は準備で返された新しいlauncherを使う。

[固定の異常・短縮入力](BPF_TEST_RUN.md)を直接実行で別に確認する。

未確認範囲はARM64 VM実起動、IPv6、TCPの限定drop、PCAP照合、異常packetへのfuzz、任意タイミングの中断/panic/電源断全体の回収、他filterとの競合、性能測定。MVPの残り受け入れ項目は[マイルストーン](MILESTONES.md)に残す。


## 故障を見逃さない受け入れ試験

正常TC比較の後、次の3種類を別々の新規netnsで注入し、最後に故障指定なしのTC比較へ戻す。[実起動の検証記録](TC_FAULT_VALIDATION.md)を参照する。

| ケース | 注入 | 必須の観測と元ケースの判定 |
|---|---|---|
| `tc-drop-disabled-001` | drop段階だけ実際のMODEをcount=1にする | 指定・対照とも4件到達、seen/pass/drop=4/4/0。通常のdrop期待値が不一致となり`failed`/1 |
| `tc-wrong-hook-001` | `rt-app`のegressに同じ参照を接続 | ingressが空、egressのprogram ID/direct-action/handleを確認。通常のattach確認が`environment_error`/3 |
| `tc-receiver-stopped-001` | drop受信helperのbind完了後、送信前に所有helperを停止 | 受信helperをsignal 9で回収。readinessファイルがあっても通常の生存確認が失敗し`environment_error`/3。drop送信・受信結果は生成しない |
| `tc-restored-001` | 故障指定なし | 全5段階が元の期待値に戻り、detach後も指定・対照が4件ずつ届く |

終了コードだけの期待失敗は採用しない。host checkerは正常ベースラインと完了したTC段階の順序、各段階の送受信payload・counter算術、故障の実証、program/mapとnamespaceの解放、未送信の段階を照合する。故障検出ケースとしての合格は、元ケースの不合格を上書きせずbatchへ別に記録する。受信helperの停止はready直後の生存確認を検証するもので、任意の時点の異常終了やキャンセル全体の確認ではない。


JSONの完成前にreadinessやsnapshotを読まないよう、guestの保存は新規`.pending`へ書き込み・file fsyncを完了してから、hard linkで最終名を原子的に公開する。既存の最終名があれば上書きせず失敗し、`.pending`も証拠として保持する。共有出力filesystemのhard link対応が必要で、非対応を黙って途中公開へ戻さない。この方式はfileの公開順序を保証するもので、VM電源断時の全証拠永続化を保証するものではない。
