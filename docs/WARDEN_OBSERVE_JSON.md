# Warden W1-1：observe専用JSON・停止解除試験

## 目的と境界

NICなしの隔離VM1台で、新規TCP接続試行のJSON出力と、SIGTERM／SIGKILL後の
所有リンク解除・通信継続を確認します。既存のpolicy／非ロード試験とは別シナリオです。
`--enforce`・`--deny`・policy更新は行いません。拒否リストが空、MODEが0であることを
稼働中と停止直前のMap読み取りで確認します。遮断からの復旧を実証する試験ではありません。

対象はMac ARM64／QEMU TCG／ARM64 Linuxです。x86_64、実サーバー、任意のsystemdサービス、
クラウド、負荷試験、企業運用の受け入れには外挿しません。

## 試験内容

1. IPv4／IPv6のループバックTCP echoをBPFロード前に確認。
2. 固定 `/warden.slice/warden-test.slice` にobserveを開始し、両リンク成立後のreadyを待つ。
3. 両familyの実イベントをPID・TID・cgroup ID・IP・portでクライアントと照合。
   JSONの `connection_result:unknown` と、受信側echo成功を分けて記録。
4. 対象外cgroupの通信が成功し、その接続がobserveカウンタ・イベントに入らないことを確認。
5. SIGTERMで終了コード0、最終統計・shutdownを確認。所有link IDとprogram IDの消失、
   対象cgroupの既存attach属性、同じ宛先への新規echo成功を確認。
6. 同じVM内でobserveを新しく起動し、SIGKILLで終了コード-9を確認。
   正常終了JSONや最終統計は要求せず、独立したBPF状態と受信側で解除・通信継続を確認。
7. guestの終端証拠、controllerのQEMU回収・同期とSIGKILLへのescalationなし、hostでの再判定を確認。

JSONのschema版、instance、連番、scope、mode、unknown、欠落カウンタも照合します。
SIGTERMでは完全な最終行を要求します。SIGKILLで途中の行があれば末尾断片として別記し、
JSONレコードとして数えたり、正常終了の証拠へ補ったりしません。

## IDを比較する範囲

systemdが管理する `sd_fw_ingress`／`sd_fw_egress` のprogram IDは、一時クライアントunitの
起動・回収中に変わり得ます。実試験でも変化しました。これらはIDだけを除いた
attach種別・名前・flags等の一致を要求し、前後の生データを残します。

Warden自身が追加した2つのlink IDとprogram IDは別に追跡し、停止後の消失を必須とします。
programは対象cgroupのattach一覧だけでなく、稼働前・稼働中・停止後のグローバル
`bpftool -j prog show` で確認します。所有IDが稼働前にはなく、稼働中にはあり、停止後にはないことを要求します。
pinや別プロセスの参照でprogram本体が残る場合も不合格です。一覧の欠落は空一覧へ補いません。
許可するのはOS側IDの変化であり、Wardenの残留を無視しません。
グローバルlink一覧は列挙順だけを無視し、全フィールド（link／program IDを含む）の前後一致を要求します。
試験前からあった対象外linkの消失・変更も不合格です。
予期しない残存リンク・欠落イベント・timeout・receiver異常は不合格です。

