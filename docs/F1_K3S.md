# F1: 固定K3sの1 VM試験

目的は、他のVeilが使うKubernetes sandboxの最小部分を確かめること。
F1は1 server nodeだけを扱う。2 node間通信はF2、Keeper接続はF3、対応ホスト全体の完成判定はF4。
物理環境・第三者試用・署名タグ・公開は含めない。

F1の完了証拠と残る対象は [検証状況](K3S_F1_VALIDATION.md) を参照する。

## 入力と隔離

固定入力は[F0契約](F0_CONTRACT.md)と[入力表](contracts/f0-inputs.json)を維持する。
K3s 1.36.4+k3s1、kernel 6.12.112、Python 3.14.7、QEMU 11.1.1を使う。
同じNixpkgsのarchitecture別airgap archiveを準備時に取得し、実SHA-256を照合する。
方式は[K3s公式airgap手順](https://docs.k3s.io/installation/airgap)と固定NixOS moduleを参照する。
fixtureは固定PythonとArena所有の[client/server](../tests/k3s/fixture.py)から作り、`imagePullPolicy: Never`で使う。
Docker archiveのconfig/layerからOCI形式のmanifestを組み立て、そのdigestとconfig digestを記録する。
このmanifestは同じ実blobの識別記録であり、registryで公開したmanifestではない。
Podの`imageID`をconfig digestと直接比較しない。runtimeの`inspecti`が固定config IDに対応付ける
repository digestとPodの実参照を照合し、`fixture-image-state.json`へ対応を保存する。

ゲストは外向きNICなし。dummy link `arena0` に10.77.10.1/24を設定し、同linkだけの既定経路を持つ。
Pod CIDRは10.42.0.0/16、Service CIDRは10.43.0.0/16。CoreDNS／Flannel VXLANを使う。
ホストのroute・DNS・AppArmor・権限は変えない。F1ではKeeperのprofileをloadしない。
F0の互換準備として、固定Coreutilsのtrue/falseをguestの`/usr/bin`へ通常のELFとして配置する。
Python・aa-execは固定packageへの互換入口、parserは固定store pathとして記録する。
ELF実体・hash・LSM・互換入口を確認し、Keeperの実行試験はF3で行う。

store image・kernel・initrd・32 GiB仮想容量の空base diskはread-onlyで再利用する。
各runは新しいqcow2 overlayを作り、`/var`に使う。K3s DB・containerd・証明書・tokenを共有しない。
run diskにはguestの秘密も残るため、非公開の0700出力内に0600で保持する。
生diskやkubeconfigをPRへ添付しない。JSONの証拠へtoken・kubeconfig・Secretは収集しない。
証拠・disk・cacheは自動削除しない。

## 準備

準備と試験を分ける。準備ではNixの取得を許すが、試験VMにはNICがない。
Linux builderはゲストと同じCPU系統、実行ホストと同じ非root UIDで使う。

```bash
# Linux ARM64またはLinux x86_64 builder側。既存Nix storeを再利用する。
bash scripts/prepare-k3s.sh guest

# 実行ホスト側（Mac ARM64または同CPUのLinux）。既存Nix storeを再利用する。
bash scripts/prepare-k3s.sh runner
```

各コマンドの最終行が生成物のstore path。`artifacts/prepared-k3s/`に生成物ごとのGC rootを保持する。
Macでguestを作れない場合は、Linuxでできたbundleの中身を新規SSDディレクトリへ転送する。
ファイルは`Image bundle.json fixtures.json initrd inputs.json kernel-params run-base.qcow2 store.img toplevel`。
既存bundleや出力への上書きをしない。Nix出力のdirectory modeはread-onlyなので、転送先は先に0700で作り、
上記ファイルだけをtarで転送する。`docker cp`でdirectory modeまでコピーすると途中で書込みを拒否される場合がある。

## 実行と判定

```bash
# 上で得た実物のpathを設定する。下の値を架空のstore pathとして実行しない。
arena_runner=/nix/store/REPLACE-WITH-PREPARED-arena-k3s
arena_bundle=/Volumes/Elecom-1TB/Linux/veil-arena/REPLACE-WITH-BUNDLE
arena_out=/Volumes/Elecom-1TB/Linux/veil-arena/k3s-acceptance-$(date +%Y%m%d-%H%M%S)
"$arena_runner/bin/arena-k3s" "$arena_bundle" acceptance "$arena_out"
arena_exit=$?
printf '終了コード: %s\n証拠: %s\n' "$arena_exit" "$arena_out"
```

`daily`は1 VM、`acceptance`は追加の新規VMを含めて2 VMを直列実行する。
起動前にhash・CPU・UID・新規出力・disk空きdailyは34 GiB以上・acceptanceは66 GiB以上・利用可能／inactive memory 18 GiB以上を確認する。
各VMは4 vCPU・16384 MiB。workflow全体600秒を2 VMで共有し、controllerのwatchdogは660秒。
Node Ready 300秒、Pod Ready 120秒、通信probe各5秒を延長しない。自動retryはしない。

各VMで次を確認する。

1. AppArmor有効・kernel版・cgroup v2を確認し、K3sを起動。
2. APIのReady条件で1 Nodeを確認。kubelet／container runtime版も照合。
3. server/clientの2 Podを作り、Ready・実配置node・実image config digestを照合。
4. Service名 `echo.arena-f1.svc.cluster.local` がServiceのIPv4に解決することを確認。
5. TCP／UDPそれぞれnonce付きpayloadのechoと、server側の受信件数1を照合。
6. guestの完了記録と、host controllerの証拠保存・所有QEMU回収を別々に照合。
7. acceptanceでは同一入力・別boot IDで2回目も成功することを確認。

正常時は`k3s-f1 acceptance: passed — .../summary.json`、終了0。
失敗時も出力を保持する。`summary.json`、各`fresh-N/result.json`のstage/detail、
`vm-stop.json`、`serial.log`を確認する。Node/Pod未成立・保存・回収失敗は3、
検証不一致は1、workflow期限は4、中断は130。passed文字列だけで完了と判断しない。

## 保存する証拠

- `bundle.json`: boot/base入力hash、airgap実hash、fixture archive/config/OCI manifest digest。
- `vm-manifest.json`: run ID、所有UID/PID、資源、入力hash、NICなし。
- `qemu-argv.json`: 実QEMU argv。diskの所有先と隔離条件を確認できる。
- `nodes-state.json`、`pods-state.json`、`coredns-state.json`: Ready・配置先・image ID。Secretは取得しない。
- `result.json`: guestの各判定とnonce照合、boot ID、所要時間。
- `vm-stop.json`: controllerのQEMU PID、reaped、exit、同期と回収状態。
- `summary.json`: 2 VMの成功と別boot IDをまとめたhost側判定。

保存済みの古いnetns bundleにはK3sがない。名前だけを変えて再利用しない。
Linux x86_64/KVMや2 node試験の成功を、Macの1 VM結果から推測しない。

最終受け入れに用いたbundleは`/Volumes/Elecom-1TB/Linux/veil-arena/k3s-f1-20261020/images/guest-4cpu16g32g`、
runnerは`/nix/store/i8m70yhmd88qy63yn947rrrv5kmss615-arena-k3s`。同じMac UID用の不変入力であり、
別ホストではそのホストUID・CPUに合わせてprepareする。
準備用ビルダーの今回の容量は100 GiB。試験用run diskの32 GiBとは別で、既存cacheを保持して拡張した。
