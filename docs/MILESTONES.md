# Veil Arena の初期MVPと製品化マイルストーン

2026年10月11日 JST。計画と確認状態。ブラックホールのオフライン接続、固定Nix環境、専用Linux VM、ホストのRust停止コントローラーを追加した。Linux x86_64／KVMゲストで成功、不具合検出、新規VMでの再成功を確認済み。続けてapp/router/serverの正常ICMP/TCP/UDP、転送停止・回復、経路故障の検出、新規VMでの再成功と後片付けを確認した。[netnsの検証記録](NETNS_VALIDATION.md)を参照する。Rust/Ayaによる[TC ingressの固定UDP比較](TC_INGRESS.md)と、drop無効化・誤ったhook・受信helper停止の[故障検出](TC_FAULT_VALIDATION.md)を一括試験へ追加した。固定異常・短縮入力の[BPF_PROG_TEST_RUN](BPF_TEST_RUN_VALIDATION.md)もLinux x86_64の新規2 VMで確認した。さらに[ロード失敗と正常試験への復帰](BPF_LOAD_FAILURE_VALIDATION.md)を新規2 VMで確認した。[一括試験のキャンセル・タイムアウトと復帰](BATCH_INTERRUPTION_VALIDATION.md)もLinux x86_64の新規2 VMで確認した。さらに[guest runner停止・ホストwatchdogと正常再実行](HOST_WATCHDOG_VALIDATION.md)を、停止VM→正常VMの新規2組で確認した。Linux ARM64／TCGとmacOS ARM64／TCGでも、それぞれ新規2 VM・各13ケースの受け入れ検証を完了した。固定fixtureと確認した組合せに限り、M1/M2の初期MVP条件を満たした。公開用の[受け入れ集計](validation/v0.1.0.json)に範囲と未検証事項を記録する。

最初の目的は、自分のネットワーク開発で、環境構築と手動確認の負担を減らすこと。その利用で価値を確かめ、他の開発者にも通用することが分かってから、主力プロダクト候補へ育てる。初期MVPは、専用Linux VM内に小さなネットワークを作り、正常通信と異常入力に対するeBPFの挙動を、証拠付きで繰り返し確認できる環境とする。

最終到達点は、対応を宣言した環境と入力について、開発者がシナリオを選ぶだけで「構築、実行、診断、結果保存、承認済み資源の後片付け」を行え、同僚やCIでも失敗を再現できる検証製品である。全攻撃への防御保証や全クラウドの完全再現を成功条件にしない。

## v0.1.0の受け入れ判定

M0〜M2の初期MVPを完了と判定する。公開・マージ済みという意味ではない。参照eBPFによる固定試験と実Blackholeのオフライン解析を別の結果として扱い、製品の実通信遮断能力を保証しない。

| 条件 | 証拠・判定 |
| --- | --- |
| 実Veil接続 | Blackholeの隔離snapshotで成功→意図的な不具合検出→元コードで再成功。現行の公開revision `a6813c4`でもMacで18段階を再確認 |
| 対応ホストの実起動 | Linux x86_64／KVM、Linux ARM64（専用Colima環境）／TCG、macOS ARM64／TCG。ARM64は各新規2 VM・各13ケースを確認 |
| 参照eBPFの正常・異常 | 正常通信、pass/count/限定drop、解除後の復帰、故障検出、70固定入力の直接実行、verifier拒否を判定 |
| 中断・復旧 | 指定checkpointのSIGINT・期限超過、runnerのSIGSTOP、途中証拠同期、所有QEMU停止・回収、正常再実行を確認 |
| 再利用 | ARM64の4受け入れVMは同じimage/kernel/initrdハッシュ。依存・image・準備出力を保持し、試験中のbuild/fetchなし |
| リリース検査 | 両OSのfmt・Clippy・単体チェックと3 systemのNix評価。並列fixture衝突を修正。CIは単体・定義評価であり、VM実起動とは区別 |

IPv6のwire試験、TCPの限定drop、未定義入力、任意時点の中断、kernel panic・電源断、kernel無応答・QEMU停止不能、Linux ARM64／KVM、他者による再現・性能・製品価値は初版の確認範囲外。以後は実開発で使い、M3の必要性を確認する。これらを全部追加するまで初版を無期限に延ばさない。

