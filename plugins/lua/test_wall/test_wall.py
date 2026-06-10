#!/usr/bin/env python3 
import os
import sys
import socket
import time
import threading
import urllib.request
import urllib.parse
import re
import argparse
import json
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed

# Windows 兼容性：SIGALRM 只在 Unix 系统存在
if sys.platform != 'win32':
    import signal

# ==================== 配置区域 ====================
SHOW_FOREIGN = True          # 是否显示外网 IP
SHOW_DOMESTIC = False        # 是否显示国内 IP
SHOW_NTP = False             # 是否显示 NTP 延迟
SHOW_CONNECTIVITY = True     # 是否显示外网连通性延迟
TIMEOUT = 4                  # 脚本整体超时时间（秒）
REQUEST_TIMEOUT = 2          # 单个请求超时时间（秒）
OUTPUT_FILENAME = "test_wall_output.txt"  # 输出文件名
DEFAULT_ENCODING = "utf-8"   # 默认编码
# ================================================

# 根据平台确定输出目录
if sys.platform == 'win32':
    CACHE_DIR = os.environ.get('TEMP', os.environ.get('TMP', tempfile.gettempdir()))
else:
    CACHE_DIR = '/tmp'

OUTPUT_FILE = os.path.join(CACHE_DIR, OUTPUT_FILENAME)

# NTP 服务器列表
NTP_SERVERS = [
    ("time.google.com", "Google"),
    ("time.cloudflare.com", "Cloudflare"),
    ("time.facebook.com", "Facebook"),
]

# 外网连通性测试站点
CONNECTIVITY_SITES = [
    ("https://www.google.com", "Google"),
    ("https://www.youtube.com", "YouTube"),
    ("https://www.facebook.com", "Facebook"),
    ("https://twitter.com", "Twitter"),
    ("https://www.instagram.com", "Instagram"),
]

# 国外 IP 查询站点
FOREIGN_IP_SITES = [
    ("https://api.ipify.org", "IPify"),
    ("https://checkip.amazonaws.com", "AWS"),
    ("https://icanhazip.com", "ICanHazIP"),
    ("https://ifconfig.me/ip", "IfConfig"),
]

# 国内 IP 查询站点
DOMESTIC_IP_SITES = [
    ("https://ip.cn", "IP.CN"),
    ("https://myip.ipip.net", "IPIP"),
    ("https://ip.sb", "IP.SB"),
]

# 国旗映射表
COUNTRY_FLAGS = {
    "CN": "🇨🇳", "US": "🇺🇸", "GB": "🇬🇧", "DE": "🇩🇪", "FR": "🇫🇷",
    "JP": "🇯🇵", "KR": "🇰🇷", "SG": "🇸🇬", "HK": "🇭🇰", "TW": "🇹🇼",
    "AU": "🇦🇺", "CA": "🇨🇦", "IN": "🇮🇳", "RU": "🇷🇺", "BR": "🇧🇷",
    "NL": "🇳🇱", "SE": "🇸🇪", "NO": "🇳🇴", "DK": "🇩🇰", "FI": "🇫🇮",
    "CH": "🇨🇭", "IT": "🇮🇹", "ES": "🇪🇸", "PL": "🇵🇱", "MX": "🇲🇽",
    "AR": "🇦🇷", "ZA": "🇿🇦", "AE": "🇦🇪", "TH": "🇹🇭", "VN": "🇻🇳",
    "ID": "🇮🇩", "MY": "🇲🇾", "PH": "🇵🇭", "NZ": "🇳🇿", "IE": "🇮🇪",
}

# IP 归属地缓存
IP_CACHE_FILE = os.path.join(CACHE_DIR, 'ip_country_cache.json')
CACHE_EXPIRE_SECONDS = 3600

# 全局编码变量
CURRENT_ENCODING = DEFAULT_ENCODING

# ==================== 核心函数 ====================

def query_ip_from_sites(site_list, timeout=REQUEST_TIMEOUT):
    """从指定的站点列表中并发查询 IP，返回最先成功的结果"""
    def query_site(url, site_name):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=timeout) as response:
                content = response.read().decode('utf-8').strip()
                ip_match = re.search(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})', content)
                if ip_match:
                    return (ip_match.group(1), site_name)
        except:
            pass
        return None

    with ThreadPoolExecutor(max_workers=len(site_list)) as executor:
        futures = [executor.submit(query_site, url, name) for url, name in site_list]
        for future in as_completed(futures):
            result = future.result()
            if result:
                return result
    return None

