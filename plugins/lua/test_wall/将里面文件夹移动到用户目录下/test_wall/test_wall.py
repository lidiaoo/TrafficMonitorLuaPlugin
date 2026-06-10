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
import subprocess
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
REQUEST_MODE = "hybrid"      # 请求模式: urllib/curl/hybrid（混合模式）
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
MAX_NTP_NAME_LEN = max(len(name) for _, name in NTP_SERVERS)

#尽量使用http 因为https比http慢50ms以上
# 外网连通性测试站点
CONNECTIVITY_SITES = [
    ("http://www.google.com/generate_204", "Google"),
    ("http://www.youtube.com/generate_204", "YouTube"),
    ("http://www.facebook.com/generate_204", "Facebook"),
    ("http://twitter.com/generate_204", "Twitter"),

]
#不行的
#("https://cp.cloudflare.com/generate_204", "Cloudflare"),
#("http://detectportal.firefox.com/success.txt", "Firefox"),
#("https://instagram.com/favicon.ico", "Instagram"),
MAX_CONN_NAME_LEN = max(len(name) for _, name in CONNECTIVITY_SITES)

# 国外 IP 查询站点
FOREIGN_IP_SITES = [
    ("https://api.ipify.org", "IPify"),
    ("https://checkip.amazonaws.com", "AWS"),
    ("https://icanhazip.com", "ICanHazIP"),
    ("https://ifconfig.me/ip", "IfConfig"),
]
MAX_FOREIGN_NAME_LEN = max(len(name) for _, name in FOREIGN_IP_SITES)

# 国内 IP 查询站点
DOMESTIC_IP_SITES = [
    ("https://ip.cn", "IP.CN"),
    ("https://myip.ipip.net", "IPIP"),
    ("https://ip.sb", "IP.SB"),
]
MAX_DOMESTIC_NAME_LEN = max(len(name) for _, name in DOMESTIC_IP_SITES)

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

# ==================== 工具函数 ====================

def get_curl_command():
    """获取跨平台的 curl 命令路径"""
    if sys.platform == 'win32':
        # Windows 上优先使用 curl.exe（避免 PowerShell 的 Invoke-WebRequest 别名）
        # 检查常见的 curl 安装路径
        curl_paths = [
            'curl.exe',
            r'C:\Windows\System32\curl.exe',
            r'C:\Program Files\curl\bin\curl.exe',
            r'C:\Program Files (x86)\curl\bin\curl.exe',
        ]
        for path in curl_paths:
            try:
                result = subprocess.run([path, '--version'], capture_output=True, timeout=2)
                if result.returncode == 0:
                    return path
            except:
                pass
        # 如果找不到 curl.exe，返回 None（会自动回退到 Python）
        return None
    else:
        # Linux/macOS 上直接使用 curl
        return 'curl'

def curl_request(url, timeout=REQUEST_TIMEOUT):
    """Use curl to make HTTP request, return latency (ms) or None"""
    curl_cmd = get_curl_command()
    if not curl_cmd:
        return None

    try:
        start_time = time.time()
        # Use appropriate null device for platform
        null_device = 'NUL' if sys.platform == 'win32' else '/dev/null'

        cmd = [
            curl_cmd,
            '-o', null_device,
            '-s',
            '-w', '%{time_total}',
            '-m', str(timeout),
            '-A', 'Mozilla/5.0',
            url
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 1)
        elapsed_ms = (time.time() - start_time) * 1000

        if result.returncode == 0 and result.stdout:
            curl_time = float(result.stdout.strip()) * 1000
            return (curl_time, elapsed_ms)
        else:
            return None
    except Exception as e:
        return None

# ==================== 核心函数 ====================

