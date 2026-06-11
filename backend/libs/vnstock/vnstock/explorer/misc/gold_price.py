from datetime import datetime

import pandas as pd
import requests
from vnai import optimize_execution

from vnstock.core.utils.user_agent import get_headers


@optimize_execution("MISC")
def sjc_gold_price(date=None):
    """
    Truy xuất giá vàng từ trang chủ SJC.

    Args:
        - date: Ngày tra cứu, mặc định là None để lấy ngày hiện tại.
                Nhập giá trị tùy chọn, định dạng YYYY-mm-dd, ví dụ 2025-01-15.
                Dữ liệu có sẵn từ ngày 2/1/2016.

    Returns:
        - Pandas DataFrame chứa thông tin giá vàng nếu thành công, ngược lại trả về None.
    """
    # Set URL
    url = "https://sjc.com.vn/GoldPrice/Services/PriceService.ashx"

    # Define the minimum allowed date
    min_date = datetime(2016, 1, 2)

    # Convert date to required format DD/MM/YYYY
    if date is None:
        input_date = datetime.now().date()
    else:
        try:
            input_date = datetime.strptime(date, "%Y-%m-%d")
            if input_date < min_date:
                raise ValueError("Ngày tra cứu phải từ 2/1/2016 trở đi.")
        except ValueError:
            raise ValueError("Định dạng ngày không hợp lệ. Vui lòng nhập theo định dạng YYYY-mm-dd.")

    # Format date for the API request
    formatted_date = input_date.strftime("%d/%m/%Y")

    # Prepare request payload and headers
    payload = f"method=GetSJCGoldPriceByDate&toDate={formatted_date}"
    headers = get_headers(data_source="SJC", random_agent=False)

    # Send request
    response = requests.post(url, headers=headers, data=payload)

    # Handle response
    if response.status_code == 200:
        data = response.json()
        if not data.get("success"):
            print("Lỗi: Không thể truy xuất dữ liệu từ API.")
            return None

        gold_data = data.get("data", [])
        if not gold_data:
            print("Lỗi: Không có dữ liệu trả về từ API.")
            return None

        # Convert to DataFrame
        df = pd.DataFrame(gold_data, columns=["TypeName", "BranchName", "BuyValue", "SellValue"])
        df = df.rename(columns={
            "TypeName": "name", "BranchName": "branch",
            "BuyValue": "buy_price", "SellValue": "sell_price"
        })

        # Add date column as datetime type
        df.loc[:, "date"] = input_date

        # Ensure numerical columns are correctly formatted
        df.loc[:, "buy_price"] = df["buy_price"].astype(float)
        df.loc[:, "sell_price"] = df["sell_price"].astype(float)

        return df
    else:
        print(f"Lỗi: Không thể kết nối đến API. Mã trạng thái: {response.status_code}")
        return None


@optimize_execution("MISC")
def btmc_goldprice(url="http://api.btmc.vn/api/BTMCAPI/getpricebtmc?key=3kd8ub1llcg9t45hnoh8hmn7t5kc2v"):
    """Parse dữ liệu giá vàng từ API JSON Bảo Tín Minh Châu.

    Args:
        url: Đường dẫn đến API JSON.

    Returns:
        DataFrame chứa dữ liệu giá vàng (đã lọc bỏ BẠC).
    """
    response = requests.get(url)
    json_data = response.json()
    data_list = json_data["DataList"]["Data"]

    data = []
    for item in data_list:
        row_number = item["@row"]
        n_key = f"@n_{row_number}"
        k_key = f"@k_{row_number}"
        h_key = f"@h_{row_number}"
        pb_key = f"@pb_{row_number}"
        ps_key = f"@ps_{row_number}"
        pt_key = f"@pt_{row_number}"
        d_key = f"@d_{row_number}"
        name = item.get(n_key, "")
        if "BẠC" in name.upper():
            continue
        buy_raw = item.get(pb_key, "0")
        sell_raw = item.get(ps_key, "0")
        data.append(
            {
                "name": name,
                "karat": item.get(k_key, ""),
                "gold_content": item.get(h_key, ""),
                "buy_price": float(buy_raw) * 10,
                "sell_price": float(sell_raw) * 10,
                "world_price": item.get(pt_key, ""),
                "time": item.get(d_key, ""),
            }
        )
    df = pd.DataFrame(data)
    df = df.sort_values(by=["sell_price"], ascending=False)
    return df


@optimize_execution("MISC")
def btmc_silver_price(url="http://api.btmc.vn/api/BTMCAPI/getpricebtmc?key=3kd8ub1llcg9t45hnoh8hmn7t5kc2v"):
    """Parse dữ liệu giá bạc từ API JSON Bảo Tín Minh Châu.

    Args:
        url: Đường dẫn đến API JSON.

    Returns:
        DataFrame chứa dữ liệu giá bạc (VND/lượng).
    """
    response = requests.get(url)
    json_data = response.json()
    data_list = json_data["DataList"]["Data"]

    data = []
    for item in data_list:
        row_number = item["@row"]
        n_key = f"@n_{row_number}"
        k_key = f"@k_{row_number}"
        h_key = f"@h_{row_number}"
        pb_key = f"@pb_{row_number}"
        ps_key = f"@ps_{row_number}"
        pt_key = f"@pt_{row_number}"
        d_key = f"@d_{row_number}"
        name = item.get(n_key, "")
        if "BẠC" not in name.upper():
            continue
        buy_raw = item.get(pb_key, "0")
        sell_raw = item.get(ps_key, "0")
        data.append(
            {
                "name": name,
                "karat": item.get(k_key, ""),
                "gold_content": item.get(h_key, ""),
                "buy_price": float(buy_raw) * 10,
                "sell_price": float(sell_raw) * 10,
                "world_price": item.get(pt_key, ""),
                "time": item.get(d_key, ""),
            }
        )
    df = pd.DataFrame(data)
    df = df.sort_values(by=["sell_price"], ascending=False)
    return df
