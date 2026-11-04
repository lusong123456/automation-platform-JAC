# 部署记录 · ARM64 离线部署

> 记录时间：2026-10-28
> 适用对象：乙方实施/开发人员
> 说明：本文是**实测记录**，与同项目的《部署手册.md》（通用说明）互补。平台源码与镜像包均位于本地 `automation-regression-platform/`。

---

## 一、目标环境与硬约束

| 项 | 实际情况 |
|---|---|
| 部署服务器 | 堡垒机资产 `ecs-合同智能审核系统-devel-0001`，内网 IP **10.120.11.70** |
| CPU 架构 | **linux/arm64/v8**（与本地 x86_64 不兼容，这是最初部署失败的直接原因） |
| 堡垒机 | JumpServer，管理地址 **192.168.6.168:2222**（只是登录入口，不是应用地址） |
| 服务器外网 | **完全断外网**，无法访问 Docker Hub、无法访问 GitHub |
| Docker Compose | **v1**，命令为 `docker-compose`（带横杠）；`docker compose` 会报 unknown command |
| 文件传输 | 只有 SFTP 图形化界面通道，无 git、无独立 SSH 直连 |
| 端口转发 | JumpServer 管理员已全局禁用本地端口转发（Koko 的 `ENABLE_LOCAL_PORT_FORWARD`），SSH 隧道不可用 |

由此得出两条部署原则：

1. **镜像必须预构建**：服务器上不能执行 `docker compose build`，构建会卡在拉基础镜像和装依赖上。
2. **全程离线**：基础镜像（`node:20-alpine`、`python:3.11-slim`、`nginx:1.27-alpine`）和 `allure-2.29.0.tgz` 都要随包带过去。

---

## 二、部署拓扑

```
浏览器 → http://10.120.11.70:8080/
          └─ nginx 容器（deploy-nginx-1，宿主 8080 → 容器 80）
               ├─ /api/      → web 容器（deploy-web-1:8000，FastAPI + pytest 执行器）
               ├─ /reports/  → 只读挂载 ./data/reports
               └─ /          → web 容器（前端静态资源，FastAPI StaticFiles 托管）
```

| 容器 | 作用 | 端口 |
|---|---|---|
| `deploy-web-1` | FastAPI 后端 + pytest 子进程执行 + Allure 生成 | 容器内 8000（不对外） |
| `deploy-nginx-1` | 唯一入口，反代 API、托管报告 | 宿主 8080 |

数据全部落在 `deploy/data/`（web 读写、nginx 只读）：`app.db`、`case_manifest.json`、`runs/`、`reports/`、`test-files/`、`test-contracts/`、`standard-results/`。

---

## 三、离线镜像包

| 项 | 内容 |
|---|---|
| 文件名 | `images-arm64.tar` |
| 大小 | 234,076,672 字节（223.2 MB） |
| 内含镜像 | `deploy-web:arm64`、`nginx:1.27-alpine`（均为 arm64） |
| 本地位置 | `automation-regression-platform/deploy/images-arm64.tar` |
| 生成时间 | 2026-10-28 09:25 |

镜像由本地 Docker Desktop 通过 QEMU 交叉构建（`--platform linux/arm64`），`deploy-web` 阶段包含：前端 `npm run build` 产物、Python 依赖、pytest 用例、Allure 命令行 + JRE。

> 重新构建镜像包（仅当代码变更、需要升级时）：
>
> ```powershell
> cd automation-regression-platform\deploy
> docker pull --platform linux/arm64 nginx:1.27-alpine
> docker build --platform linux/arm64 -f Dockerfile.web -t deploy-web:arm64 ..
> docker save deploy-web:arm64 nginx:1.27-alpine -o images-arm64.tar
> ```
>
> 构建上下文是仓库根目录（`..`），构建耗时较长（QEMU 模拟 npm/pip 安装）。

---

## 四、实测部署步骤

### 步骤 1：上传文件（SFTP）

用 SFTP 图形界面把以下两个东西传到服务器 **`/tmp`**（不要直接传 `/opt`）：

- `images-arm64.tar`
- 整个 `automation-regression-platform` 目录（至少包含 `deploy/`、`server/`、`tests/`、`pytest.ini`）

上传前必须排除：`web/node_modules/`、`web/dist/`、`.venv/`、`deploy/data/reports/`、`deploy/data/runs/`。清理后整个项目约 43.5 MB（不含镜像包）。

> 注意：SFTP 工具的「Home」按钮指向的是连接账号的家目录（如 `/root`、`/home/xxx`），而 SSH 登录后的默认目录可能是 `/`，两者不一致，容易「传上去了但 SSH 里看不见」。

### 步骤 2：落位到 /opt

