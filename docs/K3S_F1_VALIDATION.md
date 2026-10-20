# F1の検証状況（2026-10-20）

**F1完了：F0の互換入口を含む最終版が、Mac ARM64／TCGの新規2 VMで成功した。**
最終ゴールは他のVeilが使えるVM・仮想ネットワーク・K3s環境。今回はF1の1 nodeまで。
F2の2 node、F3のKeeper実行、F4の両対応ホスト・本人操作は残る。

## 実行した検証

基点mainは`f0e5bdab583b22143c6acd23f0b592ebe3f0f4e9`。
[マージ後mainのCI](https://github.com/mt4110/veil-arena/actions/runs/39353311408)成功を確認した。
受け入れ試験時点は未commit・未pushだった。基点CIを変更後のGitHub CIとして扱わず、PRの対象コミットに対するCIは別途確認する。

Mac ARM64のnative QEMU／TCG、4 vCPU・16384 MiB RAM、32 GiB仮想run disk。
既存のLinux ARM64ビルダー・Nix store・依存を再利用して専用guestを準備した。
NICなし、Flannel VXLAN、dummy link、固定airgap入力。raw disk・token・kubeconfigは公開しない。

| 新規VM | boot ID | QEMU生存時間（controller実測） | 結果と回収 |
| --- | --- | --- | --- |
| acceptance-6-4cpu16g32g/fresh-1 | `27a2534f-f3d2-4ccb-819d-809f10915409` | 113.900秒 | passed／exit 0／reaped |
| acceptance-6-4cpu16g32g/fresh-2 | `dab3fc5e-ac8b-42d7-8af5-0c89d8ca7c4d` | 105.419秒 | passed／exit 0／reaped |

各VMで、1 Node Ready、server/clientの2 Pod Ready、CoreDNS Ready、固定configに対応する実image参照、
クラスタServiceのDNS・TCP・UDP、nonceとecho・受信件数1を確認した。
証拠同期・所有QEMU回収・非escalation・exit 0が揃い、別boot ID・同一bundleで再成功した。
Node 300秒、Pod段階120秒、通信probe各5秒、workflow全体600秒・watchdog660秒は変更していない。

証拠はSSDの`Linux/veil-arena/k3s-f1-20261020/acceptance-6-4cpu16g32g/summary.json`と各`fresh-N/`。
[機械可読集計](validation/f1-k3s-20261020.json)に実入力hash・判定を記録する。
最終版は互換ELF／入口の実ファイル確認を含む。先行した2 CPU・6 GiB・24 GiBの通信試験は
`acceptance-5/`とJSONの`preliminary_network_acceptance`に残し、新しい資源設定での受け入れと区別した。

## 修正した点

- 固定QEMUでは`-drive`のserial指定が使えないため、virtio device側へ識別子を設定した。
- OpenAPI取得を通信probe用の5秒で切っていた。API操作をNode／Podの残り絶対期限に制限し、スキーマ検証を維持した。
- Podのmanifest参照とconfig IDを直接比較していた。固定configとruntimeのrepository digestの対応から実Podを照合する。
- 不完全な証拠・回収失敗を成功にしない。失敗した固定API操作の限定ログとfixture Eventsを保存する。

Pod準備中にAppArmor parserのPATH不足を仮説にしたが、実際にはPod Readyへ進み、image照合で失敗していた。
PATH不足を確定原因として扱わず、その仮説に基づくPATH変更は取り込んでいない。AppArmorを無効化していない。

## 最終版の準備と確認

F0で要求した通常ファイルの`/usr/bin/true`・`false`を固定Coreutilsから配置し、ELF／hashを照合する実装を追加した。
固定Python／aa-execの互換入口とparserの絶対pathを保存し、欠けた・danglingの入口を拒否する。
`aa-exec`の実packageは固定Nixpkgsの`apparmor-bin-utils`であり、Python用`apparmor-utils`とは分ける。
Keeper profileのsyntax・add・removeやcheckerの実行はF3。F1では行わない。

Mac／Linux ARM64の最終F1単体試験は、それぞれ13件成功した。
最終ソースのMac全チェック（fmt／clippy／Rust・Python回帰／Nix評価）は成功。
修正済み互換入力の再build・seal、追加の新規2 VMでの最終受け入れも成功した。
通常ELFのhash、Python／aa-execの実行可能な互換入口、固定parser、LSMを確認した。Keeper profileはloadしていない。

専用Colima `veil-arena-mvp-arm64` の60 GiBディスクが満杯になり、最終準備のGC root作成で失敗した。
cache・旧イメージ・失敗証拠は削除していない。専用builder／profileはStoppedへ戻した。
ユーザーの承認後、ビルダーを100 GiBへ拡張した。内部filesystemは約99 GiBとなり、
既存Nix store・Cargo cacheを保持したまま最終準備とGC root作成が成功した。終了時は専用profileをStoppedへ戻した。
試験VMも承認された4 CPU・16 GiB・32 GiBへ変更し、旧資源はF0 JSONの`initial_f1_limits`、
現行資源は`f1_limits`に記録した。期限・期待値は変更していない。

受け入れ試験時点ではcommit/push/PRは未実施。今回の変更はPRで取り込みを判断する。
物理環境・第三者試用・署名タグ・公開は対象外のままとする。

F2の2 node、F3のKeeper実行、F4のLinux x86_64／KVMを含む最終受け入れ・本人操作は未実施。
