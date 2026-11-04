"""Allure 报告生成与保留策略。"""

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from server.config import settings

logger = logging.getLogger(__name__)


def _localize_report(out_dir: Path) -> None:
    """把报告界面默认语言设为中文。

    Allure 以 index.html 的 <html lang> 作为默认语言（zh 语言包已内置），
    默认是 en，改成 zh 后导航/组件标题等界面文案即为中文。
    """
    index = out_dir / "index.html"
    try:
        html = index.read_text(encoding="utf-8")
    except OSError:
        return
    html = re.sub(r'(<html\b[^>]*\blang=")en(")', r"\1zh\2", html, count=1)
    html = html.replace("<title>Allure Report</title>", "<title>自动化接口回归测试报告</title>")
    try:
        index.write_text(html, encoding="utf-8")
    except OSError:
        pass


def _allure_argv(args: list[str]) -> list[str]:
    """解析 allure 命令的真实路径。

    Windows 下 npm 全局安装产出的是 allure.cmd（批处理），subprocess 无法直接执行，
    需经 cmd.exe 调用；Linux（容器内）是普通可执行文件，直接执行。
    """
    exe = shutil.which(settings.allure_bin) or settings.allure_bin
    if os.name == "nt" and exe.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", exe, *args]
    return [exe, *args]


def generate(run_id: str, allure_results: Path) -> tuple[bool, str | None]:
    """生成静态报告；失败不抛异常（报告是产物，不影响用例结果判定）。"""
    out_dir = settings.reports_dir / run_id
    try:
        proc = subprocess.run(
            _allure_argv(["generate", str(allure_results), "-o", str(out_dir), "--clean"]),
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
        )
    except FileNotFoundError:
        logger.warning("未找到 allure 命令（%s），跳过报告生成", settings.allure_bin)
        return False, None
    except subprocess.TimeoutExpired:
        logger.warning("allure generate 超时，run_id=%s", run_id)
        return False, None

    if proc.returncode != 0 or not (out_dir / "index.html").exists():
        logger.warning("allure generate 失败，run_id=%s，输出：%s", run_id, (proc.stderr or "")[:500])
        return False, None
    _localize_report(out_dir)
    return True, f"reports/{run_id}/index.html"


def prune(keep: int) -> list[str]:
    """按目录名（含时间戳前缀）保留最近 keep 份报告，返回被清理的 run_id 列表。"""
    reports_dir = settings.reports_dir
    if keep <= 0 or not reports_dir.exists():
        return []
    dirs = sorted((d for d in reports_dir.iterdir() if d.is_dir()), key=lambda p: p.name)
    removed = [d.name for d in dirs[:-keep]] if len(dirs) > keep else []
    for run_id in removed:
        shutil.rmtree(reports_dir / run_id, ignore_errors=True)
        shutil.rmtree(settings.runs_dir / run_id, ignore_errors=True)
    if removed:
        logger.info("按保留策略清理报告：%s", ", ".join(removed))
    return removed
