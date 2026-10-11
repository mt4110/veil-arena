# 異常・短縮入力の直接実行

固定のTC参照eBPFへ、VM内から `BPF_PROG_TEST_RUN` を呼ぶ。一括試験の `bpf-test-run-001` に含め、最後の `tc-restored-001` で実通信の比較も再確認する。直接実行はinterfaceへのattachやwire送信を行わない。実通信の到達・遮断は既存のTC試験で別に検証する。

## 入力と期待値

期待値は実行前に固定する。[Rust runner](../src/test_run.rs) と [Python checker](../scripts/check_vm_result.py) がバイト列をそれぞれ構築して照合する。Scapyやpytest、新しい依存は追加していない。

| 入力 | 数 | 期待値 |
|---|---:|---|
| 元のIPv4/UDP frameの0〜33 byte prefix | 34 | syscallがEINVAL。プログラム実行前のkernel拒否、戻り値なし、counter増分なし |
| 34〜45 byte prefix | 12 | プログラム実行、TC_ACT_OK、counter増分なし |
| 不正IHL・IP total length・UDP length、fragment、異なるIP version/protocol | 15 | プログラム実行、TC_ACT_OK、counter増分なし |
| 非IP EtherTypeの14/33 byte frame | 2 | プログラム実行、TC_ACT_OK、counter増分なし |
| 有効なIPv4 option付き対象入力 | 1 | TC_ACT_SHOT、対象seen/dropそれぞれ+1 |
| 正常な対象/対照入力 × pass/count/drop | 6 | mode 0は計数なし、mode 1はseen/pass+1、mode 2は対象seen/drop+1・対照seen/pass+1 |

合計70入力。36入力でプログラムを実行し、34入力はkernelが実行前に拒否する。短いEthernet frameやIPv4 headerをkernelが拒否する条件は、固定ゲストkernelの [Linux 6.12.112 test_run.c](https://raw.githubusercontent.com/gregkh/linux/v6.12.112/net/bpf/test_run.c) に合わせる。kernel拒否をeBPFの処理成功とは扱わない。

参照プログラムは異常入力を計数せず通過させる仕様。防御製品の一般的な「異常packetをdropする」ルールではない。今回、IP total lengthとUDP lengthを実データの長さと照合するguardを追加した。checksum検証や全IP parserの正しさを保証するものではない。

## 実行と証拠

[準備・daily・acceptanceの既存入口](NETNS_WORKFLOW.md) を使う。batch schema 6は13ケースを必須とする。dailyは1 VM、acceptanceは同じimageから2 VMを順に新規起動する。各VM内のdirect testは同じloaded program/mapへ70入力を順次与え、repeat=1、flags=0、ctx未指定で実行する。入力の生成・syscall・map読取り・所有FDの解放はRust/Ayaで行う。

`netns-batch/bpf-test-run-001/` に以下を保存する。

- 入力ごとの `.bin`、SHA-256、hex、mode、errno、戻り値、実行有無、出力hex。
- 各入力の前後のcounterと差分。同一mapの連続性、正常入力での復帰を照合する。
- 固定objectのSHA-256、program/map ID、解放確認、`result.json`。

入力fileは新規作成し、fsyncする。reportは既存の原子的な公開方式で保存する。host checkerは独立したfixture期待値と保存byte列、counter算術・連続性・戻り値・kernel拒否・program/map解放を照合し、欠落した証拠を合格にしない。予期しないsyscallエラー、ロードやmap操作の失敗は環境エラー、実行結果の期待値不一致は試験不合格とする。program/mapはpinせず、FDを閉じた後にkernelからの消失を確認する。

20秒のdirect test期限に加え、既存のbatch・guest service・host watchdogの期限を維持する。syscallから戻らないkernel hangまでこの期限だけで回収できる保証はない。

このfixture集合はboundedな回帰試験で、未知の攻撃全体、kernel panicの不発生、性能、TCP/IPv6、他kernel/CPUの互換性を証明しない。実起動の確認結果は [検証記録](BPF_TEST_RUN_VALIDATION.md) に分けて記す。
