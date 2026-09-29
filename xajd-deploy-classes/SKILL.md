---
name: xajd-deploy-classes
description: Use when 给 XAJD 的 web / manager / workspace 上线时 —— 先构建(maven + node8),再从 git 改动算出编译产物路径(区分 WEB-INF/classes 散装 class、WEB-INF/lib 里的 jar,带内部类),按 pom diff 算出新增第三方 jar,打成保留层级的单个 zip,并生成可直接粘到服务器执行的 mkdir/mv 部署命令。关键词:xajd 上线 部署 deploy class jar zip 打包 web manager workspace git 提交 commit 增量
---

# XAJD git 增量上线命令生成

## 概述

每次上线痛点:改了几个类,却要手工对照包名去 target 里翻 .class、再拼服务器路径。本 skill 让 Claude **直接读 git 改动记录**,算出每个改动源文件对应的编译产物路径,打包并生成上线命令。

核心数据来源是 git 改动(已知改了什么),而非扫描文件时间戳(会被 `mvn clean` 全量刷新污染)。

整体链路:**构建 → 算增量 → 算 jar → 查新目录 → 复制到暂存目录 → 打 zip(带顶层总目录) → 出服务器命令(解包 → chmod → mkdir → mv → chown)**。

## 何时使用

- web / manager / workspace 任意一个或多个准备上线
- 不想手工对照包名拼 .class 路径
- 需要一份"本次上线改了哪些 class / 哪些 jar"的清单
- 需要把三个项目打成一个 zip 交付,并配套服务器执行命令

产出:一个 zip + 一份服务器命令。**构建与打包会执行,上传/部署不执行。**

## 固定事实(已实测核实)

**三个**独立 git 仓库,**产物结构各不相同,必须区别对待**:

| 项目 | git 根 | 打包产物根 | 产物形态 |
|------|--------|-----------|---------|
| web | `xajd/web` | `web/dist` | 前端静态,**全量**(dist 被 gitignore,算不出增量) |
| manager | `xajd/manager` | `manager/manager-web/target/manager` | 分两路:散装 class + jar |
| workspace | `xajd/workspace` | `workspace/tycj-main/target/tycj-main` | 全部散装 class |

### 构建命令(每次上线前必须先构建)

**后端一律用 maven**,在各自 git 根执行:
```bash
cd D:/JavaSpace/deployment/xajd/manager   && mvn clean install -DskipTests
cd D:/JavaSpace/deployment/xajd/workspace && mvn clean install -DskipTests
```
实测耗时:manager ~2:15(6 子模块)、workspace ~6:15(10 模块)。可后台并行跑。

**web 必须用 node 8**:
```bash
cd D:/JavaSpace/deployment/xajd/web
nvm use 8.17.0 && npm run build      # 等价于 node build/build.js
```
- `node_modules/node-sass/vendor` 里只有 `win32-x64-57` 绑定 = **Node 8.x**。用 node 10+ 构建会报 `Missing binding ... win32-x64-64` / `Node Sass could not find a binding`,webpack 直接 Build failed。
- nvm 装的版本用 `nvm list` **看全**(别 head 截断,8.17.0 可能排在末尾)。nvm 根在 `F:/nvm`,node 8 实际路径 `F:/nvm/v8.17.0/node.exe`。
- **bash 会话里 `nvm use` 后 `node -v` 可能仍报旧版本**(PATH/命令缓存),这是假象 —— symlink 已切换。稳妥做法是用绝对路径执行:
  ```bash
  /f/nvm/v8.17.0/node build/build.js
  ```

### manager —— 分两路产物(易错点)

git 根:`D:/JavaSpace/deployment/xajd/manager`

| 子模块 | 源码路径前缀 | 产物去向 |
|--------|-------------|---------|
| `manager-web` | `manager-web/src/main/java/` | **散装 class** → `WEB-INF/classes/` |
| `manager-web` | `manager-web/src/main/resources/` | **散装资源** → `WEB-INF/classes/` |
| `manager-common`<br>`manager-domain`<br>`manager-dao`<br>`manager-service` | `manager-<x>/src/main/java/` | **打进 jar** → `WEB-INF/lib/manager-<x>-1.1.1.jar` |

