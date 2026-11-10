"""平台配置：环境变量为基线，data/settings.json 为覆盖层（由系统设置页维护）。"""

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

    # —— 被测环境凭证 ——
    auth_mode: str = "client_credentials"  # password=账号密码登录；client_credentials=客户端凭证
    test_username: str = ""
    test_password: str = ""
    app_id: str = ""
    app_secret: str = ""
    app_employee_id: str = ""

    # —— 登录鉴权（平台自身，单账号）——
    auth_enabled: bool = True       # 总开关；未配置密码时即使打开也不生效
    auth_username: str = "admin"
    auth_password: str = "test123456"
    auth_secret: str = ""           # 签名密钥，留空则由密码派生
    auth_session_ttl: int = 28800   # 会话有效期（秒），默认 8 小时

    # —— AI 语义仲裁（大模型兜底）——
    ai_judge_enabled: bool = False  # 总开关；关闭时审核正确性用例行为与改造前一致
    llm_base_url: str = ""          # OpenAI 兼容接口根地址（含 /v1）
    llm_api_key: str = ""           # Key，内网无鉴权时允许为空
    llm_model: str = ""             # 模型名
    llm_timeout: int = 30           # 单次调用超时（秒），超时即判失败，不降级
    ai_judge_scope: str = "inconsistent"  # inconsistent（只复核关键词判不一致的）/ all（每对都调）
    suggestion_overlap_threshold: float = 0.3  # 建议语义比对的 Jaccard 重叠率阈值；≥此值判一致

    # —— 审核正确性匹配 ——
    acc_group_match: bool = True    # 按规则基 ID 分组配对；false 回退 ruleId 精确匹配
    acc_pair_threshold: float = 0.3  # 组内命中原文 Jaccard 配对阈值；取值范围 (0, 1]

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


def _apply_settings_overrides() -> None:
    # 延迟导入，避免 config 与 settings_store 循环依赖
    from server.core.settings_store import apply

    apply(settings)


_apply_settings_overrides()
