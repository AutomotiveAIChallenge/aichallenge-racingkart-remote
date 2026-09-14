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
```

このリポジトリは joy の送出（送信側）だけを担う。車両からのテレメトリを受信・可視化する
役目は持たない。

## セットアップ

**すべてホストで動きます。** コンテナは使いません。

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
```

mTLS 素材（zip で別配布）を展開して `tls/` に置いてください。リポジトリには含まれません。

レース通知を使うなら、`.env` に MQTT の認証情報を書いてください（`MQTT_USERNAME` /
`MQTT_PASSWORD`）。**認証情報はリポジトリに置きません。** `MQTT_HOST` を空にすると通知を
送らず、manager はそのまま起動します。

## 使い方

### 遠隔操作

```bash
make remote VEHICLES="A2 A3 A7"   # zenoh + joy + manager + GUI（すべてホスト）
make ps                            # 生きているプロセス一覧
make logs                          # manager のログ
make remote-stop                   # 停止
```

`make remote` は `scripts/run_remote.bash` を `setsid` で起こし、そこから zenoh ブリッジ
（車両1台につき1プロセス）・joy・manager を起動します。`setsid` で端末から切り離すので
make が返っても生き残ります。

起動の前段（`.env` の読み込み・ROS 環境・`ROS_DOMAIN_ID`・DDS 設定・ログ先）は
`scripts/remote_component.bash` が持っています。`run_remote.bash` はそれを3回呼ぶだけで、
ランチャGUI も同じものを1回ずつ呼びます。前段が2箇所にあると、いずれ片方だけが直るためです。

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
- スティックが効くのは選択車だけです。非選択車にも無操作の joy が届きます（選択を切り替えた
  瞬間から手元の joy がそのまま効くようにするため）。
- **緊急停止ボタン（LB / RB / START / BACK）は選択に関係なく全台へ飛びます。**
- 解除（左右スティックの押し込み同時押し）は選択に従います。全台まとめて戻すときは
  全台選択にしてから解除してください。
- GUI の下段に「レース開始」「レース終了」があります。選択に関係なく**全車へ**、
  開始は Y（自動運転）を、終了は X（ステアのみ自動 + スロットルカット）を送ります。
  レース開始を押すと選択も「全台」に切り替わります。
  **レース終了はブレーキを掛けません。止めるのは緊急停止です。**
- manager は車両テレメトリを見ません。車両の状態は車両側のログや別途の監視手段で確認してください。
- GUI の「レース開始」「レース終了」を押したときだけ、MQTT でも通知します
  （`kart_race_start` / `kart_race_finish`）。joy の Y や緊急停止ボタンでは通知しません。
  通知が失敗しても操作は止まりません。

joy の中継・選択の GUI・レース通知は**1つのプロセス**で動きます。GUI を開けない環境では
起動しません。

### 遠隔が落ちても車両は止まりません

実機決勝に向けて、車両側は **joy 途絶による緊急停止のラッチを削除**し、**joy 途絶と
V2X peer 途絶の判定閾値をどちらも5秒から10分へ**延ばします。この遠隔操作PCは各車の
SD / SO に対する**冗長系**であり、その回線断・zenoh の再接続・manager の再起動が全車の
緊急停止に化けてはならないためです。遠隔リンクを車両のハートビートにすると、統括SD席が
単一障害点になります。

止まらなくなるわけではありません。sd / so の両方の joy が10分途切れれば、車両側は入力を
空にして**停止指令を出し続けます**。変わるのはラッチで、joy が戻れば左右スティックの
押し込みによる手動解除なしにそのまま走行に戻ります。sd が途絶えても so が生きていれば
so へフォールバックするので、遠隔側のリンク断だけではこの経路に入りません。

レース中に確実に止めるのは緊急停止ボタン（joy の LB / RB / START / BACK）であって、
遠隔を落とすことではありません。遠隔側に定期送出（タイマーによる joy の publish）を
足さないのも同じ理由です。

