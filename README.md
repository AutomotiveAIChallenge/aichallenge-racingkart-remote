# aichallenge-racingkart-remote

自動運転AIチャレンジ（レーシングカート）の**遠隔操作PC**用リポジトリです。
本体リポジトリ [aichallenge-racingkart](https://github.com/AutomotiveAIChallenge/aichallenge-racingkart)
から、遠隔側で動かすもの一式を分離したものです。

車両ECU上で動くもの（Autoware、ドライバ、車両側 zenoh）は本体に残っています。

```text
車両ECU (本体リポジトリ)                        遠隔操作PC (このリポジトリ)
  Autoware / driver / DDS                        joy → manager → zenoh   ホスト
        │                                                    │
   zenoh-bridge-ros2dds ──── TLS ── 中継サーバ ──── zenoh-bridge-ros2dds  ホスト
                                                             │
                                                       RViz (遠隔監視)    コンテナ
```

## セットアップ

**RViz 以外はすべてホストで動きます。** コンテナに残しているのは RViz だけで、Autoware の
RViz プラグインと `map_loader` が要るためです。

### ホストに入れるもの

```bash
sudo apt install ros-humble-ros-base ros-humble-joy python3-tk mosquitto-clients
sudo dpkg -i vendor/zenoh-bridge-ros2dds_1.5.0_amd64.deb
```

manager が使うのは `rclpy` + `sensor_msgs` だけです。Autoware も `racing_kart_msgs` も
要りません。`ros-humble-desktop` が既に入っていればそれで足ります。`mosquitto-clients` は
レース開始・終了通知（MQTT）に使います。

### リポジトリ側

```bash
cp .env.example .env    # 必要なら編集
./docker_build.sh rviz  # 遠隔監視イメージ（RViz のみ）
```

mTLS 素材（zip で別配布）を展開して `tls/` に置いてください。リポジトリには含まれません。

レース通知を使うなら、`.env` に MQTT の認証情報を書いてください（`MQTT_USERNAME` /
`MQTT_PASSWORD`）。**認証情報はリポジトリに置きません。** `MQTT_HOST` を空にすると通知を
送らず、manager はそのまま起動します。

RViz コンテナは `network_mode: host` なので、ホスト側のノードと同じ `ROS_DOMAIN_ID=0`
で噛み合います。

## 使い方

### 遠隔操作

```bash
make remote VEHICLES="A2 A3 A7"   # zenoh + joy + manager + GUI（すべてホスト）
make ps                            # 生きているプロセス一覧
make logs                          # manager のログ
make remote-stop                   # 停止
```

`make remote` は `scripts/run_remote.bash` を `setsid` で起こし、そこから zenoh ブリッジ
（車両1台につき1プロセス）・joy・manager を起動します。`.env` を読むのもここです。
`setsid` で端末から切り離すので make が返っても生き残ります。

PID は `output/remote.pid` の1つだけです。`setsid` によって `run_remote.bash` が
セッションリーダーになり、**子も孫も同じプロセスグループに入ります**。`make remote-stop`
はそのグループごと `kill -TERM -<PID>` で畳みます。`ros2 run` は joy_node を subprocess
で起こすため、親だけを kill すると joy_node が孤児として残るからです。停止後に残っている
プロセスがあれば `make remote-stop` が警告します。

ログは `output/<timestamp>/remote/` に `zenoh-<VEHICLE_ID>.log` / `joy.log` /
`manager.log` として並びます。`output/latest/remote` が最新のディレクトリを指します。
レース通知の送信結果も `manager.log` に出ます。

**子が落ちても上げ直しません。** 黙って復活すると不安定なまま運用を続けてしまうためです。
何が生きているかは `make ps` で見てください。zenoh ブリッジの再接続だけは
`run_zenoh.bash` が自前で面倒をみます（通信断からの復帰は当然のため）。

対象車両に既定値はありません。GUI の「全台」も緊急停止の宛先も、ここで渡した車両で決まります。

### 操作モデル

manager の仕様は [`docs/spec/joy-routing.md`](docs/spec/joy-routing.md)（joy の配り方）と
[`docs/spec/race-notification.md`](docs/spec/race-notification.md)（レース通知）にあります。要点だけ:

- GUI の上段で「未選択 / 車両1台 / 全台」を選びます。選択中のボタンが赤くなります。
- スティックが効くのは選択車だけです。非選択車には無操作の joy が届きます（送るのを止めると
  車両側が5秒で緊急停止をラッチしてしまうため）。
- **緊急停止ボタン（LB / RB / START / BACK）は選択に関係なく全台へ飛びます。**
- 解除（左右スティックの押し込み同時押し）は選択に従います。全台まとめて戻すときは
  全台選択にしてから解除してください。
- GUI の下段に「レース開始」「レース終了」があります。選択に関係なく**全車へ**、
  開始は Y（自動運転）を、終了は X（ステアのみ自動 + スロットルカット）を送ります。
  レース開始を押すと選択も「全台」に切り替わります。
  **レース終了はブレーキを掛けません。止めるのは緊急停止です。**
- manager は車両テレメトリを見ません。車両の状態は RViz で確認してください。
- レース開始・終了は MQTT でも通知します（`kart_race_start` / `kart_race_finish`）。
  GUI のボタンのほか、全台選択での Y 押下（開始）と緊急停止ボタン（終了）でも飛びます。
  通知が失敗しても操作は止まりません。

joy の中継・選択の GUI・レース通知は**1つのプロセス**で動きます。GUI を開けない環境では
起動しません。

遠隔側は常に `ROS_DOMAIN_ID=0` で動きます。車両側の domain とは無関係で、車両IDで区別します。
全車両のトピックが `/<VEHICLE_ID>/...` の下にまとめて見えます。

### ブレーキ試験（実験用）

車両のブレーキ入力に対する減速度を測るためのものです。

```bash
make remote VEHICLES="A3" BRAKE_TEST=20
```

対象車両を選び、Y で自動走行に入り、直線に入った瞬間に **B を押している間**、ステアを
自動に保ったまま 20% のブレーキが入ります（同時にスロットルはカットされます）。
`BRAKE_TEST` を渡さなければ B は素通しのままです。

**B を離しても自動運転には戻りません。** 車両側の `control_mode` はラッチなので、
自動操舵のまま惰行します。走行に戻すには Y、止めるには緊急停止を押してください。
値を変えるときは車を止めてから manager を再起動します（再起動中に joy が5秒途切れて
車両が緊急停止をラッチするので、再開時に左右スティックの押し込みで解除が要ります）。

仕様と注意点は [`docs/spec/joy-routing.md`](docs/spec/joy-routing.md) の §11 にあります。

### 遠隔監視

```bash
make rviz VEHICLE=A3   # A3 の位置・軌跡・速度を表示
make rviz              # 地図だけ表示
make rviz-stop
```

### ランチャGUI（1台ずつ手元で操作する場合）

```bash
python3 scripts/gui_tools.py        # Zenoh / RViz / Joy の start・stop・restart
./scripts/connect_zenoh.bash A3     # 単一車両に zenoh 接続（ホスト実行）
```

`make remote` が複数台をまとめて扱う（`run_zenoh.bash`、再接続あり）のに対し、
`connect_zenoh.bash` は1台に繋ぐだけで再接続しません。RViz は GUI からでも
`make rviz` 経由で Docker で起動します。

## ディレクトリ構成

| ディレクトリ | 中身 |
|---|---|
| `manager/` | 遠隔操作ロジック。`racing_kart_manager_core.py` は ROS にも Tk にも依存せず、`tests/` は ROS を起動せず pytest だけで走ります |
| `docs/` | 仕様。`docs/spec/` が manager の正本です |
| `scripts/` | 起動・接続スクリプト。`run_remote.bash` が遠隔操作一式のエントリポイントです |
| `shared/` | **本体リポジトリからの複製。同期が必要**（下記） |
| `rviz/` | 遠隔監視 RViz 用のアセット（地図、車体モデル、rviz 設定、launch、プラグイン） |
| `vendor/` | zenoh-bridge-ros2dds の deb |

## shared/ の同期について

`shared/` は本体リポジトリ `vehicle/` からの複製です。**片側だけ変更すると車両に繋がらなくなり、
しかも自動テストでは気づけません。**

| ファイル | 本体側の正本 |
|---|---|
| `shared/vehicle_ports.sh` | `vehicle/vehicle_ports.sh` |
| `shared/zenoh.json5` | `vehicle/zenoh.json5` |
| `shared/zenoh-user.json5.template` | `remote/zenoh-user.json5.template` |

車両を追加するとき、zenoh の許可リストを変えるときは、必ず両方のリポジトリを揃えてください。
CI で突き合わせる仕組みを入れる予定です（未実装）。

### 本体との既知の差分

`shared/zenoh-user.json5.template` の `allow.subscribers` に
`/__VEHICLE_ID__/v2x/vehicle_positions/markers` を足しています。本体側にはまだ入って
いません（[PR #285](https://github.com/AutomotiveAIChallenge/aichallenge-racingkart/pull/285)
は車両側 `vehicle/zenoh.json5` の publish 許可だけを追加していて、遠隔側の subscribe
許可が漏れています）。この1行が無いと、車両が V2X マーカーを送っても遠隔側のブリッジが
中継せず、RViz に他車が映りません。

突き合わせ CI を作るときは、この行を既知の差分として扱ってください。本体側が修正されたら
差分は解消されます。

`rviz/map/` と `rviz/description/` も本体からの複製ですが、コース形状と車体形状なので
更新頻度は低いものです。

## イメージ構成

| イメージ | ベース | サイズ | 用途 |
|---|---|---|---|
| `aichallenge-remote-rviz` | Autoware universe | 約 14GB | 遠隔監視 RViz |

イメージは1つだけです。遠隔操作側（joy / manager / GUI / zenoh）は Autoware を必要とせず、
ホストの ROS 2 Humble で足りるのでコンテナに入れていません。RViz 側は Autoware の RViz
プラグインと `map_loader` を使うので Autoware ベースのままです。

車体モデルと地図は `COPY` するだけで colcon build を必要としません
（`ament_auto_package(INSTALL_TO_SHARE)` と同じことを Dockerfile で行っています）。
速度計オーバーレイ（`autoware_overlay_rviz_plugin`）だけは C++ プラグインなので
イメージビルド時に約26秒かけてビルドします。

## 未完了

- [ ] `shared/` を本体と突き合わせる CI
- [ ] RViz の速度計オーバーレイ（`rviz/plugin/`）が `rviz/config/remote.rviz` から使われていません。
      Dockerfile は約26秒かけてビルドしています。`SignalDisplay` を足して使うか、プラグインごと消すか。
- [ ] `/sensing/gnss/pose_with_covariance` は `remote.rviz` に表示設定があり `run_rviz.bash` が中継
      していますが、zenoh の許可リストに無いため届きません。両リポジトリの許可リストに足して通すか、
      表示と中継を消すか。
