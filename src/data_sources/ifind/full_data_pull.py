"""
iFinD 全样本数据拉取脚本 — 基于真实 reportID (p05309 + p05310)

跑这个脚本会把 NACS 模型需要的全部新数据从 iFinD 拉下来:
1. p05309 基石投资者 (替换我们手拼的 1500 行基石数据)
2. p05310 首发信息一览 (覆盖 intl_oversub / 首发市盈率 / 募资额等 54 个字段)
3. THS_BD 公司财务三年 (营收/净利/ROE/毛利率 等)

输出 4 个 CSV 到 /tmp/, 发给 Claude 灌库.

预计运行时间: 5-15 分钟 (取决于 iFinD 限速)
"""
import sys
# Windows 控制台默认 GBK，强制 UTF-8 以支持 Unicode 字符
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import logging
import os
import time
from pathlib import Path

# 让 src/ 在 sys.path 中以便 import log 模块
_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from log import get_logger, setup_cli_logging

setup_cli_logging("INFO")
_log = get_logger("ifind.full_data_pull")

from iFinDPy import (
    THS_iFinDLogin, THS_iFinDLogout,
    THS_DR, THS_BD, THS_DataPool,
)
import pandas as pd

# ============================================================================
# 1. 配置 — 从同目录 .env 读取凭证
# ============================================================================
def _load_env(env_path: Path) -> None:
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value

_load_env(Path(__file__).parent / ".env")

USERNAME = os.environ.get("IFIND_USERNAME", "")
PASSWORD = os.environ.get("IFIND_PASSWORD", "")
if not USERNAME or not PASSWORD:
    raise SystemExit("未读到 IFIND_USERNAME / IFIND_PASSWORD，检查 .env")

# 时间范围: 2022-01-01 到 2026-05-07 (NACS 回测窗口)
SDATE = "20220101"
EDATE = "20260507"

# 项目根 = src/data_sources/ifind/../../.. (3 级回退)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
# 默认写入 data/raw/ifind/（统一数据目录），可由环境变量覆盖
OUTPUT_DIR = os.environ.get(
    "IFIND_OUTPUT_DIR",
    str(PROJECT_ROOT / "data" / "raw" / "ifind"),
)
Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

# 测试样本 (5只代表性 IPO)
TEST_CODES = "3296.HK,2513.HK,3750.HK,6082.HK,2565.HK"

# ============================================================================
# 2. 登录
# ============================================================================
login_code = THS_iFinDLogin(USERNAME, PASSWORD)
if login_code not in (0, -201):
    raise SystemExit(f"登录失败: {login_code}")
_log.info("iFinD 登录成功")


# ============================================================================
# 3. 拉取报表 A: 基石投资者 (p05309)
# ============================================================================
_log.info("=" * 70)
_log.info("[1/6] 拉取基石投资者 (p05309)")

t0 = time.time()
result_cs = THS_DR(
    'p05309',
    f'ttype=1;sdate={SDATE};edate={EDATE};sfzx=1',
    'p05309_f001:Y,p05309_f002:Y,p05309_f003:Y,p05309_f016:Y,'
    'p05309_f004:Y,p05309_f017:Y,p05309_f005:Y,p05309_f018:Y,'
    'p05309_f006:Y,p05309_f019:Y,p05309_f009:Y,p05309_f008:Y,'
    'p05309_f011:Y,p05309_f014:Y,p05309_f010:Y,p05309_f015:Y,'
    'p05309_f012:Y,p05309_f013:Y',
    'format:dataframe'
)

if result_cs.errorcode != 0:
    _log.error("p05309 失败: %s", result_cs.errmsg)
    raise SystemExit(1)

df_cs = result_cs.data
_log.info("p05309: %d 条基石记录, 耗时 %.1fs", len(df_cs), time.time() - t0)
_log.debug("  列名: %s", df_cs.columns.tolist())
df_cs.to_csv(f'{OUTPUT_DIR}/ifind_cornerstones.csv', index=False, encoding='utf-8-sig')
_log.info("已保存: ifind_cornerstones.csv")


