# Blackhole専用チャットへの引継ぎプロンプト

2026-10-16 JST時点。以下をBlackholeのチャットへ貼り付けて使う。これは引継ぎ資料であり、このArena作業ではBlackholeの実機確認を開始していない。revisionや端末の状態は再開時に確認する。

```text
このチャットはveil-blackholeを主目的にします。
GPT-6.1-sol／mediumで、まず現状確認と次の実機確認の準備まで進めてください。

長期目標は、自分のプロダクトから持続的な収益を作ることです。
Blackholeの今回のマイルストーンは、対応範囲を限定した実DNS観測について、
取得・解析・集計・正常終了を実機で確認し、実利用へ進む残課題を絞ることです。
新機能や解析対象を増やすこと自体をゴールにしないでください。

【プロジェクトの役割と経緯】
・veil-arenaは検証を実行・判定し、ログと証拠を残す基盤です。
・veil-blackholeの現行実装はmacOSの読み取り専用DNS観測・オフライン解析です。
  ArenaのLinux/Aya参照eBPFによる限定dropとは別実装です。
  Blackholeが通信を遮断できる、Linux Liveに対応する、と解釈しないでください。
・ArenaのMVP後、実プロジェクトで役立つことを確かめるため、Blackholeの既存
  オフライン試験を接続しました。その実課題としてBlackholeの修正も進みました。
・ArenaのM3では、本人が正常実行、既知の失敗の診断、正常再実行まで確認しました。
  第三者の試用は保留です。ArenaチャットはArenaの導入・再現性を主目的に戻しました。
  今後のBlackhole開発と実機確認は、この専用チャットで扱います。

【完了した修正】
1. fixture読込み時の最終要素symlink／FIFOへの差し替えを拒否する修正。
   O_NOFOLLOW | O_NONBLOCKで開き、FDが通常ファイルであることを再確認します。
   親ディレクトリや通常ファイル内容のすべての差し替えに対するsandboxではありません。
2. DNS予約Zビット0x0040をmalformedに分類。AD/CDは維持します。
   12 bytes未満のDNS固定ヘッダーもmalformedで、capture truncatedとは区別します。
3. PR #5のLive起動待ち修正。改行なしの部分出力でreadlineが期限を超えて待つ問題と、
   起動行と同時に届いた集計を先読みで失う問題を修正しました。
   nonblocking binary pipe、selectorの残期限、起動出力64 KiB上限を使います。
   既定起動期限20秒は維持。起動未確認・EOF・上限超過ではqueryへ進みません。

【基準と証拠】
・作業先：/Users/takemuramasaki/_workspace/veil-blackhole
・Arena：/Users/takemuramasaki/_workspace/veil-arena
・確認時のBlackhole main：db11a84551bb0f0e8fb356a1f84bb01c38d20db6
  PR #5のマージcommitです。CI完了は本人から報告されています。
  再開時に現在のmain・未コミット変更・CIを確認し、古い記録を現行の成功としないでください。
・起動待ち修正はMac ARM64とLinux x86_64でダミーPython子プロセスの8試験を確認済み。
  queryとsudo killは代替し、所有子の停止とwait、正常起動・集計保持を確認しました。
  Linux Live、実sudo monitor停止、BPF FD解放、実DNS観測の証拠ではありません。
・詳細：docs/LIVE_STARTUP_VALIDATION.md
・固定環境：ArenaのNix devShell。Rust 1.98.1、Cargo実バイナリ1.98.0、Python 3.14.7。
・2026-10-16にmainのrelease buildとlive-smoke.py --checkは成功しました。
  その時点ではen0と既存IPv6 resolverを確認。実取得・query送信・sudoは未実施です。
・ローカル証拠：.local/validation/live-prep-20261016/
  build.log、preflight.log、preparation.json。Git対象外であり、他の端末にあるとは限りません。
・当時のrelease binary SHA-256：
  353333b16a6ea1575e8a0c89e94c98839e7e6d58863004331cbadc788b781538
  再ビルド後のhashや別端末の成功を保証する値ではありません。

【次に優先すること】
まず現在のmainと適用される作業指示、上記scriptと試験を確認してください。
既存cacheを利用して必要なオフライン回帰と--checkを行い、実機試験の前提を整理してください。
en0、IPv6 resolver、sudoの利用可否は端末依存です。条件が違う場合は勝手に公開resolverや
別interfaceへ切り替えず、その端末で何を確認するかを提示してください。

実機確認はこの依頼だけで開始せず、具体的な操作とデータの扱いを示し、本人の実施確認を
得てください。準備済みの既存scriptを使う候補は、本人の同じターミナルで次を行うものです。

  cd /Users/takemuramasaki/_workspace/veil-blackhole
  sudo -v
  python3 -B scripts/live-smoke.py

script自体にsudoは付けません。パスワードは本人のターミナルだけで入力し、チャットに
貼らせないでください。認証を迂回したりsudoersを変えたりしないでください。
この候補はen0で10秒間取得し、起動確認後、既存IPv6 resolverへexample.com Aを1回試みます。
名前・endpointの表示と生パケット保存は既定で無効です。既存ネットワークの設定は変えません。
ただし、そのinterface上の背景DNSも取得対象になり得ます。

【実機確認の判定と限界】
起動確認、送信試行、capture終了コード、集計、所有プロセスの終了、残る資源を
実際の証拠に基づいて分けて記録してください。取得できなかった項目は未確認のまま残します。
result=observedはDNSをread・decodeしたという集計上の確認です。
自分が送ったqueryの個別同定や全通信の完全捕捉、正確な1件計数の証明ではありません。
起動期限はscript全体の期限ではなく、実sudo monitor停止や認証切れ時の回収も未検証です。
失敗したら最初の証拠を残し、同じ試行を闇雲に繰り返さないでください。
まず1回の限定試験を評価し、具体的な不具合があった場合だけ最小修正を提案してください。

【範囲と停止条件】
ArenaのVM・k8s・eBPF機能を増やさず、Blackholeの限定した実利用確認に集中してください。
既存の未コミット変更（確認時は未追跡.DS_Store）とcache・証拠を保全してください。
削除・権限変更・依存取得・追加通信は必要性と承認範囲を確認してください。
PR・push・マージ・公開前には結果を見せてください。
準備タスクは、現在の基準・確認済み事項・未確認事項・本人が行う短い手順が揃えば完了です。
実機の限定確認が済んだ後は、使う目的と残課題を判断し、根拠なく機能追加を続けないでください。
```
