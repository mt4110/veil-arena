# Wardenサービス同定：非ロード専用シナリオ

2026-10-23、証拠出力のUID・終端マーカー・開始失敗ログを修正し、Mac ARM64／TCGの隔離VM1台で成功しました。
初回失敗と修正後成功の区別、使用した入力、確認できた範囲は[検証記録](WARDEN_NONLOADING_VALIDATION.md)に残しています。
以前の`warden-policy`の成功証拠とは別のシナリオです。

## 何を確認するか

`warden-nonloading-v1`は、通常のsystemd serviceの同定を試す別ゲストです。
Wardenの`diagnose --service`を使用し、既存のloader・attach・拒否・解除シナリオは呼びません。

| ケース | CLIの期待値 | 確認内容 |
| --- | --- | --- |
| running | 1、service_identity=candidate | sleepサービスの同定。loader対象外のまま |
| missing | 1、service_identity=unknown | 存在しないunitをロード・起動しない |
| protected | 2、標準出力なし | SSH対象は診断前に拒否 |
| mixed | 2、標準出力なし | serviceとcgroupの混在指定を拒否 |

終了0だけを合格にしません。実際の終了コード、固定キー、canonical unit、BPF/ネットワークのsyscall追跡、CLI/BPFパッケージのhashを照合します。
追跡中のBPF syscallはEPERMで失敗した場合も不合格です。traceの終了記録、不在・oversize・不正なJSONも検証します。
VM停止では既存のvm-controllerによるreap・証拠同期・正常終了が揃う必要があります。

## ゲストの境界

- 2 vCPU/4 GiB、一時ディスク、NICなし、SSHなし、host home/host Nix store/cert共有なし。書き込み共有は証拠用`/out`だけ。
- hostnameは`arena-warden-preflight`。既存loaderが要求する`veil-warden-sandbox`と異なる名前。
- Warden Nixパッケージをclosureごと含め、CLIをautoPatchelf等で改変しない。BPF ELFはhash/整合性確認の対象であり、ロードしない。
- fixtureはsleepのみ。診断serviceはhostUidと同じUIDを持つゲスト専用ユーザーで実行し、0700の9p出力先に書き込む。
  capabilityはPID 1のnamespaceリンク読取とstraceに必要なCAP_SYS_PTRACEだけ。
- `NoNewPrivileges`、`SystemCallFilter=~bpf`、`RestrictAddressFamilies=AF_UNIX`を併用。CAP_BPF/SYS_ADMIN/NET_ADMINは付与しない。生成unitの設定と診断traceを確認済み。拒否syscallを意図的に発行する障害注入は未実施。
- ゲスト90秒、各CLI15秒、ホストのcontroller待機150秒。中断時は既存の所有プロセス限定の停止処理を使う。
- 成功/失敗時のpoweroffとホストcontrollerによる停止を接続し、ホストは停止証拠を照合。ゲストの通常poweroffとcontrollerによる停止は区別する。結果は`passed-nonloading`であり、`runtime_protection_verified=false`。

既存のUID方式、Keeper、旧Warden bundle、既存証拠は更新・消去しません。

## 準備と起動の分離

以下は準備と実行を分けた手順です。ローカル検証では各段階を実施済みですが、新しい環境での実行には権限・資源・入力の確認が必要です。

```sh
# 対応するLinux builder上で準備だけ（VM起動なし）
bash scripts/prepare-warden-preflight.sh guest /path/to/warden-source
# 対応ホスト上でrunnerを準備するだけ
bash scripts/prepare-warden-preflight.sh runner
```

Warden sourceには`diagnose --service`実装とNix一体パッケージが必要です。
今回使用したサービス同定実装はローカルの未コミット変更です。公開済みWarden main/Releaseがこの前提を満たすとは確認していません。
[検証集計](validation/warden-nonloading-20261023.json)のWarden source hashとパッケージhashを区別してください。
専用のprepareはローカルbuilderのみ指定し、Colima起動やリモートbuilderの自動起動はしません。
Mac ARM64はaarch64 Linuxゲスト/TCG、Linux x86_64はx86_64ゲスト/KVMという既存の実行ルートを使います。
Linux ARM64ホストの起動ルートは今回追加していません。

```sh
# 明示的なVM起動。準備コマンドと別ゲート
/path/to/runner/bin/arena-warden-preflight /path/to/new-bundle /path/to/new-output
# 終了後のゲスト証拠の再判定（VMを起動しない）
python3 -B scripts/warden_preflight.py verify /path/to/new-output/nonloading
```

後者だけではVM停止まで合格にしません。host runnerの`summary.json`と`vm-stop.json`も必要です。
診断のnested markerを閉じて同期し、`terminal.json`を書いた後、controllerが読む出力rootの`service-result.txt`を最後に発行します。開始失敗で診断ディレクトリがなくても失敗マーカーは出し、成功とは認めません。
serviceのstdout/stderrはjournalとconsoleにも接続し、開始失敗をserialへ残します。
既存の`arena-warden`には渡せません。別のscenario sealとguest-systemを検証し、旧policy bundleを拒否します。

## ローカル検証

```sh
python3 -B -m unittest discover -s tests -p 'test_warden_preflight.py'
```

mock/fixtureで判定器とホストの異常系を検証します。関連16件と全体210件（既存1件skip）が成功し、新bundleの実VM1台も成功しました。
開始失敗ログ・失敗マーカーの異常系はローカル回帰で確認したもので、今回のVMでは開始失敗を故意に注入していません。
ローカル成功から「サービスへBPFを接続できる」「企業導入可能」とは判断しません。
