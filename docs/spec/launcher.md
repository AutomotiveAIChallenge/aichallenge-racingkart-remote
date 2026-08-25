# 遠隔操作スタック ランチャ仕様

遠隔操作PCの zenoh ブリッジ・joy・`racing_kart_manager` を、GUI から**個別に**起動・停止する。

- 実装: `scripts/remote_launcher_core.py`（純ロジック）、`scripts/remote_launcher.py`（Tk とプロセス管理）、`scripts/remote_component.bash`（起動前段）
- テスト: `scripts/tests/`（§11 の観点に対応する）
- 起動されるもの本体の仕様: [`joy-routing.md`](joy-routing.md)、[`race-notification.md`](race-notification.md)

**この仕様はまだ実装されていない。** 現在のランチャは `scripts/gui_tools.py`（Zenoh / RViz / Joy、
単一車両、manager 非対応）で、§12 の移行でこれに置き換える。

## 1. 役割

ランチャは `make remote` の**対話版**である。`make remote` が3つをまとめて起こしてまとめて
畳むのに対し、ランチャは同じ3つを個別に起こして畳む。zenoh だけ繋ぎ直す、manager だけ
入れ替える、といった運用中の操作がこれで済むようにする。

- **LN-01** ランチャが管理するのは **zenoh / joy / manager の3つ**である。RViz は含めない。
  RViz はコンテナで動き、自分の子プロセスではなく lifecycle が違う。`make rviz VEHICLE=A3` のままとする。
- **LN-02** ランチャは起動コマンドを自分で組み立てない。`make remote` と同じ
  `scripts/remote_component.bash` を呼ぶ。
  起動の前段を Python と bash の両方に持つと、片方だけ直したときに GUI と CLI で挙動が割れる。
- **LN-03** GUI を開けない環境では起動しない（manager の REQ-02 と同じ）。
- **LN-04** ランチャは manager と**別プロセス**である。manager を再起動してもランチャは生き残る。
  ランチャの役目は manager を起こし直すことなので、同じプロセスに入れられない。画面は2枚になる。

## 2. 対象コンポーネント

| 名前 | 実体 | 対象車両 | ログ |
| --- | --- | --- | --- |
| zenoh | `run_zenoh.bash "A2 A3 A7"` | 要る | `zenoh.log` と車両ごとの `zenoh-<ID>.log` |
| joy | `joy.bash` | 要らない | `joy.log` |
| manager | `manager.bash A2 A3 A7 [--brake-test N]` | 要る | `manager.log` |

- **LN-05** 3つの間に依存はない。あるのは起動順の好みだけで、「すべて起動」は zenoh → joy →
  manager の順に起こす。ブリッジが繋がる前に joy が出ても、届く先が無いだけで害はない。
- **LN-06** どれか1つだけが動いている状態を許す。zenoh だけ上げて疎通を見る、joy だけ上げて
  ジョイスティックの認識を確かめる、といった使い方をする。

## 3. 対象車両

- **LN-07** ランチャが並べる車両は **A2 / A3 / A6 / A7** の4台とする。
  実機が存在するのはこの4台である（`shared/vehicle_ports.sh` の `vehicle_id_for_hostname`）。
  `VEHICLE_ID_VALID_LIST` は A1 / A5 / A8 も含むが、選べても繋がる先が無い。
- **LN-08** 起動時に4台すべてを `zenoh_port_for_vehicle_id` で引き、1台でも引けなければ
  ランチャを起動しない。
  ポート表の唯一の出どころは `shared/vehicle_ports.sh` であり、ランチャは複製を持たない。
  表から消えた車両を黙って並べると、繋がらない相手を選べてしまう。
- **LN-09** 対象車両は1台以上選ぶ。0台では起動できない（manager の REQ-07）。
- **LN-10** 対象車両は、3つのうち**何か1つでも起動している間は変更できない**。
  manager は対象車両を起動引数で確定し、選択ボタンを1回作るだけである（REQ-06）。zenoh は
  車両ごとに1プロセスを持つ。走行中に集合を変えると両者がズレ、「繋がっているのに選べない車両」
  が生まれる。変えるときは一度すべて停止する。
