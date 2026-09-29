# offline-training 构建与本地运行手册

本手册是本机所有 `offline-training` 任务的通用启动指南，不绑定某一套具体检出。无论是新拉取的任务目录，还是已有的 `training`、`training-*` 等目录，都按同一流程构建和启动。

每个任务目录都包含同一组仓库：

- `manager`：管理端 Java 多模块项目，部署上下文为 `/`
- `workspace`：业务端 Java WAR overlay 多模块项目，部署上下文为 `/ws`
- `web`：管理端 Vue 2 + webpack 3 前端，部署上下文为 `/np`

常用步骤已封装为本 skill 的 `scripts/`：`update-repos.sh`、`preflight.sh`、`build-java.sh`、`build-web.sh`、`switch-docker.sh`、`start-verify.sh`、`sync-nginx.sh`，第一个参数均为 `TASK_DIR`。本文解释各步骤的原因，并给出手工等价命令。

下文所有命令都以 `$TASK_DIR` 指代当前要操作的任务目录，执行前先设置好（见 1.1）。文中的模块结构、产物路径和环境配置基于仓库当前主线与本机 Docker/Tomcat/nginx 环境整理；分支不同导致结构有差异时，以实际代码为准。不要把密码、Token 或个人凭据写入本文档或仓库。

## 1. 工作区与仓库

### 1.1 目录约定

所有开发任务放在 `/usr/local/javaspace` 下，一个任务对应一个独立目录。新任务使用新的目录名（例如 `training-<n>` 或按需求命名），不要覆盖已有检出。任务目录的标准结构为：

```text
/usr/local/javaspace/<task>/
  manager/    # https://git.whaty.cn/offline-training/manager.git
  workspace/  # https://git.whaty.cn/offline-training/workspace.git
  web/        # https://git.whaty.cn/offline-training/web.git
```

本机可能同时存在多个任务目录。开始任何操作前，先设置 `TASK_DIR`，并确认其中三个仓库属于同一个任务、分支和 commit 符合预期：

```bash
TASK_DIR=/usr/local/javaspace/<task>   # 替换为本次要操作的任务目录
test -d "$TASK_DIR" || { echo "task dir missing: $TASK_DIR"; exit 1; }
for repo in manager workspace web; do
  echo "--- $repo ---"
  git -C "$TASK_DIR/$repo" rev-parse --abbrev-ref HEAD
  git -C "$TASK_DIR/$repo" log -1 --oneline
  git -C "$TASK_DIR/$repo" remote -v | sed -n '1,2p'
done
```

默认使用三个仓库的 `master` 分支。拉取前必须确认工作区干净；不要把未提交改动自动混入部署：

```bash
for repo in manager workspace web; do
  repo_dir="$TASK_DIR/$repo"
  test -z "$(git -C "$repo_dir" status --porcelain)" || {
    echo "未提交改动：$repo_dir" >&2
    exit 1
  }
  git -C "$repo_dir" fetch -q --prune origin
  git -C "$repo_dir" switch -q master
  git -C "$repo_dir" merge -q --ff-only origin/master
done
```

fetch 之后直接 `merge --ff-only origin/<branch>`，不要再 `pull origin <branch>`：实测 `pull -q --ff-only` 偶发 `Cannot fast-forward to multiple branches`；`-q` 用于压掉数百行的 tag/分支列表和 diffstat（`scripts/update-repos.sh` 只输出 `旧 -> 新（N 个提交）`）。

如果任务必须部署其他分支，应在任务记录中明确写出三个仓库各自的分支，并把上面的 `master` 替换为同一目标分支。

Docker 中的 Tomcat 是单活实例，同一时间只能挂载一个任务目录。不要为了省事把新任务放进 Docker 当前指向的目录；应在启动前把 Docker 配置显式切换到本次的 `TASK_DIR`（见第 7 节）。

### 1.2 源码结构

`manager` 的顶层 reactor 有 5 个模块：