```bash
# SSH 登录服务器后
ls -la /tmp | grep -i automation

rm -rf /opt/automation-regression-platform
mv /tmp/automation-regression-platform /opt/
chmod 600 /opt/automation-regression-platform/deploy/.env   # 避免权限告警
```

最终目录必须是 **`/opt/automation-regression-platform/`**，且 `deploy/` 的上一级结构不变——`docker-compose.yml` 的构建上下文指向项目根目录，层级错位会直接构建失败。

### 步骤 3：加载 ARM64 镜像

```bash
cd /opt/automation-regression-platform/deploy
docker load -i /tmp/images-arm64.tar
docker images | grep -E "deploy-web|nginx"
```

确认架构正确（输出应为 `arm64`）：

```bash
docker inspect deploy-web:arm64 --format '{{.Architecture}}/{{.Os}}'
```

### 步骤 4：改用镜像启动（不再 build）

离线部署的关键改动：把 `docker-compose.yml` 的 `web` 服务从 `build` 改为指定 `image`。实测使用的等价配置如下（`image: deploy-web:arm64`，nginx 用本地已加载的 `nginx:1.27-alpine`）：

```yaml
services:
  web:
    image: deploy-web:arm64
    env_file: .env
    environment:
      DATA_DIR: /app/data
      BASE_URL: ${BASE_URL}
    volumes:
      - ./data:/app/data
    expose:
      - "8000"
    restart: unless-stopped

  nginx:
    image: nginx:1.27-alpine
    ports:
      - "8080:80"
    volumes:
      - ./nginx.conf:/etc/nginx/conf.d/default.conf:ro
      - ./data:/data:ro
    depends_on:
      - web
    restart: unless-stopped
```

### 步骤 5：启动

```bash
cd /opt/automation-regression-platform/deploy
docker-compose up -d
docker-compose ps
docker-compose logs -f web      # 看到 "Uvicorn running on http://0.0.0.0:8000" 即成功
```

### 步骤 6：验收自检

```bash
curl -s http://127.0.0.1:8080/api/health                  # {"status":"ok"}
curl -s -X POST http://127.0.0.1:8080/api/cases/refresh    # 返回按模块分组的用例清单
```

浏览器打开 **http://10.120.11.70:8080/**，能看到「用例列表」即为部署成功。

---

## 五、访问方式（无甲方内网时）

| 场景 | 可行做法 |
|---|---|
| 在甲方内网 | 直接访问 `http://10.120.11.70:8080/` |
| 不在内网但能登录堡垒机 | 在 JumpServer **Web 终端**里执行 `curl` 验证接口，页面验收交给甲方内网同事 |
| SSH 本地端口转发 | **不可用**：堡垒机已禁用 TCP 转发，`ssh -L 18080:10.120.11.70:8080` 会失败 |
| 公网访问 | 需甲方运维走审批：安全组放行 8080，或用现有开放端口的 nginx 反代 |

端口利用优先级（当时评估结论）：安全组加开 8080（零改动，首选）> nginx 子路径反代（需改前端 base 与后端 root_path，工作量大）> 直接占用现有端口（仅当该端口服务可停用时）。

---

## 六、踩坑记录

| # | 现象 | 根因 | 处理 |
|---|---|---|---|
| 1 | 容器启动即报 `exec format error` | 本地 x86_64 构建的镜像跑在 arm64 服务器上 | 用 `--platform linux/arm64` 交叉构建，或导出 arm64 镜像包 |
| 2 | `docker compose build` 卡住 / `connection refused` | 服务器断外网，拉不到 `node:20-alpine`、`python:3.11-slim`、`nginx:1.27-alpine` | 改离线镜像包方案，全程 `docker load` |
| 3 | `docker compose` 报 unknown command | 服务器是 Compose **v1** | 全部命令改用 `docker-compose` |
| 4 | SFTP 显示上传成功，SSH 里找不到 | SFTP 的 Home 是账号家目录，SSH 默认落在 `/` | 用 `find / -maxdepth 4 -name "automation-regression-platform"` 定位后 `mv` 到 `/opt` |
| 5 | 构建极慢 / 传输中断 | 把 `web/node_modules`、`.venv`、历史报告一起传了 | 上传前排除，项目从 380 MB 降到 43.5 MB |
| 6 | 用例全部 401 | 客户端凭证与目标环境不匹配；新系统 `/ai-contract/platform/auth/token` 返回 500 | 旧系统用 `client_credentials`（appId/appSecret/employeeId）；新系统改 `AUTH_MODE=password` |
| 7 | 大文件上传稳定 504 | 被测系统网关约 30s 硬超时，客户端调大超时也无效 | 测试侧已放宽到 180s + 重试一次；仍 504 需被测方调整网关 |
| 8 | 前端改了不生效 | 浏览器缓存旧 JS | 静态资源已加 `Cache-Control: no-cache`，另可强制刷新 |
| 9 | AI 语义仲裁全部失败，报 `503 model_not_found` | 网关注册的模型名是 `glm-5`，`.env` 里写的是 `glm-5.2` | `LLM_MODEL=glm-5`；改完必须重建容器（见第七节） |