- **LN-11** ここで選んだ集合が、そのまま manager の「全台」であり**緊急停止の宛先**である
  （REQ-08、REQ-20）。**走行させる車両はすべてチェックする。** 外した車両には緊急停止が飛ばない。

## 4. 起動経路

```
remote_component.bash check                                 前提の確認だけ行う
remote_component.bash zenoh   <LOG_DIR> "A2 A3 A7"
remote_component.bash joy     <LOG_DIR>
remote_component.bash manager <LOG_DIR> A2 A3 A7 [--brake-test N]
```

- **LN-12** 起動の前段は `remote_component.bash` が持つ。前段とは次の4つを指す。
  1. `.env` の読み込み（MQTT の認証情報、`TLS_ROOT`）
  2. `/opt/ros/humble/setup.bash` の読み込み
  3. `ROS_DOMAIN_ID=0` の設定
  4. ログ先の作成と、標準出力・標準エラーのログファイルへの接続

  `joy.bash` は ROS の `setup.bash` を自分では読まない。前段抜きで起こすと ROS が見つからず、
  `.env` を読まなければレース通知が黙って止まる。
- **LN-13** `run_remote.bash` は `remote_component.bash` を3回呼ぶだけとし、前段を持たない。
  前段が2箇所にあると、いずれ片方だけが直る。
- **LN-14** 前提の確認（`ros2` と `zenoh-bridge-ros2dds` が入っているか）は
  `remote_component.bash check` の1箇所に置き、Makefile・ランチャ・`remote_component.bash`
  自身の3者がこれを呼ぶ。確認の内容を3箇所に書かない。
- **LN-15** 出力はすべてログファイルに入る。`check` の失敗も含めて例外を作らない。
  端末に出るか出ないかを呼び出し側ごとに変えると、GUI から起こしたときだけ理由が消える。

## 5. プロセスの持ち方と停止

- **LN-16** コンポーネントごとに**独立したプロセスグループ**とする（`start_new_session=True`）。
  グループを分けるので、joy を止めずに zenoh だけ繋ぎ直せる。
- **LN-17** 停止はプロセスグループごと行う。`SIGTERM` → 5秒のポーリング → 残っていれば
  `SIGKILL` → それでも残ればプロセスを列挙して報告する（`make remote-stop` と同じ手順）。
  `ros2 run` は `joy_node` を subprocess で起こし、`run_zenoh.bash` は車両ごとにサブシェルを
  起こす。親だけを kill すると孤児が残り、**再起動すると joy が二重に流れる**。
- **LN-18** 再起動は、停止の完了を待ってから起動する。停止しきる前に起こさない。
- **LN-19** 落ちた子を自動で上げ直さない（`run_remote.bash` と同じ判断）。
  黙って復活すると、不安定なまま運用を続けてしまう。zenoh ブリッジの再接続だけは
  `run_zenoh.bash` が自前で面倒をみる。
- **LN-20** 停止要求で終わった子を異常終了として扱わない。
  `run_zenoh.bash` は kill されると exit 1 で終わる。区別しないと、正常な停止が毎回赤くなる。
- **LN-21** ウィンドウを閉じるときは、起動中のコンポーネントをすべて停止する。停止するかどうかを
  確認してから閉じる。
  閉じただけで裏に残ると、誰も見ていない joy が車両へ流れ続ける。

## 6. 状態

| 状態 | 意味 | 表示 |
| --- | --- | --- |
| `STOPPED` | 起動していない | 灰 |
| `STARTING` | 起こした直後。まだ生存を確認していない | 黄 |
| `RUNNING` | 生きている | 緑 |
| `STOPPING` | 停止要求を出して待っている | 黄 |
| `FAILED` | 要求していないのに終わった | 赤 + 終了コード |

```
STOPPED ──起動──▶ STARTING ──生存確認──▶ RUNNING
   ▲                                        │
   ├────────── STOPPING ◀───停止要求────────┤
   │                                        │
   └────────── FAILED ◀─────自然死──────────┘
```

