"""
http_client.py — iFinD QuantAPI HTTP 客户端 (REST 端点)

iFinD 数据接口有两套调用方式:
  1. iFinDPy SDK         —— full_data_pull.py / market_env_fetcher.py 在用
  2. HTTP REST 端点      —— 本模块封装, 用于 SDK 未覆盖的报表 (如 report_query 公告)

认证流程:
    refresh_token (来自超级命令-账号详情)
        ─POST /api/v1/get_access_token─►  access_token (7 天 TTL)
        ─POST /api/v1/{report_query|...}─►  业务数据 (JSON)

    refresh_token 获取方式 (二选一):
      A. iFinD 超级命令客户端 → 工具 → refresh_token 查询
      B. 网页版超级命令 → 账号详情:
         https://quantapi.10jqka.com.cn/gwstatic/static/ds_web/super-command-web/index.html#/AccountDetails

    取到的 refresh_token 写入 src/data_sources/ifind/.env 的 IFIND_REFRESH_TOKEN= 行.

token 缓存:
    ~/.ifind_token_cache.json    (用户主目录, 跨进程共享)
    {"access_token": "...", "obtained_at": "ISO8601", "expires_at": "ISO8601"}

错误码:
    -1302 = access_token 过期 → 自动重新获取并重试一次
    其它  = 抛 IFindHttpError 让调用方处理
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

# ============================================================================
# .env 加载 (idempotent, 与 market_env_fetcher 同一套逻辑)
# ============================================================================
_ENV_LOADED = False


def _load_env_once() -> None:
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.exists():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    _ENV_LOADED = True


# ============================================================================
# 配置常量
# ============================================================================
DEFAULT_API_HOST = "https://quantapi.51ifind.com"
TOKEN_PATH = "/api/v1/get_access_token"           # 取当前有效 access_token (7d 内复用)
TOKEN_UPDATE_PATH = "/api/v1/update_access_token" # 强制下发新 token, 旧 token 立即失效
ACCESS_TOKEN_TTL_DAYS = 7
# 提前 1 天刷新, 防止边界过期
ACCESS_TOKEN_REFRESH_THRESHOLD_DAYS = 1

TOKEN_CACHE_FILE = Path.home() / ".ifind_token_cache.json"

DEFAULT_TIMEOUT_SEC = 30
DEFAULT_RETRY = 1  # token 过期时的自动重试次数


# ============================================================================
# 异常
# ============================================================================
class IFindHttpError(RuntimeError):
    """iFinD HTTP 接口调用错误"""

    def __init__(self, errorcode: int, errmsg: str, payload: Optional[dict] = None):
        self.errorcode = errorcode
        self.errmsg = errmsg
        self.payload = payload or {}
        super().__init__(f"iFinD HTTP error [{errorcode}]: {errmsg}")


class IFindAuthError(IFindHttpError):
    """认证失败 (refresh_token 缺失/失效)"""


# ============================================================================
# Token 缓存
# ============================================================================
@dataclass
class TokenCache:
    access_token: str
    obtained_at: datetime
    expires_at: datetime

    @property
    def is_valid(self) -> bool:
        # 提前 ACCESS_TOKEN_REFRESH_THRESHOLD_DAYS 视为失效
        threshold = self.expires_at - timedelta(days=ACCESS_TOKEN_REFRESH_THRESHOLD_DAYS)
        return datetime.utcnow() < threshold

    def to_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "obtained_at": self.obtained_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TokenCache":
        return cls(
            access_token=d["access_token"],
            obtained_at=datetime.fromisoformat(d["obtained_at"]),
            expires_at=datetime.fromisoformat(d["expires_at"]),
        )


def _load_token_cache() -> Optional[TokenCache]:
    if not TOKEN_CACHE_FILE.exists():
        return None
    try:
        data = json.loads(TOKEN_CACHE_FILE.read_text(encoding="utf-8"))
        return TokenCache.from_dict(data)
    except Exception:
        return None


def _save_token_cache(token: TokenCache) -> None:
    try:
        TOKEN_CACHE_FILE.write_text(
            json.dumps(token.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        # 缓存失败不致命, 下次重新获取
        pass


def _invalidate_token_cache() -> None:
    if TOKEN_CACHE_FILE.exists():
        try:
            TOKEN_CACHE_FILE.unlink()
        except Exception:
            pass


# ============================================================================
# 核心客户端
# ============================================================================
class IFindHttpClient:
    """
    iFinD HTTP REST 客户端 (轻量).

    用法:
        client = IFindHttpClient()
        resp = client.post("/api/v1/report_query", {...})
    """

    def __init__(
        self,
        refresh_token: Optional[str] = None,
        api_host: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ):
        _load_env_once()
        self.refresh_token = refresh_token or os.environ.get("IFIND_REFRESH_TOKEN", "").strip()
        self.api_host = (
            api_host
            or os.environ.get("IFIND_API_HOST", "").strip()
            or DEFAULT_API_HOST
        )
        self.api_host = self.api_host.rstrip("/")
        self.timeout = timeout

        if not self.refresh_token:
            raise IFindAuthError(
                errorcode=-9001,
                errmsg=(
                    "IFIND_REFRESH_TOKEN 未配置. 请在 src/data_sources/ifind/.env 填入: \n"
                    "  IFIND_REFRESH_TOKEN=<从 iFinD 超级命令客户端 工具→refresh_token查询 获取>\n"
                    "  或 https://quantapi.10jqka.com.cn/gwstatic/static/ds_web/"
                    "super-command-web/index.html#/AccountDetails"
                ),
            )

    # ------------------------------------------------------------------ token
    def _request_token(self, path: str) -> str:
        """通用 token 请求 (POST + refresh_token Header), path 为 TOKEN_PATH/TOKEN_UPDATE_PATH."""
        try:
            import requests
        except ImportError as e:
            raise IFindHttpError(
                errorcode=-9002,
                errmsg=f"requests 未安装: {e}. pip install requests",
            )

        url = f"{self.api_host}{path}"
        headers = {
            "Content-Type": "application/json",
            "refresh_token": self.refresh_token,
        }
        try:
            resp = requests.post(url=url, headers=headers, timeout=self.timeout)
        except Exception as e:
            raise IFindHttpError(
                errorcode=-9003,
                errmsg=f"{path} 网络错误: {type(e).__name__}: {e}",
            )

        if resp.status_code != 200:
            raise IFindHttpError(
                errorcode=resp.status_code,
                errmsg=f"{path} HTTP {resp.status_code}: {resp.text[:200]}",
            )

        try:
            payload = resp.json()
        except Exception as e:
            raise IFindHttpError(
                errorcode=-9004,
                errmsg=f"{path} JSON 解析失败: {e}; raw={resp.text[:200]}",
            )

        ec_raw = payload.get("errorcode", payload.get("errCode", -9999))
        try:
            ec = int(ec_raw)
        except (TypeError, ValueError):
            ec = -9999
        if ec != 0:
            errmsg = payload.get("errmsg", payload.get("errMsg", str(payload)))
            raise IFindAuthError(errorcode=ec, errmsg=errmsg, payload=payload)

        data = payload.get("data") or {}
        token = data.get("access_token") or payload.get("access_token")
        if not token:
            raise IFindHttpError(
                errorcode=-9005,
                errmsg=f"{path} 返回缺 access_token 字段: {payload}",
                payload=payload,
            )

        now = datetime.utcnow()
        cache = TokenCache(
            access_token=token,
            obtained_at=now,
            expires_at=now + timedelta(days=ACCESS_TOKEN_TTL_DAYS),
        )
        _save_token_cache(cache)
        return token

    def get_access_token(self, force_refresh: bool = False) -> str:
        """
        取 access_token. 优先用缓存, 失效时调 get_access_token 端点.
        force_refresh=True 时调 update_access_token, 让旧 token 立即失效 (权限变更场景).
        """
        if force_refresh:
            return self._request_token(TOKEN_UPDATE_PATH)

        cached = _load_token_cache()
        if cached and cached.is_valid:
            return cached.access_token

        return self._request_token(TOKEN_PATH)

    def update_access_token(self) -> str:
        """显式调 update_access_token 端点 (强制旧 token 失效, 用于权限变更后)."""
        return self._request_token(TOKEN_UPDATE_PATH)

    # ----------------------------------------------------------------- post
    def post(
        self,
        path: str,
        body: dict[str, Any],
        extra_headers: Optional[dict[str, str]] = None,
        retry: int = DEFAULT_RETRY,
    ) -> dict[str, Any]:
        """
        发起认证 POST. 自动处理 token 过期 (-1302) 重试一次.

        path 形如 '/api/v1/report_query'.
        body 为 JSON 请求体.
        返回解析后的 JSON dict; 业务错误抛 IFindHttpError.
        """
        try:
            import requests
        except ImportError as e:
            raise IFindHttpError(
                errorcode=-9002,
                errmsg=f"requests 未安装: {e}. pip install requests",
            )

        attempts = 0
        force_refresh = False
        last_err: Optional[Exception] = None

        while attempts <= retry:
            attempts += 1
            access_token = self.get_access_token(force_refresh=force_refresh)
            url = f"{self.api_host}{path}"
            headers = {
                "Content-Type": "application/json",
                "access_token": access_token,
                "ifindlang": "cn",
            }
            if extra_headers:
                headers.update(extra_headers)

            try:
                resp = requests.post(url=url, headers=headers, json=body, timeout=self.timeout)
            except Exception as e:
                last_err = e
                # 网络抖动: 短暂等待后重试
                time.sleep(0.5)
                continue

            if resp.status_code != 200:
                raise IFindHttpError(
                    errorcode=resp.status_code,
                    errmsg=f"{path} HTTP {resp.status_code}: {resp.text[:200]}",
                )

            try:
                payload = resp.json()
            except Exception as e:
                raise IFindHttpError(
                    errorcode=-9004,
                    errmsg=f"{path} JSON 解析失败: {e}; raw={resp.text[:200]}",
                )

            ec_raw = payload.get("errorcode", payload.get("errCode", 0))
            try:
                ec = int(ec_raw)
            except (TypeError, ValueError):
                ec = -9999

            if ec == 0:
                return payload

            # token 过期 → 强制刷新一次
            if ec == -1302 and attempts <= retry:
                _invalidate_token_cache()
                force_refresh = True
                continue

            errmsg = payload.get("errmsg", payload.get("errMsg", str(payload)))
            raise IFindHttpError(errorcode=ec, errmsg=errmsg, payload=payload)

        # 网络全部重试用尽
        raise IFindHttpError(
            errorcode=-9003,
            errmsg=f"{path} 网络重试 {attempts} 次失败: {last_err}",
        )


# ============================================================================
# 模块级便捷接口
# ============================================================================
_SINGLETON: Optional[IFindHttpClient] = None


def get_default_client() -> IFindHttpClient:
    """单例客户端, 复用 token 缓存."""
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = IFindHttpClient()
    return _SINGLETON
