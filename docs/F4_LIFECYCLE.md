# F4: 1 VMのcheckpoint中断・watchdog期限検出と正常復帰

実行単位はF3の1 VM。checkpointは`f3-node-ready-before-keeper`。
Node Readyを実APIで確認し、Keeper profileを追加する前に、専用observerが所有guest runnerへSIGSTOPを送る。
`/proc`で停止状態Tを観測してから、完了した証拠をatomicに公開する。
runner停止を単に仮定したり、K3s未起動を意図した停止として認めたりしない。

既存pi-nodeで準備済みrunner・guestを使う本人操作は、[具体的な自己利用手順](F4_LIFECYCLE_SELF_USE.md)を参照する。

## 準備と実行

既存のNix／Cargo cache、固定kernel・K3s・OCI入力を再利用する。
checkpoint moduleとそのhashを持つ新しいF3 guestを別の生成物として準備する。旧bundle・証拠は残す。

```bash
# matching Linux builder、実行UIDと同じ一般UIDで準備する。
bash scripts/prepare-keeper.sh guest /path/to/veil-keeper
# 実行ホスト側で準備する。
bash scripts/prepare-keeper.sh runner

# 各コマンドが返した実際のpathを設定する。
arena_runner=/nix/store/REPLACE-WITH-PREPARED-arena-keeper
arena_bundle=/path/to/new-checkpoint-capable-keeper-bundle
arena_out=/path/to/new-lifecycle-output
"$arena_runner/bin/arena-keeper" "$arena_bundle" lifecycle "$arena_out"
arena_exit=$?
printf '終了コード: %s\n証拠: %s/summary.json\n' "$arena_exit" "$arena_out"
```

ホスト・KVMアクセス・bundle照合は既存[F4入口](F4_ACCEPTANCE.md)と同じ。
同時実行は1 VM、4 CPU・16 GiB RAM・32 GiB仮想disk。
ホストは4 CPU以上・利用可能／inactive RAM 18 GiB以上と、4回分の証拠・disk保持用に空き130 GiBを確認する。
出力は新規directoryのみ。一般UIDで実行し、ホストのpolicy・route・device権限は変更しない。

## 固定した順序と期限

| run | 処理 | 必須結果 |
| --- | --- | --- |
| fresh-1 | Node Ready checkpointでrunner停止を観測し、所有controllerへTERMを送る | reason cancelled、停止状態と部分証拠、QEMU回収 |
| fresh-2 | 別の新規VMで通常F3 | Node ReadyとKeeper全段階、解除後baseline、正常停止 |
| fresh-3 | 同じcheckpointでrunner停止。host watchdog fixtureに検出させる | reason watchdog、checkpoint観測後2000 ms以上、部分証拠、QEMU回収 |
| fresh-4 | 別の新規VMで通常F3 | fresh-2と同じ成功条件 |

全4 runで**既存workflowの600秒を共有**する。runごとに期限を再設定しない。
通常のNode 300秒・Keeper各command 20秒・VM watchdog 660秒を維持する。
故障注入のtimeout runでは既存controllerの`--watchdog-fixture`を再利用する。
この既存fixtureは起動待ち120秒、実停止checkpoint観測後2秒。通常の600／660秒を2秒に変更するものではない。
120秒以内にcheckpointへ到達しなければ、想定した期限検出の成功にはしない。
正常runへ故障注入を引き継がず、自動再試行・期待値変更は行わない。

## 証拠と終了コード

各runに`vm-manifest.json`、`qemu-argv.json`、`result.json`、`cluster-nodes.json`、`vm-stop.json`を残す。
fixture runにはさらに次の証拠を残す。

- `lifecycle-control.json`：checkpointと注入action。
- `checkpoint-state.json`：停止したrunnerの実`/proc`状態、boot ID、profile一覧、Keeper未開始。
- `watchdog-ready.json`：所有observerが停止状態を観測した後の公開marker。
- `*.pending`：atomic公開に使った完了ファイル。削除せず保持する。

Node Ready・固定入力・同一boot ID・PID対応・状態T・対象profile不在を独立に検証する。
`keeper-result.json`やservice完了markerがあるfixtureは、想定した途中停止とは認めない。
`checkpoint-error.json`がある、保存不能、回収不能、QEMU非0終了、escalation、同期失敗は環境エラー3。
これらを「想定した中断／timeout」で上書きしない。

controllerは中断／watchdogを従来どおり環境エラー3として記録する。
上位summaryは、`fixture`・`fixture_verified`・`observed_exit_code`と実`controller_exit`を分けて保存する。
`observed_exit_code`は中断130／watchdog3の意味上の分類で、guestが130で終了したという主張ではない。
想定故障の独立検証と、その後の正常復帰が全て成功した場合だけ一括の終了0となる。
外部からのユーザー中断は一括全体を止め、回収正常なら130、回収異常なら3。途中の証拠を残す。

## F4の残件

2026-10-21改訂後は、本入口を[標準1 VM](F4_STANDARD_ONE_VM.md)のcheckpoint受け入れに用いる。
Linux x86_64/KVMの本人4 runは成功確認済み。Mac ARM64/TCGも[checkpoint 4 run成功](validation/f4-standard-one-vm-mac-20261021.json)。Linuxの本人操作成功を保持し、Mac本人再実行は任意とする。
Linux F2の6 CPU不足、両ホストのF2・F3統合新規受け入れ、2 VM側checkpointは追加検証TODOとして保持する。
購入や別マシン追加を標準完成の前提にしない。1 VM成功を追加検証の成功へ読み替えない。
物理環境・第三者試用・署名タグ・公開は対象外。
