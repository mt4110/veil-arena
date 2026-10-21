# F3: 固定KeeperのAppArmor exec smokeをVMで判定する

[完成計画](COMPLETION_PLAN.md)の実Veil接続。K3s 1 NodeのReadyを確認した専用VM内で、
F0の既存checker/profileを変更せず実行する。PodへKeeperを移植せず、station-api・SQLite・独自BPF LSM・本番保護の認定は含めない。

## 固定入力と準備

正本は[F0入力表](contracts/f0-inputs.json)の`keeper.input_sha256`。
Keeperのgit HEADだけではなく、選定された2ファイルの実SHA-256を照合する。
元のKeeper checkoutは変更しない。欠落・symlink・過大入力・hash不一致で停止し、既存snapshotを上書きしない。

```bash
# guestと同CPU系統・同じ通常host UIDのLinux builder。既存Nix store/cacheを再利用。
# ../veil-keeperは選定2ファイルがあるcheckoutへの実pathへ置換する。
bash scripts/prepare-keeper.sh guest ../veil-keeper

# 実行ホスト側。現段階の実起動対象はMac ARM64 / QEMU TCG。
bash scripts/prepare-keeper.sh runner
```

入力は`artifacts/keeper-inputs/`の新しいread-only snapshotからNix storeへ固定する。
guest内`/inputs`はこのread-only入力のみ。`/usr/bin/true`・`false`は固定Coreutilsの通常ELF、
Python/aa-execは固定packageの互換入口であり、入力hash・ELF・kernel・LSMを起動後も確認する。
ホストのpolicy・DNS・routeを変更しない。guestには外向きNIC・ホストstore・資格情報を渡さない。

Macへbundleを移す場合は[F1の転送手順](F1_K3S.md)と同じ9ファイルを新規0700 directoryへ転送する。
旧bundle・raw証拠・cacheは保持する。

## 実行

```bash
arena_runner=/nix/store/REPLACE-arena-keeper
arena_bundle=/Volumes/Elecom-1TB/Linux/veil-arena/REPLACE-keeper-bundle
arena_out=/Volumes/Elecom-1TB/Linux/veil-arena/keeper-acceptance-$(date +%Y%m%d-%H%M%S)
"$arena_runner/bin/arena-keeper" "$arena_bundle" acceptance "$arena_out"
arena_exit=$?
printf '終了コード: %s\n証拠: %s\n' "$arena_exit" "$arena_out"
```

REPLACEは準備結果の実在pathへ置換する。`daily`は新規1 VM、`acceptance`は追加の新規VMを含む2 VMを直列実行。
VMは4 CPU・16 GiB RAM・32 GiBの書込みdisk。Node上限300秒、各parser/checker上限20秒、
workflow全体600秒を2 VMで共有し、controller watchdog660秒を維持する。
checker自身の固定ELF試験上限は各5秒。自動延長・通信追加・失敗の自動retryはしない。

## 判定と所有範囲

1. 固定版のK3s Node Ready、kernel・互換ELF・AppArmor稼働LSMを確認する。
2. profile名・mode一覧を保存。対象profileや子profileが既存ならadd/removeせず環境エラー。
3. 一般ユーザーuid501/gid1000・補助groupなしでbaselineを実行。unconfined、true終了0・false終了1、checker終了0を要求。
4. profile未選択で`--expect deny-true`を実行。unconfined・trueのexec成功・checker終了1を要求し、拒否不成立を検出する。
5. `--skip-kernel-load --skip-cache`でsyntaxを確認。一覧の完全一致と対象profile不在を要求する。
6. 同じhashのprofileを`--skip-cache --add`。成功時だけ所有開始。enforceの実一覧と、他profile不変を確認する。
7. 同じ一般ユーザーからaa-execで明示profileを選択。実labelが対象enforce、trueのexecがEACCES/EPERM、false終了1、checker終了0を要求。
8. 所有したprofileのみ、同じhashの定義を`--skip-cache --remove`へ渡す。元の一覧へ復帰した後にbaselineを再実行する。

失敗してもfinallyで所有profileを回収する。追加失敗・syntax成功だけでは所有を主張しない。
remove/一覧復元の失敗は環境エラー3を優先して元の失敗を別欄へ残す。
非zero、timeout、label不一致、ENOENTを「拒否成功」に置き換えない。

## 証拠

- 上位`summary.json`：VMごとの結果・boot ID・controller終了。
- 各VMの`result.json`・`cluster-nodes.json`：入力・実Node・guest判定。
- `keeper-result.json`：各checkerの実label/errno/終了、parser action、前/syntax後/追加後/解除後のprofile一覧。
- `keeper-command-timeout.json`：発生時のstage/argv/budget/所有子回収。
- `vm-stop.json`・`service-result.txt`：証拠同期・停止・QEMU回収。

ホスト側は独立してchecker各段階とprofile lifecycle、VM lifecycleを検証する。
parser stdout/stderr、checker stdoutには固定試験のみを扱い、join token・kubeconfig・Secretを取得しない。
生disk/serialは公開しない。profile一覧の一致は、他profileの本体すべての一致を証明するものではない。

Linux x86_64/KVM、指定checkpointの中断・期限超過と本人操作はF4に残す。
物理環境・第三者試用・署名タグ・公開は対象外。
