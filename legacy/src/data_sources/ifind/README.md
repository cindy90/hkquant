# iFinD 数据源

同花顺机构金融终端（iFinD QuantAPI）的港股 IPO 数据拉取脚本。

## 前置依赖

1. **iFinD 客户端**：从 [quantapi.10jqka.com.cn](https://quantapi.10jqka.com.cn/) 下载并安装；运行客户端目录下的 `iFinDPy 注册.exe`，会把 `iFinDPy.pth` 写入 Python 的 site-packages，从而 `from iFinDPy import ...` 可用。
2. **凭证**：复制 `.env.example` 为 `.env`，填入 iFinD 账号/密码。

## 验证连通

最小验证（不消耗配额，只验登录）：

```bash
cd <项目根>
PYTHONUTF8=1 python -c "
import sys, os; from pathlib import Path
sys.path.insert(0, 'src/data_sources/ifind')
env_lines = open('src/data_sources/ifind/.env', encoding='utf-8').read().splitlines()
for ln in env_lines:
    if '=' in ln and not ln.startswith('#'):
        k, _, v = ln.partition('='); os.environ[k.strip()] = v.strip().strip(\"'\\\"\")
from iFinDPy import THS_iFinDLogin, THS_iFinDLogout
code = THS_iFinDLogin(os.environ['IFIND_USERNAME'], os.environ['IFIND_PASSWORD'])
print('login code:', code)  # 0 = 全新成功, -201 = 已登录
THS_iFinDLogout()
"
```

## 完整拉取

```bash
cd <项目根>
PYTHONUTF8=1 python src/data_sources/ifind/full_data_pull.py
```

输出 7 个 CSV 到 `<项目根>/data/raw/ifind/`：
- `ifind_cornerstones.csv` — 基石投资者（p05309）
- `ifind_ipo_info.csv` — 首发信息一览（p05310，54 字段）
- `ifind_financials_annual.csv` — 公司年报财务（THS_BD）
- `ifind_secondary_offerings.csv` — 增发信息（p05493）
- `ifind_share_capital.csv` — 股本: 后/实际发行/前
- `ifind_blocks.csv` — 上市章节板块成分（18A/18C/A+H）
- `ifind_indicator_catalog.csv` — 指标字典（人工维护）

预计耗时 5-15 分钟（取决于 iFinD 限速）。

## 自定义输出位置

```bash
IFIND_OUTPUT_DIR=/some/other/path python src/data_sources/ifind/full_data_pull.py
```