```text
manager-common (jar)
manager-domain (jar)
manager-dao (jar)
manager-service (jar)
manager-web (war -> target/manager)
```

`workspace` 顶层 reactor 的实际结构是 10 个模块：

```text
tycj-dependencies (pom)
tycj-parent      (pom)
tycj-api         (war, attachClasses 供其他模块引用)
tycj-web         (pom)
  tycj-web-base      (war)
  tycj-web-mobile    (war)
  tycj-web-pc        (war)
  tycj-web-workspace (war)
tycj-main         (war -> target/tycj-main)
```

`web` 是 Vue 2.6、Vue Router 3、webpack 3.12、`node-sass` 4.14.1 的旧前端，构建脚本实际执行 `node build/build.js`。

## 2. 先决条件

### 2.1 Java/Maven

- JDK 8；当前项目使用 `1.8` 编译目标。
- Maven 3.9 可以使用，但会拦截外部 HTTP 仓库。
- 当前本机已验证：OpenJDK 8、Maven 3.9.x。
- Java 项目的数据库通常是 MySQL 8、Redis 和若干外部服务；构建阶段不要求这些服务，运行和登录验证阶段需要。

确认工具：

```bash
java -version
mvn -version
```

### 2.2 Node/npm

使用项目已验证的 Node 8（推荐 `8.17.0`）。不要用系统 Node 24 等新版本构建 webpack 3/node-sass 4。

当前机器使用 nvm 时，构建前记录当前版本，构建结束无论成功失败都还原：

```bash
export NVM_DIR="$HOME/.nvm"
. "$NVM_DIR/nvm.sh"
nvm current
nvm alias default
```

### 2.3 网络与凭据

- Maven 私服必须通过 HTTPS 镜像访问。
- npm 使用 `https://registry.npmmirror.com`。
- Docker 镜像优先从 `docker.1panel.live` 拉取后再打本地 tag。
- 认证信息只放在本机受保护的配置、环境变量或密码管理系统中，权限至少保证 `~/.m2/settings.xml` 为 `600`。

## 3. Maven settings（首次构建前必须检查）

项目 POM 仍声明了若干 `http://maven.webtrn.cn/...` 仓库。Maven 3.9 的 `maven-default-http-blocker` 会直接阻断它们，因此 `~/.m2/settings.xml` 必须把 POM 中的 repository **name** 分别映射到 HTTPS，不能只配置一个笼统的聚合镜像。

| POM repository name | HTTPS 地址 |
|---|---|
| `Whaty Release Repositories` | `https://maven.webtrn.cn/repository/maven-releases/` |
| `Whaty Central Repositories,mvnRepository` | `https://maven.webtrn.cn/repository/maven-central/` |
| `Whaty 3rd Repositories Old` | `https://maven.webtrn.cn/repository/whaty-repo-3rd-old/` |
| `Whaty Public Repositories Old` | `https://maven.webtrn.cn/repository/whaty-repo-public-old/` |
| `Whaty Release Repositories Old` | `https://maven.webtrn.cn/repository/whaty-repo-release-old/` |

每个镜像 ID 都必须在 `<servers>` 中有同 ID 的认证。不要把账号密码复制到本文档。不要把所有仓库改成单个 `maven-public`：

- `whaty.exam:exam-commons:3.0.0` 只在 old 仓库中可用；
- `com.lowagie:iTextAsian:2.1.2`、`com.whaty.alidayu:taobao-sdk-java-auto:1.0` 也可能只在 old 仓库中可用。

检查配置是否仍残留 Windows 本地仓库路径或 HTTP 镜像：

```bash
stat -c '%a %n' "$HOME/.m2/settings.xml"
rg -n 'F:|\\\\maven|http://maven.webtrn.cn|mirrorOf|localRepository' "$HOME/.m2/settings.xml"
```

本地仓库应为 `${user.home}/.m2/repository` 或明确的 Linux 绝对路径。修正镜像后，清理失败标记而不是删除已缓存依赖：

```bash
find "$HOME/.m2/repository" -name '*.lastUpdated' -delete
```