- 散装 classes 根:`D:/JavaSpace/deployment/xajd/manager/manager-web/target/manager/WEB-INF/classes`
- jar 所在目录:`D:/JavaSpace/deployment/xajd/manager/manager-web/target/manager/WEB-INF/lib`
- `manager-web` 的顶层包只有 `com/whaty/{api,framework,products,web}`;`manager-domain` 的 `com/whaty/domain/...` **不在 classes 里**,去 classes 找必然找不到。
- 4 个非 web 子模块的 `src/main/resources` 实测为空,资源只可能来自 `manager-web`。

### workspace —— 全部散装

git 根:`D:/JavaSpace/deployment/xajd/workspace`

- 所有子模块(`tycj-api`、`tycj-web/tycj-web-*` 等)的 class 与 resources **全部 unpack 进** `WEB-INF/classes`
- classes 根:`D:/JavaSpace/deployment/xajd/workspace/tycj-main/target/tycj-main/WEB-INF/classes`
- `WEB-INF/lib` 里只有第三方 jar(`whaty-common`、`whaty-sso-client` 等),无自有业务 jar,不参与增量上线

### web —— 全量,不走增量

- `web/dist` 被 `.gitignore` 忽略(`/dist/`),**无法从 git 推导哪些 dist 文件属于本次改动**。
- 构建即全量重建(实测 798 文件 / 47M,mtime 全部为构建时刻),且 webpack 带 hash 的 chunk 文件名每次变化。
- 因此 web 一律**整个 dist 全量上线**:`index.html` + `static/` 整目录替换。
- 线上若已有 `static`,直接 `mv static` 会嵌套成子目录 → 必须先把旧的挪走(见"需人工处理项")。

服务器 class 部署路径是 `WEB-INF/classes`(用户口语里的 `WEB-INFO/class` 是笔误)。

## 工作流程

### 第 0 步:先构建(不可跳过)

**每次上线前都要重新构建**,否则 target 里是上一次的产物,算出来的 class 是旧的。用户明确要求:后端 maven、前端 node 8。

只构建**本次有改动**的项目;三个都改了就三个都构建。maven 两个项目可后台并行(互不依赖):
```bash
cd D:/JavaSpace/deployment/xajd/manager   && mvn clean install -DskipTests
cd D:/JavaSpace/deployment/xajd/workspace && mvn clean install -DskipTests
cd D:/JavaSpace/deployment/xajd/web       && /f/nvm/v8.17.0/node build/build.js
```

构建结束必须确认 `BUILD SUCCESS` / `Build complete`,失败就停下报给用户,**不要拿半成品 target 继续往下算**。

### 第 1 步:确定改动范围

**先探测,别直接假设"今天"。** 按顺序检查,拿到非空结果即停:

```bash
# a) 未提交的工作区改动(改完还没 commit 就上线的场景)
git -C "<模块git根>" status --porcelain

# b) 最近提交概览 —— 用来判断该取哪个范围
git -C "<模块git根>" log -8 --pretty=format:"%h %ad %s" --date=format:"%Y-%m-%d %H:%M"
```

拿到 log 后按实际情况选范围,**不要默认 `--since="today 00:00"`**(实测常常今天无提交,白跑一轮):

```bash
# 指定单次提交
git -C "<根>" show --name-status --pretty=format: <commit>

# 某个提交之后的全部
git -C "<根>" diff --name-status <commit>..HEAD

# 按时间
git -C "<根>" log --since="<范围>" --name-status --pretty=format: | grep -v '^$' | sort -u
```