def query_ip_from_sites(site_list, timeout=REQUEST_TIMEOUT, mode="hybrid"):
    """从指定的站点列表中并发查询 IP，返回最先成功的结果"""
    def test_latency_urllib(url, site_name):
        """纯 urllib 测试延迟"""
        try:
            start_time = time.time()
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=timeout) as response:
                elapsed_ms = (time.time() - start_time) * 1000
                return (site_name, elapsed_ms, 'urllib')
        except:
            pass
        return None

    def test_latency_curl(url, site_name):
        """纯 curl 测试延迟（返回 curl 内部测量的真实 HTTP 时间）"""
        try:
            result = curl_request(url, timeout)
            if result:
                curl_time, real_time = result
                return (site_name, curl_time, 'curl')
        except:
            pass
        return None

    # 检查系统是否支持 curl
    curl_available = get_curl_command() is not None

    # 第一步：并发测试延迟，找出最快的站点和方式
    fastest_result = None

    if mode == "hybrid" and curl_available:
        # 混合模式：curl 和 urllib 同时跑，全部完成后按延迟值比较取最小
        with ThreadPoolExecutor(max_workers=len(site_list) * 2) as executor:
            future_to_task = {}
            for url, name in site_list:
                future_to_task[executor.submit(test_latency_urllib, url, name)] = (url, name, 'urllib')
                future_to_task[executor.submit(test_latency_curl, url, name)] = (url, name, 'curl')

            # 收集所有成功结果，按延迟值取最小
            best_result = None
            best_latency = float('inf')
            for future in as_completed(future_to_task):
                result = future.result()
                if result:
                    site_name, latency, method = result
                    if latency < best_latency:
                        best_latency = latency
                        best_result = result
            fastest_result = best_result

    elif mode == "urllib":
        with ThreadPoolExecutor(max_workers=len(site_list)) as executor:
            future_to_task = {}
            for url, name in site_list:
                future_to_task[executor.submit(test_latency_urllib, url, name)] = (url, name, 'urllib')
            for future in as_completed(future_to_task):
                result = future.result()
                if result:
                    site_name, latency, method = result
                    url, name, expected_method = future_to_task[future]
                    if method != expected_method:
                        print(f"Warning: Method mismatch! Expected {expected_method}, got {method}", file=sys.stderr)
                    fastest_result = result
                    break

    elif mode == "curl" and curl_available:
        with ThreadPoolExecutor(max_workers=len(site_list)) as executor:
            future_to_task = {}
            for url, name in site_list:
                future_to_task[executor.submit(test_latency_curl, url, name)] = (url, name, 'curl')
            for future in as_completed(future_to_task):
                result = future.result()
                if result:
                    site_name, latency, method = result
                    url, name, expected_method = future_to_task[future]
                    if method != expected_method:
                        print(f"Warning: Method mismatch! Expected {expected_method}, got {method}", file=sys.stderr)
                    fastest_result = result
                    break

    if not fastest_result:
        return None

    # 第二步：用最快的方式获取 IP（不加入任务池，直接请求）
    site_name, latency, method = fastest_result

    # 找到对应的 URL
    target_url = None
    for url, name in site_list:
        if name == site_name:
            target_url = url
            break

    if not target_url:
        return None

    # 获取 IP 内容
    try:
        req = urllib.request.Request(target_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            content = response.read().decode('utf-8').strip()
            ip_match = re.search(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})', content)
            if ip_match:
                return (ip_match.group(1), site_name, latency, method)
    except:
        pass

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