### 3.1 构建前停止容器并检查运行时目录

不要在 Tomcat 正在使用 exploded 目录时执行 `clean`。只有 Docker 当前指向本 `TASK_DIR` 时才需要在构建前停止容器；指向其他任务时保持运行，构建完成后再切换并重建，停机只有重建的 30~40 秒：

```bash
cd /home/whaty/tomcat/docker
sg docker -c 'docker compose stop tomcat7'
```

`incoming` 由运行时写入。目录已存在时，检查当前用户可写，且没有容器遗留的其他所有者文件；检查失败时先由有权限的操作者修正权限。新检出或尚未构建的任务目录没有 `incoming`，直接跳过（`scripts/preflight.sh` 已包含此检查）：

```bash
INCOMING="$TASK_DIR/manager/manager-web/target/manager/incoming"
if [ -e "$INCOMING" ]; then
  test -w "$INCOMING" || { echo "incoming 不可写：$INCOMING" >&2; exit 1; }
  foreign=$(find "$INCOMING" ! -uid "$(id -u)" -print -quit)
  test -z "$foreign" || {
    echo "incoming 存在非当前用户拥有的文件：$foreign" >&2
    exit 1
  }
fi
```

启动容器前，`incoming` 必须已由当前用户创建（`scripts/start-verify.sh` 会 `mkdir -p`）；挂载源不存在时 Docker 会以 root 身份创建它，之后 `mvn clean` 会删除失败。

## 4. Java 构建

所有命令都从对应项目根目录执行。日志按任务命名放到 `/tmp/offline-training-<task>-*.log`，避免长时间构建被前台会话截断，也避免多个任务互相覆盖日志；实际结果以 `Reactor Summary` 为准。

### 4.1 manager

只验证编译时：

```bash
cd "$TASK_DIR/manager"
mvn -B clean compile -DskipTests > /tmp/offline-training-$(basename "$TASK_DIR")-manager-compile.log 2>&1
```

要运行或部署到 Tomcat 时必须走 package，推荐直接 install：

```bash
cd "$TASK_DIR/manager"
mvn -B clean install -DskipTests > /tmp/offline-training-$(basename "$TASK_DIR")-manager-install.log 2>&1
```

成功产物：`manager/manager-web/target/manager/`（exploded webapp），以及 `manager-web` 的 WAR。不要只运行 `compile` 后启动 Tomcat：`target/manager` 可能不存在或不完整。

### 4.2 workspace

`workspace` 是 WAR overlay 结构，`tycj-api` 和 `tycj-web-*` 通过 `type=jar`、`classifier=classes`、`scope=provided` 等方式互相引用。附属 `xxx-classes.jar` 在 war 插件 package 阶段通过 `attachClasses` 生成，compile 阶段不会生成并安装到本地仓库。

因此 workspace **必须**这样构建：

```bash
cd "$TASK_DIR/workspace"
mvn -B clean install -DskipTests > /tmp/offline-training-$(basename "$TASK_DIR")-workspace-install.log 2>&1
```

不要用 `mvn clean compile` 代替。典型错误是下游模块报 `package com.whaty.tycj... does not exist`，但对应源码就在 `tycj-api` 中；先检查是否缺少已安装的 classes jar：

```bash
find "$HOME/.m2/repository/com/whaty/tycj" -type f 2>/dev/null | sort | head -40
```

成功产物：`workspace/tycj-main/target/tycj-main/`，以及 `tycj-main.war`。正常的 Reactor Summary 应包含 10 个模块且全部 `SUCCESS`。

### 4.3 Java 构建验证

```bash
for log in /tmp/offline-training-$(basename "$TASK_DIR")-manager-install.log /tmp/offline-training-$(basename "$TASK_DIR")-workspace-install.log; do
  echo "--- $log ---"
  rg -n 'Reactor Summary|BUILD SUCCESS|BUILD FAILURE|SUCCESS|FAILURE|systemPath' "$log" | tail -80
done

test -d "$TASK_DIR/manager/manager-web/target/manager"
test -d "$TASK_DIR/workspace/tycj-main/target/tycj-main"
```