def load_cache():
    """安全加载 IP 缓存文件"""
    if not os.path.exists(IP_CACHE_FILE):
        return {}
    try:
        with open(IP_CACHE_FILE, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            if not content:
                return {}
            return json.loads(content)
    except:
        return {}

def save_cache(cache):
    """安全保存 IP 缓存文件"""
    try:
        with open(IP_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False)
        return True
    except:
        return False

def get_ip_country(ip_address, timeout=REQUEST_TIMEOUT):
    """使用 ip-api.com 查询 IP 所属国家，返回国旗 emoji"""
    cache = load_cache()

    if ip_address in cache:
        cached = cache[ip_address]
        if isinstance(cached, dict):
            ts = cached.get('timestamp', 0)
            cc = cached.get('country_code', '')
            if cc and time.time() - ts < CACHE_EXPIRE_SECONDS:
                return (cc, cached.get('flag', COUNTRY_FLAGS.get(cc, '🌐')))

    try:
        req = urllib.request.Request(
            f'http://ip-api.com/json/{ip_address}?fields=countryCode',
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode('utf-8'))
            if 'countryCode' in data and data['countryCode']:
                country_code = data['countryCode'].upper()
                flag = COUNTRY_FLAGS.get(country_code, '🌐')

                cache[ip_address] = {
                    'country_code': country_code,
                    'flag': flag,
                    'timestamp': time.time()
                }
                save_cache(cache)
                return (country_code, flag)
    except:
        pass

    return ('', '🌐')

def test_connectivity(timeout=REQUEST_TIMEOUT, max_workers=5):
    """并发测试外网连通性，返回最快响应的延迟"""
    def test_site(url, site_label):
        try:
            start_time = time.time()
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=timeout) as response:
                elapsed_ms = (time.time() - start_time) * 1000
                if response.status < 400:
                    return (elapsed_ms, site_label, url)
        except:
            pass
        return None

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_site = {
            executor.submit(test_site, url, label): (url, label)
            for url, label in CONNECTIVITY_SITES
        }
        for future in as_completed(future_to_site):
            result = future.result()
            if result:
                return result
    return None

def query_ntp(server_info, timeout=REQUEST_TIMEOUT):
    """查询单个 NTP 服务器的延迟"""
    server_name, server_label = server_info
    request = b'\x1b' + 47 * b'\0'

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        send_time = time.time()
        sock.sendto(request, (server_name, 123))
        data, addr = sock.recvfrom(1024)
        recv_time = time.time()
        sock.close()

        if len(data) >= 48:
            delay_ms = (recv_time - send_time) * 1000
            return (delay_ms, server_label, server_name)
    except:
        pass
    return None

def get_fastest_ntp(timeout=REQUEST_TIMEOUT, max_workers=8):
    """并发查询所有 NTP 服务器，返回最快响应的结果"""
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_server = {
            executor.submit(query_ntp, server, timeout): server
            for server in NTP_SERVERS
        }
        for future in as_completed(future_to_server):
            result = future.result()
            if result:
                return result
    return None

def parse_args():
    parser = argparse.ArgumentParser(description='IP and NTP status checker')
    parser.add_argument('--foreign', choices=['show', 'hide'],
                        default='show' if SHOW_FOREIGN else 'hide',
                        help='显示外网IP (默认: show)')
    parser.add_argument('--domestic', choices=['show', 'hide'],
                        default='show' if SHOW_DOMESTIC else 'hide',
                        help='显示国内IP (默认: hide)')
    parser.add_argument('--ntp', choices=['show', 'hide'],
                        default='show' if SHOW_NTP else 'hide',
                        help='显示NTP延迟 (默认: hide)')
    parser.add_argument('--connectivity', choices=['show', 'hide'],
                        default='show' if SHOW_CONNECTIVITY else 'hide',
                        help='显示外网连通性延迟 (默认: show)')
    parser.add_argument('--loop', '-l', action='store_true', default=False,
                        help='启用循环模式，持续运行 (默认: 关闭)')
    parser.add_argument('--interval', '-i', type=int, default=30,
                        help='循环模式下的间隔秒数 (默认: 30秒)')
    parser.add_argument('--encoding', '-e', type=str, default=DEFAULT_ENCODING,
                        help=f'输出编码（控制台和文件）(默认: {DEFAULT_ENCODING})')
    args = parser.parse_args()

    return {
        'show_foreign': args.foreign == 'show',
        'show_domestic': args.domestic == 'show',
        'show_ntp': args.ntp == 'show',
        'show_connectivity': args.connectivity == 'show',
        'loop': args.loop,
        'interval': args.interval,
        'encoding': args.encoding
    }

