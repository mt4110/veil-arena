# 本人向け: pi-nodeで1 VM lifecycle試験を実行する

2026-10-21 JST。対応mainは`7017ca72432ce4107ce413ee08771a0685944ebf`（PR #26取り込み後）。
準備済みrunnerの関連9ソースとmain、guestの8起動ファイルとhashを照合済み。
これは既存`pi-node`専用の手順。mainや準備物が変わった場合は対応を再確認する。
今回は手順準備と起動前確認まで。本人が下記を実行して結果を確認するまでは、本人操作TODOを完了にしない。

同日の再確認でもremote main・runnerの9ソース・bundle manifestは一致し、既存Docker経由のpreflightに成功した。
その時点の利用可能RAMは25,111 MiB、空きdiskは263 GiB。実行時に手順2で再確認する。
KVMアクセスはこのDocker経路で確認しており、ホスト一般ユーザーの直接アクセスを前提にしない。

## 1. MacからLinuxへ接続する

```bash
ssh pi-node
```

以降のブロックは、このSSH接続先のLinuxターミナルへ貼り付ける。
既存のDocker権限を持つ一般UID 1000を使う。sudoやホストのgroup／device権限変更は不要。
この手順は既存cacheを使い、guestの再buildやdownloadを行わない。

## 2. 準備物・資源・KVMを起動前に確認する

このブロックではLinuxゲストを起動しない。KVM APIと空VMのfd作成・解放、入力hash、資源を確認する。
新しい名前のpreflightコンテナは実行後も停止状態で保持する。

```bash
arena_base=/home/maakie/veil-arena-validation/f4-checkpoint-20261021
arena_store=/home/maakie/veil-arena-validation/run-1791624441124999000/results/kvm-runtime-copy/nix/store
arena_bundle="$arena_base/image"
arena_runner=/nix/store/5mafjb93l4a2lq5zfdp97x47mcd8zbd4-arena-keeper
arena_image=nixos/nix@sha256:65397cf0c10c60415c5e98ce4c639f1abed1abd256ef88f455028500a204c43b
arena_stamp="$(TZ=Asia/Tokyo date +%Y%m%d-%H%M%S)-$$"
arena_session="$arena_base/self-use-$arena_stamp"
arena_container="veil-arena-selfuse-$arena_stamp"
arena_ready=0

if docker run --pull=never --name "veil-arena-selfuse-preflight-$arena_stamp" \
  --user "$(id -u):$(id -g)" --group-add "$(stat -c %g /dev/kvm)" \
  --device /dev/kvm --cap-drop ALL --security-opt no-new-privileges --network none \
  --cpus 4 --memory 18g --memory-swap 18g \
  --mount "type=bind,src=$arena_store,dst=/nix/store,readonly" \
  --mount "type=bind,src=$arena_bundle,dst=/bundle,readonly" \
  "$arena_image" /nix/store/lb41b0anx1f98y9y5s9mdv97gjgsq740-python3-3.14.7/bin/python3 -B -c '
import os, shutil, sys
from pathlib import Path
sys.path.insert(0, "/nix/store/8kq95wsgjh756a2fka18h1cabig6qjbp-scripts")
from k3s_bundle import check, sha
from k3s_execution import execution
from k3s_host import available_memory_mib
bundle = Path("/bundle")
inputs, seal = check(bundle)
assert sha(bundle / "bundle.json") == "df2aa2ccf51a04d5ade68eb5f4a9661701b9a53bd01c30fbd7e0dc08e1284a47", "prepared bundle changed"
assert os.getuid() == inputs["hostUid"] == 1000, "UID differs from prepared guest"
assert inputs["scenario"] == "k3s-f3" and inputs["system"] == "x86_64-linux", "guest differs"
assert inputs["lifecycle_checkpoint_sha256"] == sha(Path("/nix/store/8kq95wsgjh756a2fka18h1cabig6qjbp-scripts/k3s_checkpoint.py")), "checkpoint source differs"
selected = execution()
assert selected["accelerator"] == "kvm", "KVM required"
cpus = os.cpu_count() or 0
memory = available_memory_mib()
free = shutil.disk_usage(bundle).free
assert cpus >= 4, f"requires 4 CPUs; got {cpus}"
assert memory >= 18432, f"requires 18432 MiB available RAM; got {memory}"
assert free >= 130 * 1024**3, f"requires 130 GiB free disk; got {free // 1024**3}"
print(f"preflight: passed — uid=1000, accelerator=kvm, cpus={cpus}, available_mib={memory}, disk_free_gib={free // 1024**3}")
'; then
  arena_ready=1
  printf '起動前確認に成功。証拠の予定先: %s/lifecycle\n' "$arena_session"
else
  printf '起動前確認に失敗。試験は開始せず、上のエラーを確認してください。\n'
fi
```

期待する表示は`preflight: passed — ...`と「起動前確認に成功」。RAM・diskの数値は実行時の余力で変わる。
4 CPU・利用可能RAM 18 GiB・空きdisk 130 GiBを要求する。F2の6 CPU条件を変更する手順ではない。
preflightと本試験の両方で確認するため、この後に余力が減れば本試験の事前確認で停止する。

## 3. 4 runを1 VMずつ実行する

