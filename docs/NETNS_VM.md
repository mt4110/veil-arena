# VM内の正常通信ベースライン

専用Linux VM内にapp・router・serverの3つのnetwork namespaceを作り、eBPFを接続する前の通信経路を検証する。ブラックホールの人工入力試験は別の入口として維持する。IPv4のICMP、TCP、UDPが今回の対象で、IPv6、DNS、eBPF、NetEm、外部接続は含めない。

2026-10-10、Linux x86_64／KVMで正常・経路故障・新規VMでの再成功を確認済み。[検証記録と証拠](NETNS_VALIDATION.md)を参照する。

```mermaid
flowchart LR
  app["app: app0 10.77.1.10/24"] --- left["router: rt-app 10.77.1.1/24"]
  left --- right["router: rt-server 10.77.2.1/24"]
  right --- server["server: server0 10.77.2.10/24"]
```

appには`10.77.2.0/24 via 10.77.1.1`、serverには`10.77.1.0/24 via 10.77.2.1`の経路だけを追加する。routerのIPv4転送を有効にする。default route、NAT、host bridgeは作らない。VMにも外部NICはない。

このガイドの「親namespace」はゲスト内の試験runnerが所属するnamespaceを指す。ホストOSのnamespaceではない。

## 起動

通常の入口は[準備・一括試験・受け入れ確認](NETNS_WORKFLOW.md)。下記の単一ケース用launcherは診断用として維持する。現在の一括試験では同じVM内で16ケースを順に実行し、各通信ケースのnamespaceを新規作成する。

対応するLinuxビルダーで、固定したNix環境とゲストイメージを準備する。[固定版とVMの共通境界](NIX_VM.md)も参照する。Rust 1.98.1、Cargo実バイナリ1.98.0、Python 3.14.7、kernel 6.12.112は継続する。新しいCargo依存は追加していない。

```sh
# Linuxで現在のユーザーが既に/dev/kvmを利用できる場合。
ARENA_VM_ACCELERATOR=kvm bash scripts/build-netns-vm.sh

# 返されたstore pathを指定し、新規の結果ディレクトリへ出力する。
/nix/store/<returned-path>-arena-vm-netns/bin/arena-vm-netns \
  "$PWD/artifacts/netns-vm-001"
```

KVMを使わない対応環境では`ARENA_VM_ACCELERATOR=tcg`を指定する。ホストでroot、sudo、netns操作を実行する入口ではない。ゲストの試験サービスのみrootで動かす。既存出力への上書きと自動削除は行わない。

## 判定と保存

Rustの`veil-arena-netns`で通信試験の期限を90秒にする。各コマンドにも8秒の上限がある。echoの接続・読み書きには2秒の上限があり、サーバーはreadinessを保存してから送信を始める。後片付けには別の猶予を設け、所有子プロセスごとに通常終了2秒・kill後の回収2秒、親ネットワークの確認に合計8秒を上限とする。外側のVM watchdogも継続する。

- 親namespaceがloopbackのみでIPv4経路を持たず、実kernelが固定版であること。
- app・router・serverが親および互いに異なるnamespaceであること。
- 両側gatewayへのpingと、app/server間の双方向ping。
- app→server、server→appのTCP/UDP echo。各payloadの応答を送信内容と照合する。
- routerの転送を止めた際の両方向ping失敗。終了コード1を期待し、実行エラーや期限超過を通信遮断の成功にしない。
- 転送を戻した際のping回復と、両方向TCP/UDPの再成功。
- neighbor情報の保存、所有子プロセスの終了・回収、所有namespaceへのプロセス参照がないこと、親のlink/routeが試験前後で不変であること。

正常runは構築・診断・通信・後片付けを含む51段階。これは攻撃パターン数ではない。`netns-results/result.json`、`topology.json`、段階ごとのstdout/stderr、echo readiness、helperログに加え、VM共通のmanifest・boot input hash・kernel・cmdline・serial・service終了・ホスト停止記録を保存する。ホストの結果checkerは段階ID・順序・終了コード・出力収集・後片付けを照合する。

終了コードは0=正常通信と後片付け成功、1=通信などの期待値との不一致、2=引数不正、3=環境・回収・後片付けエラー。ゲスト内の失敗とホストのVM停止失敗は別に記録する。

## namespaceの寿命

`ip netns add`による名前付きmountを作らず、Rustが所有する3つの子プロセス内で`unshare(CLONE_NEWNET)`を呼ぶ。PIDとnamespace識別子を保存して照合し、そのPIDを指定した`nsenter --net`でiproute2等を実行する。`nsenter`による対象namespaceの指定は[util-linuxの説明](https://www.man7.org/linux/man-pages/man1/nsenter.1.html)に従う。

vethは新規のapp/router namespace内で作る。親namespaceには作らない。通常終了・試験不一致とも、echo helperを停止してからnamespace保持子プロセスのstdinを閉じ、終了を回収する。namespaceを保持するプロセスとFDを自分のコードが残さない構成で、vethの寿命もnamespaceに従う。ファイルや既存namespaceを削除する処理はない。

子プロセスには親終了時のSIGKILLも設定する。ただしVM panic、host停止、外部rootによる介入などで、結果保存と確認が完了する保証ではない。今回の後片付け証拠は所有helperの回収、`/proc`のプロセスnamespace参照、親のlink/route比較であり、任意の第三者が保持したFDを完全に監査する機能ではない。

実行入口とhelperにroot・専用VM boot flagの確認を入れ、通常ホストでの誤起動を拒否する。boot flagは操作ミスを防ぐ条件であり、悪意あるrootコードを封じる仕組みではない。kernelの境界は専用VMに置く。

## 経路故障の対照試験

明示的な試験用定義`flake.lib.mkNetnsVm { ...; omitServerRoute = true; }`を使うと、serverの戻り経路だけを省く。両gatewayへのpingが成功しても、app/server間の通信は失敗することを確認する。正常系の期待値を変更して失敗を合格にしない。

単一ケースの故障指定はVM manifestとboot cmdlineに残す。正常系と同じ読み取り専用ゲストイメージを再利用し、故障指定のためだけにイメージを複製しない。一括試験ではRustの子プロセスに`--omit-server-route`を渡し、ケースの`topology.json`に残す。故障後の正常ケースは同じVM内の新しいnamespaceで実行する。別の新規VMでの確認は受け入れモードで追加する。

## 確認の限界

通信ベースラインの成立は、eBPFのattach・count・dropが正しい証拠ではない。別の[TC ingress参照試験](TC_INGRESS.md)を一括ケースへ接続し、固定IPv4/UDPでこの正常経路との比較を行う。既存veil-blackholeのmacOS DNS観測をLinux遮断器として扱わない。

Linux x86_64／KVMの実起動で確認し、Mac ARM64とLinux ARM64のVMは定義評価と実起動を区別する。PCAPによるpacket単位の照合、IPv6経路、VM panic、host電源断、netnsシナリオ全体のキャンセル試験は今回の対象外。
