"""data_sources.ifind.http_client 的单元测试.

仅测试纯逻辑部分 (TokenCache, 异常类), 不真正调 iFinD 接口.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest

from data_sources.ifind.http_client import (
    TokenCache,
    IFindHttpError,
    IFindAuthError,
    ACCESS_TOKEN_TTL_DAYS,
    ACCESS_TOKEN_REFRESH_THRESHOLD_DAYS,
)


class TestTokenCache:
    def test_valid_when_fresh(self):
        now = datetime.utcnow()
        tc = TokenCache(
            access_token="tok_abc",
            obtained_at=now,
            expires_at=now + timedelta(days=ACCESS_TOKEN_TTL_DAYS),
        )
        assert tc.is_valid is True

    def test_invalid_when_expired(self):
        now = datetime.utcnow()
        tc = TokenCache(
            access_token="tok_abc",
            obtained_at=now - timedelta(days=10),
            expires_at=now - timedelta(hours=1),
        )
        assert tc.is_valid is False

    def test_invalid_when_near_expiry(self):
        now = datetime.utcnow()
        # expires_at 只差 12 小时, 但 threshold 是 1 天 → 视为失效
        tc = TokenCache(
            access_token="tok_abc",
            obtained_at=now - timedelta(days=6),
            expires_at=now + timedelta(hours=12),
        )
        assert tc.is_valid is False

    def test_round_trip_dict(self):
        now = datetime(2024, 6, 1, 12, 0, 0)
        tc = TokenCache(
            access_token="tok_xyz",
            obtained_at=now,
            expires_at=now + timedelta(days=7),
        )
        d = tc.to_dict()
        tc2 = TokenCache.from_dict(d)
        assert tc2.access_token == tc.access_token
        assert tc2.obtained_at == tc.obtained_at
        assert tc2.expires_at == tc.expires_at


class TestExceptions:
    def test_ifind_http_error_fields(self):
        err = IFindHttpError(errorcode=-9001, errmsg="test error")
        assert err.errorcode == -9001
        assert "test error" in str(err)
        assert err.payload == {}

    def test_ifind_auth_error_is_http_error(self):
        err = IFindAuthError(errorcode=-1302, errmsg="token expired")
        assert isinstance(err, IFindHttpError)
        assert err.errorcode == -1302

    def test_error_with_payload(self):
        payload = {"data": {"detail": "foo"}}
        err = IFindHttpError(errorcode=500, errmsg="server error", payload=payload)
        assert err.payload == payload


class TestIFindHttpClientInit:
    def test_raises_without_refresh_token(self, monkeypatch):
        """refresh_token 缺失时应抛 IFindAuthError."""
        monkeypatch.delenv("IFIND_REFRESH_TOKEN", raising=False)
        # 强制清除 _ENV_LOADED 防止上次测试残留
        import data_sources.ifind.http_client as mod
        mod._ENV_LOADED = True  # 跳过 .env 加载
        with pytest.raises(IFindAuthError) as exc_info:
            from data_sources.ifind.http_client import IFindHttpClient
            IFindHttpClient(refresh_token="")
        assert exc_info.value.errorcode == -9001
