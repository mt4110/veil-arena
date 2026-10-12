# M4の最小試用：別の開発者による実行・診断・再実行

状態：手順の準備。第三者の試用は未実施。外部への連絡、k8s実装は行っていない。本記録はPR提出前の準備内容で、提出後のCI・マージ・公開状態はPRで別途確認する。

## 検証する問いと終了条件

Rust/Nixを使う別の開発者1人が、既存のBlackholeオフライン入口で正常実行、既知の失敗の診断、正常再実行まで進めるかを確認する。これはM3で成立した本人の利用を、他者も再現できるかという最小検証。VMやk8s、未知の攻撃・性能評価へ広げない。

実行・診断・再実行の結果と、導入で詰まった点・必要だった説明・続けて使う理由が記録できれば、この試用は終了する。失敗した場合も終了結果として残す。試用のためだけに環境対応を無制限に増やさない。

## 前提を揃える

- 信頼するArenaとBlackholeのソースを隣接ディレクトリへ置く。第三者へコードを送ったりcloneしたりする操作は、この準備作業では実施しない。
- 最小の基準revisionはArena `9d2c20b9ef6d05c1da42d892a89768d6cd4998ef`、Blackhole `d5037b326beb9a29f3845ec8561ec1ada95f7cd7`。実際に使うrevisionと未コミット変更の有無を記録する。既存作業を強制checkout・削除して揃えない。
- 基準の固定版はRust 1.98.1、Cargo実バイナリ1.98.0、Python 3.14.7。[固定環境](NIX_VM.md)を参照。最初は確認済みのMac ARM64またはLinux x86_64から選ぶ。第三者の新しい環境での成功を確認済みとは扱わない。
- Nixの利用と必要依存の事前準備が必要。インストール・権限変更・初回downloadは試用者が判断する別の準備工程とし、所要時間も試験本体と分けて記録する。

依存が未準備の場合、Arenaルートの固定Nix環境から準備できる。次はネットワーク取得を伴い得るため、cacheが揃っていれば省略する。flake/lockは更新しない。

```sh
nix --extra-experimental-features 'nix-command flakes' develop --no-update-lock-file \
  --command cargo fetch --locked
nix --extra-experimental-features 'nix-command flakes' develop --no-update-lock-file \
  --command cargo fetch --locked --manifest-path ../veil-blackhole/Cargo.toml
```

Linuxで既存vendorを使う場合は[Cargo homeの分離](BLACKHOLE_WORKFLOW.md#cargoキャッシュを分ける)に従う。共有設定を変更せず、試用中も同じcacheを使う。依存不足を試験失敗と読み替えない。準備できなければその原因を記録して止める。

## 最小の3操作

### 1. 正常なBlackholeを実行する

Arenaルートから、他の試験が終わっていることを確認して実行する。

```sh
bash scripts/blackhole-workflow.sh --project ../veil-blackhole
```

通常試験2回の成功と全体結果を確認する。表示されたsummaryの保存先を記録する。確認済みcacheでの成功と、新規端末での導入成功は別の結果。

### 2. 既知の失敗を診断する

診断用snapshotは担当者が[既存の準備手順](BLACKHOLE_WORKFLOW.md#before-snapshotを準備する)で用意する。修正前commitへ追加decoder回帰試験だけを適用したものを使い、base revision・patch・snapshotパスを記録する。開発者の個人パスやGitignored artifactが第三者にも存在すると仮定しない。

```sh
bash scripts/blackhole-workflow.sh --project /absolute/path/to/prepared-snapshot
```

この既知のsnapshotでは終了1が予定どおり。コンソールだけで、失敗段階、実際と期待値の違い、詳細を確認するログ先、後続の状態を説明してもらう。必要なら表示されたログを開き、どの説明が必要だったかを記録する。正常mainを壊して失敗を作らない。

snapshot作成は初回の試用者の必須操作にしない。担当者が準備できないなら試用を止める。第三者が自分でsnapshotを準備できるかは、今回の試用と別の観察項目。

### 3. 正常なBlackholeへ戻って再実行する

操作1のコマンドを再実行し、正常2回と新しい保存先を確認する。cache・以前の証拠を削除して復旧した扱いにしない。cache lockなら先行実行の完了を確認する。

## 試用記録と継続判断

```text
OS/CPU、Arena/Blackhole revision、未コミット変更の有無：
準備済みcacheの使用／新規準備、導入で詰まった点：
正常実行：成功／失敗／未実施、summaryの場所：
失敗診断：段階・実際と期待・ログ先・後続の説明：
正常再実行：成功／失敗／未実施：
追加で必要だった説明・操作：
準備／実行／理解に使った時間：未計測でもよい
既存手順と比べて使う理由、使わない理由：
```

記録はローカルで保管し、ログ全体や端末情報の外部提出を必須にしない。自己申告と担当者の直接観察を区別する。失敗を修正後の成功で隠さない。

他者が実行・診断・再実行でき、実課題で続けて使う理由があれば、その詰まりだけ改善する。既存のCargo/スクリプトで十分、導入・サポート負担が増える、他者利用の必要がない場合は拡張を止める。1人の試用成功を普遍的な製品価値や支払意思の証拠にしない。Pod/CNIの実課題がない限りk8sを追加しない。

この文書の準備は試用実施の承認ではない。第三者への連絡・配布・公開は、対象と内容をレビューしてから別途判断する。