# ============================================================================
# 4. 拉取报表 B: 首发信息一览 (p05310) — 54 字段
#    带 IFindKey 港股全集 (含 _1 副牌、80xxx GEM、H 类), 避免 sfzx=1 漏副牌
# ============================================================================
_log.info("=" * 70)
_log.info("[2/6] 拉取首发信息一览 (p05310)")

t0 = time.time()

# 全部 54 字段 (基于"生成命令"的列表, f001 到 f054)
all_fields = ",".join([f"p05310_f{i:03d}:Y" for i in range(1, 55)])

# 港股代码全集 (来自 iFinD 客户端「板块成分入口」生成, 一行逗号分隔)
hk_universe_path = Path(__file__).parent / "hk_universe.txt"
ifind_key = ""
if hk_universe_path.exists():
    ifind_key = hk_universe_path.read_text(encoding="utf-8").strip()
    _log.info("使用 IFindKey: %d 个港股代码", ifind_key.count(',') + 1)

ttype_str = f'ttype=1;sdate={SDATE};edate={EDATE};sfzx=1'
if ifind_key:
    ttype_str += f';IFindKey={ifind_key}'

result_ipo = THS_DR(
    'p05310',
    ttype_str,
    all_fields,
    'format:dataframe'
)

if result_ipo.errorcode != 0:
    _log.error("p05310 失败: %s", result_ipo.errmsg)
    _log.info("备用方案: 只拉关键字段")
    key_fields = ",".join([
        f"p05310_f{i:03d}:Y" for i in
        [1,2,3,4,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,
         28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49,50,
         51,52,53,54]
    ])
    result_ipo = THS_DR('p05310',
                         f'ttype=1;sdate={SDATE};edate={EDATE};sfzx=1',
                         key_fields, 'format:dataframe')
    if result_ipo.errorcode != 0:
        raise SystemExit(f"备用方案也失败: {result_ipo.errmsg}")

df_ipo = result_ipo.data
_log.info("p05310: %d 条 IPO, 耗时 %.1fs", len(df_ipo), time.time() - t0)
_log.debug("  列名 (%d 个): %s...", len(df_ipo.columns), df_ipo.columns.tolist()[:10])
df_ipo.to_csv(f'{OUTPUT_DIR}/ifind_ipo_info.csv', index=False, encoding='utf-8-sig')
_log.info("已保存: ifind_ipo_info.csv")


# ============================================================================
# 5. 提取股票代码列表, 用于拉财务数据
# ============================================================================
# 从 ipo_info 里找股票代码列: p05310_f001 是 thscode (如 1187.HK), f002 才是公司名
code_col = None
for c in df_ipo.columns:
    sample = df_ipo[c].dropna().astype(str).head(20)
    if (sample.str.endswith(".HK")).any():
        code_col = c
        break

if not code_col:
    _log.warning("找不到股票代码列, 用前 5 只测试")
    all_codes = TEST_CODES.split(",")
else:
    _log.info("使用代码列: %s", code_col)
    all_codes = df_ipo[code_col].dropna().astype(str).unique().tolist()
    # 过滤掉非标准代码 (含 _1 副牌)
    all_codes = [c for c in all_codes if "_" not in c and c.endswith(".HK")]

_log.info("抽取到 %d 只股票, 准备拉财务", len(all_codes))


# ============================================================================
# 6. 拉取报表 C: 公司财务 (THS_BD) — 港股专用指标名
#    根据 iFinD「基础数据→全球数据→港股股票」生成命令:
#      total_oi                       总营业收入        参数 104,100,OC
#      gross_selling_rate             销售毛利率        参数 104
#      net_profit_margin_on_sales     销售净利率        参数 104
#      ths_roe_hks                    ROE              参数 报告期-12-31,100
#    第三参数按指标用 ; 分隔, 每个指标对应自己的参数
# ============================================================================
_log.info("=" * 70)
_log.info("[3/6] 拉取公司财务 (港股年报)")

BATCH_SIZE = 50

annual_indicators = (
    'total_oi;'                        # 总营业收入
    'gross_selling_rate;'              # 销售毛利率
    'net_profit_margin_on_sales;'      # 销售净利率
    'ths_roe_hks;'                     # ROE
    'ni_attr_to_cs'                    # 归母净利润 (港股基础数据·全球数据)
)