- **LN-22** 生存確認は 500ms ごとの `poll()` で行う。
- **LN-23** `FAILED` は終了コードを添えて表示し、次に起動するまで消さない。
  復帰は人間が押す（LN-19）ので、何が落ちたかが画面に残り続ける必要がある。

## 7. ログ

- **LN-24** ログの配置は `make remote` と同じとし、ランチャは**追尾するだけ**とする。

  ```
  output/<timestamp>/remote/{zenoh.log, zenoh-A2.log, ..., joy.log, manager.log}
  output/latest/remote -> 上記
  ```

  zenoh ブリッジの実際の出力は `run_zenoh.bash` が車両ごとのファイルに直接書いている。
  子の標準出力をパイプで拾う作りにすると、**車両ごとのログが GUI から見えない**。
  ファイルを追尾すればこれが要らず、`make logs` と事後解析も壊れない。
- **LN-25** セッションディレクトリはランチャの起動時に1つ作り、`output/latest/remote` を
  張り替える。コンポーネントを再起動しても同じファイルに追記する。
  再起動のたびに切り替えると、1回の運用のログが散らばる。
- **LN-26** 画面に出すのは `zenoh`・`zenoh-<ID>`（対象車両ぶん）・`joy`・`manager` の
  タブとする。
  zenoh は2種類ある。`zenoh.log` は前段と監視プロセスの出力で、**前提が足りずに起動
  できなかった理由もここにしか出ない**。車両ごとの実際のブリッジ出力は
  `zenoh-<ID>.log` にある。前者が無いと、起動しなかったときに何も分からない。

## 8. 安全

- **LN-27** 停止・再起動には確認を挟む。
  zenoh・joy・manager のどれを止めても joy の配信が途切れ、**5秒で車両側が緊急停止をラッチする**。
  解除には両スティックの押し込みが要る（REQ-14）。走行中に押されると事故になる。
- **LN-28** 二重起動を拒否する。`output/remote.pid` が生きていればランチャは起動を拒否し、
  ランチャが起こしたものが生きていれば `make remote` を促さない。
  `make remote` とランチャを同時に使うと **joy が二重に流れる**。これがこの GUI で唯一の
  致命的な失敗モードである。
- **LN-29** ランチャはコンポーネントごとに pid ファイルを `output/` に置き、`make ps` から
  何が生きているか見えるようにする。
- **LN-30** ブレーキ試験（§11 of `joy-routing.md`）は GUI から渡せる。渡した値は manager の
  ウィンドウタイトルに出るので、走行前に何%が仕込まれているか目で確認できる。

## 9. 画面

```
┌─ Remote Launcher ────────────────────────────────────────┐
│ 対象車両  ☑A2 ☑A3 ☐A6 ☑A7            (起動中は変更不可) │
│ ブレーキ試験 ☐ 有効 [20]%       ログ: output/latest/remote│
│ [ すべて起動 ]   [ すべて停止 ]                          │
├───────────────┬───────────────┬──────────────────────────┤
│ Zenoh         │ Joy           │ Manager                  │
│ ● 稼働中      │ ● 稼働中      │ ○ 停止                   │
│ bridges: 3    │ joy_node      │ A2 A3 A7                 │
│ [起動] [停止] │ [起動] [停止] │ [起動] [停止]            │
│ [  再起動  ]  │ [  再起動  ]  │ [  再起動  ]             │
├───────────────┴───────────────┴──────────────────────────┤
│ [zenoh-A2][zenoh-A3][zenoh-A7][joy][manager] ← ログタブ  │
└──────────────────────────────────────────────────────────┘
```

- **LN-31** Tk のウィジェットに触るのはメインスレッドだけとする。ログの追尾は別スレッドが
  キューに積み、メインスレッドが `after()` で拾う（manager と同じ約束）。
- **LN-32** 判断は `remote_launcher_core.py` の純関数が行い、`remote_launcher.py` は
  「押されたら呼ぶ、返ったら描く」だけの薄い層に徹する（manager と同じ作り）。
  純関数が持つのは、対象車両の検証・引数の組み立て・状態遷移・操作の可否判定である。