项目可能输出两条关于 `com.sun:tools:jar` `systemPath` 不是绝对路径的 ERROR，但仍然 `BUILD SUCCESS`。这两条是遗留 POM 告警，不要仅凭 ERROR 行数判定失败；应检查每个模块的 Reactor 状态。

## 5. Web 前端构建

### 5.1 Node 版本与依赖 ABI

`node-sass` 的预编译二进制绑定 Node ABI；Node 8 使用 ABI 57（`linux-x64-57`）。切换 Node 大版本后不能直接混用 `node_modules`，需要清理依赖：

```bash
cd "$TASK_DIR/web"
rm -rf node_modules dist
```

不要为重复的同一 Node 版本构建无条件清理依赖，这会浪费大量时间。`scripts/build-web.sh` 在 `node_modules/.offline-training-node-major` 中记录安装依赖的 Node 大版本，只有与本次构建不一致（或显式 `--clean-deps`）时才清理；无记录的旧依赖不清理，交给下面的 binding 检查和 `npm rebuild`。

安装完成后必须检查 Node 8 的 binding；已有其他 Node 版本依赖时先尝试重建：

```bash
test -f node_modules/node-sass/vendor/linux-x64-57/binding.node || npm rebuild node-sass
test -f node_modules/node-sass/vendor/linux-x64-57/binding.node
```

### 5.2 可恢复的完整构建

推荐直接运行 `scripts/build-web.sh "$TASK_DIR"`。下面是等价的手工版本：在 `( )` 子 shell 中切换 Node，并用 `trap` 确保中断或失败也恢复原版本；不会修改 `nvm alias default`。必须用 `( )` 而不是 `{ }`：`{ }` 在当前 shell 执行，内部 `exit` 会直接退出，后面的 `exit code:` 不会写入日志。

```bash
cd "$TASK_DIR/web"
LOG=/tmp/offline-training-$(basename "$TASK_DIR")-web-build.log
(
  export NVM_DIR="$HOME/.nvm"
  . "$NVM_DIR/nvm.sh"
  ORIG_NODE=$(nvm current)
  restore_node() {
    nvm use "$ORIG_NODE" >/dev/null 2>&1 || true
    echo "restored node: $(node -v)"
  }
  trap restore_node EXIT

  nvm use 8.17.0 || exit 1
  echo "build node: $(node -v), npm: $(npm -v)"
  export npm_config_registry=https://registry.npmmirror.com
  export SASS_BINARY_SITE=https://registry.npmmirror.com/-/binary/node-sass
  export npm_config_sass_binary_site="$SASS_BINARY_SITE"
  export npm_config_disturl=https://registry.npmmirror.com/-/binary/node
  export PYTHON=/usr/bin/python3

  npm install --no-package-lock --no-audit --no-fund || exit 1
  test -f node_modules/node-sass/vendor/linux-x64-57/binding.node || npm rebuild node-sass || exit 1
  test -f node_modules/node-sass/vendor/linux-x64-57/binding.node || exit 1
  npm run build || exit 1
) > "$LOG" 2>&1
status=$?
echo "exit code: $status" >> "$LOG"
```

成功标志：日志包含 `Build complete.` 且最终退出码为 0。典型产物是 `web/dist/index.html` 及约 1442 个静态文件。重新构建静态资源后不需要重启 Tomcat，刷新页面即可。

```bash
rg -n 'Build complete\.|exit code:' /tmp/offline-training-$(basename "$TASK_DIR")-web-build.log
node -v
nvm alias default
git -C "$TASK_DIR/web" status --short | grep -vE '^(.. )?(dist|node_modules)/' || true
```

`package-14.4.0.json` 是旧备份，不是当前依赖清单；构建使用当前 `package.json`。

## 6. 多套检出的 Maven 隔离

