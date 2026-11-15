import baostock as bs
import pandas as pd
import numpy as np
import time
from datetime import datetime, timedelta

# ---------------------------- 工具函数 ----------------------------

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
                frequency=frequency, adjustflag="3"  # 不复权
            )
            if rs.error_code == '0':
                data_list = []
                while rs.next():
                    data_list.append(rs.get_row_data())
                if data_list:
                    df = pd.DataFrame(data_list, columns=fields.split(','))
                    # 转换数值列
                    numeric_cols = ['open','high','low','close','preclose','pctChg','volume']
                    for col in numeric_cols:
                        if col in df.columns:
                            df[col] = pd.to_numeric(df[col], errors='coerce')
                    return df
                else:
                    return None
            else:
                print(f"查询 {code} 失败: {rs.error_msg}，重试 {i+1}/{retries}")
                time.sleep(2)
        except Exception as e:
            if "10038" in str(e) or "socket" in str(e).lower():
                print(f"Socket 错误，重新登录并重试 {code}，第 {i+1} 次")
                bs.logout()
                lg = safe_login()
                if lg is None:
                    time.sleep(3)
                    continue
            else:
                print(f"查询异常: {e}")
            time.sleep(2)
    return None

def get_previous_trading_day(date_str):
    """获取前一个交易日（简单跳过周末，但节假日可能不准）"""
    dt = datetime.strptime(date_str, '%Y-%m-%d')
    dt -= timedelta(days=1)
    while dt.weekday() >= 5:  # 5=周六, 6=周日
        dt -= timedelta(days=1)
    return dt.strftime('%Y-%m-%d')

def get_latest_trading_day():
    """
    获取最近的交易日（今天如果是交易日则返回今天，否则往前找）
    使用 baostock 的 query_trade_dates 接口判断
    """
    today = datetime.now().strftime('%Y-%m-%d')
    # 从今天开始往前查找，最多找10天
    for i in range(10):
        check_date = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
        # 查询该日期是否为交易日
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
            else:
                # 查询失败，继续尝试下一天
                continue
        except Exception as e:
            print(f"查询交易日失败: {e}，尝试下一天")
            continue
    # 如果都没有找到，则使用简单跳过周末（最后一天）
    print("无法通过接口获取交易日，使用简单跳过周末")
    dt = datetime.now()
    while dt.weekday() >= 5:
        dt -= timedelta(days=1)
    return dt.strftime('%Y-%m-%d')

def calc_limit_price(preclose, code, is_st):
    """
    计算涨停价
    - 主板（60/00）：10%
    - 创业板（30）：20%
    - 科创板（68）：20%
    - ST/*ST：5%
    """
    if is_st:
        ratio = 0.05
    else:
        if code.startswith(('30', '68')):
            ratio = 0.20
        else:
            ratio = 0.10
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

# ---------------------------- 核心计算 ----------------------------

