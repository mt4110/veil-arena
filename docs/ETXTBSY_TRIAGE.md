# ダミーlauncherのETXTBSY切り分け

2026-10-18 JST。基点はmain `e2961902fe4eee01b32c6a7405820b5923ded557`。
再現調査は既存Linux x86_64環境・固定ツール・依存キャッシュで実施した。
対象はホスト側workflowのRust単体試験4件のみ。VM・BPF・ネットワーク試験は起動しない。
以下は2026-10-18のローカル検証時点の記録であり、当時未実施のGitHub CIやリリースの確認を意味しない。

## 保存済み証拠との照合

旧 `failed_first_vm_prevents_acceptance_boot_and_preserves_logs` のlauncher内容は、
mainが生成する内容とbyte単位で一致した。旧summaryは `runs: []`、終了3、
`Text file busy (os error 26)`。最初の `--describe` のspawn時に失敗している。
元のarchiveと検証記録は変更していない。

## 上限付きの再現確認

対象1件の単独実行と、同じ単体試験4件の並行実行を比較した。
各条件は最大500反復・150秒、キャッシュ済みビルドと追加追跡を含む調査全体は600秒を上限とした。
予期しない失敗が出た条件はそこで停止し、成功するまで再試行する処理にはしていない。

- 単独条件は500反復で未再現。
- 4並行条件は57回目にETXTBSYを再現。別のfixtureでも同じ起動エラーを記録した。
- open／write／close等を含む広いstraceは60回で未再現。
- 実行系syscallに絞った追跡は28回目、open／closeを含めた追跡は12回目にETXTBSYを再現した。

追跡実行は同じ固定Python・テスト実行ファイルを用いたが、devShell外で実行したためTMPDIR等の
環境は最初の比較と同一ではない。追跡の有無による再現率の比較や、安定性の確率評価には使わない。

## 再現ケースの機構

`trace-fd-11.strace` で確認した順序は次のとおり。PID/TIDは所有テストのnamespace内の値。

1. TID 4142が自分のlauncherをFD 7で `O_WRONLY|O_CLOEXEC` として開く。
2. FD 7が開いている間に、TID 4139が子4143を生成する。cloneはFD表を共有するフラグを持たない。
3. 親側はFD 7をcloseする。
4. 子4143のexec開始前に、別の子4144がFD 7に対応するlauncherをexecし、ETXTBSYで失敗する。
5. その後で子4143がexecする。

Linuxのexecveは、対象が書き込み用に開かれている場合ETXTBSYを返す。
子プロセスはopen FDを継承し、close-on-execはexecまでの保持を防がない。
[execve(2)](https://man7.org/linux/man-pages/man2/execve.2.html)、
[clone(2)](https://man7.org/linux/man-pages/man2/clone.2.html)も参照。
この順序から、並行fixture生成と別テストの子生成が重なり、継承された書き込みFDが
実行を妨げた競合と判断した。旧1件にはsyscall追跡がなく、旧1件も同じ保持元だったとは断定しない。

## 最小修正

Rust単体試験は、実行時にlauncherを書き込む方式から `src/fixtures/` の固定実行可能scriptを
参照する方式へ変更した。3つのscript内容は元の生成内容とbyte単位で同じで、
期待する終了コード・ログ・既存出力拒否のアサーションは維持する。
試験時に生成するのは結果用のdirectoryであり、実行対象への書き込みFDは作らない。

実際の `execute()`、watchdog、診断、VM回収の本番コードは変更しない。
リトライ、sleep、試験の直列化、期限や期待値の緩和は追加しない。
固定scriptは既存のNixソースfilesetの `src/` 内に置き、パッケージ内の試験でも利用できるようにした。

## 証拠と公開判断

ローカル証拠は `artifacts/etxtbsy-triage-20261018/`、Linux側の専用ディレクトリにも
試験出力と追跡を保持する。traceはこの調査で起動した子孫だけを対象とし、他のプロセスは停止しない。
修正前の実行ファイル、旧失敗archive、再現時の出力、元のcacheも保持する。
反復成功だけでは任意の負荷・全環境での無発生は保証しない。
この変更をレビュー・取り込み後、対象headとmainのCIを確認してから公開判断へ進む。

修正後は単独500反復と4並行500反復の両方で未再現だった。
Macではチェック一式に加え、固定fixtureを含むNixパッケージのビルド・内蔵試験も成功した。
Linuxでもチェック一式が成功し、root起動でworkflow単体4件と診断統合15件が成功した。
builderは元の停止状態へ戻し、他の稼働containerの起動時刻が変わっていないことを確認した。
確認範囲・ソースhash・証拠archiveのhashは[公開用集計](validation/etxtbsy-triage-20261018.json)を参照。
反復の件数を任意の環境での無発生保証や改善率には換算しない。
