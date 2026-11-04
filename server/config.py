"""平台配置：全部来自环境变量注入（容器经 .env 注入，本地裸跑用 $env: 注入）。

不读取文件型配置文件，避免容器注入的环境变量被静默覆盖。
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 仓库根目录（server 的上一级）
REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    # —— 被测环境 ——
    base_url: str = ""              # 被测合同审核测试环境根地址（必填，执行前校验）
    http_timeout: int = 60          # 单个接口请求超时（秒），由用例侧读取；上传文件等慢接口需足够时间

    # —— 平台运行 ——
    run_timeout: int = 1800         # 单次执行总超时（秒）
    report_keep: int = 20           # 报告保留份数
    operator_default: str = ""      # 前端默认操作人
    log_level: str = "info"
    allure_bin: str = "allure"      # allure 命令（镜像内已内置）

    # —— AI 语义仲裁（大模型兜底）——
    ai_judge_enabled: bool = False  # 总开关；关闭时审核正确性用例行为与改造前一致
    llm_base_url: str = ""          # OpenAI 兼容接口根地址（含 /v1）
    llm_api_key: str = ""           # Key，内网无鉴权时允许为空
    llm_model: str = ""             # 模型名
    llm_timeout: int = 30           # 单次调用超时（秒），超时即判失败，不降级
    ai_judge_scope: str = "inconsistent"  # inconsistent（只复核关键词判不一致的）/ all（每对都调）
    suggestion_overlap_threshold: float = 0.3  # 建议语义比对的 Jaccard 重叠率阈值；≥此值判一致

    # —— 路径 ——
    data_dir: Path = REPO_ROOT / "data"
    db_path: Path | None = None     # 默认 data_dir/app.db

    @property
    def database(self) -> Path:
        return self.db_path or (self.data_dir / "app.db")

    @property
    def tests_dir(self) -> Path:
        return REPO_ROOT / "tests"

    @property
    def static_dir(self) -> Path:
        return REPO_ROOT / "static"

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def manifest_cache(self) -> Path:
        return self.data_dir / "case_manifest.json"


settings = Settings()