def test_connectivity(timeout=REQUEST_TIMEOUT, max_workers=5, mode="hybrid"):
    """并发测试外网连通性，返回最快响应的延迟"""
    def test_site_python(url, site_label):
        try:
            start_time = time.time()
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=timeout) as response:
                elapsed_ms = (time.time() - start_time) * 1000
                if response.status < 400:
                    return (elapsed_ms, site_label, url, 'urllib')
        except:
            pass
        return None

    def test_site_curl(url, site_label):
        try:
            result = curl_request(url, timeout)
            if result:
                curl_time, real_time = result
                return (curl_time, site_label, url, 'curl')
        except:
            pass
        return None

    # 检查系统是否支持 curl
    curl_available = get_curl_command() is not None

    # 根据模式决定任务池大小
    if mode == "hybrid" and curl_available:
        pool_size = max_workers * 2
    else:
        pool_size = max_workers

    with ThreadPoolExecutor(max_workers=pool_size) as executor:
        future_to_site = {}

        # 根据模式添加任务（带标识）
        if mode == "urllib" or mode == "hybrid":
            for url, label in CONNECTIVITY_SITES:
                future_to_site[executor.submit(test_site_python, url, label)] = (url, label, 'urllib')

        if (mode == "curl" or mode == "hybrid") and curl_available:
            for url, label in CONNECTIVITY_SITES:
                future_to_site[executor.submit(test_site_curl, url, label)] = (url, label, 'curl')

        if mode == "hybrid" and curl_available:
            # 混合模式：全部完成后按延迟值比较取最小
            best_result = None
            best_latency = float('inf')
            for future in as_completed(future_to_site):
                result = future.result()
                if result:
                    elapsed_ms, site_label, url, method = result
                    if elapsed_ms < best_latency:
                        best_latency = elapsed_ms
                        best_result = result
            return best_result
        else:
            # 单模式：返回最先成功的
            for future in as_completed(future_to_site):
                result = future.result()
                if result:
                    _, _, _, method = result
                    url, label, expected_method = future_to_site[future]
                    if method != expected_method:
                        print(f"Warning: Method mismatch! Expected {expected_method}, got {method}", file=sys.stderr)
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
                        help='循环模式下的间隔秒数 (默认: 30)')
    parser.add_argument('--encoding', '-e', type=str, default=DEFAULT_ENCODING,
                        help=f'输出编码（控制台和文件）(默认: {DEFAULT_ENCODING})')
    parser.add_argument('--mode', choices=['urllib', 'curl', 'hybrid'],
                        default=REQUEST_MODE,
                        help='请求模式：urllib/curl/hybrid（混合模式）(默认: hybrid)')
    parser.add_argument('--show-method', action='store_true', default=False,
                        help='显示请求方式（curl/urllib）(默认: 不显示)')
    parser.add_argument('--check-curl', action='store_true', default=False,
                        help='检查系统中是否安装了 curl')
    parser.add_argument('--ip-latency', choices=['show', 'hide'],
                        default='hide',
                        help='显示/隐藏IP来源的延迟 (默认: hide)')
    parser.add_argument('--ip-source', choices=['show', 'hide'],
                        default='hide',
                        help='显示/隐藏IP来源站点名称 (默认: hide)')
    parser.add_argument('--latency-source', choices=['show', 'hide'],
                        default='show',
                        help='显示/隐藏延迟测试来源站点名称 (默认: show)')
    parser.add_argument('--ip-display', choices=['flag', 'ip', 'both'],
                        default='flag',
                        help='IP显示方式: flag(仅国旗图标)/ip(仅IP)/both(混合显示) (默认: flag)')
    args = parser.parse_args()

    return {
        'show_foreign': args.foreign == 'show',
        'show_domestic': args.domestic == 'show',
        'show_ntp': args.ntp == 'show',
        'show_connectivity': args.connectivity == 'show',
        'loop': args.loop,
        'interval': args.interval,
        'encoding': args.encoding,
        'mode': args.mode,
        'show_method': args.show_method,
        'check_curl': args.check_curl,
        'show_ip_source': args.ip_source == 'show',
        'show_latency_source': args.latency_source == 'show',
        'show_ip_latency': args.ip_latency == 'show',
        'ip_display': args.ip_display,
    }