M3は[fixture読込み境界の実開発検証](M3_FIXTURE_OPEN_VALIDATION.md)から着手した。既存動作の問題を再現し、Blackhole修正とArenaでの回帰確認をMac／Linuxで実施。[準備・反復・集計の入口](BLACKHOLE_WORKFLOW.md)を追加し、[M3-A〜Cの技術的検証](M3_WORKFLOW_VALIDATION.md)もまとめた。細かな変更ごとにPRを作らず、このまとまりでレビューした。後続の[個別手順との比較と差分レビュー](M3_SELF_USE_VALIDATION.md)で、機械的な比較と証拠検査の補強を完了した。両プロジェクトのPR #3はマージ済み。続けて[DNS予約Zビットの分類修正](M3_DNS_RESERVED_Z_VALIDATION.md)を既存入口で検証した。[失敗表示の改善](M3_FAILURE_DISPLAY_VALIDATION.md)後、本人がコンソールだけで失敗段階と実際・期待の違いを理解し、正常再実行も成功したと報告した。[M3の初期自己利用の受け入れ](M3_SELF_USE_ACCEPTANCE.md)は成立。本人の操作・理解時間、工数削減、維持費は未取得で、当初の全測定条件や製品価値を確認済みとは扱わない。機能追加は止めて普段のBlackhole開発で使う。

## 現在地と前提

netns試験の反復運用は、準備と試験を分離する方式へ変更した。Nix storeの依存と読み取り専用imageを保持し、日常は1 VM内の正常・経路故障・正常・TC比較・3種類のTC故障・BPF直接実行・ロード失敗・一括キャンセル・TC復帰・一括タイムアウト・TC復旧の13ケース一括試験、受け入れ時は追加の新規VMを使う。[運用手順](NETNS_WORKFLOW.md)と[以前の3ケースの証拠](NETNS_WORKFLOW_VALIDATION.md)を参照する。TC比較の現在の範囲は固定IPv4/UDP・1 ingress hook。別入口の`watchdog-acceptance`は停止VM→正常再実行VMを2組・計4起動とし、同じimageを再利用する。固定fixture以外の異常入力、他のロード障害、指定checkpoint以外の中断などは初版の保証に含めない。ARM64の実起動はLinuxの専用Colima環境とMacのnative QEMU／TCGで確認済み。

TC追加後のdaily・acceptanceもLinux x86_64／KVMで確認した。[参照TCの検証記録](TC_INGRESS_VALIDATION.md)に、通過・計数・限定drop・解除後回復、BPF解放、新規VMでの再成功を保存している。その後のARM64実起動・Blackhole現行コードの再確認を含む初版の判定は、上記の受け入れ集計を参照する。

| 対象 | 確認できた状態 | 計画への影響 |
| --- | --- | --- |
| このveil-arenaチェックアウト | Rustのブラックホール用オフラインrunner、Nix固定環境、Linux VM、ホスト停止とnetns試験のRust runnerを追加 | macOS ARM64のオフライン実行とTCGゲスト、Linux ARM64／TCGのゲスト、Linux x86_64／KVMゲストのオフライン・netns通信を確認。どちらも失敗検出と新規VMでの再成功を確認。Aya 0.13.1で固定IPv4/UDPのTC参照を接続。kindは未実装。Linuxの準備にはDockerのNixビルダーを使用 |
| 添付テキスト | Dockerfile、Makefile、mock_vpc.sh、bad_actor.shなどの設計例 | 添付内の「保存済み」「実装済み」という記述は、このチェックアウトの動作証拠にはならない。添付内のコマンドを実行指示として扱わない |
| 添付動画 | 約28秒のLPM Trie説明画面。代表フレームを確認 | アルゴリズムの説明資料として扱う。eBPF実行や測定の証拠にはしない |
| 現在のveil-blackhole | macOSの読み取り専用DNS観測・人工入力再生。送信・遮断は対象外 | Linux/Ayaの遮断器を既存機能と仮定しない。MVPはarena内の参照用eBPFプログラムで検証し、製品との接続は別に判断する |