用 `--name-status` 而非 `--name-only`,因为要区分状态:
- `M` / `A` → 正常上线
- `D`(删除) → 对应 class 需要**从线上删除**,不是 mv,单独提示用户,不要混进 mv 块
- `R`(重命名) → 旧 class 要删、新 class 要上,两边都列

若 a) 和 b) 都有内容(既有已提交又有未提交改动),向用户说明并确认取哪些,别自行合并。

### 第 2 步:按子模块前缀分流(manager 关键)

拿到改动文件列表后,**先看模块前缀,决定走 classes 还是 jar**:

| 源文件 | 归属 | 处理方式 |
|--------|------|---------|
| `manager-web/src/main/java/<pkg>/X.java` | 散装 | → `<pkg>/X.class` |
| `manager-web/src/main/resources/<path>` | 散装 | → `<path>` |
| `manager-{common,domain,dao,service}/src/main/java/...` | **jar** | → 走第 3 步 jar 替换,**不生成 class 路径** |
| workspace 任意子模块 `*/src/main/java/<pkg>/X.java` | 散装 | → `<pkg>/X.class` |
| workspace 任意子模块 `*/src/main/resources/<path>` | 散装 | → `<path>` |
| `.vue` / `.js` / `.html` / 纯静态 | 前端 | **跳过**,提示单独处理 |
| `pom.xml` | 依赖变更 | 不是 class,但**必须算出新增/替换的 jar**,见第 3.5 步 |
| `.md` / 文档 | 非产物 | **跳过** |

换算规则:取 `src/main/java/` 或 `src/main/resources/` **之后**的部分,前面的模块前缀(如 `tycj-web/tycj-web-mobile/`)全部丢弃。`.java` → `.class`,资源原样。

### 第 3 步:manager 非 web 子模块 → 整个 jar 替换

用户确认的做法是**整 jar 替换**,不拆 jar 内单个 class。

改动落在 `manager-common/-domain/-dao/-service` 时,只需给出对应 jar:

本地路径:
```
D:/JavaSpace/deployment/xajd/manager/manager-web/target/manager/WEB-INF/lib/manager-<x>-1.1.1.jar
```

服务器命令:
```bash
mv "manager-<x>-1.1.1.jar" "<SRV>/WEB-INF/lib/"
```

必须先 `ls` 确认 jar 实际文件名与版本号(版本号可能随 pom 变),不要照抄 `1.1.1`:
```bash
ls "D:/JavaSpace/deployment/xajd/manager/manager-web/target/manager/WEB-INF/lib/" | grep -i manager
```

同一子模块改了多个类只需替换一次 jar —— 输出时按 jar 去重,别按文件重复列。

### 第 3.5 步:pom 有改动 → 算出新增的第三方 jar

改动里有 `pom.xml` 时,**不能只看 pom diff 里写了什么**。升一个依赖版本可能拖进十几个传递依赖,漏一个线上就 `ClassNotFoundException`。

正确做法是**跑两遍依赖树再 diff**。用 `git show` 把旧 pom 提到临时目录,**不要用 `git stash`**(会动工作区,风险高):

```bash
# 1) 旧 pom 提到 /tmp(保持相对目录结构)
G="D:/JavaSpace/deployment/xajd/manager"
mkdir -p /tmp/oldpom && cd /tmp/oldpom
git -C "$G" archive HEAD -- pom.xml manager-*/pom.xml | tar -x

# 2) 分别列依赖(-o 离线,快)
cd "$G"        && mvn -o dependency:list -DincludeScope=runtime -DoutputFile=/tmp/new.txt -DappendOutput=true -q
cd /tmp/oldpom && mvn -o dependency:list -DincludeScope=runtime -DoutputFile=/tmp/old.txt -DappendOutput=true -q

# 3) diff
sort -u /tmp/old.txt > /tmp/o && sort -u /tmp/new.txt > /tmp/n
diff /tmp/o /tmp/n
```