annual_dfs = []
for year in [2022, 2023, 2024, 2025]:
    _log.info("[年报] %d 年...", year)
    # 各指标参数 (探测后确认):
    #   total_oi: 单独日期 (带 104,100,OC 后缀反而 None)
    #   gross_selling_rate / net_profit_margin_on_sales: 日期,104 (104=年报期)
    #   ths_roe_hks: 日期,100 (100=合并)
    #   ni_attr_to_cs: 日期,100,OC (100=合并, OC=原始币种)
    annual_params = (
        f'{year}-12-31;'
        f'{year}-12-31,104;'
        f'{year}-12-31,104;'
        f'{year}-12-31,100;'
        f'{year}-12-31,100,OC'
    )
    year_dfs = []
    for i in range(0, len(all_codes), BATCH_SIZE):
        batch = all_codes[i:i+BATCH_SIZE]
        codes_str = ",".join(batch)
        result = THS_BD(codes_str, annual_indicators, annual_params)
        if result.errorcode == 0 and result.data is not None:
            df_year = result.data.copy()
            df_year['report_year'] = year
            year_dfs.append(df_year)
        else:
            _log.warning("batch %d 失败: %s", i // BATCH_SIZE, result.errmsg)
        time.sleep(0.5)
    if year_dfs:
        df_year_combined = pd.concat(year_dfs, ignore_index=True)
        annual_dfs.append(df_year_combined)
        _log.info("  %d: %d 行", year, len(df_year_combined))

if annual_dfs:
    df_annual = pd.concat(annual_dfs, ignore_index=True)
    df_annual.to_csv(f'{OUTPUT_DIR}/ifind_financials_annual.csv', index=False, encoding='utf-8-sig')
    _log.info("年报已保存: ifind_financials_annual.csv (%d 行)", len(df_annual))
else:
    _log.warning("年报数据全部失败")


# ============================================================================
# 7. 拉取报表 D: 增发信息一览 (p05493)
#    用于跟踪 IPO 后再融资 (配售/供股) 情况, 评估稀释压力
#    字段含义 (基于 2565.HK 派格生物样本):
#      f001=代码 f002=简称 f003=增发方式 f004=状态
#      f005=公告日 f006=到账日 f007=增发数量 f008=占增发前股本比例
#      f009=增发价 f010=募资总额 f011=募资净额 f012=用途 f013=币种
#      f015/f016=行业 f021/f023=主承销商
# ============================================================================
_log.info("=" * 70)
_log.info("[4/6] 拉取增发信息一览 (p05493)")

t0 = time.time()
p05493_fields = ",".join(
    [f"p05493_f{i:03d}:Y" for i in range(1, 32)] + ["jydm_mc:Y"]
)
p05493_ttype = f'sdate={SDATE};edate={EDATE};datetype=1'
if ifind_key:
    p05493_ttype += f';IFindKey={ifind_key}'

result_so = THS_DR('p05493', p05493_ttype, p05493_fields, 'format:dataframe')

if result_so.errorcode == 0 and result_so.data is not None:
    df_so = result_so.data
    df_so.to_csv(f'{OUTPUT_DIR}/ifind_secondary_offerings.csv',
                 index=False, encoding='utf-8-sig')
    _log.info("p05493: %d 条增发记录, 耗时 %.1fs", len(df_so), time.time() - t0)
else:
    _log.error("p05493 失败: %s", result_so.errmsg)


# ============================================================================
# 8. 拉取报表 E: 股本指标 (THS_BD 静态)
#    ths_total_shares_after_ipo_ld_global  首发后总股本(上市日)
#    currency_unit                          实际发行总股本 (iFinDPy 列名误导,
#                                                          实际是发行股本含超配)
#    推算: 上市前总股本 = 首发后 - 实际发行 (含超额配售选择权)
# ============================================================================
_log.info("=" * 70)
_log.info("[5/6] 拉取股本指标, 推算上市前总股本")

t0 = time.time()
share_indicators = 'ths_total_shares_after_ipo_ld_global;currency_unit'
share_dfs = []
for i in range(0, len(all_codes), BATCH_SIZE):
    batch = all_codes[i:i+BATCH_SIZE]
    codes_str = ",".join(batch)
    result = THS_BD(codes_str, share_indicators, ';')
    if result.errorcode == 0 and result.data is not None:
        share_dfs.append(result.data.copy())
    else:
        _log.warning("batch %d 失败: %s", i // BATCH_SIZE, result.errmsg)
    time.sleep(0.5)

if share_dfs:
    df_shares = pd.concat(share_dfs, ignore_index=True)
    # 列重命名 + 计算上市前总股本
    df_shares = df_shares.rename(columns={
        'ths_total_shares_after_ipo_ld_global': 'post_ipo_shares',
        'currency_unit': 'actual_issued_shares',
    })
    df_shares['post_ipo_shares'] = pd.to_numeric(
        df_shares['post_ipo_shares'], errors='coerce')
    df_shares['actual_issued_shares'] = pd.to_numeric(
        df_shares['actual_issued_shares'], errors='coerce')
    df_shares['pre_ipo_shares'] = (
        df_shares['post_ipo_shares'] - df_shares['actual_issued_shares']
    )
    df_shares.to_csv(f'{OUTPUT_DIR}/ifind_share_capital.csv',
                     index=False, encoding='utf-8-sig')
    n_ok = df_shares['pre_ipo_shares'].notna().sum()
    _log.info("股本: %d 行, 推算到 %d 只上市前股本, 耗时 %.1fs",
              len(df_shares), n_ok, time.time() - t0)
else:
    _log.warning("股本数据全部失败")


# ============================================================================
# 9. 拉取板块成分: 18A / 18C / A+H 名单
# ============================================================================
_log.info("=" * 70)
_log.info("[6/6] 拉取上市章节板块成分 (18A/18C/A+H)")

BLOCK_IDS = {
    "18A": "001005348",  # 生物科技公司(18A)
    "18C": "001011051",  # 特专科技公司(18C)
    "AH":  "001005299",  # AH股
    "18B_SPAC": "001012088",
}

block_dfs = {}
for name, block_id in BLOCK_IDS.items():
    try:
        # 取最新成分; THS_DataPool 返回 OrderedDict, 用键访问而非属性
        result = THS_DataPool(
            'block',
            f'2026-05-07;{block_id}',
            'date:Y,thscode:Y,security_name:Y'
        )
        errorcode = result.get('errorcode') if isinstance(result, dict) else getattr(result, 'errorcode', -1)
        errmsg = result.get('errmsg', '') if isinstance(result, dict) else getattr(result, 'errmsg', '')
        if errorcode == 0:
            tables = result.get('tables') if isinstance(result, dict) else getattr(result, 'data', None)
            # tables 是 list[OrderedDict] 或直接是 DataFrame
            if isinstance(tables, list):
                df_block = pd.DataFrame(tables[0]) if tables else pd.DataFrame()
            else:
                df_block = pd.DataFrame(tables) if tables is not None else pd.DataFrame()
            df_block['block_name'] = name
            block_dfs[name] = df_block
            _log.info("  %s: %d 只", name, len(df_block))
        else:
            _log.warning("  %s 失败: %s (block_id 可能不对)", name, errmsg)
    except Exception as e:
        _log.warning("  %s 异常: %s", name, e)

if block_dfs:
    df_blocks = pd.concat(block_dfs.values(), ignore_index=True)
    df_blocks.to_csv(f'{OUTPUT_DIR}/ifind_blocks.csv', index=False, encoding='utf-8-sig')
    _log.info("已保存: ifind_blocks.csv")


# ============================================================================
# 10. 退出
# ============================================================================
THS_iFinDLogout()

_log.info("=" * 70)
_log.info("全部完成! 输出 CSV:")
_log.info("  %s/ifind_cornerstones.csv          (基石投资者)", OUTPUT_DIR)
_log.info("  %s/ifind_ipo_info.csv              (首发信息一览)", OUTPUT_DIR)
_log.info("  %s/ifind_financials_annual.csv     (公司年报财务)", OUTPUT_DIR)
_log.info("  %s/ifind_secondary_offerings.csv   (增发信息)", OUTPUT_DIR)
_log.info("  %s/ifind_share_capital.csv         (股本: 后/实际发行/前)", OUTPUT_DIR)
_log.info("  %s/ifind_blocks.csv                (上市章节成分)", OUTPUT_DIR)
