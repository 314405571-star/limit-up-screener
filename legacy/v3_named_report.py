import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import warnings
import os
import csv
from typing import List, Dict, Optional
import traceback
from tqdm import tqdm

try:
    import baostock as bs
except ImportError:
    print("❌ 请先安装 baostock：pip install baostock")
    exit(1)

warnings.filterwarnings('ignore')


# ===================== 工具函数 =====================
def safe_login(retries=3):
    """带重试的登录"""
    for i in range(retries):
        try:
            lg = bs.login()
            if lg.error_code == '0':
                return lg
            else:
                print(f"登录失败: {lg.error_msg}，重试 {i+1}/{retries}")
                time.sleep(2)
        except Exception as e:
            print(f"登录异常: {e}，重试 {i+1}/{retries}")
            time.sleep(2)
    return None


def safe_query(code, start_date, end_date, frequency, fields, retries=3):
    """
    带重试的数据查询，自动处理 socket 错误
    返回 pandas.DataFrame 或 None
    """
    for i in range(retries):
        try:
            rs = bs.query_history_k_data_plus(
                code, fields,
                start_date=start_date, end_date=end_date,
                frequency=frequency, adjustflag="3"
            )
            if rs.error_code == '0':
                data_list = []
                while rs.next():
                    data_list.append(rs.get_row_data())
                if data_list:
                    df = pd.DataFrame(data_list, columns=fields.split(','))
                    numeric_cols = ['open','high','low','close','preclose','pctChg','volume','amount']
                    for col in numeric_cols:
                        if col in df.columns:
                            df[col] = pd.to_numeric(df[col], errors='coerce')
                    return df
                return None
            else:
                if "10038" in str(rs.error_msg) or "socket" in str(rs.error_msg).lower():
                    print(f"Socket 错误，重新登录并重试 {code}，第 {i+1} 次")
                    bs.logout()
                    safe_login()
                else:
                    print(f"查询 {code} 失败: {rs.error_msg}，重试 {i+1}/{retries}")
                time.sleep(2)
        except Exception as e:
            print(f"查询异常: {e}，重试 {i+1}/{retries}")
            time.sleep(2)
    return None


def get_previous_trading_day(date_str):
    """获取前一个交易日（简单跳过周末）"""
    dt = datetime.strptime(date_str, '%Y-%m-%d')
    dt -= timedelta(days=1)
    while dt.weekday() >= 5:  # 5=周六, 6=周日
        dt -= timedelta(days=1)
    return dt.strftime('%Y-%m-%d')


def get_latest_trading_day():
    """获取最近的交易日（通过 baostock 接口，包含今天）"""
    for i in range(10):
        check_date = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
        try:
            rs = bs.query_trade_dates(start_date=check_date, end_date=check_date)
            if rs.error_code == '0':
                data_list = []
                while rs.next():
                    data_list.append(rs.get_row_data())
                if data_list:
                    df = pd.DataFrame(data_list, columns=rs.fields)
                    if not df.empty and df.iloc[0]['is_trading_day'] == '1':
                        return check_date
        except:
            continue
    dt = datetime.now()
    while dt.weekday() >= 5:
        dt -= timedelta(days=1)
    return dt.strftime('%Y-%m-%d')


def calc_limit_price(preclose, code, is_st):
    """计算涨停价"""
    if is_st:
        ratio = 0.05
    else:
        ratio = 0.20 if code.startswith(('30','68')) else 0.10
    return round(preclose * (1 + ratio), 2)


def check_is_limit_up(daily_data, code):
    """判断当日是否涨停（收盘价 >= 涨停价）"""
    if daily_data is None or daily_data.empty:
        return False
    preclose = daily_data['preclose']
    if pd.isna(preclose) or preclose == 0:
        return False
    is_st = daily_data['isST'] == '1'
    limit_price = calc_limit_price(preclose, code, is_st)
    return daily_data['close'] >= limit_price


def check_touched_limit_up(minute_df, preclose, code, is_st):
    """判断盘中是否曾触及涨停（5分钟线最高价 >= 涨停价）"""
    if minute_df is None or minute_df.empty:
        return False
    if pd.isna(preclose) or preclose == 0:
        return False
    limit_price = calc_limit_price(preclose, code, is_st)
    return minute_df['high'].max() >= limit_price