---

## 七、配置修改与重启（重要）

`.env` 是通过 `env_file` 在**容器启动时**注入的，改完必须重建容器，`restart` 不生效：

```bash
cd /opt/automation-regression-platform/deploy
cp .env .env.bak-$(date +%Y%m%d%H%M)
# 例：改模型名
sed -i 's/^LLM_MODEL=.*/LLM_MODEL=glm-5/' .env

docker-compose up -d --force-recreate web
docker exec deploy-web-1 printenv LLM_MODEL     # 确认新值已注入
```

验证大模型连通（容器内，带真实 prompt）：

```bash
docker exec deploy-web-1 python -c "
import os, json, urllib.request
req = urllib.request.Request(
    os.environ['LLM_BASE_URL'] + '/chat/completions',
    data=json.dumps({'model': os.environ['LLM_MODEL'],
                     'messages': [{'role': 'user', 'content': '只回复两个字：收到'}],
                     'max_tokens': 64}).encode(),
    headers={'Content-Type': 'application/json',
             'Authorization': 'Bearer ' + os.environ['LLM_API_KEY']})
print(urllib.request.urlopen(req, timeout=30).read().decode()[:300])
"
```

> 不要用 `curl http://192.168.191.144:11000/glm5/v1/models` 判断网关健康：该 GET 请求会被 nginx 回落到前端首页 HTML，看起来「有响应」但毫无意义。要验证就 POST 一次 `chat/completions`。

---

## 八、离线升级流程

代码变更后，服务器上没有构建能力，升级等于「换镜像包」：

1. 本地重新构建并导出（见第三节）
2. SFTP 上传新的 `images-arm64.tar` 到 `/tmp`
3. 服务器执行：

```bash
cd /opt/automation-regression-platform/deploy
docker-compose down                       # 只停容器，./data 数据保留
docker load -i /tmp/images-arm64.tar
docker-compose up -d
curl -s http://127.0.0.1:8080/api/health
```

4. 页面点【刷新清单】（等价于 `POST /api/cases/refresh`）让新用例生效

> 只改 `.env`（如模型名、被测地址、凭证）时不需要换镜像，走第七节的 `--force-recreate` 即可。

---

## 九、常用运维命令

```bash
cd /opt/automation-regression-platform/deploy

docker-compose ps                          # 容器状态
docker-compose logs -f web                 # 实时日志
docker-compose logs --tail=100 web         # 最近 100 行
docker exec deploy-web-1 cat /app/data/case_manifest.json        # 用例清单缓存
docker exec deploy-web-1 tail -n 100 /app/data/runs/<run_id>/pytest.log
docker-compose down                        # 停止（数据保留）
docker-compose down -v                     # 停止并删卷（慎用）
df -h                                      # 磁盘（报告会累积，REPORT_KEEP=20 自动清理）
```

数据备份只需备份 **`deploy/data/`**（`app.db`、`test-contracts/`、`standard-results/`、`reports/`）。

---

## 十、当前配置快照（脱敏）

```ini
BASE_URL=http://192.168.191.144:11000
APP_ID=htglxt
APP_SECRET=<已存于服务器 .env>
APP_EMPLOYEE_ID=JK20260034

HTTP_TIMEOUT=60
RUN_TIMEOUT=1800
REPORT_KEEP=20

AI_JUDGE_ENABLED=true
AI_JUDGE_SCOPE=all
LLM_BASE_URL=http://192.168.191.144:11000/glm5/v1
LLM_API_KEY=<已存于服务器 .env>
LLM_MODEL=glm-5.2          # ← 应为 glm-5（见第六节第 9 条）
LLM_TIMEOUT=30
SUGGESTION_OVERLAP_THRESHOLD=0.3
```

> `.env` 不入库，仓库只保留 `.env.example`。真实密钥只存在于服务器 `deploy/.env` 与本地 `deploy/.env`，两者需保持一致。

---

## 十一、部署后的已知状态

- 平台本身健康：`/api/health` 返回 ok，`/api/cases` 能返回用例清单
- 旧内网环境（`192.168.191.144:11000`）的客户端凭证可用
- 新环境（`http://39.145.124.6:15000`）旧凭证失效，`/ai-contract/platform/auth/token` 返回 500，需要新的 appId/appSecret/employeeId 或改用 `AUTH_MODE=password`
- AI 仲裁：网关模型名应为 `glm-5`，实测 `glm-5` 可正常返回合法 JSON 仲裁结论（单次 1~2 秒）
