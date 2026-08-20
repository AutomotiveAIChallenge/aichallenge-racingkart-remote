# 遠隔操作PC用イメージ。用途ごとにステージを分ける。
#
#   ./docker_build.sh remote   遠隔操作（joy / manager / GUI）
#   ./docker_build.sh rviz     遠隔監視（RViz）
#
# remote 側は Autoware を必要としない。rclpy + sensor_msgs + std_msgs だけで足りるため
# ros:humble-ros-base を使う（Autoware ベースは 13.8GB）。
# rviz 側は Autoware の RViz プラグインと map_loader を使うので Autoware ベースを使う。
#
# zenoh ブリッジはここに入れない。make remote がホストで scripts/run_zenoh.bash を
# 叩くため。ホストへは vendor/ の deb を dpkg -i で入れる（README 参照）。

########################################
# remote: 遠隔操作
########################################
FROM ros:humble-ros-base AS remote

RUN apt-get update && apt-get install -y --no-install-recommends \
      ros-humble-joy \
      python3-tk \
    && rm -rf /var/lib/apt/lists/*

COPY manager /manager
COPY scripts /scripts
COPY shared  /shared
WORKDIR /scripts

########################################
# rviz: 遠隔監視
########################################
FROM ghcr.io/automotiveaichallenge/autoware-universe:humble-latest AS rviz

# 車体モデルは ament_auto_package(INSTALL_TO_SHARE) と同じことを COPY で行う。
# コンパイル対象が無いパッケージなので colcon build は不要。
COPY rviz/description /opt/ros/humble/share/racing_kart_description
RUN touch /opt/ros/humble/share/ament_index/resource_index/packages/racing_kart_description

# RViz の速度計オーバーレイ (SignalDisplay) だけは C++ プラグインなのでビルドする（約26秒）。
COPY rviz/plugin /ws/src/autoware_overlay_rviz_plugin
RUN . /opt/ros/humble/setup.bash \
    && . /autoware/install/setup.bash \
    && cd /ws \
    && colcon build --packages-select autoware_overlay_rviz_plugin \
         --cmake-args -DCMAKE_BUILD_TYPE=Release \
    && rm -rf /ws/build /ws/log

COPY rviz    /rviz
COPY scripts /scripts
COPY shared  /shared
WORKDIR /scripts