- `>` 开头 = **新增/升版本的 jar,要上线**;`<` 开头 = 旧版本,**线上需挪走**。
- 算完必须逐个到 `WEB-INF/lib` 核验存在:
  ```bash
  for j in <jar1> <jar2>; do ls "$L/$j" >/dev/null 2>&1 && echo "OK $j" || echo "MISS $j"; done
  ```
- `scope=test` 的依赖(junit、`org.jacoco.agent-runtime` 等)**不进 lib**,MISS 属正常,不要当问题报。
- 实测例:manager 根 pom 把 `com.whaty.cbb:api-sdk` 从 3.0.0 升到 4.2.36,连带 **17 个新 jar**(api-domain、阿里云 tea 系列、chatbot20220408、bcpkix/bcprov-jdk15on、hutool-all、okhttp-sse 等)、**5 个旧 jar 需挪走**(api-sdk-3.0.0、api-domain-3.0.0、fastjson-1.2.28、httpmime-4.5.12、hutool-all-5.8.5)。只看 pom diff 只能看出 1 处版本号变化。
- workspace 若 pom 无改动,直接跳过这步。

### 第 4 步(关键):列出真实产物,含内部类

一个 `.java` 编译后常产生多个 class:`X.class`(主类)、`X$1.class`(匿名内部类)、`X$Inner.class`(具名内部类)等。**只搬 X.class 会导致线上 `NoClassDefFoundError`。**

对每个散装 .java,**必须实际 ls 确认产物**,不能凭猜:

```bash
ls "<classes根>/<pkg>/X.class" "<classes根>/<pkg>/X\$"*.class 2>/dev/null
```

批量确认多个类时:
```bash
C="<classes根>"
for p in <pkg1>/X1 <pkg2>/X2; do
  echo "=== $p ==="; ls "$C/$p.class" "$C/$p\$"*.class 2>/dev/null | sed "s|$C/||"
done
```

**注意 `$` 必须转义成 `\$`**,否则 bash 会当变量展开。

### 第 5 步:通配符只能用于 `$` 段(严重易错点)

**绝对不要用 `X*.class`。** 实测 workspace 975 个类中存在大量同目录前缀冲突:

```
com/whaty/tycj/domain          : CheckFlow*  会误搬 CheckFlowDetail / CheckFlowCopyPerson / CheckFlowGroup
com/whaty/tycj/domain          : ClassCourse* 会误搬 ClassCourseTimeTable
com/whaty/tycj/framework/cache : CacheKey*   会误搬 CacheKeys
com/whaty/tycj/domain/common   : WsGrid*     会误搬 WsGridColumn / WsGridColumnRule / WsGridMenu
```

误搬的是**未重新编译的旧 class**,会静默混进上线包。

正确形式(实测通过):

**有 `$` 内部类** —— 主类显式写 + 内部类用 `"X\$"*.class`:
```bash
mv "X.class" "X\$"*.class "<SRV>/WEB-INF/classes/<pkg>/"
```
实测:`CheckFlow.class` + `CheckFlow$1.class` + `CheckFlow$Inner.class` 精确搬走,`CheckFlowDetail.class` 未受影响。

**无 `$` 内部类** —— 只写主类,**不要加通配段**:
```bash
mv "X.class" "<SRV>/WEB-INF/classes/<pkg>/"
```
无内部类时若误加 `"X\$"*.class`,mv 会报 `cannot stat` 并返回码 1(主类仍搬成功,但报错会干扰批量执行)。所以第 4 步的 ls 结果决定加不加通配段。

### 第 6 步(必做):检测新增包目录,生成 mkdir

**线上目录并非都存在。** 新功能常引入全新包(如新增 `controller/ai/`),该目录在服务器上不存在,`mv` 会直接失败:`No such file or directory`。

**判定方法**:拿目标包目录去 git HEAD 查有没有文件 —— HEAD 里为空 = 这次才新建 = 线上没有:

```bash
# 对每个改动 class 的所在目录判定
for d in <模块>/src/main/java/<pkg1> <模块>/src/main/java/<pkg2>; do
  n=$(git -C "<git根>" ls-tree -r --name-only HEAD -- "$d" | wc -l)
  [ "$n" -eq 0 ] && echo "新目录 → 需 mkdir: $d" || echo "已存在($n): $d"
done
```

也可反向确认:列出 HEAD 里该父包下所有子目录,看目标子目录在不在:
```bash
git -C "<git根>" ls-tree -r --name-only HEAD -- "<模块>/src/main/java/<父包>/" \
  | awk -F/ '{print $(NF-1)}' | sort -u
```

判定为新目录的,在该模块**所有 mv 命令之前**生成:
```bash
mkdir -p "<SRV>/WEB-INF/classes/<pkg>"
```

**判定范围要覆盖三类目录,别只查 java 包:**

| 类型 | 查什么 |
|------|--------|
| java 包目录 | `<模块>/src/main/java/<pkg>` |
| 资源目录 | `<模块>/src/main/resources/<resDir>`(含 `templates/`、`spring/` 等) |
| 部署根下的业务资源目录 | 如 `templatefile/<业务>/<子目录>` —— 不在 target 里,但线上要有,同样得 mkdir + chown |

- 只为**确认是新增**的目录生成 mkdir,不要给已存在目录批量加(用户偏好纯 mv,不要无谓的 mkdir)。
- `mkdir -p` 幂等,多层新目录一条即可。
- 每个新目录都要在第 7 步配一条 `chown -R tomcat:tomcat`,**mkdir 与 chown 成对出现,清单同一份**。
- 实测例:某次上线 manager 新增 `com/whaty/products/controller/ai/`(HEAD 里 0 文件)和 `templatefile/entrustedContract/training/`,而 `analyse`、`clazz`、`spring` 都已存在 —— 只给这两个目录出 mkdir + chown。

### 第 7 步:生成部署命令

每个项目输出两段:

**(1) 本地绝对路径**(用于上传):
```
<classes根>/<相对路径>
```

**(2) 服务器命令**。占位符:`<SRV_MGR>`(manager 部署根,实测 `/data/webapps/manager-api`)、`<SRV_WS>`(workspace 部署根)、`<SRV_WEB>`(前端静态根)、`<UP>`(解包后的总目录,如 `/data/update/deploy-<日期>`)。**用户偏好:纯 `mv`,不要 `cp`;源只写文件名(无路径前缀);目标写完整包目录(末尾带 `/`)**:

```bash
# 0) 解包 + 放行解包出来的文件(否则 root 解出的文件搬进去 tomcat 读不到)
cd /data/update && unzip -oq deploy-<日期>.zip && cd deploy-<日期>
chmod -R 777 manager/ web/ workspace/

# 1) 仅对新增目录(第 6 步判定)
mkdir -p "<SRV>/WEB-INF/classes/<新pkg>"

# 2) class(无内部类)
cd <UP>/<项目>/WEB-INF/classes/<pkg>
mv "X.class" "<SRV>/WEB-INF/classes/<pkg>/"

# 3) class(有内部类)
mv "X.class" "X\$"*.class "<SRV>/WEB-INF/classes/<pkg>/"

# 4) 资源(无内部类问题)
mv "res.ftl" "<SRV>/WEB-INF/classes/<resDir>/"

# 5) manager 的 jar(自有子模块 + 新引入的第三方)
cd <UP>/manager/WEB-INF/lib
mv "manager-<x>-<ver>.jar" "<SRV_MGR>/WEB-INF/lib/"

# 6) web 全量
cd <UP>/web
mv index.html static "<SRV_WEB>/"

# 7) 把第 1 步新建的目录归还 tomcat(mkdir 出来的属主是 root)
chown -R tomcat:tomcat "<SRV>/WEB-INF/classes/<新pkg>"
```

- 源只写文件名,所以每组前面配一条 `cd <UP>/...` 定位;同目录多文件可合并到一条 mv。
- 服务器路径一律正斜杠。
- 用户会自己批量替换占位符。