`~/.m2/repository` 是全局共享的。`workspace` 的 install 会写入约 10 个 `com.whaty.tycj` 构件，版本通常都是 `1.0-SNAPSHOT`；后构建的任务目录会覆盖先构建的构件。

- 各任务目录的 `workspace` 代码相同：共享本地仓库通常没有影响；先用 `git log -1` 确认。
- 各任务目录的代码不同（不同分支或未合并改动）：推荐给每个任务指定独立仓库，避免 classes jar 串用：

```bash
cd "$TASK_DIR/workspace"
mvn -Dmaven.repo.local="$TASK_DIR/.m2repo" -B clean install -DskipTests
```

隔离仓库会重新下载依赖，首次构建较慢。`web/node_modules` 和 `web/dist` 本来就在各自任务目录中，不共享这个 Maven 状态。

## 7. 本地运行：Docker + Tomcat 7 + nginx

### 7.1 部署关系

Tomcat 7 容器将宿主 `/usr/local/javaspace` 以相同路径只读挂载，`server.xml` 使用 exploded 目录：

| URL | docBase |
|---|---|
| `/` | `<TASK_DIR>/manager/manager-web/target/manager` |
| `/np` | `<TASK_DIR>/web/dist` |
| `/ws` | `<TASK_DIR>/workspace/tycj-main/target/tycj-main` |

`/home/whaty/tomcat/docker/server.xml` 和 `docker-compose.yml` 中写的是上一次启动的任务目录。切换任务时，必须同时把两份配置中的 docBase 和 `incoming` 可写挂载改为本次的 `TASK_DIR`，不要只改其中一份。这是共享运行配置，修改前先展示差异并确认：

```bash
scripts/switch-docker.sh "$TASK_DIR"          # 只预览 diff，不写入
scripts/switch-docker.sh "$TASK_DIR" --apply  # 确认后写入，自动备份为 *.bak-<时间>
```

脚本只替换 docBase 行与 `/incoming:` 挂载行，并按 `<旧目录>/<仓库>/` 精确匹配，避免 `training` 与 `training-2` 这类前缀误替换；写入前校验两份配置只引用 `TASK_DIR`。手工修改时同样要覆盖 3 个 docBase 和 1 条挂载（挂载行宿主、容器两侧路径都要改）。

改完后必须重建容器使挂载生效（`scripts/start-verify.sh "$TASK_DIR" --recreate`，见 7.2）。

`server.xml` 的 Host 必须使用空的 `webapps-empty`，并关闭自动部署，避免镜像自带 ROOT 与 path `/` 冲突。不要给 Tomcat 7.0.109 的 Context 写已废弃的 `allowLinking="true"` 属性。

manager 的打印、导出和上传会写 `manager-web/target/manager/incoming`。主挂载是只读时，必须在 compose 中对这个子目录单独加 `:rw` 覆盖，否则会出现 `Read-only file system`。

### 7.2 启动、重建和检查

推荐用 `scripts/start-verify.sh "$TASK_DIR" [--recreate]` 一次完成启动、等待、网关核对和 HTTP 检查。顺序必须是：启动 → `Server startup in` → `running 0` → 取网关 → 核对 `extra_hosts` → HTTP。容器停止时 `docker inspect` 取不到网关（为空），所以网关检查只能在启动后进行；不一致时改 compose 后 `--recreate` 重跑。

当前 gateway 进程可能尚未获得 Docker 组权限，命令统一用 `sg docker -c`；如果已确认当前会话有权限，可以省略外层包装：

```bash
cd /home/whaty/tomcat/docker
sg docker -c 'docker compose up -d'
sg docker -c 'docker inspect training-tomcat7 --format "{{.State.Status}} {{.RestartCount}}"'
sg docker -c 'docker compose logs -f tomcat7'
```

成功条件是日志出现 `Server startup in NNNNN ms`，且 `RestartCount` 为 `0`。修改 `extra_hosts` 后 `restart` 不会重写容器 hosts，必须强制重建：

```bash
sg docker -c 'docker compose up -d --force-recreate'
```

