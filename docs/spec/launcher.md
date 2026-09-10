# 遠隔操作GUI仕様

`scripts/gui_tools.py` は、遠隔操作PCで Zenoh / RViz / Joy / Manager を個別に
起動・停止・再起動するGUIである。

実装は本体リポジトリの
[`remote/gui_tools.py`](https://github.com/AutomotiveAIChallenge/aichallenge-racingkart/blob/experiment/remote/gui_tools.py)
を基準とし、Manager の操作だけを追加する。基準実装との不要な差分を作らない。

## 操作対象

| 対象 | Start | Stop | Restart | ログペイン |
| --- | --- | --- | --- | --- |
| Zenoh | `remote_component.bash zenoh` | GUIが起動したプロセスを停止 | 再接続 | Zenoh Log |
| RViz | `rviz.bash <RVIZ_VEHICLE>` | `rviz.bash down` | `rviz.bash restart <RVIZ_VEHICLE>` | RViz Log |
| Joy | `remote_component.bash joy` | GUIが起動したプロセスを停止 | 再起動 | Joy Log |
| Manager | `remote_component.bash manager` | GUIが起動したプロセスを停止 | 再起動 | Manager Log |

Zenoh / Manager の対象車両はチェックボックスで複数選択する。チェックボックスの一覧は
`shared/vehicle_ports.sh` の `VEHICLE_ID_VALID_LIST` から実行時に読み込む
（`load_vehicle_ids`）ので、大会サーバー側で車両を増減しても GUI 側のコード変更は
要らない。既定でチェックが入る車両は `.env` の `REMOTE_VEHICLES` で決める
（`default_selected_vehicles`）。統括SD (フリート監督) PC はここに予備以外の全車を
書いておけば、GUI を開いた時点で全台選択になる。未設定・空なら何も選ばれない
（`make remote` が `VEHICLES` の既定値を持たないのと同じ理由）。RVizは1台分の
prefixだけを剥がして表示するため、専用の `RViz Vehicle` で1台を選ぶ。既定値は
選択済み車両の先頭、なければ全車両リストの先頭。

Zenoh / Joy / Manager は `REMOTE_COMPONENT_STDIO=1` で共通起動前段を通し、`.env`、
ROS 2、`ROS_DOMAIN_ID=0`、CycloneDDS設定を読み込む。出力は基準実装と同じくGUIの
ログパイプへ渡しつつ、`output/gui-launcher/remote/<component>.log` にも `tee` で
追記する（`remote_component.bash` の STDIO モード）。GUI を閉じるとログペインの
内容は失われるが、ファイルには残るので、あとから故障解析できる。

## UI

- Devias Kit Pro Neon Blue風のダークテーマ、配色、余白、ボタンスタイルを基準実装と揃える。
- 上部に複数車両のチェックボックス、RViz専用車両、Stop All、コマンドのプレビューを置く。
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
Managerが二重起動するため、両方向にガードを入れている (LN-15)。

- **GUI 自身の多重起動防止**: 起動時に `output/gui-launcher.pid` へ自分のPIDを書く。
  既存ファイルが生きたプロセスを指していれば「ランチャは既に起動しています」と
  エラーダイアログを出して終了する (`acquire_launcher_lock`)。ファイルが無い・
  死んだプロセスを指す (stale) 場合は上書きする。正常終了時 (`main()` の finally) に
  ファイルを消す (`release_launcher_lock`)。
- **`make remote` が動いている間はGUIから起動・再起動させない**: Zenoh / Joy /
  Manager の Start・Restart (および「Zenoh and RViz」の Zenoh 部分) を押す前に、
  `output/remote.pid` のプロセスグループが生きていないか確認する
  (`remote_stack_pid`)。生きていれば「make remote が動いています。先に
  make remote-stop してください」と警告して起動しない。RViz単体の操作は
  コンテナなので対象外。
- **`make remote` はGUIが動いている間は起動しない**: Makefile の `remote:` が
  `output/gui-launcher.pid` を見て、生きていれば「ランチャ GUI が動いています。
  GUI を閉じてから make remote してください」とエラーを出して終了する。
  `make ps` は `launcher: up (PID n)` / `down` も表示する。

いずれのガードも「先に相手を止めてください」という案内で止まるだけで、
自動でどちらかを終了させることはしない。
