---
name: offline-training-deploy
description: Build, update, deploy, and verify the local offline-training manager, workspace, and web repositories under /usr/local/javaspace through Maven, Node 8, Docker/Tomcat 7, and nginx. Use when the user asks to pull the latest offline-training code, build or deploy a training task directory, switch the local Tomcat container to another task, check that the local training site is up, or read backend error logs during automated testing. 中文触发：部署 training、拉最新代码构建、打包 manager/workspace/web、切换 Docker 到 training-N、本地起 Tomcat、验证 np 页面能否访问、自动化测试、查看后端报错/Tomcat 日志。
---

# Offline Training Deploy

本地部署 offline-training 三仓库：`manager`（`/`）、`workspace`（`/ws`）、`web`（`/np`），运行在单实例 Docker Tomcat 7 + 宿主 nginx 上。

完整说明、原理和故障速查在 [references/offline-training-build.md](references/offline-training-build.md)。先按下面的流程执行脚本；脚本报错或遇到流程没覆盖的情况时，再读 reference 的对应章节。

## 约定

- 脚本目录记为 `S=<skill 目录>/scripts`，所有脚本第一个参数都是 `TASK_DIR`（`/usr/local/javaspace/<task>`）。
- 构建很慢且输出量大：Maven 和 npm 的完整日志按任务区分，写到 `/tmp/offline-training-<task>-{manager-install,workspace-install,web-build}.log`，终端只打印摘要。用 Bash 后台运行或设置较长 timeout，只看脚本的摘要和退出码，不要把完整日志读进上下文；失败时再 `grep`/`tail` 相关日志。
- 任何脚本以非 0 退出都要停下来，向用户报告原因，不要绕过检查继续。
- 不要把密码、Token 写进命令参数、日志、脚本或仓库。

## 流程

1. **确认任务和分支。** 用户没指定分支时默认 `master`。需要拉取最新代码时：
   `$S/update-repos.sh $TASK_DIR [branch]`，三个仓库任一有未提交改动就整体停止，由用户决定。
   用户要求直接部署当前分支或本地改动时跳过这一步。

2. **预检。** `$S/preflight.sh $TASK_DIR`：检查 JDK 8、Maven、nvm 中的 Node 8.17.0、`settings.xml` 的 HTTPS 镜像，以及 `incoming` 的所有者和写权限（新目录里还没有 `incoming` 时跳过）。输出 `BLOCK` 就停止；`WARN` 告诉用户即可。

3. **按需停止 Tomcat。** 只有 preflight 显示 Docker 当前指向的就是本 `TASK_DIR` 时才需要停：Tomcat 占用 exploded 目录时不能执行 `mvn clean`。
   `cd /home/whaty/tomcat/docker && sg docker -c 'docker compose stop tomcat7'`
   （当前会话已有 docker 组权限时可以省掉 `sg docker -c`）。
   如果 Docker 指向的是别的任务，就不要停，让旧任务继续提供服务，等到第 7 步重建时再切换，这样停机时间只有容器重建的 30~40 秒。如果误判了，`build-java.sh` 会拒绝在正在使用中的目录上执行 clean。

4. **构建。** manager、workspace、web 三者互不依赖，可以同时在后台运行（实测：Java 并行约 50 秒，web 首次安装依赖约 130 秒）。Java 部分：
   `$S/build-java.sh $TASK_DIR manager`、`$S/build-java.sh $TASK_DIR workspace`。
   脚本以 `mvn -B clean install -DskipTests` 的退出码和 Reactor Summary 里每个模块都是 SUCCESS 为准；`com.sun:tools` 的 `systemPath` ERROR 是遗留告警，可以忽略。workspace 必须用 install，不能用 compile 代替。

5. **web 构建**（和第 4 步同时进行）。`$S/build-web.sh $TASK_DIR`：切到 Node 8.17.0，使用 npmmirror，检查 node-sass 的 ABI 57 binding，要求日志出现 `Build complete.`，结束后恢复原 Node 版本。
   只有 `node_modules` 是用其他 Node 大版本安装的时候，脚本才会自动清理，同版本重复构建会复用依赖。只有依赖明显损坏时才加 `--clean-deps`。