容器运行时的网桥网关不是固定的 `172.17.0.1`。当前 compose 网络通常是 `172.18.0.1`，重建网络后重新取值：

```bash
sg docker -c 'docker inspect training-tomcat7 --format "{{range .NetworkSettings.Networks}}{{.Gateway}}{{end}}"'
```

修改或重建网络后，确认 compose 中两个业务域名使用的地址与实际 gateway 一致，再执行 `--force-recreate`：

```bash
GATEWAY=$(sg docker -c 'docker inspect training-tomcat7 --format "{{range .NetworkSettings.Networks}}{{.Gateway}}{{end}}"')
rg -n "whatypx\.cep\.webtrn\.cn:$GATEWAY|control-training\.webtrn\.cn:$GATEWAY" /home/whaty/tomcat/docker/docker-compose.yml
```

`whatypx.cep.webtrn.cn` 和 `control-training.webtrn.cn` 应映射到这个宿主网桥地址，让容器内回调也经过宿主 nginx:80。不要使用本机 compose 不支持的 `host-gateway` 字面值，也不要映射到容器自身的 `127.0.0.1`。

如果镜像不存在或 Docker Hub 超时：

```bash
sg docker -c 'docker pull docker.1panel.live/library/tomcat:7-jdk8'
sg docker -c 'docker tag docker.1panel.live/library/tomcat:7-jdk8 tomcat:7-jdk8'
```

### 7.3 nginx

nginx 源配置是 `/home/whaty/tomcat/docker/nginx-training.conf`，生效配置是 `/etc/nginx/conf.d/training.conf`：

每次 Java 或 web 代码构建都不需要更新 nginx。只有首次部署、域名/代理规则变化，或源配置与生效配置确实不一致时，才执行复制、测试和 reload：

用 `scripts/sync-nginx.sh`：配置一致时直接退出；不一致时先打印 diff、检查必需指令，再复制、`nginx -t`、reload。生效配置当前由 `whaty` 拥有，可直接 `cp`；不可写时改用 `sudo -n cp`。`nginx -t` 失败会还原原配置；任何一步需要密码或被拒绝时退出码为 2，并打印交给有权限者执行的命令：

```bash
cmp -s /home/whaty/tomcat/docker/nginx-training.conf /etc/nginx/conf.d/training.conf \
  && echo 'nginx 配置未变化' \
  || echo 'nginx 配置有变化，需要测试并 reload'
# 仅在有变化时执行；目标不可写则把 cp 换成 sudo -n cp
cp /home/whaty/tomcat/docker/nginx-training.conf /etc/nginx/conf.d/training.conf
sudo -n nginx -t
sudo -n systemctl reload nginx
```

nginx 必须监听 `0.0.0.0:80`，并且不能保留会抢占请求的 `sites-enabled/default`；不要给 training 站点添加第二个 `default_server`。代理至少要保留：

- `proxy_set_header Host $host`，否则应用可能生成 `127.0.0.1:8080` 的绝对地址；
- `client_max_body_size 1024m`，否则附件上传默认会被 1 MB 限制拦截；
- 带 `Upgrade`/`Connection` 头的 WebSocket location，`tycj-main` 的 sockjs 需要它。

如果安全策略拦截对 `/etc/nginx` 的写入，不要反复重试 patch 或 `cp`，让有权限的操作者执行明确的复制、`nginx -t` 和 reload 命令。

## 8. 访问与端到端验证

### 8.1 必须使用域名

管理端入口使用：

```text
http://whatypx.cep.webtrn.cn/np/
```

超管入口使用：

```text
http://control-training.webtrn.cn/
```

不要用 IP 代替域名。前端的 `getClientIdByDomain()` 按访问域名取 `client_id`，IP 访问会导致配置接口 400、登录 iframe 地址错误。根路径 `/` 可能跳转 HTTPS，而本机通常只提供 HTTP；验证前端请使用 `/np/`。