def get_share_capital_from_baostock(code: str):
    """
    通过 baostock 季频盈利能力接口获取最近一期总股本和流通股本（单位：万股）
    返回 (total_share_股数, float_share_股数) 或 (None, None)
    """
    today = datetime.now()
    year = today.year
    quarter = (today.month - 1) // 3 + 1

    for _ in range(8):  # 最多回溯2年
        rs = bs.query_profit_data(code=code, year=year, quarter=quarter)
        if rs.error_code == '0':
            data_list = []
            while rs.next():
                data_list.append(rs.get_row_data())
            if data_list:
                row = data_list[-1]
                fields = rs.fields
                try:
                    ts_idx = fields.index('totalShare')
                    ls_idx = fields.index('liqaShare')
                    total_share_wan = float(row[ts_idx])
                    liqa_share_wan  = float(row[ls_idx])
                    # 单位：万股 → 股
                    return total_share_wan * 1e4, liqa_share_wan * 1e4
                except (ValueError, IndexError):
                    return None, None
        quarter -= 1
        if quarter == 0:
            quarter = 4
            year -= 1
    return None, None


# ===================== 主分析器 =====================
class FirstBoardAnalyzer:
    def __init__(self):
        self.request_delay = 0.2
        self.is_logged_in = False

    def login(self):
        if safe_login():
            self.is_logged_in = True
            print("✅ 登录成功")
            return True
        print("❌ 登录失败")
        return False

    def logout(self):
        if self.is_logged_in:
            bs.logout()
            print("✅ 已登出")
            self.is_logged_in = False

    def read_stock_codes_from_csv(self, csv_path, code_column='code'):
        stock_codes = []
        if not os.path.exists(csv_path):
            print(f"❌ 文件不存在: {csv_path}")
            return stock_codes
        try:
            encodings = ['utf-8', 'gbk', 'gb2312', 'utf-8-sig', 'latin-1']
            for enc in encodings:
                try:
                    df = pd.read_csv(csv_path, encoding=enc)
                    if code_column not in df.columns:
                        for col in df.columns:
                            if 'code' in str(col).lower():
                                code_column = col
                                break
                        else:
                            code_column = df.columns[0]
                    codes = df[code_column].astype(str).str.strip()
                    for code in codes:
                        if pd.isna(code) or code == 'nan' or code == '':
                            continue
                        code = code.replace('"','').replace("'","").replace(" ","")
                        if not code.startswith(('sh.','sz.','bj.')):
                            if code.startswith('6'): code = f'sh.{code}'
                            elif code[0] in ['0','3']: code = f'sz.{code}'
                            elif code.startswith('8'): code = f'bj.{code}'
                            elif len(code)==6 and code.isdigit(): code = f'sh.{code}'
                            else: continue
                        stock_codes.append(code)
                    print(f"📊 从CSV读取了 {len(stock_codes)} 个代码")
                    return stock_codes
                except:
                    continue
            # 兜底 csv 模块
            for enc in encodings:
                try:
                    with open(csv_path, 'r', encoding=enc, errors='ignore') as f:
                        reader = csv.reader(f)
                        header = next(reader, None)
                        if not header: continue
                        code_col = 0
                        for i,c in enumerate(header):
                            if 'code' in str(c).lower(): code_col = i; break
                        for row in reader:
                            if len(row) > code_col:
                                c = str(row[code_col]).strip()
                                if c and c != 'nan':
                                    if not c.startswith(('sh.','sz.','bj.')):
                                        if c.startswith('6'): c = f'sh.{c}'
                                        elif c[0] in ['0','3']: c = f'sz.{c}'
                                        elif c.startswith('8'): c = f'bj.{c}'
                                        else: continue
                                    stock_codes.append(c)
                    if stock_codes:
                        print(f"✅ 使用 {enc} 读取到 {len(stock_codes)} 个代码")
                        return stock_codes
                except:
                    continue
            print("❌ 无法读取CSV文件")
            return []
        except Exception as e:
            print(f"❌ 读取CSV文件失败: {e}")
            return []

    def get_history_k(self, code, start, end, fields='date,open,high,low,close,volume,amount'):
        return safe_query(code, start, end, 'd', fields)

    def get_day_k(self, code, date):
        df = self.get_history_k(code, date, date)
        return None if df is None or df.empty else df.iloc[0]

    def calc_profit_ratio(self, day_data):
        """收盘获利比例 = (收盘价 - 均价) / 均价 * 100"""
        if day_data is None: return None
        close = day_data['close']
        amount = day_data['amount']
        volume = day_data['volume']
        if volume == 0 or pd.isna(volume): return None
        avg_price = amount / volume
        return (close - avg_price) / avg_price * 100

    def get_market_cap(self, code, close_price):
        """返回 (总市值_亿, 流通市值_亿)"""
        total_share, float_share = get_share_capital_from_baostock(code)
        if total_share is None or float_share is None:
            return None, None
        total_cap = (total_share * close_price) / 1e8
        float_cap = (float_share * close_price) / 1e8
        return total_cap, float_cap

    def get_stock_name(self, code: str) -> str:
        """获取股票中文名称"""
        try:
            rs = bs.query_stock_basic(code=code)
            if rs.error_code == '0':
                data = []
                while rs.next():
                    data.append(rs.get_row_data())
                if data and len(data[0]) > 1:
                    return data[0][1]
        except:
            pass
        return "未知"

    # ---------- 策略实现 ----------
    def strategy_one_sbdk(self, first_board_list, target_date):
        """首板低开选股：近60日相对位置 ≤ 0.5，成交额 ≥ 1亿"""
        selected = []
        start_60 = (datetime.strptime(target_date,'%Y-%m-%d') - timedelta(days=100)).strftime('%Y-%m-%d')
        for fb in first_board_list:
            code = fb['code']
            close_t = fb['close']
            df_hist = self.get_history_k(code, start_60, target_date)
            if df_hist is None or len(df_hist) < 60:
                continue
            df_60 = df_hist.tail(60)
            high_60 = df_60['high'].max()
            low_60 = df_60['low'].min()
            rp = (high_60 - close_t) / (high_60 - low_60) if high_60 != low_60 else 1
            if rp > 0.5:
                continue
            amount_t = fb.get('amount', None)
            if amount_t is None:
                day = self.get_day_k(code, target_date)
                amount_t = day['amount'] if day is not None else 0
            if amount_t < 1e8:
                continue
            selected.append(fb)
        return selected

    def strategy_two_sbgk(self, first_board_list, target_date):
        """首板高开选股：获利比例≥7%，成交额5.5~20亿，市值70~520亿"""
        selected = []
        for fb in first_board_list:
            code = fb['code']
            day = self.get_day_k(code, target_date)
            if day is None: continue
            profit = self.calc_profit_ratio(day)
            if profit is None or profit < 7:
                continue
            amount = day['amount']
            if not (5.5e8 <= amount <= 20e8):
                continue
            total_cap, float_cap = self.get_market_cap(code, day['close'])
            if total_cap is not None and float_cap is not None:
                if total_cap < 70 or float_cap > 520:
                    continue
            else:
                print(f"⚠️ 无法获取 {code} 市值，跳过市值过滤")
            selected.append(fb)
        return selected

    def strategy_three_rzq(self, exploded_list, target_date):
        """弱转强选股：近3日涨幅≤28%，当日K线实体≥-5%，获利比例≥-4%，成交额3~19亿，市值70~520亿"""
        selected = []
        start_10 = (datetime.strptime(target_date,'%Y-%m-%d') - timedelta(days=10)).strftime('%Y-%m-%d')
        for ep in exploded_list:
            code = ep['code']
            df_recent = self.get_history_k(code, start_10, target_date)
            if df_recent is None or len(df_recent) < 3:
                continue
            df_recent = df_recent.sort_values('date')
            last3 = df_recent.tail(3)
            if len(last3) < 3: continue
            first_close = last3.iloc[0]['close']
            last_close = last3.iloc[-1]['close']
            if first_close == 0: continue
            cum_pct = (last_close - first_close) / first_close * 100
            if cum_pct > 28:
                continue
            today = last3.iloc[-1]
            open_t = today['open']
            close_t = today['close']
            if open_t == 0: continue
            entity = (close_t / open_t - 1) * 100
            if entity < -5:
                continue
            profit = self.calc_profit_ratio(today)
            if profit is None or profit < -4:
                continue
            amount = today['amount']
            if not (3e8 <= amount <= 19e8):
                continue
            total_cap, float_cap = self.get_market_cap(code, close_t)
            if total_cap is not None and float_cap is not None:
                if total_cap < 70 or float_cap > 520:
                    continue
            else:
                print(f"⚠️ 无法获取 {code} 市值，跳过市值过滤")
            selected.append(ep)
        return selected

    def pressure_test(self, candidate_list, target_date):
        """
        压力测试：从T-1日前100日找首个收盘价>T-1高点的位置，
        取其后5日最大成交量，要求T日成交量 ≥ 最大量 × 0.9
        """
        passed = []
        prev_date = get_previous_trading_day(target_date)
        start_lookback = (datetime.strptime(target_date,'%Y-%m-%d') - timedelta(days=150)).strftime('%Y-%m-%d')
        for cand in candidate_list:
            code = cand['code']
            prev_day = self.get_day_k(code, prev_date)
            if prev_day is None: continue
            pressure_high = prev_day['high']
            df_hist = self.get_history_k(code, start_lookback, prev_date)
            if df_hist is None or df_hist.empty: continue
            df_hist = df_hist.sort_values('date')
            df_before = df_hist[df_hist['date'] < prev_date]
            idx = None
            for i in range(len(df_before)-1, -1, -1):
                if df_before.iloc[i]['close'] > pressure_high:
                    idx = i
                    break
            if idx is None: continue
            obs_start = idx + 1
            obs_end = min(obs_start + 5, len(df_before))
            obs_df = df_before.iloc[obs_start:obs_end]
            if obs_df.empty: continue
            max_vol = obs_df['volume'].max()
            today = self.get_day_k(code, target_date)
            if today is None: continue
            if today['volume'] >= max_vol * 0.9:
                passed.append(cand)
        return passed

    # ---------- 全流程批量分析 ----------
    def run_batch_analysis(self, csv_path, target_date=None, code_column='code', output_dir='output'):
        if target_date is None:
            target_date = get_latest_trading_day()

        print("=" * 60)
        print("📈 首板/炸板池 + 策略选股系统（市值=收盘价×股本）")
        print("=" * 60)
        if not self.login():
            return

        try:
            codes = self.read_stock_codes_from_csv(csv_path, code_column)
            if not codes:
                print("❌ 未读取到股票代码")
                return

            # ---------- 生成首板/炸板池 ----------
            first_board_pool = []
            exploded_pool = []
            prev_date = get_previous_trading_day(target_date)
            print(f"开始分析 {len(codes)} 只股票，目标日期 {target_date} ...")
            with tqdm(total=len(codes), desc="首板/炸板") as pbar:
                for code in codes:
                    pbar.set_description(f"处理 {code}")
                    daily_df = safe_query(code, target_date, target_date, "d",
                                          "date,code,open,high,low,close,preclose,pctChg,isST,volume,amount")
                    if daily_df is None or daily_df.empty:
                        pbar.update(1); time.sleep(self.request_delay); continue
                    d = daily_df.iloc[0]
                    if pd.isna(d['close']) or d['close'] == 0:
                        pbar.update(1); time.sleep(self.request_delay); continue

                    prev_df = safe_query(code, prev_date, prev_date, "d",
                                         "date,code,open,high,low,close,preclose,pctChg,isST")
                    prev_d = prev_df.iloc[0] if prev_df is not None and not prev_df.empty else None
                    prev_limit = check_is_limit_up(prev_d, code) if prev_d is not None else False
                    today_limit = check_is_limit_up(d, code)

                    minute_df = safe_query(code, target_date, target_date, "5",
                                           "date,time,open,high,low,close,volume")
                    touched = check_touched_limit_up(minute_df, d['preclose'], code, d['isST']=='1')

                    if today_limit and not prev_limit:
                        first_board_pool.append({
                            'code': code,
                            'close': d['close'],
                            'pctChg': d['pctChg'],
                            'amount': d['amount']
                        })
                    elif touched and not today_limit and not prev_limit:
                        exploded_pool.append({
                            'code': code,
                            'close': d['close'],
                            'high': d['high'],
                            'pctChg': d['pctChg'],
                            'amount': d['amount']
                        })
                    pbar.update(1)
                    time.sleep(self.request_delay)

            fb_df = pd.DataFrame(first_board_pool)
            ep_df = pd.DataFrame(exploded_pool)
            os.makedirs(output_dir, exist_ok=True)
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            if not fb_df.empty:
                fb_df.to_csv(os.path.join(output_dir, f'首板池_{target_date}_{ts}.csv'), index=False, encoding='utf-8-sig')
            if not ep_df.empty:
                ep_df.to_csv(os.path.join(output_dir, f'炸板池_{target_date}_{ts}.csv'), index=False, encoding='utf-8-sig')
            print(f"\n✅ 首板池: {len(fb_df)} 只, 炸板池: {len(ep_df)} 只")

            # ---------- 运行策略 ----------
            print("\n===== 运行选股策略 =====")
            sbdk = self.strategy_one_sbdk(first_board_pool, target_date)
            sbgk = self.strategy_two_sbgk(first_board_pool, target_date)
            rzq = self.strategy_three_rzq(exploded_pool, target_date)

            sbgk_pass = self.pressure_test(sbgk, target_date)
            rzq_pass = self.pressure_test(rzq, target_date)

            # ---------- 显示详细结果 ----------
            print("\n" + "="*60)
            print("📊 选股结果详情")
            print("="*60)

            # 策略一
            if sbdk:
                print(f"\n🎯 策略一（首板低开）- 共 {len(sbdk)} 只：")
                for item in sbdk:
                    name = self.get_stock_name(item['code'])
                    print(f"  {item['code']}  {name}  收盘价:{item['close']:.2f}  成交额:{item.get('amount',0)/1e8:.2f}亿")
                pd.DataFrame(sbdk).to_csv(os.path.join(output_dir, f'策略一_首板低开_{target_date}_{ts}.csv'), index=False, encoding='utf-8-sig')
            else:
                print(f"\n🎯 策略一（首板低开）- 无股票通过")

            # 策略二
            if sbgk_pass:
                print(f"\n🚀 策略二（首板高开 + 压力通过）- 共 {len(sbgk_pass)} 只：")
                for item in sbgk_pass:
                    name = self.get_stock_name(item['code'])
                    print(f"  {item['code']}  {name}  收盘价:{item['close']:.2f}  成交额:{item.get('amount',0)/1e8:.2f}亿")
                pd.DataFrame(sbgk_pass).to_csv(os.path.join(output_dir, f'策略二_首板高开_压力通过_{target_date}_{ts}.csv'), index=False, encoding='utf-8-sig')
            else:
                print(f"\n🚀 策略二（首板高开 + 压力通过）- 无股票通过")

            # 策略三
            if rzq_pass:
                print(f"\n🔄 策略三（弱转强 + 压力通过）- 共 {len(rzq_pass)} 只：")
                for item in rzq_pass:
                    name = self.get_stock_name(item['code'])
                    print(f"  {item['code']}  {name}  收盘价:{item['close']:.2f}  成交额:{item.get('amount',0)/1e8:.2f}亿")
                pd.DataFrame(rzq_pass).to_csv(os.path.join(output_dir, f'策略三_弱转强_压力通过_{target_date}_{ts}.csv'), index=False, encoding='utf-8-sig')
            else:
                print(f"\n🔄 策略三（弱转强 + 压力通过）- 无股票通过")

            # 汇总最终选股代码
            final_codes = []
            if sbdk:
                final_codes.extend([x['code'] for x in sbdk])
            if sbgk_pass:
                final_codes.extend([x['code'] for x in sbgk_pass])
            if rzq_pass:
                final_codes.extend([x['code'] for x in rzq_pass])
            final_codes = list(set(final_codes))

            if final_codes:
                print(f"\n🏆 最终汇总（去重后）共 {len(final_codes)} 只：")
                for code in final_codes:
                    name = self.get_stock_name(code)
                    print(f"  {code}  {name}")
                pd.DataFrame({'code': final_codes}).to_csv(
                    os.path.join(output_dir, f'最终选股_{target_date}_{ts}.csv'), index=False, encoding='utf-8-sig')
            else:
                print("\n⚠️ 没有股票通过所有筛选")

        except KeyboardInterrupt:
            print("\n⚠️ 用户中断")
        except Exception as e:
            print(f"\n❌ 出错: {e}")
            traceback.print_exc()
        finally:
            self.logout()


