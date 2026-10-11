# eBPFロード失敗と正常試験への復帰

`bpf-load-failure-001` は、専用VM内でkernel verifierが拒否する有効なELFをロードする試験。失敗を通信遮断の証拠とせず、環境エラー・終了3として保存する。その直後の `tc-restored-001` では同じVM内に新しいnetnsを作り、正常な参照eBPFの通過・カウント・限定drop・detach後の通信を確認する。

## 固定fixtureと合格条件

[reference/tc_load_reject.c](../reference/tc_load_reject.c) は固定Clangでコンパイルし、存在しないhelper番号999999を呼ぶ。正常な参照objectは変更しない。fixtureをattach・実行・送信に使わない。Linux 6.12.112では範囲外helperに対する [verifierの処理](https://raw.githubusercontent.com/gregkh/linux/v6.12.112/kernel/bpf/verifier.c) が特定の診断を出してEINVALを返す。

Rust/Aya runnerは次の順序で検証する。

1. 準備時に固定したfixtureを保存し、ELF解析と2 mapの作成を行う。
2. `SchedClassifier::load()` の `ProgramError::LoadError` を取得する。errnoがEINVAL、診断に独立した行 `invalid func unknown#999999` が存在することを要求する。
3. program FDを取得できず、attach・通信試験を開始していないことを記録する。map counterは前後とも0であることを確認する。
4. Ebpf所有FDを解放し、作成したmap IDがkernelから消失したことを照合する。ロード前後のprogram ID集合が同じであることも確認する。

元の `result.json` は `environment_error` / exit 3のまま保持する。一括試験は、正しい原因と後片付けがそろった場合だけ「期待したロード故障を検出した」ケースとして `passed` を記録する。権限不足、別のEINVAL、壊れたELF、map作成失敗、予期しないロード成功、解放失敗、証拠欠落は合格にしない。予期しない成功でもattachや送信へ進まず、所有program/mapを解放する。

## 一括実行と証拠

[既存の準備・daily・acceptance入口](NETNS_WORKFLOW.md) を使う。batch schema 6は13ケースを必須とする。dailyは1 VM、acceptanceは同じimageから新規2 VM。ソース変更に必要な準備を行った後、試験中はビルド・依存取得をしない。旧image、store、依存cache、GC root、過去の証拠は保持する。

`netns-batch/bpf-load-failure-001/` に以下を保存する。

- `fixture.o`、object SHA-256、`verifier.log`とerrno。
- program FD取得有無、map ID、前後のprogram ID集合・counter。
- 未attach・未送信、所有program/mapの解放、`result.json`。

host checkerは保存fixtureのELF64/BPF header・classifier内のhelper call、SHA-256、保存ログとJSONの一致、診断・counter・解放条件を照合する。終了コード3だけでは期待故障にしない。後続の `tc-restored-001` が成立しなければ一括試験全体は合格にしない。

この試験は固定のverifier拒否と後続の正常試験を確認する。あらゆるロード故障、メモリ不足、任意のdriver/kernel、VM panic、キャンセルを網羅するものではない。[実起動の検証記録](BPF_LOAD_FAILURE_VALIDATION.md)に確認済みの範囲を分けて記す。