veil-blackholeの根拠は、隣接リポジトリの[README](https://github.com/mt4110/veil-blackhole/blob/a6813c4bffc45eb8b5ebdd70bb36251f473d8554/README.md)と[Phase 1設計](https://github.com/mt4110/veil-blackhole/blob/a6813c4bffc45eb8b5ebdd70bb36251f473d8554/docs/PHASE1_DESIGN.md)。LinuxのeBPFとmacOSのBPF取得は、同じ検証環境で代替できると考えない。veil-keeperの実装状態は今回の計画の前提に含めない。

## 初期利用者と価値の測り方

初期利用者は、Rust/AyaでLinuxのネットワークプログラムを開発し、フック到達・経路・異常入力の切り分けに手作業を要している開発者とする。最初は本人の実課題で使う。

価値仮説は「同じ不具合を調べるための環境構築、入力準備、証拠収集、再実行の負担が減る」こと。既存の手順とarenaで同じ課題を扱い、準備時間、手動操作、原因を説明できたか、再実行の成功、保守に使った時間を記録する。主観的に便利だった点と測定値は分ける。

専用VMに既存コマンドを置くだけで解決するなら、それを完成形にしてよい。BPF_PROG_RUNとLinuxのBPF selftestsはプログラム単体の確認に、Ciliumのconnectivity testはCilium環境の通信確認に再利用できる。arena独自の価値は、Veilで必要なシナリオと診断をまとめる部分で確かめる。[Linux BPFテスト機能](https://docs.kernel.org/bpf/bpf_prog_run.html)、[Ciliumの通信確認](https://docs.cilium.io/en/stable/gettingstarted/k8s-install-default/)

主力への格上げは、機能数では決めない。自分の反復利用、他の開発者による利用、支払意思と提供費用、現在の主力を遅らせる機会費用を順に確認する。それまでは既存プロダクトを支える検証基盤として扱う。

## 初期MVPの範囲

MVP完了はM2の受け入れ条件に加え、少なくとも1つの実Veilプロジェクトで変更の正否を判定し、繰り返し使えることを確認した時点とする。最初の接続は[ブラックホールのオフライン検証](BLACKHOLE_OFFLINE.md)とする。NixとLinux VMはMVPの必須要件であり、macOS上のオフライン成功だけで完成としない。[固定版と確認状態](NIX_VM.md)を参照する。

| 項目 | MVPに含める振る舞い |
| --- | --- |
| 実行環境 | 専用Linux VMを1種類。ホストはmacOS ARM64とLinux ARM64／x86_64。各ホストと同じCPU系統のゲストを選ぶ。Linuxでの実起動とmacOSからの起動を確認し、未確認の組合せを対応済みとしない |
| 依存固定 | Nix flake/lockを必須とし、Rust 1.98.1・Python 3.14.7・ゲストkernelを固定。実バージョンの照合と証拠保存を行う |
| ネットワーク | app、router、serverの3つのnetns。2つの異なるサブネットをrouterがL3転送する。インターネットへの経路・NAT・ホストLANへのブリッジを設けない |
| 検証対象 | arena専用の小さなAya参照プログラム。まずTC ingressで到達・カウント・限定したテスト通信のdropを確認する。実製品の防御能力とは別の結果にする |
| 入力 | 合成の正常UDP/TCP、明示したルールへの一致・不一致、短い入力や不正長などの固定fixture。送信先、数量、速度、時間を有限にする |
| 自動化 | RustのCLIとguest runnerで起動確認、実行、判定、保存を行う。pytestは導入しない。ip、tc、bpftool、tcpdump等の既存コマンドを再利用する |
| 診断 | pass、期待動作との不一致、環境エラー、未対応、キャンセルを区別する。前提確認が失敗したケースを防御成功にしない |
| 証拠 | 環境manifest、入力ハッシュ、attach情報、カウンタ、受信記録、終了理由、後片付けの状態をrun単位で保存する |
| 復旧 | 途中失敗やタイムアウトでも保存可能な証拠を残す。削除範囲を明示して事前承認された、そのrunの資源だけを後片付けする |

VM定義はNixOS + QEMUを採用し、TCGまたはLinuxでのKVMを明示選択する。確認したLinux x86_64ではTCGの初回コンパイルが300秒の試験期限を超え、KVMでは同じ期限と期待値で成功した。CPU条件はmanifestで区別する。ARM64は専用のLinux Colimaビルダーで準備した同一ゲストをLinux／TCGとMac／TCGで起動し、再実行まで確認した。Linux ARM64／KVMやMac／HVFは未確認。独自ハイパーバイザーは作らない。[QEMUの対応環境](https://www.qemu.org/docs/master/system/introduction.html)

MVPではk8s、k3s、複数CNI、Firecracker、全クラウドの模倣、実マルウェアの実行、脅威フィードの自動取得、公開IPへの通信、遠隔Workerによる削除、GUI、ナノ秒性能の保証を実装しない。Scapyなどの追加は、最小の検証ループが成立した後に行う。

## マイルストーンと判断点

順序はM0 → M1 → M2 → M3 → M4 → M5。M2でMVPを完成させ、M3以降は実利用で必要性が確認された部分に進む。各段階は、成果物と証拠を本人が見て継続判断できる単位にする。後段を自動的に着手済みとは扱わない。

| 段階 | 目的と成果物 | 完了を判断する証拠 | 本人が見るもの |
| --- | --- | --- | --- |
| M0 検証契約 | 最初の実課題、VM方式、TCフック、対象入力、期待動作、安全境界、作業上限を決める。既存実装が別の場所にあれば再利用を判断する | 正常系・異常系・環境失敗を区別したケース表。具体的な作成・削除対象と承認範囲。解消できない仕様矛盾は未決事項にする | 1ページの仕様、ケース表、実行予定の操作 |
| M1 隔離・再現環境 | 固定Nix環境と専用Linux VMを実起動し、共通オフライン試験を接続する。その後app/router/serverとeBPFなしの経路を作る | 固定版の照合、Linuxゲスト実行、停止・新規run。ARP、gateway、双方向経路、正常UDP/TCP到達。外向き接続がないこと | flake.lock、VM manifest、ゲストの成功・失敗・再実行証拠、トポロジー・preflight結果 |
| M2 Rustで自動判定 | 参照eBPF、Rust runner、固定fixture、証拠保存、復旧をつなぎ、実Veilプロジェクトを少なくとも1つ接続する。ここが初期MVP | 下記の受け入れケース。経路・attachの故障を区別し、実プロジェクトの意図的な不具合を検出できる。元コードで新しいrunが成功する | 実プロジェクトのデモ、result.json、失敗の検出と再実行の証拠 |
| M3 実開発で反復利用 | 接続済み対象を実開発で使う。Linux対象に必要なScapy入力、合成PCAPリプレイ、NetEm、bpftrace診断を追加する | 実課題の不具合または変更の再現と回帰確認。対照試験との差、原因、手作業の削減と維持費の記録 | 実課題の前後比較。役立った機能と使わなかった機能 |
| M4 他者とk8sで検証 | 他の開発者が使う。Pod/CNI課題があれば専用VM内のkindとCNIを1種類追加する | 本人以外が手順で起動・診断・再実行できる。Pod間・Service経路とフック順序の試験。導入負担、支払意思、サポート負担の記録 | 利用者の試用結果、k8s試験の証拠、製品化の判断材料 |
| M5 対応範囲を固定してv1 | 必要なkernel/CPU/フックだけに対応表を作り、CI、更新、復旧、配布を整える | 宣言した組合せで機能・異常系・復旧の確認。未対応を正しく拒否。性能条件と再現手順。提供費用を踏まえた販売判断 | 対応表、リリース候補、既知の制約、価格・保守案 |

M3でLinuxの実課題が見つからなければ、k8sへ進まず、現在のmacOS製品に必要なfixtureや観測検証への接続を再検討する。Linux環境の完成を、それだけでVeil製品の進展と数えない。

M4のk8s拡張は利用場面に応じた分岐とする。他者がVM/netnsの検証だけに価値を感じるなら、その構成で製品化を判断する。CNIやkernelの種類を増やすためだけの拡張はしない。

2026-10-12の現在地は、M3の自己利用の成立と[M4最小試用手順](M4_MINIMAL_TRIAL.md)の準備まで。M4の試用は未実施。最初は別の開発者1人による既存Blackholeオフライン入口の実行・診断・再実行だけを確認する。外部への連絡、配布、k8s実装は行っていない。工数削減・維持費の定量記録と第三者価値の確認は残条件として維持する。他者利用の必要がなければ準備で止め、Blackholeの実開発へ戻る。

## MVPの受け入れケース

ケース数は安全性の証明にならない。次の異なる失敗条件を覆い、期待値は実行前に固定する。

| ケース | 合格条件 |
| --- | --- |
| 実Veilプロジェクトの接続 | 実コードの検証が成功する。隔離したコピーに意図的な不具合を入れると失敗し、元コードの再実行は成功する。参照eBPFと別に結果を記録する |
| eBPFなしの正常通信 | 制御されたUDP/TCPが送信側から受信側に届く。これが成立しなければ遮断試験を開始しない |
| count/pass参照プログラム | 指定interface・方向へのattachを確認し、対象入力によるカウンタ増分と受信が整合する |
| drop参照プログラム | 定義したルールの対象入力でdropカウンタが増え、受信側に対応入力が届かない。同じ構成の許可入力は届く |
| malformedまたは短い入力 | BPF_PROG_RUNで定義済みの戻り値・カウンタを確認する。実送信できない入力を無理にwire試験へ混ぜない |
| attach先の間違い・ロード失敗 | 対象外の場所にattachする、またはロードを失敗させたとき、passを出さず環境エラーを報告する |
| routeまたはcaptureの故障 | 正常系対照または収集器のreadiness確認が失敗し、空PCAPを遮断成功にしない |
| タイムアウト・Ctrl-C | 実行を止め、可能な証拠と終了理由を残す。保存や後片付けの失敗も表示する。指定TC attach checkpointのSIGINT・500ms期限と復帰は[確認済み](BATCH_INTERRUPTION_VALIDATION.md)。任意時点・強制停止は未確認 |
| VM無応答・runner異常終了 | ホストのwatchdogが期限で検知する。serial記録とmanifestを保持し、次回起動時に未完了runを照合する。完全な結果が取れない場合は不完全と記録する。guest batchのSIGSTOP・ホスト期限・QEMU回収・新規VMでの正常再実行は[確認済み](HOST_WATCHDOG_VALIDATION.md)。guest kernel無応答・QEMU停止不能は未確認 |
| 後片付け・再実行 | 承認対象のnetns、link、BPF link/pin、VM等の状態を照合する。証拠は残る。既存または他runの資源に触れず、新しいrunを開始できる |

BPF_PROG_RUNの通常モードは戻り値を確認する試験であり、実際のネットワークへのdropを証明しない。VM内の通信試験と結果を分ける。[Linuxの定義](https://docs.kernel.org/bpf/bpf_prog_run.html)

## 実行と証拠の構成

```text
macOS host
  Rust CLI ── VM起動・期限管理・run manifest・結果表示
      │       入力は固定fixture、結果は制限した経路で回収
      ▼
  専用Linux VM                 外部ネットワーク接続なし
    guest runner
      app netns ── router netns ── server netns
                      │
                 TC参照プログラム
      送信記録   attach/カウンタ   受信記録

  artifacts/<run-id>/           生存期間をVMから分離
```

routerにはapp側とserver側でそれぞれ到達可能なgatewayを置く。添付の例はappが10.0.1.10/24なのにgatewayが10.0.0.1で、通常の接続経路ではgatewayがon-linkにならない。共通bridgeと/16の設定だけを「分離されたsubnet間routing」と見なさず、L3構成と往復経路を明示して作り直す。

Rustが実行状態、期限、子プロセス、判定、資源所有を管理する。シェルは既存ツールの呼び出し補助に留める。試験シナリオはguest操作の定義を使い、VMやkindを直接作成・削除しない。外部コマンドは引数を分け、シナリオ入力を任意のシェルコマンドとして展開しない。

```text
environments/vm/    VM起動定義と固定したゲスト情報
crates/arena/       Rust CLIと実行・判定・復旧管理
reference/          arena検証用のeBPF参照プログラム
tests/scenarios/    入力・期待動作・期限のケース定義
tests/fixtures/     合成パケットと出所・期待値
docs/               安全境界、手順、検証結果
artifacts/          run単位の結果。Git管理対象外
```

これは配置案であり、ディレクトリや実装を一括作成しない。Nixとenvironments/vmは最初の実装に含めた。Docker・kindは必要になった段階で追加する。

証拠にはkernel release/config識別情報、BTFの有無、CPU/仮想化方式、ツール版、ソースrevisionと未コミット差分の識別、eBPF objectと入力のhash、topology、フック位置、offload設定、seed、開始・終了・終了理由を含める。PCAPとログは量を制限し、fixture以外の通信やホスト情報を取り込まない。guestからの出力を未信頼として、保存先の逸脱、symlink、既存成果物への上書きを拒否する。

正常・不一致・環境エラー等の結果と、artifact保存・後片付けの状態は別フィールドにする。失敗を黙って再実行して成功扱いにせず、最初の失敗も残す。

入力はrunごとの識別子や限定したflowで対応づけ、対象外のARPや背景通信をdrop件数へ混ぜない。キャプチャ開始は時間待ちだけで判断せず、readinessを確認してから送信する。

## 追加ツールの導入順序

| 機能 | 導入時期と検証する問い | 注意する境界 |
| --- | --- | --- |
| Scapy | M3。SYN+FIN、不正長、checksum、fragment等のうち実課題に必要なものを生成する | Pythonは入力生成に使う。Rustの試験制御と役割を分ける。異常入力をすべて悪性・drop対象と仮定しない |
| tcpreplay | M3。まず合成PCAPで数量・速度・受信確認を行う | 記録のL2宛先やroutingを合わせる。PCAP再生はC2サーバーや応答・マルウェア状態の再現ではない。実検体PCAPは入手元、利用条件、個人情報、宛先書換えを別途確認する |
| NetEm | M3。遅延、loss、duplicate、reorderを単独から検証する | 定義した位置と方向へ適用。dropがNetEm由来かeBPF由来か分ける。再送・重複をどう数えるかは対象製品の仕様で決める |
| bpftrace | M3。既存ログ・counterだけでは解けないkernel経路の調査 | probe/BTFの利用可能性を確認。TCP socket経路のprobeだけでraw packet/XDP到達を判定しない。VM内で対象を限定する |
| tshark | PCAPの解析が必要になった段階 | captureのreadiness、解析失敗、欠落を結果に含める。まずtcpdumpとRustの限定判定で十分か確認する |
| kindとCilium等 | M4。実際にPod、Service、CNI競合の問題が出た段階 | kindは1種類、CNIも1種類から。attach順序や既存filterを確認する。AWS/GCP/Azureのサービス意味論を完全再現したとは呼ばない |
| Nix | M1/M2必須。flake/lockによる依存固定と版照合を実装 | kernelを固定してもdriver、CPU、仮想化まで同一になる保証ではない。依存固定と実測再現を分ける |

NetEmはkernelのtimer粒度や他qdiscの影響を受け、配置によってTCPの結果も変わる。seedと設定を残しても、異なる環境で同一タイミングになるとは限らない。[NetEmの制約](https://man7.org/linux/man-pages/man8/tc-netem.8.html)

## 安全境界と復旧

通常の開発ホストや共用Docker Desktop VMに、破壊試験を持ち込まない。netnsとコンテナはゲスト内部のトポロジー分離に使い、kernel故障の境界は専用VMに置く。特権コンテナは制約を弱めるため、それ自体をkernelの安全な隔離と呼ばない。[Dockerの権限と隔離](https://docs.docker.com/engine/security/)、[Kubernetesの特権コンテナ](https://kubernetes.io/docs/concepts/security/linux-kernel-security-constraints/)

テストguestにホストのHOME、認証情報、Docker socket、汎用の書込み可能なworkspaceを渡さない。入力はread-onlyで渡し、結果は専用領域から制限して回収する。依存取得は準備段階で行い、試験中のネットワーク経路とは分ける。VMでもハイパーバイザーの不具合やhost資源への影響は残るため、無条件の安全保証はしない。

preflightはVM識別、kernel/BTF/必要機能、ツール、権限、経路、資源上限、出力先を確認する。rkhunterやlisten port表示を通過しても「ホストは感染していない」とは結論しない。rkhunterのbaseline更新やホスト設定変更を自動で行わない。

添付のbad_actor.shはそのまま採用しない。乱数ファイルは有効な実行形式を保証せず、その子プロセスと別に起動するcurlにも因果関係がない。公開IPへの通信をローカルの応答stubへ置き換え、有効な合成プロセスを使う。終了コード137だけでは対象検知器によるSIGKILLを証明できず、curlの終了コード9をSIGKILLと同一視しない。対象PID、検知イベント、実際の終了signalを照合する試験は、プロセス検知が必要になった段階で別途設計する。

自動後片付けは、対象、目的、影響、復旧可否を示して明示承認された範囲に限定する。run manifestで所有を照合し、名前が一致するだけの既存資源や共有imageは削除しない。削除承認がない段階は計画表示までとする。今回の設計作業は削除や特権実行の承認ではない。

trapやRustのDropは、SIGKILL、VM panic、host停止で動かない場合がある。ホスト側のwatchdog、serialログ、未完了runの次回照合を組み合わせる。回収できない結果や残存資源はそのまま報告し、「必ず破棄」「必ず証拠保存」と保証しない。

Architecture Guardは、シナリオがenvironment管理を直接実行しないこと、許可外のhost mount・外向きNICがないこと、artifactを追跡しないことを確認する。構造化設定と依存関係を検査し、違反例を入れたときに検査が落ちることを確認する。文字列検索だけを境界保証にしない。

## 性能と理論の扱い

性能評価はMVPの合否から切り離す。M5ではまず測定可能性を確認し、eBPFなし、count/pass、dropの比較を、同じ環境・入力で行う。pps、CPU、メモリ、欠落、run時間を測り、遅延分布は観測点と時刻の対応を確認できる場合に限って測る。ログを有効にした調査と、性能測定を分ける。VM/vethでの結果を実NICやクラウド本番の性能へ一般化しない。

Hash Mapの平均的な検索計算量は、常に1回のメモリアクセスになる保証ではない。容量、collision、cache、同時更新を含む実測が必要になる。LPM TrieのIPv4/IPv6のprefix長は32/128bitだが、それを固定nanosecondの保証に置き換えない。[Hash Map](https://docs.kernel.org/bpf/map_hash.html)、[LPM Trie](https://docs.kernel.org/bpf/map_lpm_trie.html)

Mapの要素更新のatomic性と、複数ルール全体の一括切替は別問題である。RCUがあるだけで全更新中に一貫した世代を読むとは限らない。世代全体の切替が必要になった場合に、Map-in-Map等の方式、各packetの読取り、更新失敗時の維持、回収を設計し試験する。[Mapの更新](https://docs.kernel.org/bpf/map_hash.html)、[Map-in-Map](https://docs.kernel.org/bpf/map_of_maps.html)

「ナノ秒単位で記録できる」「kernel内で処理する」「未知の攻撃を防げる」は別の主張である。実測していない性能値、100%の回帰防止、絶対防壁を製品説明に使わない。

## 投資上限と継続判断

初期の提案は、M0〜M2を実作業10日以内の時間枠で進めること。これは未実装状態からの仮の上限で、納期予測や承認済み予算ではない。M0で実装の再利用、VM起動の難所、実際に使える時間を見て本人が上限を決める。各段階終了時に見直し、上限に到達したら追加を自動で続けない。

M2の次は、実際の開発変更で反復利用する。環境構築が減っても診断が難しくなる、専用VMと既存コマンドだけで十分、Linuxでの実課題がない、保守が主力のリリース・販売を妨げる場合は、機能追加を止めて構成を縮める。

M3の次に主力候補へ進む条件は、他の開発者に同じ不便があり、導入から結果の理解までを再現でき、既存代替を選ばない理由を説明できること。支払意思は仮説として扱い、製品化前に確認する。価格、配布、外部利用者への連絡、公開はこの設計だけで実行しない。

本人が最初に判断するのは、最初の実課題、Linux実装との接続、MVPの範囲、VMと削除の承認範囲、使える時間の上限。最終目標は固定しつつ、実利用の結果で後段の順序と範囲を変える。