def run_once(show_foreign, show_domestic, show_ntp, show_connectivity, show_ip_source=False, show_latency_source=True, show_ip_latency=False, mode="hybrid", show_method=False, ip_display="flag"):
    """执行一次检测并返回输出文本"""
    parts = []
    futures = {}

    with ThreadPoolExecutor(max_workers=10) as executor:
        if show_foreign:
            futures['foreign'] = executor.submit(query_ip_from_sites, FOREIGN_IP_SITES, REQUEST_TIMEOUT, mode)
        if show_domestic:
            futures['domestic'] = executor.submit(query_ip_from_sites, DOMESTIC_IP_SITES, REQUEST_TIMEOUT, mode)
        if show_ntp:
            futures['ntp'] = executor.submit(get_fastest_ntp)
        if show_connectivity:
            futures['connectivity'] = executor.submit(test_connectivity, REQUEST_TIMEOUT, 5, mode)

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

    # Build output content
    if foreign_result:
        ip_address, site_name, latency_ms, method = foreign_result
        try:
            _, flag = get_ip_country(ip_address, timeout=1.0)
        except:
            flag = "🌐"

        # 根据 ip_display 参数决定显示内容
        if ip_display == "flag":
            ip_str = flag
        elif ip_display == "ip":
            ip_str = ip_address
        else:  # both
            ip_str = f"{flag} {ip_address}"
        ip_str = f"{ip_str} "

        if show_ip_latency:
            delay_int = int(round(latency_ms))
            icon = "🚀" if delay_int < 100 else "📡" if delay_int < 250 else "⚠️" if delay_int < 500 else "🐌"
            method_str = f"{method:7} " if show_method else "" # curl→"curl  "，urllib→"urllib"
            site_str = site_name.ljust(MAX_FOREIGN_NAME_LEN)
            site_str = f"{site_str} "
            delay_str = f"{icon} {delay_int}ms"
            delay_str = delay_str.rjust(8)
            if show_ip_source:
                parts.append(f"{ip_str}{method_str}{site_name}{delay_str}")
            else:
                parts.append(f"{ip_str}{method_str}{delay_str}")
        else:
            parts.append(f"{ip_str}")
    elif show_foreign:
        parts.append("❌ 外网")

    if show_domestic and domestic_result:
        ip_address, site_name, latency_ms, method = domestic_result

        # 根据 ip_display 参数决定显示内容
        if ip_display == "flag":
            ip_str = "🇨🇳"
        elif ip_display == "ip":
            ip_str = ip_address
        else:  # both
            ip_str = f"🇨🇳 {ip_address}"
        ip_str = f"{ip_str} "

        if show_ip_latency:
            delay_int = int(round(latency_ms))
            icon = "🚀" if delay_int < 100 else "📡" if delay_int < 250 else "⚠️" if delay_int < 500 else "🐌"
            method_str = f"{method:7} " if show_method else "" # curl→"curl  "，urllib→"urllib"
            site_str = site_name.ljust(MAX_DOMESTIC_NAME_LEN)
            site_str = f"{site_str} "
            delay_str = f"{icon} {delay_int}ms"
            delay_str = delay_str.rjust(8)
            if show_ip_source:
                parts.append(f"{ip_str}{method_str}{site_str}{delay_str}")
            else:
                parts.append(f"{ip_str}{method_str}{delay_str}")
        else:
            parts.append(f"{ip_str}")
    elif show_domestic:
        parts.append("❌ 国内")

    if show_ntp:
        if ntp_result:
            delay_ms, server_label, server_name = ntp_result
            delay_int = int(round(delay_ms))
            icon = "🚀" if delay_int < 100 else "📡" if delay_int < 250 else "⚠️" if delay_int < 500 else "🐌"
            server_str = server_name.ljust(MAX_NTP_NAME_LEN)
            server_str = f"{server_str} "
            delay_str = f"{icon} {delay_int}ms"
            delay_str = delay_str.rjust(8)
            if show_latency_source:
                parts.append(f"{server_str}{delay_str}")
            else:
                parts.append(f"{delay_str}")
        else:
            parts.append("❌ NTP")

    if show_connectivity:
        if connectivity_result:
            delay_ms, site_label, url, method = connectivity_result
            delay_int = int(round(delay_ms))
            icon = "🚀" if delay_int < 100 else "📡" if delay_int < 250 else "⚠️" if delay_int < 500 else "🐌"
            method_str = f"{method:7} " if show_method else "" # curl→"curl  "，urllib→"urllib"
            site_str = site_label.ljust(MAX_CONN_NAME_LEN)
            delay_str = f"{icon} {delay_int}ms"
            delay_str = delay_str.rjust(8)
            if show_latency_source:
                parts.append(f"{method_str}{site_str}{delay_str}")
            else:
                parts.append(f"{method_str}{delay_str}")
        else:
            parts.append("❌ 连通性")

    if parts:
        # Use pipe separator
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

