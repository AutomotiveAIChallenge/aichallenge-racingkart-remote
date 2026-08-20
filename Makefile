# 遠隔操作PC用の Makefile。本体リポジトリ (aichallenge-racingkart) の Makefile と
# 同じ作法で書いている。イメージのビルドは ./docker_build.sh を使う。
SHELL := /bin/bash

.PHONY: remote remote-stop rviz rviz-stop down ps logs

# compose がコンテナのユーザーに使う。output/ の生成物がホストユーザー所有になる。
HOST_UID ?= $(shell id -u)
HOST_GID ?= $(shell id -g)
# joy_node が /dev/input/event* を非 root で読むのに要る。
HOST_GID_INPUT ?= $(shell getent group input | cut -d: -f3)
export HOST_UID HOST_GID HOST_GID_INPUT
# ホストシェルの ROS_DOMAIN_ID が compose 補間で .env を上書きするのを防ぐ。
# ただし `make foo ROS_DOMAIN_ID=N` の明示指定は通す。
unexport ROS_DOMAIN_ID
ifeq ($(origin ROS_DOMAIN_ID),command line)
export ROS_DOMAIN_ID
endif

TIMESTAMP := $(shell date +%Y%m%d-%H%M%S)
LOG_DIR := /output/$(TIMESTAMP)

# 遠隔操作PC一式（zenoh ブリッジ + joy + manager + 操作GUI）
#   make remote VEHICLES="A2 A3 A7"
# 対象車両に既定値を置かない。使わない車両が UNKNOWN のまま残ると停止確認が取れず、
# すべての操作が塞がるため。
# 遠隔側は常に ROS_DOMAIN_ID 0。車両側の domain とは無関係で、車両IDで区別する。
#
# zenoh ブリッジだけはコンテナに入れずホストで動かす。compose は network_mode: host
# なので、ホストのブリッジとコンテナ側の manager は同じ ROS_DOMAIN_ID 0 で噛み合う。
# setsid で端末から切り離すので make が返ってもブリッジは生き残る。
# ホストに zenoh-bridge-ros2dds の deb が入っていること (README 参照)。
remote:
	@test -n "$(VEHICLES)" || { \
		echo 'Error: VEHICLES を指定してください。  例: make remote VEHICLES="A2 A3 A7"' >&2; \
		exit 1; \
	}
	@command -v zenoh-bridge-ros2dds >/dev/null || { \
		echo 'Error: zenoh-bridge-ros2dds が見つかりません。' >&2; \
		echo '       sudo dpkg -i vendor/zenoh-bridge-ros2dds_1.5.0_amd64.deb' >&2; \
		exit 1; \
	}
	@mkdir -p output
	@setsid ./scripts/run_zenoh.bash "$(VEHICLES)" "$(PWD)/output/$(TIMESTAMP)" \
		</dev/null >/dev/null 2>&1 & echo $$! > output/zenoh.pid
	ROS_DOMAIN_ID=0 docker compose up -d joy manager manager-gui
	@echo "対象車両: $(VEHICLES)"
	@echo "zenoh: ホストで起動 (PID $$(cat output/zenoh.pid))"
	@echo "ログ: output/$(TIMESTAMP)/remote/zenoh-<VEHICLE_ID>.log"
	@echo "状態: make ps / ログ: make logs / 停止: make remote-stop"

remote-stop:
	docker compose stop manager-gui manager joy
	@# run_zenoh.bash は TERM を受けると子のブリッジを全部畳む
	-@[ -f output/zenoh.pid ] && kill -TERM "$$(cat output/zenoh.pid)" 2>/dev/null; \
		rm -f output/zenoh.pid

# 遠隔監視 RViz
#   make rviz VEHICLE=A3
# 車両トピックは /<VEHICLE_ID>/... の prefix 付きで届く。VEHICLE を渡すと prefix を
# 剥がす中継が立ち、その車両が RViz に映る。未指定だと地図しか出ない。
rviz:
	docker compose stop rviz2
	RVIZ_VEHICLE_ID="$(VEHICLE)" docker compose up -d rviz2

rviz-stop:
	docker compose stop rviz2

down:
	docker compose down --remove-orphans

ps:
	docker compose ps

logs:
	docker compose logs -f manager