bpftoolの単一object／1要素arrayを区別して受け取り、Mapの4バイト値は
`0x00` 形式で厳密に照合します。
[Map JSON実装](https://github.com/libbpf/bpftool/blob/main/src/map.c)と
[バイト列出力実装](https://github.com/libbpf/bpftool/blob/main/src/common.c)を参考にし、実VM出力も回帰fixtureへ保全しました。

## 準備と実行

準備コマンドはVMを起動しません。Colimaの自動起動、リモートbuilder、DROP適用も行いません。
入力は先に検証したWarden Linuxパッケージと、そのsource archive・hashを使います。

```sh
# Mac：現在のWardenソースとLinux検証成果物を照合して新規snapshotを作る
python3 -B scripts/warden_observe_inputs.py \
  /path/to/warden-source /path/to/w1-linux-evidence /path/to/new-inputs

# 専用ARM64 Linux builder：新規bundleを準備。UIDは実行するMacのUID
ARENA_HOST_UID=501 bash scripts/prepare-warden-observe.sh guest /path/to/new-inputs

# Mac：host runnerを準備。出力されるNix store pathを記録
bash scripts/prepare-warden-observe.sh runner

# 別ゲート：起動前確認後にだけVMを実行する
/path/to/runner/bin/arena-warden-observe /path/to/new-bundle /path/to/new-output
```

UIDは固定値を流用せず実行ホストの `id -u` に合わせます。
起動前にbundle seal／scenario／CPU／ソースとrunnerの一致、5 GiBの空き・inactiveメモリ、
新規出力先、他のArena試験が動いていないことを確認します。
既存の手動起動VMや他プロジェクトのVMを、停止・回収の対象へ混ぜません。

CLIは新規derivationでArenaのELF依存へrebaseし、入力CLIと稼働CLIの両hashを記録します。
稼働CLIとBPFのhashをguestでも採取し、bundle内の期待値へ照合します。

rootで行うBPF試験の証拠はguest内 `/run` に保存します。
終端処理だけをhost所有者と同じUIDへ落とし、専用9p出力へ新規ファイルとして輸出します。
`terminal.json` を同期後、controllerが読むrootの `service-result.txt` を最後に発行します。
開始失敗でも失敗markerを残し、診断成功として扱いません。

## 結果・再判定

- `summary.json`：hostのシナリオ判定。
- `observe/result.json`：Map、リンク、プロセス、クライアント、受信側の生証拠。
- `observe/term.jsonl`／`kill.jsonl`：実CLIの出力。
- `terminal.json`：出力ファイルhash・書込UID・systemd終了状態。
- `vm-stop.json`：QEMU回収・同期・停止結果。

host runnerの期限は180秒です。今回の検証では、証拠ディレクトリ内の外側実行ラッパーにも240秒の期限を設けました。期限切れは不合格です。
controller終了待ちに失敗した場合は所有controllerの有限停止処理を呼びます。
解除成功とVM停止は別の条件で、両方の証拠が必要です。

[2026-10-23検証記録](validation/warden-observe-json-20261023.json)を参照してください。
初回の解除判定失敗と旧bundleは保全し、修正版を別bundle・別証拠ディレクトリで試験しました。
回帰試験は以下で実行できます。fixture成功を実VM成功と同一視しません。

```sh
python3 -B -m unittest discover -s tests -p 'test_warden_observe.py'
```

## PR #36レビュー修正の検証境界

既存global linkの消失を見逃す部分集合判定を、全フィールドを保つ集合比較へ修正しました。
guestの解除待ちとhostの再判定は同じ比較を使います。SIGTERM／SIGKILLそれぞれについて、
既存linkの一部消失・全消失・追加・program ID変更を拒否し、列挙順の変更は許可する回帰試験を追加しました。

この修正後は追加VM起動・BPFロード・新bundleのbuildを行っていません。
保存済み成功証拠に対する新host判定の再実行と、ローカル回帰試験で確認しています。
旧bundleと成功・失敗証拠は変更せず保全しました。新しいguest判定を含むbundleの実VM検証は未実施です。

2026-10-23の修正後ローカル検証では、対象12件、リポジトリ全体の
`scripts/check.sh`（Rust fmt／Clippy／テスト、Python 222件中1件skip、Nix全system評価）が成功しました。
保存済みのSIGTERM／SIGKILL成功証拠も新host判定で合格しています。
Nix評価はLinux実行検証を意味しません。修正前は追加した消失回帰ケースが不合格となり、見逃しを再現しました。

### 追加再レビュー：global program残留の判定

対象cgroupからのdetachだけでは、program本体のアンロードを証明できませんでした。
guestの終了待ちとhost再判定にglobal programの前・中・後の一覧を必須化し、
linkと対象cgroupから消えてもglobal一覧に所有IDが残るケースを拒否します。

**過去の実VM記録の制限:**
[元の検証記録](validation/warden-observe-json-20261023.json)は当時の判定と値を保全しています。
そこでの `owned_programs_absent: true` は対象cgroupのattach一覧から消えたことだけを意味し、
global一覧からの消失を証明しません。旧記録・実fixture・旧bundleには必須一覧がなく、
現在の判定器では合格にできません。[再レビュー補足記録](validation/warden-observe-program-release-review-20261023.json)を併せて参照してください。

追加VM起動・BPFロード・新bundle buildは行っていません。修正後のglobal program採取と
アンロード判定の実VM検証は未実施です。ローカル回帰で残留の見逃しを再現して修正し、
旧証拠の不足を拒否することも確認しました。過去のリンク解除・通信継続の観測と、新条件の合格を区別します。

### 追加再レビュー：壊れた証拠の終端状態

JSONとして読めてもrecordやMap要素がobjectでない場合は検証失敗とします。
残る入れ子型・要素参照の例外もworkflowで失敗として記録し、終了コード3と
`summary.json` の `status: failed` を返します。VM起動をmockしたhost回帰で、
不正record／Map要素がtracebackで漏れたり `running` のまま残ったりしないことを確認しました。

### 追加再レビュー：ライフサイクルと経過時刻

startup／readyは先頭の各1件のみを許可し、その後の再出現を拒否します。
`elapsed_ns` はASCIIの非負整数文字列として読み、レコード順に非減少であることを要求します。
同時刻は許可し、逆行は拒否します。SIGTERM／SIGKILLの両方で、途中の複数位置へ
startup／readyを挿入するケース、時刻逆行、同時刻、不正な時刻表現を回帰試験で確認しました。
この修正もhost判定のローカル検証のみで、追加VM起動・BPFロードは行っていません。

### 追加再レビュー：途中statsと最終stats

途中のstatsを含む全レコードで `attempted`／`emitted`／`decoded` とbooleanの `final` を必須とします。
この隔離シナリオでは対象接続を2件だけ作るため、各カウンタは文字列の0〜2に限定し、
stats間の逆行を拒否します。正当な途中の0→1→2は許可します。
SIGTERMの最終statsは1件のみで、shutdown直前に置くことを必須にしました。
最終statsの重複、後続イベント、途中statsの欠落・型不正・範囲外・逆行を回帰で拒否します。
追加VM起動・BPFロードは行っていません。

### 追加再レビュー：Map・実行成果物の対応付け

MODEと拒否MapのIDは異なる正整数を要求し、両方が所有する各programの `map_ids` に
含まれることをguest／hostで照合します。無関係なMapのゼロ値・空dumpでは合格にしません。

bundleの封印対象 `inputs.json` にCLI・BPFの絶対Nix storeパスを記録し、guestの設定パス、
各caseの実行argvのCLI／BPFパス、hashを確認する成果物を対応付けます。
Map参照の欠落・型不正・別IDと、別CLI／BPFへの差し替えを合成証拠の回帰で拒否しました。

旧bundleにはこのパスメタデータがなく、旧実VM証拠には必要なglobal program参照情報も
不足しています。旧bundle・実fixture・証拠へ値を補わず、現在の条件では証拠不足として拒否します。
新しいguest／bundle定義の実ビルド・実VM検証は未実施です。追加VM起動・BPFロードは行っていません。