容器内外都检查。判定标准：`/np/` 容器内外都必须返回 200，这是唯一的硬性条件；`/ws/`、`/` 和超管入口只记录状态码供判断（本机当前均为 200），`/` 跳转 HTTPS 不算失败：

```bash
curl -sS -o /dev/null -w '%{http_code}\n' http://whatypx.cep.webtrn.cn/np/
sg docker -c 'docker exec training-tomcat7 sh -c "curl -sS -o /dev/null -w %{http_code} http://whatypx.cep.webtrn.cn/np/"'
```

容器刚重建时 `curl` 暂时返回 `000` 是启动窗口，等 `Server startup in` 后重试。

### 8.2 Playwright 登录验证

本机 Playwright 环境位于 `/home/whaty/.hermes/venvs/playwright/`，已有脚本位于 `/home/whaty/tomcat/docker/do-login3.py`。Chromium 需要显式指定当前安装的 `chromium-1243`，并使用 `--no-sandbox`、`--disable-dev-shm-usage` 等参数。

验证脚本要点：

1. 使用授权的测试账号，并从受保护的凭据来源读取密码；不要把密码写入本文档、脚本或命令历史。统一认证失败次数可能触发账号锁定，不能反复试密码。
2. 登录表单在外部 ucenter iframe 内，先选择 URL 包含 `ucenter` 的 frame，再操作表单。
3. 登录元素是 `a.login-btn`，不是 `button` role。
4. 不要拦截 `static-gaoxiao.webtrncdn.com` 上的 Vue、Vue Router、lodash 核心脚本。
5. 截图使用 `page.screenshot()`；`Frame` 没有 `screenshot()` 方法。
6. 需要可读中文截图时，宿主和容器应有文泉驿中文字体。

登录成功的可观察结果是：统一认证 POST 返回 `success: true`，`/core/user/info` 返回 200，页面从 `#/login` 进入 `#/home`，并能看到数据看板及菜单。

### 8.3 测试时读取后端错误日志

容器以 root 运行，Tomcat 7 默认 `UMASK=0027`，所以 `/home/whaty/tomcat/docker/logs` 下的文件都是 `root:root 640`，目录是 `750`，宿主用户读不了。日志的实际去向：

| 内容 | 位置 | 读取方式 |
|---|---|---|
| 应用 log4j 输出（含 ERROR、异常堆栈、MyBatis SQL）、JULI 控制台 | 容器 stdout | `docker logs --since <时间> training-tomcat7` |
| Tomcat 启动/Context 错误 | `catalina.<日期>.log`、`localhost.<日期>.log`，同时输出到 stdout | 同上，或 `docker exec training-tomcat7 tail ...` |
| 请求状态码 | `localhost_access_log<日期>.txt`（仅文件） | `docker exec training-tomcat7 tail ...` |
| 其他业务日志（`ssoFilterLog.log`、`whatycache/` 等） | 仅文件 | `docker exec` |

`scripts/logs.sh` 封装了这些读取方式：`mark` 记录时间点和 access log 行数；`check` 找出这之后的 ERROR/SEVERE 和异常堆栈（过滤已知噪音），以及 5xx 请求，有问题时退出码为 1；另有 `tail`、`file` 子命令。access log 的格式是 `%h %l %u %t "%r" %s %b %D`，状态码是第二对引号之后的第一个字段。

如果希望宿主直接读文件，可以在 compose 的 `environment` 中加 `UMASK: "0022"` 并 `--force-recreate`；之后新建的文件是 644，已有文件还需在容器里执行一次 `chmod -R a+rX /usr/local/tomcat/logs`。这需要修改共享配置，按需执行。

## 9. 配置与数据库检查

运行时配置分布在：

- `manager/manager-web/src/main/filters/filter-dev.properties`
- `workspace/tycj-main/src/main/resources/application-dev.yaml`

当前本地运行使用的管理库应确认是 `training_manage_all`；旧文档或旧脚本中仍可能出现 `training_manage`。看到 `Table '...system_variables' doesn't exist` 时，先确认当前分支和目标数据库，再检查最新代码，不要直接修改 `target` 产物中的配置。

