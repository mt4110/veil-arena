# Wardenの最小接続試験

2026-10-21。Arenaの隔離VMと既存の所有QEMU controllerを使う追加入口。
K3s/F4の完成条件は変更しない。Warden本体のhostname・cgroup・既定observe・停止時fail-openも変更しない。

[今回の検証集計](validation/warden-policy-20261021.json)：Mac ARM64/TCGの新規2 VMで全10段階が成功。
boot IDは別、起動入力は同じ、両VMの証拠同期・QEMU回収を確認した。失敗した準備・起動の記録も保持している。

## 対象と境界

- 初版の入力は所有するaarch64 Linux CLIとBPF object。実行ホストはMac ARM64/QEMU TCG。
- 2 CPU・4 GiBのNixOS VM、外向きNICなし、SSHなし。ホストのhome/store/資格情報をmountしない。
  新規の証拠出力だけ9Pで書込み共有する。ホストのpolicy・route・DNSは変更しない。
- guest名は `veil-warden-sandbox`。guest内に `warden.slice/warden-test.slice` を作り、
  テストクライアントだけを `systemd-run` で所属させる。rootでBPFを扱うのはこのguest内だけ。
- IPv4/IPv6ともloopbackの合成echo。実PHP、実CVE、外部攻撃対象、実秘密は使わない。
  仮想ネットワークのwire試験や本番PHP-FPM受入の代用にはしない。

## 入力と準備

Wardenの既存ビルドディレクトリには `source.tar.gz`、`veil-warden`、`veil-warden-ebpf` が必要。
信頼して所有している成果物だけを使い、未知のダウンロードを実行しない。

```sh
# Arenaのcheckoutで実行。NEW_*は既存でない専用パスへ置換する。
python3 scripts/warden_inputs.py ../veil-warden WARDEN_BUILD_DIRECTORY NEW_INPUT_SNAPSHOT
# 同CPUの専用Linuxビルダーで準備。実行ホストの通常UIDを指定する。
ARENA_HOST_UID=HOST_UID bash scripts/prepare-warden.sh guest NEW_INPUT_SNAPSHOT
# Mac側で、準備済みbundleを新規ディレクトリへファイル単位で転送してから準備する。
bash scripts/prepare-warden.sh runner
```

snapshotはWardenのruntime source・manifest・lock・選定helperのファイル一覧と内容を保存archiveと照合し、
symlink・不一致・欠落・既存出力を拒否する。Git revisionだけで一致とは扱わない。
これは所有するキャッシュの再利用条件であり、署名認証や再現ビルドの証明ではない。

旧CLIのNix glibc/OpenSSL参照は別のNix derivationでautoPatchelfし、Arenaの固定runtimeに合わせる。
元ファイルは変更しない。元のCLI/BPF/helperのhashは `inputs.json`、
実行時CLI/BPFのhashはguest証拠の `runtime-inputs.sha256` に分けて保存する。
patched CLIでの成功を元の配布CLIの任意環境互換性へ一般化しない。

guest bundleは `store.img`、`Image`、`initrd`、`kernel-params`、`toplevel`、`inputs.json`、
`SHA256SUMS` の7ファイル。同じ内容のguestを反復し、実行中はbuild/fetchしない。
Nix store directoryを `docker cp` で丸ごとコピーするとread-only modeで転送に失敗するため、
既存出力を上書きせず、新規の書込み可能directoryへ各ファイルを転送する。

## 実行と判定

```sh
/nix/store/REPLACE-arena-warden/bin/arena-warden BUNDLE daily NEW_OUTPUT
/nix/store/REPLACE-arena-warden/bin/arena-warden BUNDLE acceptance NEW_OUTPUT
```

`daily`は新規1 VM、`acceptance`は新規2 VMを直列実行。同じCPUのbundleと通常host UIDを要求する。
4 GiB VMに対しhost空き/inactive RAMは保守的に5 GiB以上を要求する（測定した最低値ではない）。
guest試験は180秒、Arena controllerは既存660秒、外側の待機は660秒。
中断ではcontrollerへTERMを送り、所有QEMUを回収する。既存出力・失敗証拠・cacheは削除しない。
終了0は成功、3は環境/証拠/検証の失敗、130はworkflow中断。この入口は検証不一致も3で停止する。

| 段階 | 要求する証拠 |
| --- | --- |
| 正常 | IPv4/IPv6の新規TCPが成功し、受信側に各1件の合成payload |
| observe | TCP成功、該当PIDのallowイベント、ルール変更は拒否 |
| enforce | 両familyの新規TCPがEPERM、denyイベントと非ゼロrule ID、受信増分ゼロ |
| 既存接続 | ルール追加前の接続が追加後もecho成功 |
| UDP | 拒否中の同じIPv4/portへUDPが到達 |
| 対象外cgroup | 同じ拒否宛先への新規TCPが成功 |
| 未登録宛先 | 同じportの別IPv4アドレスへ到達 |
| ルール解除 | 両familyの新規TCPが再成功 |
| SIGTERM後 | 拒否を再確認して停止。両family復帰、link一覧復元、制御endpoint消失 |
| SIGKILL後 | 新しいmonitorで拒否を再確認して強制終了。同じ復帰条件 |

拒否はtimeout/ECONNREFUSEDや単なる非ゼロ終了では代用しない。
hostの独立判定器は段階の欠落・拒否errno/受信/対象scope・復帰・boot ID・証拠同期・QEMU回収を照合する。
想定外の通信影響、受信ゼロ不成立、回収失敗は合格にしない。

各runの `warden-result.json`、`warden-inputs.json`、`runtime-inputs.sha256`、
`guest-boot-id.txt`、`vm-stop.json`、`serial.log`、`qemu-argv.json` と上位 `summary.json` を保存する。
生のserialには合成テストの起動引数が含まれる。実秘密を入れず、無確認で公開しない。

## この試験で保証しないこと

既存接続・UDP・対象外cgroup・未登録宛先は、実際に通過することを記録する。
QUICアプリ、HTTP応答による持ち出し、socket受渡し、root/cgroup脱出、負荷・長時間運用、
VM再起動後のルール復元、無停止更新、クラウド互換性、PHP/CVEへの効果は試験しない。
参照TC試験のdropと、実Wardenのcgroup/connect拒否は別の結果として扱う。

将来のAuditについては [設計メモ](WARDEN_AUDIT_DESIGN.md) を参照。今回未実装。
