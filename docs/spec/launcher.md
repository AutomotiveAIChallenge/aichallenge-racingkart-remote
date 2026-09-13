# 遠隔操作GUI仕様

`scripts/gui_tools.py` は、遠隔操作PCで Zenoh / Joy / Manager を個別に
起動・停止・再起動するGUIである。

実装は本体リポジトリの
[`remote/gui_tools.py`](https://github.com/AutomotiveAIChallenge/aichallenge-racingkart/blob/experiment/remote/gui_tools.py)
を基準とし、Manager の操作だけを追加する。基準実装との不要な差分を作らない。

## 操作対象

| 対象 | Start | Stop | Restart | ログペイン |
| --- | --- | --- | --- | --- |
| Zenoh | `remote_component.bash zenoh` | GUIが起動したプロセスを停止 | 再接続 | Zenoh Log |
| Joy | `remote_component.bash joy` | GUIが起動したプロセスと孤児ノードを停止 | 再起動 | Joy Log |
| Manager | `remote_component.bash manager` | GUIが起動したプロセスを停止 | 再起動 | Manager Log |

Zenoh / Manager の対象車両は `A2 A3 A6 A7` のチェックボックスで複数選択し、既定では
4台すべてを選択する。Zenoh / Joy / Manager は `REMOTE_COMPONENT_STDIO=1` で
共通起動前段を通し、`.env`、ROS 2、`ROS_DOMAIN_ID=0`、CycloneDDS設定を読み込む。
出力はファイルへリダイレクトせず、基準実装と同じくGUIのログパイプへ渡す。

## UI

- Devias Kit Pro Neon Blue風のダークテーマ、配色、余白、ボタンスタイルを基準実装と揃える。
- 上部に複数車両のチェックボックス、Stop All、コマンドのプレビューを置く。
- Zenoh / Joy / Manager の操作列を横に並べる。
- Zenoh / Joy / Manager の3つのログペインを横に並べる。
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

Joy の Stop / Restart は、GUI がプロセスを追跡していない場合にも、残った
`lib/joy/joy_node` 実行ファイルを停止する。Stop Joy は未追跡の状態でも有効とし、
起動待ち・停止処理中は無効にする。

## 運用上の注意

GUIが追跡するのはGUI自身が起動したプロセスだけである。`make remote` と同時に使うとJoyや
Managerが二重起動するため、両方向にガードを入れている (LN-15)。

- **GUI 自身の多重起動防止**: 起動時に `output/gui-launcher.pid` へ自分のPIDを書く。
  既存ファイルが生きたプロセスを指していれば「ランチャは既に起動しています」と
  エラーダイアログを出して終了する (`acquire_launcher_lock`)。ファイルが無い・
  死んだプロセスを指す (stale) 場合は上書きする。正常終了時 (`main()` の finally) に
  ファイルを消す (`release_launcher_lock`)。
- **`make remote` が動いている間はGUIから起動・再起動させない**: Zenoh / Joy /
  Manager の Start・Restart を押す前に、`output/remote.pid` のプロセスグループが
  生きていないか確認する (`remote_stack_pid`)。生きていれば「make remote が動いて
  います。先に make remote-stop してください」と警告して起動しない。
- **`make remote` はGUIが動いている間は起動しない**: Makefile の `remote:` が
  `output/gui-launcher.pid` を見て、生きていれば「ランチャ GUI が動いています。
  GUI を閉じてから make remote してください」とエラーを出して終了する。
  `make ps` は `launcher: up (PID n)` / `down` も表示する。

いずれのガードも「先に相手を止めてください」という案内で止まるだけで、
自動でどちらかを終了させることはしない。