6. **切换 Docker 指向。** `$S/switch-docker.sh $TASK_DIR` 默认只预览 `server.xml` 三个 docBase 和 compose 里 `incoming` rw 挂载的差异。
   - 输出"无需修改"：直接进入第 7 步。
   - 有差异：这是在修改共享的运行配置，先把差异给用户看，确认后再执行 `--apply`（两份配置一起改，并自动备份 `.bak-<时间>`），然后第 7 步必须带 `--recreate`。

7. **启动并验证。** `$S/start-verify.sh $TASK_DIR [--recreate]`：
   - 挂载或 `extra_hosts` 改过时必须加 `--recreate`（`restart` 不会生效）。
   - 顺序是：启动 → 等日志出现 `Server startup in` → 状态为 `running 0` → 扫描 `SEVERE`/`startup failed` → 取网桥网关 → 核对 compose 里两个业务域名的映射 → HTTP 检查。容器停止时拿不到网关，所以网关检查放在启动之后。`Server startup in` 只说明 Tomcat 起来了，单个 Context 失败时它照样会打印，所以还要扫错误日志。
   - 网关不一致时，脚本会提示修改 compose 的 `extra_hosts`；修改前先征求用户同意，改完加 `--recreate` 重跑。
   - HTTP 检查：`/np/` 在容器内外都必须返回 200，并且返回的 `index.html` 要和 `$TASK_DIR/web/dist` 里的一致，以此证明确实切到了本任务。`/ws/`、`/`、超管入口返回 2xx/3xx 都算通过（根路径可能跳转 HTTPS），404、5xx、000 说明对应的 Context 没起来。必须用域名访问，不要用 IP。
   - 失败时查看日志用 `sg docker -c 'docker logs training-tomcat7'`。宿主 `logs/` 目录下的 `catalina.*.log`、`localhost.*.log` 是容器里的 root 写的，宿主用户读不了。
   - 回滚到之前的任务：`$S/switch-docker.sh <旧 TASK_DIR> --apply`，然后执行 `$S/start-verify.sh <旧 TASK_DIR> --recreate`。旧任务的产物还在，不需要重新构建。

8. **nginx（通常跳过）。** 普通的 Java 或 web 构建不需要动 nginx。只有首次部署或 `nginx-training.conf` 有变化时才运行 `$S/sync-nginx.sh`。配置没变化时它直接退出；`nginx -t` 失败会自动还原；退出码为 2 表示需要更高权限，这时把脚本打印的命令交给用户执行，不要重试，也不要尝试其他写法。

9. **可选：登录验证。** 只有用户提供了授权测试账号才做，见 reference 8.2。统一认证失败次数多了会锁账号，不能反复试密码。

## 自动化测试时判断后端报错

宿主 `logs/` 下的文件都是容器里的 root 以 640 权限写的，宿主用户读不了，不要去读它们，也不要想办法 sudo 提权。统一用 `$S/logs.sh`，它通过 `docker logs` / `docker exec` 读取日志，不受文件权限影响：

1. 每轮测试（Playwright 脚本、curl 调接口）开始前先执行 `$S/logs.sh mark`。
2. 测试结束后执行 `$S/logs.sh check`。它会列出标记之后应用的 ERROR/SEVERE 日志和异常堆栈（每条最多 40 行），以及 access log 里的 5xx 请求。退出码 1 表示后端有报错，必须把报错内容和对应的请求一起报告给用户，不能因为页面看起来正常就判定测试通过。加 `--warn` 可以把 WARN 也纳入检查。
3. 需要更多上下文时：`$S/logs.sh tail 500` 查看最近的容器输出（其中包含 MyBatis SQL）；`$S/logs.sh file localhost.<日期>.log` 读取 Tomcat 的某个日志文件。
4. 前端的问题（接口 4xx、JS 报错）不会出现在这里，要看 Playwright 抓到的 console 和 response。4xx 不算后端报错，例如未登录时返回 401 是正常的。

已知无害的噪音（druid 的 `maxIdle is deprecated`、Tomcat 停止时的内存泄漏提示、CGLIB 代理告警）已经在 `common.sh` 的 `NOISE` 里过滤掉了；以后发现新的噪音，就追加到这里。

## 边界

只操作本机 Docker/Tomcat/nginx，不部署任何外部或生产环境，不修改数据库，不修改 `target` 里的配置产物（配置问题见 reference 第 9 节）。最后按 reference 第 11 节的清单汇报：三个仓库的分支和 commit、各构建结果、Docker 当前指向、HTTP 检查结果。
