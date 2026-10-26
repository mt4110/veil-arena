# F2: 2 VM checkpoint中断・期限検出・正常復帰

対象は追加検証のF2のみ。1 VM標準の完成条件を増やさず、任意時点の中断・電源断・peer故障全般の保証とはしない。

## 固定する故障注入契約

- checkpointは`f2-nodes-ready-before-pods`。serverのAPIで2 Node Ready・固定版を確認し、agentも固定imageの準備を保存した後、Pod/通信fixture適用前にserver runnerを止める。
- 既存1 VMのobserver処理を共通化。所有parentだけへSIGSTOPを送り、Linux procfsで状態Tを観測してから、boot ID・PID・peer boot ID・部分結果をatomicに公開する。
- agentは止めずに所有VMとして待機。hostがagentのatomic markerと部分結果を照合し、専用control領域へpeer-readyを公開する。serverは元のNode期限内でだけこれを待つ。
- 中断時は証拠を確認してから所有server controllerへTERM。期限検出はserver controllerだけの既存watchdog fixtureを使う。server停止後は、同じrunが所有するagentも回収する。
- 両VMの回収・同期失敗、QEMU非0終了、強制回収は環境エラー3を優先。想定した故障で覆い隠さず、後続VMを開始しない。

| run | 注入・処理 | 要求する結果 |
| --- | --- | --- |
| fresh-1 | 上記checkpointでserver runner停止→host TERM | server/agentともcontroller終了3・reason cancelled。停止状態と2 Node/peer/部分証拠、同期、両QEMU回収 |
| fresh-2 | 新規server+agentで通常F2 | 全通常段階、限定deny/対照通過/解除後復帰、正常回収 |
| fresh-3 | 同じcheckpointのserverをwatchdogが検出 | server reason watchdog、agent reason cancelled、各controller終了3。checkpoint後2000 ms以上と両QEMU回収 |
| fresh-4 | 別の新規server+agentで通常F2 | fresh-2と同じ通常成功条件 |

全4クラスタ・8 VMで**600秒を共有**する。Node300秒・Pod120秒・通信probe5秒・通常watchdog660秒・既存外側670秒と資源は変更しない。
故障注入用watchdogは既存1 VMと同じ、起動待ち120秒・実checkpoint観測後2000 ms。通常試験の期限を変更するものではない。
server4 CPU/16 GiB＋agent2 CPU/4 GiB、各disk32 GiB。同時起動は2 VM。hostは6 CPU・利用可能RAM24 GiB以上、出力先空き258 GiB以上（8 disk分＋2 GiB）を要求する。
容量を空けるために旧証拠・cacheを削除しない。

## 準備と実行

新しいguestはF2 checkpoint moduleと共通observerのhashを入力へ持つ。`lifecycle`は不一致・非対応の旧guestをVM起動前に拒否する。
旧bundleは通常のdaily/acceptance用として保持し、同じ名前や保存先へ上書きしない。

```bash
# matching Linux builder/hostの通常ユーザー。同じ固定lockとcacheを再利用する。
bash scripts/prepare-k3s.sh f2-server
bash scripts/prepare-k3s.sh f2-agent
bash scripts/prepare-k3s.sh f2-runner
# 準備出力の実在pathと、新しい証拠先を確認して設定する。
"$arena_f2_runner/bin/arena-k3s-f2" "$arena_server" "$arena_agent" lifecycle "$arena_out"
arena_exit=$?
printf '終了コード: %s\n証拠: %s/summary.json\n' "$arena_exit" "$arena_out"
```

期待は`k3s-f2 lifecycle: passed`と一括終了0。rawの故障runは環境エラー3のまま保持する。
上位summaryだけが、想定故障の独立検証と正常復帰を確認してpassedにする。
`fixture_verified`、`observed_exit_code`（中断130/watchdog3）、実`controller_exits`を区別する。
普通の外部中断は一括全体を止める。回収失敗は3を優先し、入力・証拠不正で次runへ進まない。

## 証拠

- `summary.json`：4 run、想定fixtureとcontroller終了、各boot ID、回収結果。
- 各VMの`result.json`・`vm-stop.json`・`vm-manifest.json`・`qemu-argv.json`。
- 故障serverの`lifecycle-control.json`・`checkpoint-state.json`・`watchdog-ready.json`・2 Node状態。
- agentの`agent-ready.json`と部分result、private controlのpeer-ready。正常runにfault設定を引き継がない。
- checkpointの`.pending`も完了内容の証拠として保持。join token・生disk・serial・kubeconfigは公開しない。

## 実行結果

2026-10-26、Linux x86_64/KVMのhp-ubuntuで4クラスタ・8 VMが成功した。
[機械可読記録](validation/f2-lifecycle-hp-ubuntu-20261026.json)に、base commit `4b22910`に加えた未コミット実装のhash、固定bundle、実終了値、回収結果を保存した。取り込み済みmainの証拠とは区別する。

中断・watchdogでは両controller終了3を保持し、続く各正常クラスタは両controller終了0。
watchdogはcheckpoint観測後2267 msで検出し、2000 msの条件を満たした。
8つのboot IDはすべて異なり、全QEMUが終了0・回収済み・強制回収なし・証拠同期成功。
独立判定器で再照合し、所有QEMU/controller PID不在、host link/main route不変も確認した。

回帰検証はLinuxのK3s関連92件、Macの同92件（Linux procfs用1件skip）が成功。
Macの`check.sh`も成功し、Python217件中1件skip、Rust・Clippy・fmt・3 systemのNix評価を含む。
生証拠はhp-ubuntuの`/home/take/veil-arena-lab/runs/f2-lifecycle-20261026-a01/`に保持する。
Macへ退避した選択証拠はprimary checkoutの`artifacts/hp-ubuntu-f2-lifecycle-20261026-a01/`。

同じ準備済み入力を再利用する場合の入口は次のとおり。既存出力先へ上書きしない。

```bash
# hp-ubuntu / take。profileは今回の固定source・bundle・runnerを参照する。
. /home/take/veil-arena-lab/profiles/f2-lifecycle-prepared.env
arena_out="$arena_base/runs/f2-lifecycle-$(date +%Y%m%d-%H%M%S)"
"$arena_f2_runner/bin/arena-k3s-f2" "$arena_server" "$arena_agent" lifecycle "$arena_out"
arena_exit=$?
printf '終了コード: %s\n証拠: %s/summary.json\n' "$arena_exit" "$arena_out"
```

Macでの2 VM lifecycle実起動と現行F2/F3統合受け入れは未検証のまま。
この試験はserver runnerの停止と両VM回収に限定し、agent停止側・VM panic・物理ホスト停止は別の範囲。
