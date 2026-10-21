# F4追加検証: F2・F3の統合受け入れ入口

2026-10-21改訂: 標準完成は [1 VM](F4_STANDARD_ONE_VM.md)。本入口は追加検証として維持し、既存条件・未完了TODOは変更しない。

この入口は、既存F2の新規2クラスタ（計4 VM）とF3の新規2 VMを順番に実行する。
K3sをKubernetes実装として使う。別のkind／kubeadm版は追加しない。
1 VMのcheckpoint中断・期限超過は[Linuxで受け入れ成功](F4_LIFECYCLE_VALIDATION.md)。
2 VM側・残る対応ホスト・本人操作確認は別の残件であり、この入口の成功だけでF4完了とはしない。

## ホストと事前確認

| 実行ホスト | QEMU | CPU設定 | 条件 |
| --- | --- | --- | --- |
| macOS ARM64 | TCG | max | 既存F2・F3と同じ |
| Linux x86_64 | KVM | host | 実行UIDから`/dev/kvm`のopen、API 12、空VM作成が成功する |

KVMが使えないときは環境エラー（3）として止め、TCGへ自動変更しない。
Linux ARM64はguest準備用に利用できるが、この完成契約の実行ホストには含めない。
KVM確認は[Linuxの公開API](https://docs.kernel.org/virt/kvm/api.html)を使い、確認用VMのfdを閉じる。
ホストのgroup、device権限、route、AppArmorは変更しない。
Docker経由なら、既に承認された専用実行経路で`/dev/kvm`を渡し、実行UIDに対してdeviceのgroupをコンテナ内で指定する。
group番号を固定せず、`stat -c %g /dev/kvm`から取得する。コンテナのroot／privileged実行は試験の代用にしない。

最低条件は6論理CPU、利用可能／inactive RAM 24 GiB、出力先の空き194 GiB。
194 GiBはF2の4枚とF3の2枚の32 GiB仮想diskをすべて残すための保守的な事前条件で、実使用量の測定値ではない。
F2は4 CPU・16 GiBと2 CPU・4 GiB、F3は4 CPU・16 GiBを使い、同時に6 VMは動かさない。
準備済みbundleはホストと同じCPU系統・同じ非root UIDを必要とする。

## 準備と実行

F2のserver／agentは[F2手順](F2_K3S.md)、Keeper bundleは[F3手順](F3_KEEPER.md)で準備する。
既存bundleとNix／Cargoキャッシュを再利用し、試験中はdownload/buildしない。

```bash
bash scripts/prepare-k3s.sh f4-runner
# 最終行に出る実際のstore pathを設定する。
arena_f4_runner=/nix/store/REPLACE-WITH-PREPARED-arena-k3s-f4
arena_server=/path/to/server-bundle
arena_agent=/path/to/agent-bundle
arena_keeper=/path/to/keeper-bundle
arena_out=/path/to/new-f4-output
"$arena_f4_runner/bin/arena-k3s-f4" \
  "$arena_server" "$arena_agent" "$arena_keeper" "$arena_out"
arena_exit=$?
printf '終了コード: %s\n証拠: %s/summary.json\n' "$arena_exit" "$arena_out"
```

既存出力・symlinkを拒否する。失敗した証拠も残し、次は別の出力を指定する。
F2は既存の共有600秒以内で2クラスタ、続くF3は既存の共有600秒以内で2 VMを実行する。
各Node 300秒・Pod 120秒・probe 5秒・VM watchdog 660秒などの期限と期待値を変更しない。
統合したことで1つの工程へ1200秒を与えることはない。自動再試行はしない。
停止時は既存の工程が所有controller／QEMUを回収する。工程間の中断でも次工程へ進まない。

## 判定と証拠

- 最初にコンソールの`Failure: f2/f3/final_verification: 原因`と終了コードを見る。
- 全体は`summary.json`、各工程は`f2/summary.json`と`f3/summary.json`。
- F2は`f2/fresh-{1,2}/{server,agent}/`、F3は`f3/fresh-{1,2}/`に実証拠を残す。
- `vm-stop.json`の回収・同期、`vm-manifest.json`の固定入力、`qemu-argv.json`のaccelerator、guestの`result.json`を確認する。

F4は各工程のschema／mode／成功状態に加え、既存のF2・F3判定器で生の証拠を再検証し、6 VMのboot IDが重複しないことを確認する。
F2が失敗したらF3を開始しない。終了コード1（不一致）・3（環境）・4（期限）・130（中断）を保持する。
成功summaryがあっても、生のVM回収・通信・Keeper証拠が不正なら環境エラーにする。
準備・資源の事前確認で止まった場合はVMを起動せずstderrを出すため、全体summaryがまだ存在しないことがある。

## 完成判断に残るもの

Linux x86_64でKVM APIが使えることと、F2・F3の実VM受け入れ成功は別の証拠。
両ホストの実受け入れ、指定checkpointの中断／期限超過→回収→正常再実行、本人操作が揃うまでF4全体は未完了。
過去のLinux／KVM期限超過は原因未確定のまま保持する。
物理環境、第三者試用、署名タグ、公開はこの作業の対象外。