この前提は `racing_kart_interface` 側の変更が入って初めて成立します。変更前の車両と繋ぐ
間は、joy を5秒止めると緊急停止がラッチします（解除は左右スティックの押し込み同時押し）。
詳細は [`docs/spec/joy-routing.md`](docs/spec/joy-routing.md) §7 にあります。

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
値を変えるときは車を止めてから manager を再起動します。joy 途絶による緊急停止が削除された
車両では、再起動で joy が途切れても緊急停止はラッチしません（下記）。削除前の車両では
5秒でラッチするので、再開時に左右スティックの押し込みで解除が要ります。

仕様と注意点は [`docs/spec/joy-routing.md`](docs/spec/joy-routing.md) の §11 にあります。

### ランチャGUI（Zenoh / Joy / Manager を個別に操作する）

```bash
./scripts/gui_tools.py
```

本体リポジトリの `remote/gui_tools.py` と同じGUIに Manager の列とログを加えたものです。
上部のチェックボックスで Zenoh / Manager の対象車両を複数選択できます（既定は
`A2 A3 A4 A6 A7`）。Manager と Joy は共通起動前段を通すため、`.env`、ROS 2、
CycloneDDS設定も読み込まれます。

プロセスは専用グループで起動され、停止は SIGTERM から SIGKILL へ段階的に進みます。
Restart は停止完了を待ってから起動します。ログが大量に流れてもGUIを固めないよう、
有界キューと描画時間の上限も設けています。

**`make remote` と同時には使わないでください。** JoyやManagerが二重起動します。
先に `make remote-stop` で一括起動側を止めてください。GUIは起動時に
`output/gui-launcher.pid` へ自分のPIDを書き、2枚目のGUIや、GUI起動中の
`make remote` を検出するとエラーで止まります（GUI自身の多重起動、
`make remote` が動いている間のZenoh/Joy/Managerの起動・再起動、
GUIが動いている間の `make remote` の3方向）。詳しい仕様は
[`docs/spec/launcher.md`](docs/spec/launcher.md) にあります。

### 単車を手で扱う

```bash
./scripts/connect_zenoh.bash A3     # 1台に zenoh 接続（再接続なし）
./scripts/restart.bash A3           # 1台だけ zenoh に繋ぎ直す
```

`make remote` が複数台をまとめて扱う（`run_zenoh.bash`、再接続あり）のに対し、
`connect_zenoh.bash` は1台に繋ぐだけで再接続しません。

## ディレクトリ構成

| ディレクトリ | 中身 |
|---|---|
| `manager/` | 遠隔操作ロジック。`racing_kart_manager_core.py` は ROS にも Tk にも依存せず、`tests/` は ROS を起動せず pytest だけで走ります |
| `docs/` | 仕様。`docs/spec/` が manager とランチャの正本です |
| `scripts/` | 起動・接続スクリプトとランチャGUI。`remote_component.bash` が構成要素1つ分の起動を、`run_remote.bash` が一式の起動を担います |
| `shared/` | **本体リポジトリからの複製。同期が必要**（下記） |
| `vendor/` | zenoh-bridge-ros2dds の deb |

## shared/ の同期について

`shared/` は本体リポジトリ `vehicle/` からの複製です。**片側だけ変更すると車両に繋がらなくなり、
しかも自動テストでは気づけません。**

| ファイル | 本体側の正本 |
|---|---|
| `shared/vehicle_ports.sh` | `vehicle/vehicle_ports.sh` |
| `shared/zenoh-user.json5.template` | `remote/zenoh-user.json5.template` |

車両を追加するとき、zenoh の許可リストを変えるときは、必ず両方のリポジトリを揃えてください。
CI で突き合わせる仕組みを入れる予定です（未実装）。

このリポジトリの `shared/zenoh-user.json5.template` は `allow.subscribers` を持ちません。
このリポジトリ（remote）は joy の送出だけを担い、車両からのテレメトリを受信・可視化する
役目は持たないためです。本体側 `vehicle/zenoh.json5` の許可リストと揃えるのは
`allow.publishers` の車両ID・トピック名だけです。

## 未完了

- [ ] `shared/` を本体と突き合わせる CI