manager 和 workspace 都依赖 Redis、MySQL 及外部域名。容器 `extra_hosts` 至少要覆盖项目实际使用的 Redis、MySQL 和两个业务域名；缺少映射会出现 `UnknownHostException` 或容器内自调用失败。

## 10. 故障速查

| 现象 | 优先检查 |
|---|---|
| `Blocked mirror for repositories` | `settings.xml` 是否按 repository name 配 HTTPS 镜像 |
| `present, but unavailable` | 删除 `.lastUpdated` 后重试，保留 jar/pom |
| `mvn clean` 无法删除 `incoming` | 构建前停止容器，检查 `incoming` 递归所有者和可写性 |
| workspace 找不到 `com.whaty.tycj...` | 是否误用 `compile`；重新 `clean install` |
| manager 编译成功但 Tomcat 找不到根应用 | 是否执行 package/install，检查 `manager-web/target/manager` |
| node-sass ABI 或 `did not self-register` | 检查 `linux-x64-57/binding.node`，必要时执行 `npm rebuild node-sass` |
| `BUILD SUCCESS` 但某模块失败 | 逐模块看 `Reactor Summary`，不要只看最后一行 |
| 容器 hosts 还是旧 IP | `restart` 无效，使用 `up -d --force-recreate` |
| 容器访问宿主 80 被拒绝 | 检查网桥 Gateway、nginx 是否监听 `0.0.0.0:80` |
| `Server startup in` 出现但 `/` 或 `/ws/` 404 | Context 启动失败；`docker logs training-tomcat7` 中搜 `SEVERE`/`startup failed` |
| 宿主读不了 `logs/catalina.*.log` | 容器以 root、UMASK 0027 写入；用 `scripts/logs.sh`（见 8.3） |
| 测试页面正常但数据不对 | 测试前 `logs.sh mark`、测试后 `logs.sh check`，看是否有被前端吞掉的后端异常 |
| `/np/` 200 但页面是旧版本 | 比较 `/np/` 返回的 index.html 与 `$TASK_DIR/web/dist/index.html`，确认 Docker 已切换并重建 |
| `Cannot fast-forward to multiple branches` | fetch 后用 `merge --ff-only origin/<branch>` 代替 `pull` |
| 页面 API 全部 400 或一直加载 | 用域名 `/np/` 访问，不要用 IP 或 `/` |
| 上传 413 | nginx `client_max_body_size` 是否为 `1024m` |
| 上传/打印 `Read-only file system` | compose 是否对 `incoming` 子目录加了 `:rw` |
| 页面空白 | 是否拦截 CDN 上的 Vue 依赖；查看浏览器控制台和 nginx/Tomcat 日志 |

## 11. 完成前清单

```text
[ ] 当前任务目录在 /usr/local/javaspace 下，未覆盖其他检出
[ ] manager/workspace/web 的 commit 和分支已确认
[ ] ~/.m2/settings.xml 使用 HTTPS 私服镜像，权限为 600，无 Windows 本地路径
[ ] .lastUpdated 已按需清理
[ ] 构建前已停止 Tomcat，incoming 目录（若存在）可写且所有者正确
[ ] manager 至少 package；部署场景使用 install
[ ] workspace 使用 clean install，10 个模块全部 SUCCESS
[ ] web 使用 Node 8、npmmirror，Node-sass ABI 57 检查通过，日志有 Build complete.，Node 已还原
[ ] 三个 exploded 产物目录存在且对应同一 TASK_DIR
[ ] server.xml、docker-compose.yml 的 docBase 和 incoming 挂载指向同一 TASK_DIR
[ ] Docker 容器 RestartCount 为 0，日志有 Server startup in
[ ] nginx 仅在配置变化时测试并 reload
[ ] 使用域名 /np/ 进行容器内外 HTTP 检查，均为 200
[ ] Playwright 登录成功，且未在文档/代码中保存凭据
[ ] git status 中没有误提交的 target、dist、node_modules 或凭据文件
```
