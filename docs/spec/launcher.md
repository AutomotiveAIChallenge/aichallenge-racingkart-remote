# 遠隔操作GUI仕様

`scripts/gui_tools.py` は、遠隔操作PCで Zenoh / RViz / Joy / Manager を個別に
起動・停止・再起動するGUIである。

実装は本体リポジトリの
[`remote/gui_tools.py`](https://github.com/AutomotiveAIChallenge/aichallenge-racingkart/blob/experiment/remote/gui_tools.py)
を基準とし、Manager の操作だけを追加する。基準実装との不要な差分を作らない。

## 操作対象

| 対象 | Start | Stop | Restart | ログペイン |
| --- | --- | --- | --- | --- |
| Zenoh | `connect_zenoh.bash` | GUIが起動したプロセスを停止 | 再接続 | Zenoh Log |
| RViz | `rviz.bash` | `rviz.bash down` | `rviz.bash restart` | RViz Log |
| Joy | `joy.bash` | GUIが起動したプロセスを停止 | 再起動 | Joy Log |
| Manager | `remote_component.bash manager` | GUIが起動したプロセスを停止 | 再起動 | Manager Log |

Vehicle ID は基準実装と同じ1つの入力欄で選び、Zenoh、Zenoh + RViz、Managerへ渡す。
Manager は `REMOTE_COMPONENT_STDIO=1` で共通起動前段を通し、`.env`、ROS 2、
`ROS_DOMAIN_ID=0`、CycloneDDS設定を読み込む。出力はファイルへリダイレクトせず、
基準実装と同じくGUIのログパイプへ渡す。

## UI

- Devias Kit Pro Neon Blue風のダークテーマ、配色、余白、ボタンスタイルを基準実装と揃える。
- 上部に Vehicle ID と Stop All、コマンドのプレビューを置く。
- Zenoh / RViz / Joy / Manager / Zenoh and RViz の操作列を横に並べる。
- Zenoh / RViz / Joy / Manager の4つのログペインを横に並べる。
- 各ログに状態、Clear、Autoscroll、縦横スクロールを置く。

## 並行処理と終了処理

基準実装の次の保証をそのまま維持する。

- `self.processes` は `_processes_lock` で保護する。
- プロセスごとに世代トークンを割り当て、古い読み取りスレッドが新しい登録を消さない。
- 子は専用プロセスグループで起動し、停止時はグループへ SIGTERM、3秒後にSIGKILLを送る。
- Restart は旧プロセスの終了確認後に起動し、Stop Allと終了操作で予約を取り消す。
- 停止処理中のプロセスも `_escalating` に保持し、ウィンドウ終了時に回収する。
- ログキューは10,000件に制限し、満杯時は生成側をブロックしない。
- Tk側は1回400件・10msまでをまとめて描画し、イベントループを飢餓状態にしない。
- SIGINT / SIGTERM ハンドラはTk APIを呼ばず、フラグだけを立てる。

## 運用上の注意

GUIが追跡するのはGUI自身が起動したプロセスだけである。`make remote` と同時に使うとJoyや
Managerが二重起動するため、GUIを使う前に `make remote-stop` で一括起動側を停止する。
