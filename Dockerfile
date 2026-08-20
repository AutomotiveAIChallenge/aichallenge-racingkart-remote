# 遠隔監視 RViz 用イメージ。
#
#   ./docker_build.sh rviz
#
# コンテナに入れているのは RViz だけ。Autoware の RViz プラグインと map_loader を
# 使うので Autoware ベースのままにしている。
#
# zenoh ブリッジ・joy・manager・操作GUI はホストで動かす（make remote）。
# ホストには ROS 2 Humble（rclpy / sensor_msgs / std_msgs / joy / tkinter）と
# vendor/ の zenoh-bridge-ros2dds deb を入れておくこと。README 参照。

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