def run_with_loop(show_foreign, show_domestic, show_ntp, show_connectivity, interval, encoding, show_ip_source=False, show_latency_source=True, show_ip_latency=False, mode="hybrid", show_method=False, ip_display="flag"):
    """Loop mode: run continuously with specified interval"""
    global CURRENT_ENCODING
    CURRENT_ENCODING = encoding

    info_lines = [
        f"Starting loop mode with interval {interval} seconds",
        f"Request mode: {mode}",
        f"Output file: {OUTPUT_FILE} (encoding: {encoding})",
        "Press Ctrl+C to stop\n"
    ]
    for line in info_lines:
        write_output(line, encoding)

    while True:
        try:
            output_text = run_once(show_foreign, show_domestic, show_ntp, show_connectivity, show_ip_source, show_latency_source, show_ip_latency, mode, show_method, ip_display)
            write_output(output_text, encoding)
        except Exception as e:
            error_msg = f"Error: {e}"
            try:
                sys.stderr.buffer.write(error_msg.encode(encoding, errors='replace') + b'\n')
            except:
                print(error_msg, file=sys.stderr)
            write_output(f"ERROR {error_msg}", encoding)

        time.sleep(interval)

def run_single(show_foreign, show_domestic, show_ntp, show_connectivity, encoding, show_ip_source=False, show_latency_source=True, show_ip_latency=False, mode="hybrid", show_method=False, ip_display="flag"):
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
        output_text = run_once(show_foreign, show_domestic, show_ntp, show_connectivity, show_ip_source, show_latency_source, show_ip_latency, mode, show_method, ip_display)
        write_output(output_text, encoding)
    finally:
        if sys.platform != 'win32':
            signal.alarm(0)

def check_curl_availability():
    """检查系统中是否安装了 curl"""
    curl_cmd = get_curl_command()
    if curl_cmd:
        try:
            sys.stdout.buffer.write(f"OK curl available: {curl_cmd}\n".encode('utf-8'))
            result = subprocess.run([curl_cmd, '--version'], capture_output=True, text=True, timeout=2)
            if result.stdout:
                lines = result.stdout.split('\n')
                if lines:
                    sys.stdout.buffer.write(f"   Version: {lines[0]}\n".encode('utf-8'))
        except:
            print(f"OK curl available: {curl_cmd}")
    else:
        try:
            sys.stdout.buffer.write("NO curl not available, using Python urllib\n".encode('utf-8'))
        except:
            print("NO curl not available, using Python urllib")
    try:
        sys.stdout.buffer.write(b"\n")
    except:
        print()

def main():
    args = parse_args()

    # 检查 curl 可用性
    if args['check_curl']:
        check_curl_availability()
        return

    if args['loop']:
        # 循环模式
        run_with_loop(
            args['show_foreign'],
            args['show_domestic'],
            args['show_ntp'],
            args['show_connectivity'],
            args['interval'],
            args['encoding'],
            args['show_ip_source'],
            args['show_latency_source'],
            args['show_ip_latency'],
            args['mode'],
            args['show_method'],
            args['ip_display']
        )
    else:
        # 单次模式
        run_single(
            args['show_foreign'],
            args['show_domestic'],
            args['show_ntp'],
            args['show_connectivity'],
            args['encoding'],
            args['show_ip_source'],
            args['show_latency_source'],
            args['show_ip_latency'],
            args['mode'],
            args['show_method'],
            args['ip_display']
        )

if __name__ == "__main__":
    main()
