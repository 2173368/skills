# 公共常量与函数，由其他脚本 source，不单独执行。
DOCKER_DIR=/home/whaty/tomcat/docker
CONTAINER=training-tomcat7
SERVER_XML="$DOCKER_DIR/server.xml"
COMPOSE_YML="$DOCKER_DIR/docker-compose.yml"
NGINX_SRC="$DOCKER_DIR/nginx-training.conf"
NGINX_DST=/etc/nginx/conf.d/training.conf
NODE_VERSION=8.17.0
NODE_ABI_BINDING=node_modules/node-sass/vendor/linux-x64-57/binding.node

# Tomcat/应用日志中已知无害的噪音：启动告警、停止时的内存泄漏提示
NOISE='maxIdle is deprecated|failed to unregister it when the web application was stopped|appears to have started a thread named|Unable to proxy interface-implementing method'

die() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "==> $*"; }

# 解析并校验任务目录，输出绝对路径
resolve_task_dir() {
  local dir="${1:-}"
  [ -n "$dir" ] || die "用法：$0 <TASK_DIR> ..."
  dir=$(cd "$dir" 2>/dev/null && pwd) || die "任务目录不存在：$1"
  case "$dir" in
    /usr/local/javaspace/*) ;;
    *) die "任务目录必须在 /usr/local/javaspace 下：$dir" ;;
  esac
  for repo in manager workspace web; do
    git -C "$dir/$repo" rev-parse --git-dir >/dev/null 2>&1 || die "缺少仓库：$dir/$repo"
  done
  echo "$dir"
}

# 日志按任务目录区分，避免多个任务的构建互相覆盖
log_path() { echo "/tmp/offline-training-$(basename "$1")-$2.log"; }

# docker 命令：当前会话有 docker 组权限时直接执行，否则用 sg 包装
dk() {
  if docker info >/dev/null 2>&1; then
    docker "$@"
  else
    sg docker -c "$(printf '%q ' docker "$@")"
  fi
}

# server.xml 中 path="/" 的 Context 当前指向的任务目录
current_docker_task() {
  grep -o 'docBase="/usr/local/javaspace/[^"]*/manager/manager-web/target/manager"' "$SERVER_XML" \
    | head -1 | sed -e 's/^docBase="//' -e 's#/manager/manager-web/target/manager"$##'
}
