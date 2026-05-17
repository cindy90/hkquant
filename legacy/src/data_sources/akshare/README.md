# akshare 数据源

封装 [akshare](https://akshare.akfamily.xyz/) 用于获取 iFinD 未覆盖的纯新闻资讯。

## 模块清单

| 文件 | 用途 |
|------|------|
| `news_fetcher.py` | 港股个股新闻 (`ak.stock_news_em`) |

## 与 iFinD 的分工

| 数据 | 数据源 | 模块 |
|------|--------|------|
| 上市公司公告 (PDF) | iFinD HTTP `report_query` | `ifind/announcement_fetcher.py` |
| 个股新闻资讯 | akshare `stock_news_em` | `akshare/news_fetcher.py` |
| 行情/财务/基石 | iFinDPy SDK | `ifind/full_data_pull.py` |
| 市场环境 (HSI/南向) | iFinDPy SDK | `ifind/market_env_fetcher.py` |

## 安装

```
pip install akshare>=1.18
```

无需 token / 账号。直接走东方财富网公开接口，但有访问频率限制（建议批量调用时 `sleep ≥0.5s`）。

## 用法

```python
from src.data_sources.akshare.news_fetcher import fetch_news, fetch_news_batch

# 单股: '00700.HK' / '00700' / '700.HK' 都接受
news = fetch_news('00700.HK', limit=20)
for n in news:
    print(n.published_at, n.headline, n.source_url)

# 批量
batch = fetch_news_batch(['00700.HK', '09988.HK', '03690.HK'], limit_per_symbol=10)
print(batch['_errors'])  # 失败列表
```

## 代码格式

港股代码统一在内部 zero-pad 到 5 位（akshare 期望格式），返回的 `NewsRecord.stock_code` 始终带 `.HK` 后缀。