## 10. 保証しないこと

- **車両の状態を見ない。** 繋がっているかどうかはログと RViz で確認する。manager がテレメトリを
  購読しないのと同じ理由による（[`joy-routing.md`](joy-routing.md) §9）。
- **落ちた子を上げ直さない**（LN-19）。
- **運用中に対象車両を変えられない**（LN-10）。
- **RViz を扱わない**（LN-01）。
- **遠隔から操作できない。** 遠隔操作PCの画面の前にいる人が押すことだけを想定する。

## 11. テスト観点

Tk も ROS も subprocess も起こさずに `remote_launcher_core.py` だけで確かめる。

### 対象車両

- A2 / A3 / A6 / A7 だけが並ぶこと（LN-07）
- 4台すべてがポート表から引けること。引けない車両があれば起動を拒否すること（LN-08）
- 0台では起動できないこと（LN-09）
- 何か1つでも起動していれば、集合の変更が拒否されること（LN-10）

### 引数の組み立て

- zenoh に渡るのが選択順ではなく安定した順序の1つの文字列であること（LN-12）
- manager に渡るのが車両IDの並びであること
- ブレーキ試験が有効なときだけ `--brake-test` が付くこと。0〜100 の外を弾くこと（LN-30）

### 状態遷移

- 起動要求で `STOPPED` → `STARTING` → `RUNNING` と進むこと（LN-22）
- 停止要求で終わった子が `FAILED` にならないこと（LN-20）
- 要求していない終了が終了コード付きの `FAILED` になり、次の起動まで残ること（LN-23）
- 再起動が、停止の完了を待ってから起動すること（LN-18）

### 操作の可否

- `RUNNING` のものに対する起動が拒否されること
- `STOPPED` のものに対する停止が拒否されること
- 二重起動ガードが `output/remote.pid` の生死で決まること（LN-28）

## 12. 移行

この仕様を入れるときの増減。

| | ファイル | 内容 |
| --- | --- | --- |
| 新規 | `scripts/remote_component.bash` | §4 の前段と `check` |
| 新規 | `scripts/remote_launcher.py` | Tk とプロセス管理 |
| 新規 | `scripts/remote_launcher_core.py` | 純ロジック |
| 新規 | `scripts/tests/` | §11 |
| 書換 | `scripts/run_remote.bash` | 前段を失い、`remote_component.bash` を3回呼ぶだけになる |
| 書換 | `Makefile` | 前提の確認を `remote_component.bash check` に委ね、`ps` にランチャぶんを足す |
| 削除 | `scripts/gui_tools.py` | ランチャに置き換わる |
| 書換 | `scripts/restart.bash` | `./rviz.bash` の呼び出しを `SCRIPT_DIR` 基準に直す（下記） |

`scripts/connect_zenoh.bash`・`scripts/rviz.bash`・`scripts/restart.bash` は**残す**。単車を
手で扱うための道具で、RViz だけ単体で上げ直すときに使う。ランチャがこの3本を呼ぶことはない。

ただし `restart.bash` は `./rviz.bash` を**カレントディレクトリ基準**で呼んでいる。今までは
`gui_tools.py` が `scripts/` を作業ディレクトリにして起こしていたので動いていただけで、
リポジトリルートから叩くと落ちる。`gui_tools.py` が消えると単体起動しか残らないので、
`connect_zenoh.bash` と同じく `SCRIPT_DIR` 基準に直す。

`rviz.bash` は `make rviz` を呼ぶだけで **VEHICLE を渡せない**（地図しか出ない）。車両を映す
なら `make rviz VEHICLE=A3` を使う。

実装は次の順で行う。1 を終えた時点で `make remote` に退行がないことを確かめてから 2 へ進む。

1. `remote_component.bash` の切り出しと `run_remote.bash` / `Makefile` の書き換え
2. `remote_launcher_core.py` と `scripts/tests/`
3. `remote_launcher.py`
4. 旧ファイルの削除と README の更新
