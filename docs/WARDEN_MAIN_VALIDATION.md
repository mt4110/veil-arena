# Warden：mainの検証結果と再実行手順

2026-10-22、PR #29を取り込んだmainで、Wardenの自動隔離試験を実行した。
Mac ARM64／QEMU TCGの新規2 VMがそれぞれ全10段階を通過し、通信復帰・証拠同期・QEMU回収まで成功した。
本番環境には触れていない。

## 今回確認したこと

| 項目 | 実行条件・結果 |
| --- | --- |
| Arena | main `e489def8a89c983d73ae24653b076aab97653dde`。このcheckoutからrunnerを準備 |
| guest | NixOS、2 vCPU／4 GiB、外向きNICなし、IPv4/IPv6 loopbackのみ |
| Warden入力 | 保持済みのCLI/BPFと準備済みguest bundleを再利用。最新Wardenソースの再ビルドではない |
| 入力照合 | guestソース・Nix guest定義・flake lockの一致と、bundle全入力のhashを確認 |
| ホスト回帰 | Wardenの25件が成功 |
| VM反復 | `acceptance`で新規2 VM。同じbundle、異なるboot ID、証拠形式v3 |
| 終了確認 | 両VMで証拠同期・QEMU回収が成功。強制回収なし。実行後に記録されたcontroller/QEMUのPIDが不在と確認 |
| ホスト状態 | ソース変更なし。Colimaの状態は変更せず、専用ビルダーも停止状態を維持 |

| 試験 | 両VMの結果 |
| --- | --- |
| 正常なIPv4/IPv6 TCP | 各宛先へ接続・受信成功 |
| observe | 接続成功と、同じPID・宛先のallowイベント。ルール変更は拒否 |
| enforce | 両familyで`EPERM`、対応するdenyイベント、非ゼロrule ID、受信増分ゼロ |
| 既存TCP接続 | ルール追加前からの同じsocketで通信継続 |
| UDP | 同じIPv4宛先・portへ到達 |
| 対象外cgroup | 拒否した宛先へ接続成功 |
| 未登録宛先 | 同じportの別IPv4へ到達 |
| ルール解除 | 元IPv4/IPv6宛先への新規TCPが復帰 |
| SIGTERM後 | 両family復帰・link一覧復元・制御endpoint消失 |
| SIGKILL後 | 拒否を再確認した新しいmonitorを停止し、同じ復帰条件を確認 |

集計・ソースhash・起動入力hash・boot ID・証拠hashは
[公開集計](validation/warden-main-20261022.json)に保存した。
これはマージ後mainでの新規実行であり、[マージ前のv3試験](validation/warden-policy-20261022-v3.json)とは別の記録である。

## 準備済みbundleから再実行する

通常ユーザーで、Arenaのリポジトリルートから実行する。
Nixと、同CPUのWarden guest bundleが必要。bundleのある外部ディスクは事前にmountしておく。
この入口の現在のguest準備はaarch64 Linux向けで、今回の受入実績はMac ARM64／TCGに限る。

今回のMacでは次のbundleを使用した。別の環境では、手元で準備したbundleの絶対パスへ置き換える。

```sh
export WARDEN_BUNDLE='/Volumes/Elecom-1TB/Linux/veil-arena/warden-20261022-review-v3/image-files'
```

次のブロックで、現在のcheckoutからrunnerを準備し、新規1 VMを実行する。
runnerのNix storeパスを固定せず、準備スクリプトの出力を使う。

```sh
(
  set -eu
  : "${WARDEN_BUNDLE:?準備済みbundleの絶対パスを設定してください}"
  mkdir -p "$PWD/artifacts"
  warden_runner=$(bash scripts/prepare-warden.sh runner)
  warden_output="$PWD/artifacts/warden-run-$(date +%Y%m%d-%H%M%S)"
  "$warden_runner/bin/arena-warden" "$WARDEN_BUNDLE" daily "$warden_output"
)
```

`daily`は新規1 VMで全10段階を確認する。新規2 VMで繰り返す場合は、上の`daily`を`acceptance`へ変更する。
出力先は毎回新規とし、既存ディレクトリは上書きしない。同じ秒に再実行して名前が重複した場合も拒否される。
bundle・出力先のパスにカンマや改行は使えない。

VMは2 vCPU／4 GiBを使い、hostの空き/inactive RAMが保守的に5 GiB以上あることを起動前に要求する。
再実行中にLinuxビルダーやColimaを起動する必要はない。
runner準備ではNix依存の取得・ビルドが発生し得るが、VM試験中の外向きNICはない。

## 初めての環境で必要な準備

checkoutだけではWardenのCLI/BPFやguest bundleは揃わない。上の外部ディスクのパスも他の環境に存在するとは限らない。
[Wardenの入力と準備](WARDEN_POLICY.md#入力と準備)に従い、次を用意する。

1. 所有するaarch64 Linux CLI/BPFと保存ソースを照合して入力snapshotを作る。
2. 同CPUの専用Linuxビルダーでguest bundleを準備する。
3. 実行ホストへbundleの7ファイルを転送し、`WARDEN_BUNDLE`を設定する。

これは初回準備を省略した配布パッケージではない。元CLIのELF参照をArena用runtimeへ合わせた成果物を使用しており、任意のLinux環境で動くことも保証しない。

## 結果の読み方と証拠

成功時は`warden-policy daily: passed`（または`acceptance: passed`）を表示し、終了コード0になる。
環境・証拠・検証の失敗は3、中断は130。`running`のまま残った記録は成功と扱わない。
`acceptance`では同じ起動入力で異なるboot IDの2 runが必要になる。

- `summary.json`：全体の`status`、起動入力hash、各runのboot IDと結果。
- `fresh-1/warden-result.json`：10段階のclient・event・復帰証拠。`acceptance`では`fresh-2/`も作る。
- `fresh-*/vm-stop.json`：`evidence_synced`・`qemu_reaped`、強制回収の有無と終了理由。
- `fresh-*/serial.log`、`qemu-argv.json`等：失敗時の調査資料。失敗した出力も残して原因確認に使う。

今回の生証拠は検証者の`artifacts/warden-main-20261022/`に保持した。
同ディレクトリの`RESULT.md`に実行時の具体的なパス、`preflight.json`と`postcheck.json`に入力照合とプロセス不在確認を保存している。
`artifacts/`はGit対象外なので、これらは公開リポジトリには含まれない。公開するのは上記の集計であり、VMイメージや生serialは含めない。

## 確認していないこと

既存TCP・UDP・対象外cgroup・未登録宛先の通過は、制御できない範囲を確認した結果である。
UDP/QUIC遮断、PHP/CVE封じ込め、routed wire、クラウド互換性、本番保護、負荷・長時間運用、
再起動後のルール維持、無停止更新、Shadow Audit・動的モード切替、Linux x86_64/KVM受入を証明しない。
K3s/F4の完成条件や、他のVeil試験の受入範囲も変更しない。
