# 江淮智能合同审核 · 自动化接口回归测试平台

一个面向甲方测试人员的"勾选执行器"：**展示用例清单 → 勾选触发执行 → 实时展示进度 → 生成并托管 Allure 报告**。
只做接口回归，不含 UI/E2E、不依赖 Swagger、不做登录鉴权。

- 后端：FastAPI + SQLite + pytest 子进程执行（`server/`，端口 8000）
- 前端：Vue3 + Vite + Element Plus + vue-router（hash 路由）（`web/`）
- 部署：web + nginx 双容器，nginx 为唯一入口（宿主 8080）

> 详细设计见上级目录《自动化接口回归测试平台开发设计.md》（同仓库 `../自动化接口回归测试平台开发设计.md`）。
> 本文档只描述"怎么跑起来、怎么部署、怎么加用例"。

## 目录结构

```
automation-regression-platform/
├── server/                     后端 + 执行器（Python 3.11 / FastAPI）
│   ├── main.py                 应用装配、启动恢复、静态资源托管
│   ├── config.py               环境变量配置
│   ├── db.py                   SQLite 连接、DDL、WAL 初始化
│   ├── schemas.py              API 请求模型
│   ├── api/                    health / cases / runs 三组接口
│   ├── core/                   collector（收集）/ runner（执行）/ report（报告）/ state（锁）
│   └── requirements.txt
├── tests/                      乙方维护的接口用例（平台不修改）
│   ├── conftest.py             清单钩子、结果钩子、全局 fixture
│   └── api/<模块>/             按模块组织（目录名默认即模块名）
├── web/                        前端（Vue3 + Vite + Element Plus）
│   ├── src/
│   │   ├── views/CaseList.vue      用例列表页（#/cases）
│   │   ├── views/RunHistory.vue    执行记录页（#/runs）
│   │   ├── components/             CaseGroup.vue、RunProgress.vue
│   │   ├── api/index.ts            API 封装（统一错误处理）
│   │   ├── composables/useRun.ts   执行轮询（2 秒）
│   │   └── router/index.ts         hash 路由
│   └── package.json、vite.config.ts
├── deploy/
│   ├── Dockerfile.web          多阶段构建：node 构建前端 → python 运行镜像
│   ├── docker-compose.yml      web + nginx 两服务
│   ├── nginx.conf              入口反代与 /reports 静态托管
│   └── .env.example            环境变量样例（真实 .env 不入库）
├── pytest.ini                  注册 regcase marker 与全局 addopts
└── README.md
```

运行时数据统一落在 `data/`（web 读写、nginx 只读）：

```
data/
├── app.db                     SQLite 执行历史
├── case_manifest.json         用例清单缓存
├── runs/{run_id}/             单次执行工作目录（manifest 子集、results.jsonl、pytest.log、allure-results）
└── reports/{run_id}/          allure generate 产出的静态报告（nginx 托管）
```

## 一、本地裸跑（不用 Docker）