def run_once(show_foreign, show_domestic, show_ntp, show_connectivity):
    """执行一次检测并返回输出文本"""
    parts = []
    futures = {}

    with ThreadPoolExecutor(max_workers=10) as executor:
        if show_foreign:
            futures['foreign'] = executor.submit(query_ip_from_sites, FOREIGN_IP_SITES)
        if show_domestic:
            futures['domestic'] = executor.submit(query_ip_from_sites, DOMESTIC_IP_SITES)
        if show_ntp:
            futures['ntp'] = executor.submit(get_fastest_ntp)
        if show_connectivity:
            futures['connectivity'] = executor.submit(test_connectivity)

        foreign_result = domestic_result = ntp_result = connectivity_result = None

        start = time.time()
        while time.time() - start < TIMEOUT - 0.1 and futures:
            for name, future in list(futures.items()):
                if future.done():
                    try:
                        result = future.result(timeout=0.1)
                        if name == 'foreign' and result:
                            foreign_result = result
                        elif name == 'domestic' and result:
                            domestic_result = result
                        elif name == 'ntp' and result:
                            ntp_result = result
                        elif name == 'connectivity' and result:
                            connectivity_result = result
                    except:
                        pass
                    del futures[name]
            time.sleep(0.05)

    # 构建输出内容
    if foreign_result:
        ip_address, site_name = foreign_result
        try:
            _, flag = get_ip_country(ip_address, timeout=1.0)
        except:
            flag = "🌐"
        parts.append(f"{flag} {ip_address}")
    elif show_foreign:
        parts.append("❌ 外网")

    if show_domestic and domestic_result:
        ip_address, site_name = domestic_result
        parts.append(f"🇨🇳 {ip_address}")
    elif show_domestic:
        parts.append("❌ 国内")

    if show_ntp:
        if ntp_result:
            delay_ms, server_label, server_name = ntp_result
            delay_int = int(round(delay_ms))
            icon = "🚀" if delay_int < 100 else "📡" if delay_int < 250 else "⚠️" if delay_int < 500 else "🐌"
            parts.append(f"{icon} {delay_int}ms")
        else:
            parts.append("❌ NTP")

    if show_connectivity:
        if connectivity_result:
            delay_ms, site_label, url = connectivity_result
            delay_int = int(round(delay_ms))
            icon = "🚀" if delay_int < 100 else "📡" if delay_int < 250 else "⚠️" if delay_int < 500 else "🐌"
            parts.append(f"{icon} {delay_int}ms")
        else:
            parts.append("❌ 连通性")

    if parts:
        # 使用全角竖线分隔
        return "｜".join(parts)
    else:
        return "⚠️ 无数据"

def write_output(text, encoding):
    """输出到控制台和文件，使用指定的编码"""
    # 输出到控制台（使用指定的编码）
    try:
        # 尝试将文本编码为指定编码后输出到 stdout
        encoded_text = text.encode(encoding, errors='replace')
        sys.stdout.buffer.write(encoded_text)
        sys.stdout.buffer.write(b'\n')
        sys.stdout.buffer.flush()
    except Exception as e:
        # 如果出错，回退到系统默认编码
        try:
            print(text)
        except:
            # 最后的备选方案
            sys.stdout.buffer.write(text.encode('ascii', errors='replace') + b'\n')

    # 输出到文件（使用指定的编码）
    try:
        with open(OUTPUT_FILE, 'w', encoding=encoding, errors='replace') as f:
            f.write(text)
    except Exception as e:
        error_msg = f"警告：写入文件失败 {OUTPUT_FILE}: {e}"
        try:
            sys.stderr.buffer.write(error_msg.encode(encoding, errors='replace') + b'\n')
        except:
            print(error_msg, file=sys.stderr)

def run_with_loop(show_foreign, show_domestic, show_ntp, show_connectivity, interval, encoding):
    """循环模式：持续运行，按指定间隔更新"""
    # 设置全局编码
    global CURRENT_ENCODING
    CURRENT_ENCODING = encoding
    
    # 这些信息使用指定的编码输出
    info_lines = [
        f"启动循环模式，间隔 {interval} 秒",
        f"输出文件: {OUTPUT_FILE} (编码: {encoding})",
        "按 Ctrl+C 停止运行\n"
    ]
    for line in info_lines:
        write_output(line, encoding)

    while True:
        try:
            output_text = run_once(show_foreign, show_domestic, show_ntp, show_connectivity)
            write_output(output_text, encoding)
        except Exception as e:
            error_msg = f"运行出错: {e}"
            try:
                sys.stderr.buffer.write(error_msg.encode(encoding, errors='replace') + b'\n')
            except:
                print(error_msg, file=sys.stderr)
            write_output(f"⚠️ {error_msg}", encoding)

        time.sleep(interval)

def run_single(show_foreign, show_domestic, show_ntp, show_connectivity, encoding):
    """单次模式：执行一次后退出"""
    # 设置全局编码
    global CURRENT_ENCODING
    CURRENT_ENCODING = encoding
    
    # Windows 超时处理
    if sys.platform == 'win32':
        timer = threading.Timer(TIMEOUT + 1, lambda: (write_output("❌ 超时", encoding), os._exit(1)))
        timer.daemon = True
        timer.start()
    else:
        signal.alarm(TIMEOUT + 1)

    try:
        output_text = run_once(show_foreign, show_domestic, show_ntp, show_connectivity)
        write_output(output_text, encoding)
    finally:
        if sys.platform != 'win32':
            signal.alarm(0)

def main():
    args = parse_args()

    if args['loop']:
        # 循环模式
        run_with_loop(
            args['show_foreign'],
            args['show_domestic'],
            args['show_ntp'],
            args['show_connectivity'],
            args['interval'],
            args['encoding']
        )
    else:
        # 单次模式
        run_single(
            args['show_foreign'],
            args['show_domestic'],
            args['show_ntp'],
            args['show_connectivity'],
            args['encoding']
        )

if __name__ == "__main__":
    main()