# ===================== 交互主程序 =====================
def create_sample_csv():
    sample = pd.DataFrame({
        'code': ['sh.600000','sz.000001','sh.600519','sz.300750','sh.688981',
                 'sh.600030','sz.002475','sh.601318','sz.002415','sh.600036']
    })
    sample.to_csv('stock_list.csv', index=False, encoding='utf-8-sig')
    print("✅ 示例 stock_list.csv 已创建")
    return 'stock_list.csv'


def main():
    print("=" * 60)
    print("📈 首板/炸板池 + 多策略选股系统")
    print("=" * 60)
    analyzer = FirstBoardAnalyzer()

    while True:
        print("\n1. 运行批量分析（含策略选股）")
        print("2. 创建示例CSV文件")
        print("3. 退出")
        choice = input("请选择: ").strip()
        if choice == '1':
            csv_path = input("CSV文件路径: ").strip()
            if not csv_path or not os.path.exists(csv_path):
                print("❌ 文件不存在")
                continue
            target = input("分析日期(YYYY-MM-DD，回车使用最近交易日): ").strip()
            target = None if not target else target
            out_dir = input("输出目录(默认output): ").strip() or 'output'
            analyzer.run_batch_analysis(csv_path, target_date=target, output_dir=out_dir)
        elif choice == '2':
            create_sample_csv()
        elif choice == '3':
            print("👋 再见")
            break
        else:
            print("❌ 无效选择")


if __name__ == "__main__":
    os.makedirs('output', exist_ok=True)
    main()