前置：Python 3.11+、Node.js 18+。要能看到报告，本机需额外安装 [Allure 命令行](https://github.com/allure-framework/allure2/releases) 与 JRE。

### 1. 后端（PowerShell）

```powershell
cd automation-regression-platform

# 创建并激活虚拟环境
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 安装依赖
pip install -r server\requirements.txt

# 注入环境变量（本地裸跑用 $env: 注入，不读配置文件）
$env:BASE_URL        = "<被测环境后端地址>"          # 必填，如 http://192.168.191.144:11000/prod-api
$env:APP_ID          = "<appId>"
$env:APP_SECRET      = "<appSecret>"
$env:APP_EMPLOYEE_ID = "<employeeId>"
$env:DATA_DIR        = "$PWD\data"

# 启动后端（首次启动会自动收集用例清单）
uvicorn server.main:app --reload --port 8000
```

自检：`curl http://localhost:8000/api/health` 应返回 `{"status":"ok"}`；`curl http://localhost:8000/api/cases` 应返回按模块分组的用例清单。

### 2. 前端（另开一个 PowerShell 窗口）

```powershell
cd automation-regression-platform\web
npm install
npm run dev
```

打开 `http://localhost:5173`；`vite.config.ts` 已把 `/api` 与 `/reports` 代理到 `http://localhost:8000`。

若不跑 dev server，也可 `npm run build` 后把 `web\dist` 的内容复制到仓库根 `static\`，后端启动时会自动托管前端页面（直接访问 `http://localhost:8000/`）。

## 二、Windows 本地 Docker 验证

用于开发阶段在本机自测整套链路（无需甲方环境，可配 httpbin 假服务）。

前置条件：

| 项 | 要求 |
|---|---|
| Docker | Docker Desktop（WSL2），`docker version` 与 `docker compose version` 均可用 |
| 部署路径 | **放在纯英文路径**（如 `C:\work\jac-regression`），避免中文路径导致的构建/挂载异常 |
| 端口 | 宿主 8080 空闲（`Get-NetTCPConnection -LocalPort 8080 -ErrorAction SilentlyContinue` 无输出即空闲） |
| 资源 | 建议给 Docker Desktop 分配 2 核 / 2GB 以上（镜像含 JRE） |

步骤（PowerShell）：

```powershell
# 0）可选：真实接口未就绪时，用 httpbin 充当被测系统
docker run -d --name mock-api -p 9000:80 kennethreitz/httpbin

cd automation-regression-platform\deploy
Copy-Item .env.example .env
# 编辑 .env，至少填写 BASE_URL：
#   假服务   → BASE_URL=http://host.docker.internal:9000
#   甲方环境 → BASE_URL=http://<被测环境IP>:<端口>

docker compose up -d --build
docker compose logs -f web          # 观察启动与用例收集日志
# 浏览器打开 http://localhost:8080
```

自测验收要点：

| # | 操作 | 预期结果 |
|---|---|---|
| 1 | `curl http://localhost:8080/api/health` | 返回 ok |
| 2 | 打开 `http://localhost:8080` | 按模块分组展示用例，中文名可读 |
| 3 | 勾选 2 条 → 点【执行】 | 2 秒内进度条推进，显示"正在执行 X/Y：用例名" |
| 4 | 执行结束 | 通过数/失败数正确，失败用例带中文断言摘要 |
| 5 | 点【查看报告】 | Allure 报告打开（新标签页），失败用例可展开报文 |
| 6 | 执行中再点【执行】 | 提示"已有任务在执行中"，不产生并发执行 |
| 7 | `.env` 设 `RUN_TIMEOUT=5` 后跑 sleep 用例 | 状态为 error，提示"执行超时，已中断" |
| 8 | `.env` 设 `REPORT_KEEP=2` 后连跑 3 次 | 最旧报告被清理，记录仍在但报告链接置灰 |
| 9 | 执行中 `docker compose restart web` | 重启后该记录状态为 error，"服务重启导致执行中断" |
| 10 | 打开执行记录页 | 历史列表、耗时、操作人、报告链接均正确 |

> 第 7、8 条改完 `.env` 后需 `docker compose up -d web` 重建容器使配置生效。

常用排查命令：

```powershell
docker compose ps
docker compose logs -f web
docker compose exec web cat /app/data/case_manifest.json
docker compose exec web ls -l /app/data/runs
docker compose exec web tail -n 100 /app/data/runs/<run_id>/pytest.log
docker compose exec web ls -l /app/data/reports
docker compose down                      # 停止容器（./data 数据保留）
# 清空历史数据：手动删除 deploy\data 目录后再启动
```

## 三、部署（甲方测试服务器）

```bash
# 首次部署
cd deploy
cp .env.example .env          # 填写 BASE_URL、测试账号
docker compose up -d --build

# 验收自检
curl http://localhost:8080/api/health      # 返回 ok
# 浏览器打开 http://<服务器IP>:8080/       # 看到用例清单

# 用例更新 / 平台升级：重建 web 镜像，清单自动刷新
docker compose up -d --build web
```

说明：

- `.env` 统一 UTF-8 + LF 保存，变量值不要留尾随空格（否则容器内解析出错）
- `deploy/.env` 不入库，仓库只保留 `.env.example`
- 单镜像策略：用例代码随镜像发布，平台升级只需重建 `web` 镜像
- 端口冲突时把 `docker-compose.yml` 中 `8080:80` 改成 `8081:80`

## 四、新增用例的约定（乙方遵守）

平台只认以下约定，约定之外无需平台感知：

| 约定 | 内容 |
|---|---|
| 目录组织 | `tests/api/<模块>/test_<流程>.py`，模块名默认取目录名，可用 marker 覆盖 |
| 用例粒度 | 一条用例 = 一条核心流程（约 20 条），只覆盖接口层，不写 UI |
| 元数据 | `@pytest.mark.regcase(id=..., module=..., name="中文标题")`，`name` 同时用于 Allure 标题 |
| 命名 | 函数名英文、docstring 首行中文描述流程 |
| 数据 | 每条用例自造数据、执行完清理，用例之间不得有执行顺序依赖 |
| 断言 | 断言消息写中文，便于甲方在报告中直接读懂失败原因 |
| 报文留证 | 请求/响应报文通过 `allure.attach` 附加到用例，失败时可在报告中展开 |
| 登录 | session 级 fixture 用客户端凭证（appId/appSecret/employeeId）换 Token，按 TTL 提前续期，凭证取自 .env / 环境变量 |
| 环境信息 | `conftest.py` 向 allure-results 写 `environment.properties`（被测地址、执行人、版本） |
| 禁止 | 用例不得依赖平台内部实现、不得写文件到平台数据目录 |

新增/修改用例后：本地重跑时重启后端即可（启动会重新收集）；容器内执行 `docker compose up -d --build web`，或在页面点【刷新清单】触发 `POST /api/cases/refresh`。