起動前確認と同じSSHセッションで実行する。新しい証拠directoryとコンテナだけを作り、
既存guest・Nix runtime storeはread-onlyで渡す。過去の証拠directoryは本試験コンテナへ渡さない。
内部のcheckpoint中断・timeout注入は自動実行されるので、手動でCtrl-Cを押す必要はない。

```bash
if [ "${arena_ready:-0}" != 1 ]; then
  printf '先に手順2の起動前確認を成功させてください。\n'
elif (umask 077; mkdir -m 700 "$arena_session"); then
  arena_exit=0
  docker run --pull=never --name "$arena_container" \
    --user "$(id -u):$(id -g)" --group-add "$(stat -c %g /dev/kvm)" \
    --device /dev/kvm --cap-drop ALL --security-opt no-new-privileges --network none \
    --cpus 4 --memory 18g --memory-swap 18g \
    --mount "type=bind,src=$arena_store,dst=/nix/store,readonly" \
    --mount "type=bind,src=$arena_bundle,dst=/bundle,readonly" \
    --mount "type=bind,src=$arena_session,dst=/out" \
    "$arena_image" "$arena_runner/bin/arena-keeper" \
      /bundle lifecycle /out/lifecycle || arena_exit=$?
  printf '終了コード: %s\n証拠: %s/lifecycle/summary.json\nコンテナ: %s\n' \
    "$arena_exit" "$arena_session" "$arena_container"
  docker inspect "$arena_container" --format 'running={{.State.Running}} exit={{.State.ExitCode}}'
else
  printf '新しい証拠directoryを作れませんでした。既存のものは上書きしません。\n'
fi
```

**本試験のブロックを繰り返すと既存directoryを拒否する。** 再実行するときは手順2からやり直して新しい名前を作る。
失敗したdirectory・停止コンテナ・cacheは削除しない。
実行途中はコンソールに出力がない時間がある。全4 runでworkflow期限600秒を共有し、停止処理の時間が別にかかる場合がある。

成功表示の例:

```text
k3s-f3 lifecycle: passed — /out/lifecycle/summary.json
終了コード: 0
証拠: /home/maakie/veil-arena-validation/f4-checkpoint-20261021/self-use-.../lifecycle/summary.json
コンテナ: veil-arena-selfuse-...
running=false exit=0
```

最初の`/out/...`はコンテナ内のpath。ホスト上で見るのは後に表示される`$arena_session/lifecycle/`。

## 4. 結果と証拠を確認する

同じSSHセッションで以下を実行する。

```bash
cat "$arena_session/lifecycle/summary.json"
```

| run | 正常な表示・証拠の読み方 |
| --- | --- |
| fresh-1 | fixture cancel、fixture_verified true、controller_exit 3。停止部分証拠を確認済みという意味 |
| fresh-2 | fixture null、status passed、controller_exit 0。中断後の新規VMで通常Keeper試験に成功 |
| fresh-3 | fixture timeout、fixture_verified true、controller_exit 3。watchdog期限検出を確認済みという意味 |
| fresh-4 | fixture null、status passed、controller_exit 0。timeout後も通常試験に復帰 |

**一括成功は上位の`status: passed`・`exit_code: 0`と4 runの成功で判断する。**
fresh-1／3のguestや`vm-stop.json`の`environment_error`・終了3は、独立検証に合格した故障注入の記録。
単に終了3なら成功という意味ではない。`fixture_verified: true`と、その後の正常runが必要。

各runの証拠は`$arena_session/lifecycle/fresh-{1,2,3,4}/`。

- `vm-stop.json`：reason、qemu_reaped true、qemu_exit_code 0、escalated false、evidence_synced true。
- fresh-1／3の`checkpoint-state.json`・`watchdog-ready.json`：Node Ready後、runnerが実停止状態T、Keeper未開始。
- fresh-3の`vm-stop.json`：reason watchdog、checkpoint_seen true、checkpoint_elapsed_msが2000以上。
- fresh-2／4の`keeper-result.json`：status passed、profileの解除・一覧復帰。
- `result.json`・`vm-manifest.json`：guest結果・固定入力・boot ID。4 VMは異なるboot IDを持つ。

失敗時はコンソールの`Failure:`と`$arena_session/lifecycle/summary.json`のdetail・最後のrunを先に確認する。
事前確認で終了した場合は、VM・summaryがまだ作られていない場合がある。
Docker起動自体の失敗は125などDocker側の終了コードになる場合があるので、エラー文も残す。
SSHが切れた場合は、再接続して表示済みのコンテナ名を`docker inspect コンテナ名`で確認し、
動作中の同じ試験を重複起動しない。`docker logs コンテナ名`で出力を読み直せる。

## 5. 本人確認の報告

次の4点をこのチャットへ知らせる。生disk・serial全体・kubeconfig・tokenは貼らない。

1. 最終表示がpassed、終了コード0、running=false exit=0だったか。
2. fresh-1／3の終了3と、一括試験の終了0の違いを読み取れたか。
3. fresh-2／4の正常復帰と、証拠の保存先を確認できたか。
4. 表示されたホスト側の証拠directory。失敗ならFailure行と終了コード。

本人操作TODOは、この報告を確認した後に更新する。
資源不足のF2／F4統合受け入れ、2 VM側・残る対応ホストのcheckpoint試験は別のTODOのまま。
物理環境・第三者試用・署名タグ・公開は対象外。
