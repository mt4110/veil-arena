# Warden Audit案：今回の実装対象外

2026-10-21。Arena接続の試験中に提示された案を、現行仕様と分けて残す。
今回実装・検証するのは既存のobserve/enforce、宛先拒否、解除、停止後の復帰である。
動的なAudit/Enforce切替、allowlist、YAML、ログ抑制、pinningは追加していない。

## 現行observeとの差

現行Wardenは既定observeで新規TCP接続試行を表示するが、observe中のルール変更を拒否し、
kernelのobserve判定では拒否Mapを評価しない。「このルールなら拒否した」というshadow判定ではない。
Macの `--dry-run` はさらに別で、架空のイベントを扱いBPFをロードしない。

Auditを採用するなら、同じルール評価から「一致したルール」「本来の判断」「実際の処置」を分ける。
Auditでは本来DENYでも実際はALLOW、Enforceでは実際にDENYとなることを受信側も含めて検証する。
接続試行を記録しただけで、接続成功やアプリケーションの安全性を認定しない。

## 提示例からそのまま採用しない点

- Ring Buffer予約の成功時だけdropするTC例は、予約失敗でALLOWになる。
  判定と拒否カウンターを先に確定し、reserve/submitの成否とは独立して同じ判断を返す。
  現行Wardenのこの性質は維持する。
- TCの返値とcgroup/connectの返値は別。現行connectでは0が拒否、1が許可で、
  プログラムから `-EPERM` を返す設計へ置き換えない。
- 提示されたMODE/ACTIONの数値は現行と逆。現行はobserve=0、enforce=1、
  eventはallowed=0、denied=1である。既存ABIや保存記録の意味を反転させない。
  Auditのshadow判断を載せる場合は、decoderの整合性検査を含むABI変更として設計する。
- モード切替だけのためにpinningは必須ではない。現行のroot認証付き制御socketを通し、
  Mapを所有するプロセスが書き換える案を先に比較する。MapだけのpinはBPF linkの保持や無停止更新を保証しない。
- カーネルのログ抑制を採用しても遮断判断を省略しない。IPだけのLRUはport/family/cgroupを潰し、
  同時CPU更新やLRU追い出しにより厳密な「全体で1秒1回」にはならない。
  抑制・Ring Buffer欠落・ユーザーqueue欠落を区別し、総試行数と拒否数は別に数える。

## 採用判断に必要な最小試験

1. 同じ合成ルールに対し、Auditで受信成功・shadow DENY、EnforceでEPERM・受信ゼロ。
2. 稼働中の切替前後、制御失敗、未定義モード、ルール世代の不一致を区別する。
   モード1値の更新を、複数ルール全体のアトミックな切替と呼ばない。
   Mapだけでなく、Controllerの操作権限・CLI/TUIの表示も一致させ、不整合を成功表示しない。
3. ログ出力を意図的に欠落させてもEnforceの拒否が変わらず、Auditの通信も通る。
4. 既存接続・UDP・対象外cgroupが現行connect制御の対象外であること、停止後fail-openを維持することを再確認する。
5. 業務に必要な通信・資源負荷・管理経路・解除を対象環境で確認する。

「導入摩擦ゼロ」「影響ゼロ」「データ持ち出しゼロ」は今回も保証しない。
まず現在のArena/Warden試験を完了し、具体的な対象サービスの要求を確認してから実装を判断する。
