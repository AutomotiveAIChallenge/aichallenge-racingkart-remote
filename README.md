# aichallenge-racingkart-remote

自動運転AIチャレンジ（レーシングカート）の**遠隔操作PC**用リポジトリです。
本体リポジトリ [aichallenge-racingkart](https://github.com/AutomotiveAIChallenge/aichallenge-racingkart)
から、遠隔側で動かすもの一式を分離したものです。

車両ECU上で動くもの（Autoware、ドライバ、車両側 zenoh）は本体に残っています。

```text
車両ECU (本体リポジトリ)                        遠隔操作PC (このリポジトリ)
  Autoware / driver / DDS                        joy → manager → zenoh
        │                                                    │
   zenoh-bridge-ros2dds ──── TLS ── 中継サーバ ──── zenoh-bridge-ros2dds
                                                             │
                                                       RViz (遠隔監視)
```

## セットアップ

```bash
cp .env.example .env          # 必要なら編集
./docker_build.sh remote      # 遠隔操作イメージ
./docker_build.sh rviz        # 遠隔監視イメージ
```

mTLS 素材（zip で別配布）を展開して `tls/` に置いてください。リポジトリには含まれません。

### zenoh ブリッジ（ホストに入れる）

zenoh ブリッジだけはコンテナに入れず**ホストで動かします**。`make remote` がホストの
`scripts/run_zenoh.bash` を直接叩くので、deb を入れておいてください。

```bash
sudo dpkg -i vendor/zenoh-bridge-ros2dds_1.5.0_amd64.deb
```

compose は `network_mode: host` なので、ホストのブリッジとコンテナ側の manager は
同じ `ROS_DOMAIN_ID=0` で噛み合います。

ランチャGUI（`gui_tools.py`）から joy をホストで起動する場合は、ROS 2 Humble
（`ros-humble-joy`）もホストに必要です。

## 使い方

### 遠隔操作

```bash
make remote VEHICLES="A2 A3 A7"   # zenoh(ホスト) + joy/manager/GUI(コンテナ)
make ps                            # コンテナの状態
make logs                          # manager のログ
make remote-stop                   # 停止
```

zenoh ブリッジは車両1台につき1プロセスがホストで立ちます（`setsid` で端末から切り離す
ので make が返っても生き残ります）。PID は `output/zenoh.pid`、ログは
`output/<timestamp>/remote/zenoh-<VEHICLE_ID>.log` です。`make remote-stop` が TERM を
送ると、`run_zenoh.bash` が子のブリッジを全部畳みます。

対象車両に既定値はありません。使わない車両を渡すとその車の状態が UNKNOWN のまま残り、
停止確認が取れずに全操作が塞がるためです。

遠隔側は常に `ROS_DOMAIN_ID=0` で動きます。車両側の domain とは無関係で、車両IDで区別します。
全車両のトピックが `/<VEHICLE_ID>/...` の下にまとめて見えます。

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
`connect_zenoh.bash` は1台に繋ぐだけで再接続しません。どちらもホストで動きます。
RViz は GUI からでも `make rviz` 経由で Docker で起動します。

## ディレクトリ構成

| ディレクトリ | 中身 |
|---|---|
| `manager/` | 遠隔操作ロジック。`racing_kart_manager_core.py` は ROS 非依存で、`tests/` は ROS を起動せず pytest だけで走ります |
| `scripts/` | 起動・接続スクリプト（zenoh、joy、manager、RViz） |
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

`rviz/map/` と `rviz/description/` も本体からの複製ですが、コース形状と車体形状なので
更新頻度は低いものです。

## イメージ構成

| イメージ | ベース | サイズ | 用途 |
|---|---|---|---|
| `aichallenge-remote` | `ros:humble-ros-base` | 約 1.0GB | joy / manager / GUI |
| `aichallenge-remote-rviz` | Autoware universe | 約 14GB | 遠隔監視 RViz |

遠隔操作側は Autoware を必要としません。`rclpy` + `sensor_msgs` + `std_msgs` だけで足ります。
RViz 側は Autoware の RViz プラグインと `map_loader` を使うので Autoware ベースのままです。

車体モデルと地図は `COPY` するだけで colcon build を必要としません
（`ament_auto_package(INSTALL_TO_SHARE)` と同じことを Dockerfile で行っています）。
速度計オーバーレイ（`autoware_overlay_rviz_plugin`）だけは C++ プラグインなので
イメージビルド時に約26秒かけてビルドします。

## 未完了

**manager はまだ起動できません。** 操作ブロックの仕組み（緊急停止・停止確認・control_mode の
チェック）が `racing_kart_msgs` と `autoware_auto_vehicle_msgs` に依存していますが、
遠隔操作イメージ（`ros:humble-ros-base`）にはこれらが入っていないためです。

仕様変更でこれらのチェックを撤廃し、常に任意の車両を操作できるようにする予定です。
それまで `make remote` は manager の起動に失敗します（zenoh と joy は動きます）。

- [ ] 操作ブロックの撤廃（`Tri` / `BlockerCode` / `Blocker` / `stopped_of` / `emergency_of` / `control_mode_of` / `can_enter_*` の削除）
- [ ] 上記に伴う `manager/tests/` の書き直し
- [ ] `shared/` を本体と突き合わせる CI