**权限命令必须给全,这是漏了就会线上 500 的一环:**

| 时机 | 命令 | 为什么 |
|------|------|--------|
| mv **之前**(刚解包) | `chmod -R 777 manager/ web/ workspace/` | root 解包出的文件默认 `644`/`root:root`。`mv` **保留原属主与权限**,搬进部署目录后 tomcat 进程读不到 → class 加载失败 |
| mv **之后**(仅新建目录) | `chown -R tomcat:tomcat <新目录>` | `mkdir -p` 建出的目录属主是 root。目录不可写会影响 freemarker 缓存、上传落盘等 |

- `chown` 的目标是**第 1 步 mkdir 出来的每一个新目录**,一条一个,不要笼统 chown 整个部署根(会把无关文件属主一起改掉)。
- 新目录不止 `WEB-INF/classes` 下的包目录 —— **业务资源目录同样算**。实测例:
  ```bash
  chown -R tomcat:tomcat /data/webapps/manager-api/WEB-INF/classes/com/whaty/products/controller/ai
  chown -R tomcat:tomcat /data/webapps/manager-api/templatefile/entrustedContract/training
  ```
- **`chmod` 不接受 `user:group`。** `chmod -R tomcat:tomcat ...` 会报 `invalid mode`,静默什么都没改 —— 改属主只能用 `chown`。两条命令别写混。
- 已存在的目录不需要 chown:里面被 mv 覆盖的文件已经在第 0 步 `chmod 777` 过了,父目录属主本来就是 tomcat。

### 第 8 步:打成一个 zip(zip 内必须带一层顶层目录)

三个项目放进**同一个 zip**。**zip 内最外层必须是一个总目录 `deploy-<日期>/`**,三个项目在它下面:

```
deploy-20260918/          ← 顶层总目录,解包后自成一个文件夹
├── manager/WEB-INF/classes/com/...
├── manager/WEB-INF/lib/*.jar
├── workspace/WEB-INF/classes/...
└── web/{index.html,static/}
```

**不要让 manager/ workspace/ web/ 直接做 zip 顶层。** 那样在服务器 `unzip` 会把三个目录**散落到当前目录**,跟同目录下已有的历史上线文件、其它批次残留混在一起,分不清哪批是哪批,`chmod -R` 也容易误伤。带一层总目录后,一次上线 = 一个独立文件夹,处理完可整体删掉。

各项目内部保留完整目录层级(`WEB-INF/classes/com/...`),与部署根一一对应。

先复制到暂存目录再打包(复制 class 时同样要带内部类):
```bash
UPROOT="D:/JavaSpace/deployment/xajd/up"
NAME="deploy-<日期>"
STG="$UPROOT/$NAME"
rm -rf "$STG" && mkdir -p "$STG"
# class:主类 + 内部类
d="$STG/<项目>/WEB-INF/classes/<pkg>"; mkdir -p "$d"
cp "$C/<pkg>/X.class" "$d/"
for f in "$C/<pkg>/X\$"*.class; do [ -f "$f" ] && cp "$f" "$d/"; done
# web 全量
mkdir -p "$STG/web" && cp -r "<web>/dist/." "$STG/web/"
```

**打包工具必须用 JDK 的 `jar`**,且**在暂存目录的父目录执行**,打包对象是 `$NAME` 目录本身(这正是顶层目录的来源):
```bash
cd "$UPROOT" && "/d/JDK/jdk1.8.0/bin/jar" cfM "$NAME.zip" "$NAME"
```

