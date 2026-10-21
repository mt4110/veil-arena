# F3 Keeper最小接続の検証（2026-10-21）

最終ゴールは他のVeilの変更を判定できるVM・仮想ネットワーク・Kubernetes環境。
F3ではF0で選定した既存KeeperのAppArmor exec smokeだけを接続した。
基点mainはPR #24のマージコミット`f6c4ca1f395f73e709e7541c3a47fcad89092de3`。
受け入れ時は未commit・未push。GitHub CIは今後の対象コミットで別途確認する。

## 固定入力と境界

Keeperの`tools/m0_exec_check.py`と`experiments/apparmor/m0-exec-check.profile`を、F0のSHA-256で照合。
元checkoutは変更せず、新しいread-only snapshotをNix storeへ固定した。
F0のparser・Python・Coreutils・K3s・kernelと、4 CPU/16 GiB RAM/32 GiBの単一VMを使用。
Node300秒・各parser/checker20秒・workflow600秒（2 VM共有）・watchdog660秒を維持した。
固定checker自身の各ELF上限5秒も変更していない。

ホストにはprofile・route・DNSの変更を加えず、外向きNIC・ホストstore・資格情報をguestへ渡さない。
trialの管理は専用guest root、checkerはuid501/gid1000・補助groupなし。
対象は明示aa-exec選択の固定profileだけで、replace・全profile reload・killallは使わない。

## 回帰

- Mac全チェック成功：fmt/clippy、Rust回帰、Python146件、Nix全system評価。
- Linux ARM64のPython回帰146件成功。F3対象は15件。
- 固定入力のhash/size/symlink/書込みmode不一致、profile衝突を拒否。
- syntaxで一覧が変わる場合やadd失敗で所有を主張せず、removeしない。
- add成功直後の証拠保存失敗でも、確定済みの所有状態に従い対象をremoveする。
- checker timeout・別errno・別label・別uid・対照falseの終了不一致を拒否成功にしない。
- remove失敗は環境エラー3を優先し、元の失敗を保持する。
- 実ダミー子のtimeoutで所有process groupの停止・子回収と次の正常実行を確認。
- ホストverifierも独立してNode、checker各段階、parser action、一覧復元とVM lifecycleを照合。

## 最終ソースの新規VM受け入れ

Mac ARM64／QEMU TCGで、最終bundleの新規2 VMを直列実行し、両方成功・一括終了0。
先行版の新規2 VMも成功しているが、add直後の保存失敗への補強前なので最終証拠とは区別し、保持した。

| 段階 | 両VMでの実観測 |
| --- | --- |
| K3s | 固定版の1 Node Ready |
| baseline-before | unconfined、true終了0、false終了1、checker終了0 |
| 拒否不成立の対照 | profile未選択のdeny-true。unconfined、true終了0、false終了1、checker終了1を検出 |
| syntax | 非loadのsyntax成功、profile名/mode一覧が完全一致、対象不在 |
| confined | `veil_keeper_m0_exec_check_20261016 (enforce)`、trueのexecがEACCES（errno13）、false終了1、checker終了0 |
| 解除・復帰 | 所有profileをremove、元の一覧へ完全復元、対象不在。baseline-afterが再成功 |
| VM停止 | 証拠同期・同期後停止・QEMU exit0・reaped・非escalation |

boot IDは`7a678351-3639-4a27-a32a-a5bd3d378efa`と`3c6a107d-1c10-484b-9962-0f7212aeaf41`で異なる。
同じ固定bundleを使い、終了時にもsealを再照合した。
QEMU生存時間は各controller実測70.432秒／70.763秒。workflow全体の性能保証には読み替えない。
終了後の所有controller/QEMU PID不在、現行source hashと受け入れpinの一致を再確認した。
専用builder/containerは停止し、cache・旧bundle・先行証拠を保持。元Keeper checkoutのdirty状態も変更していない。

[機械可読記録](validation/f3-keeper-20261021.json)に実観測・固定入力・source hash・lifecycleを保存した。
SSDのraw証拠は`Linux/veil-arena/keeper-f3-20261021/acceptance-final/`、最終bundleは`image-final/`、source pinは`source-final.json`。
先行版は同じrootの`acceptance/`・`image/`・`source.json`へ保持した。
生disk・serial・秘密を含むK3s stateは公開しない。

## 残る範囲

F3の最小exec smokeは成立した。Keeperのstation-api/SQLite/独自BPF LSMや本番保護は未検証。
F4のMac ARM64とLinux x86_64/KVMによる最終一括受け入れ、指定checkpointの中断・期限超過・正常再実行、本人操作が残る。
物理環境・第三者試用・署名タグ・公開は対象外。今回の受け入れ時点ではcommit・push・PRは未実施。