def calc_pools_from_csv(csv_path, target_date, output_csv=True):
    """
    从 CSV 文件读取股票代码，计算首板池和炸板池
    csv_path: CSV文件路径，必须包含 'code' 列（如 'sh.600000'）
    target_date: 目标日期，格式 'YYYY-MM-DD'
    output_csv: 是否保存结果文件
    """
    # 1. 读取股票列表
    try:
        df_stocks = pd.read_csv(csv_path)
        if 'code' not in df_stocks.columns:
            raise ValueError("CSV 文件必须包含 'code' 列")
        stock_codes = df_stocks['code'].dropna().astype(str).tolist()
    except Exception as e:
        print(f"读取 CSV 失败: {e}")
        return None, None

    if not stock_codes:
        print("股票列表为空")
        return None, None

    print(f"共读取 {len(stock_codes)} 只股票，目标日期: {target_date}")

    # 2. 登录
    lg = safe_login()
    if lg is None:
        print("登录失败，程序退出")
        return None, None

    # 3. 前一个交易日
    prev_date = get_previous_trading_day(target_date)

    first_board_pool = []   # 首板池
    exploded_pool = []      # 炸板池

    # 4. 遍历每只股票
    for idx, code in enumerate(stock_codes):
        if (idx + 1) % 50 == 0:
            print(f"已处理 {idx + 1}/{len(stock_codes)}")

        # 每次请求前休眠，避免频率过高
        time.sleep(0.2)

        try:
            # ---- 获取当日日线 ----
            daily_df = safe_query(code, target_date, target_date, "d",
                                  "date,code,open,high,low,close,preclose,pctChg,isST")
            if daily_df is None or daily_df.empty:
                continue
            daily_data = daily_df.iloc[0]

            # 跳过停牌（收盘价为0或缺失）
            if pd.isna(daily_data['close']) or daily_data['close'] == 0:
                continue

            # ---- 获取前一日日线 ----
            prev_df = safe_query(code, prev_date, prev_date, "d",
                                 "date,code,open,high,low,close,preclose,pctChg,isST")
            prev_daily = prev_df.iloc[0] if prev_df is not None and not prev_df.empty else None

            # ---- 判断前一日是否涨停 ----
            prev_limit_up = check_is_limit_up(prev_daily, code) if prev_daily is not None else False

            # ---- 判断当日是否涨停 ----
            today_limit_up = check_is_limit_up(daily_data, code)

            # ---- 获取当日5分钟线 ----
            minute_df = safe_query(code, target_date, target_date, "5",
                                   "date,time,open,high,low,close,volume")

            # ---- 判断是否曾触及涨停 ----
            preclose = daily_data['preclose']
            is_st = daily_data['isST'] == '1'
            touched = check_touched_limit_up(minute_df, preclose, code, is_st)

            # ---- 归类 ----
            # 首板池：当日涨停，且前一日未涨停
            if today_limit_up and not prev_limit_up:
                first_board_pool.append({
                    'code': code,
                    'close': daily_data['close'],
                    'pctChg': daily_data['pctChg']
                })

            # 炸板池：曾触及涨停但收盘未封板，且前一日未涨停
            if touched and not today_limit_up and not prev_limit_up:
                exploded_pool.append({
                    'code': code,
                    'close': daily_data['close'],
                    'high': daily_data['high'],
                    'pctChg': daily_data['pctChg']
                })

        except Exception as e:
            print(f"处理 {code} 时出错: {e}")
            continue

    # 5. 注销
    bs.logout()

    # 6. 转换为 DataFrame
    first_df = pd.DataFrame(first_board_pool)
    exploded_df = pd.DataFrame(exploded_pool)

    # 7. 保存（可选）
    if output_csv:
        if not first_df.empty:
            first_df.to_csv(f"首板池_{target_date}.csv", index=False, encoding='utf-8-sig')
            print(f"首板池已保存至 首板池_{target_date}.csv")
        if not exploded_df.empty:
            exploded_df.to_csv(f"炸板池_{target_date}.csv", index=False, encoding='utf-8-sig')
            print(f"炸板池已保存至 炸板池_{target_date}.csv")

    return first_df, exploded_df

# ---------------------------- 主函数 ----------------------------

def main():
    # 配置参数
    CSV_PATH = "test_stocks.csv"                      # CSV文件路径

    # 登录以获取交易日（临时登录）
    lg = safe_login()
    if lg is None:
        print("无法登录获取交易日，使用简单跳过周末")
        target_date = datetime.now().strftime('%Y-%m-%d')
        # 简单跳过周末
        dt = datetime.now()
        while dt.weekday() >= 5:
            dt -= timedelta(days=1)
        target_date = dt.strftime('%Y-%m-%d')
    else:
        # 获取最近的交易日
        target_date = get_latest_trading_day()
        bs.logout()

    OUTPUT_CSV = True                                 # 是否保存结果文件

    print(f"目标日期：{target_date}")
    print("开始计算首板池与炸板池...")
    first, exploded = calc_pools_from_csv(CSV_PATH, target_date, output_csv=OUTPUT_CSV)

    # 打印结果
    print("\n" + "="*60)
    print(f"【首板池】共 {len(first) if first is not None else 0} 只")
    if first is not None and not first.empty:
        print(first.to_string(index=False))
    else:
        print("无")

    print("\n" + "="*60)
    print(f"【炸板池】共 {len(exploded) if exploded is not None else 0} 只")
    if exploded is not None and not exploded.empty:
        print(exploded.to_string(index=False))
    else:
        print("无")

    print("\n计算完成！")

if __name__ == "__main__":
    main()