- 这台机器**没有 `zip` / `7z`**(实测),只有 `tar` 和 JDK 的 `jar`。
- **不要用 PowerShell `Compress-Archive`**:它生成的条目分隔符是**反斜杠**(`manager\WEB-INF\classes\...`),Linux `unzip` 解不出目录树,会得到一堆带 `\` 的怪文件名。
- 打完必须验证首行是总目录、分隔符是正斜杠:
  ```bash
  "/d/JDK/jdk1.8.0/bin/jar" tf "$NAME.zip" | head -5
  # 期望:deploy-<日期>/  deploy-<日期>/manager/  ...
  ```
- `Compress-Archive` 若已生成过错误的 zip,`rm -f` 后重打。

服务器端解包(在上传目录执行,得到一个独立文件夹):
```bash
cd /data/update && unzip -oq deploy-<日期>.zip && cd deploy-<日期>
```

## 输出格式约定

按项目分段(manager / workspace / web 各一段)。每段顺序:

1. **构建结果**(命令 + 是否成功 + 耗时)
2. **改动源文件清单**(标注 M/A/D,以及归属:散装 class / jar / 资源 / 跳过)
3. **本地路径清单**(供上传)
4. **服务器命令块**,内部按下列顺序分组,各自独立代码块。**顺序即执行顺序,不能调**:
   - 解包 + `chmod -R 777`(第一组,必须在任何 mv 之前)
   - `mkdir -p`(仅新增目录)
   - `.class`(注明哪些带内部类)
   - 资源文件(`.ftl`/`.xml`/`.properties`/`.yaml`)
   - jar(仅 manager:自有子模块 jar + 新引入的第三方 jar)
   - web 全量(index.html + static)
   - `chown -R tomcat:tomcat`(最后一组,逐条对应上面每个 mkdir 的目录)
5. **需人工处理项**:删除的类、被替换的旧版本 jar、web 旧 static 挪走、Spring XML 改动需重启

## 常见问题

- **改动范围为空**:先跑 `git status --porcelain` 看未提交改动,再用 `log -8` 看最近提交时间,拿实际时间跟用户确认范围。不要反复试 `--since`。
- **改了 manager-domain 的类却在 classes 里找不到 .class**:正常,它在 `WEB-INF/lib/manager-domain-*.jar` 里,走 jar 替换。这是本 skill 最常见的误判点。
- **改了 .java 且 jar/classes 里都找不到产物**:该模块未重新构建,先 `mvn clean install -DskipTests`。
- **`mv` 报 `No such file or directory`**:目标包目录线上不存在。漏了第 6 步的新目录检测 —— 补 `mkdir -p`。每次上线都要跑一遍这个检测,不能凭印象跳过。
- **内部类遗漏或误搬**:根因都在第 4、5 步 —— 必须先 ls 看真实产物,再决定用不用 `"X\$"*.class`;永远不用 `X*.class`。
- **jar 版本号变化**:每次都 `ls WEB-INF/lib | grep -i manager` 现取文件名,别照抄文档里的 `1.1.1`。
- **升级第三方依赖后线上报 NoSuchMethodError**:旧版本 jar 还在 `WEB-INF/lib` 里,classpath 同类多版本。必须把旧版本 jar `mv` 到备份目录(**不要 `rm`**),新旧不能共存。
- **web 构建报 `Missing binding ... win32-x64-64`**:用了 Node 10+。切 8.17.0,必要时用绝对路径 `/f/nvm/v8.17.0/node build/build.js`。
- **zip 解不出目录**:用了 PowerShell `Compress-Archive`(反斜杠分隔符)。改用 JDK `jar cfM`,并用 `jar tf` 验证。
- **解包后三个项目散落在上传目录里**:打包时漏了顶层总目录。必须 `cd 父目录 && jar cfM <name>.zip <name>`,而不是进到 `<name>` 里打 `manager workspace web`。
- **上线后 500 / `NoClassDefFoundError`,但文件明明搬过去了**:权限问题。`mv` 保留 root 属主与 644 权限,tomcat 读不到。解包后先 `chmod -R 777` 再 mv;新建目录 mv 完补 `chown -R tomcat:tomcat`。
- **`chmod -R tomcat:tomcat` 报 invalid mode**:`chmod` 只收权限位,改属主要用 `chown`。这条命令等于没执行,别以为属主已经改好了。
