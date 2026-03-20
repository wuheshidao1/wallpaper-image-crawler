import os
import re
import json
import platform
import requests
import threading
import urllib3
from pathlib import Path
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
from concurrent.futures import ThreadPoolExecutor, as_completed

# ========== 跨系统环境适配（核心修复） ==========
# 1. 抑制Mac Tk弃用警告、urllib3 SSL警告
os.environ["TK_SILENCE_DEPRECATION"] = "1"
urllib3.disable_warnings(urllib3.exceptions.NotOpenSSLWarning)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 2. 系统判断 + 跨系统配置
SYSTEM = platform.system()
CONFIG = {
    "STEAM_API_KEY": "",  # 替换为你的Steam API Key
    "SUPPORT_EXTS": {".jpg", ".jpeg", ".png", ".bmp", ".mp4", ".webm"},
    "WORKSHOP_API": "https://api.steampowered.com/IPublishedFileService/QueryFiles/v1/",
    "DOWNLOAD_THREADS": 3,
    "TIMEOUT": 15,
    "headers": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36" if SYSTEM == "Windows" 
        else "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    },
    "DEFAULT_OUTPUT": str(Path.home() / "Desktop" / "WallpaperCrawler")
}

# ------------------------ 工具函数（跨系统兼容） ------------------------
def select_directory(title: str = "选择目录") -> str:
    """跨系统目录选择"""
    path = filedialog.askdirectory(title=title)
    return path if path else ""

def show_message(title: str, msg: str, type_: str = "info"):
    """统一消息提示"""
    if type_ == "error":
        messagebox.showerror(title, msg)
    elif type_ == "warning":
        messagebox.showwarning(title, msg)
    else:
        messagebox.showinfo(title, msg)

def sanitize_filename(filename: str) -> str:
    """跨系统文件名清理"""
    return re.sub(r'[\\/:*?"<>|]', "_", filename)

# ------------------------ 爬虫类（核心功能） ------------------------
class SteamWorkshopCrawler:
    def __init__(self, api_key: str):
        self.api_key = api_key.strip()
        self.headers = CONFIG["headers"]

    def search_wallpapers(self, keyword: str, page_size: int = 10) -> list:
        """搜索Wallpaper Engine创意工坊壁纸"""
        if not self.api_key:
            show_message("错误", "Steam API Key不能为空！", "error")
            return []

        params = {
            "key": self.api_key,
            "format": "json",
            "appid": 431960,
            "search_text": keyword.strip(),
            "numperpage": min(page_size, 20),
            "return_details": True,
            "return_previews": True,
            "sort_column": "published",
            "sort_dir": "desc",
            "language": "schinese",
            "include_recent_votes_only": False,
            "cursor": "*"
        }

        try:
            response = requests.get(
                CONFIG["WORKSHOP_API"],
                params=params,
                headers=self.headers,
                timeout=CONFIG["TIMEOUT"],
                verify=False
            )
            response.raise_for_status()
            data = response.json()

            wallpapers = data.get("response", {}).get("publishedfiledetails", [])
            valid_wallpapers = [wp for wp in wallpapers if wp.get("result") == 1]
            return valid_wallpapers

        except requests.exceptions.Timeout:
            show_message("错误", "API调用超时！请检查网络/加速器", "error")
        except requests.exceptions.ConnectionError:
            show_message("错误", "网络连接失败！", "error")
        except requests.exceptions.HTTPError as e:
            show_message("错误", f"API错误：{e.response.status_code}", "error")
        except Exception as e:
            show_message("错误", f"搜索失败：{str(e)}", "error")
        
        return []

# ------------------------ 下载器类（多线程） ------------------------
class WallpaperDownloader:
    def __init__(self, output_path: str):
        self.output_path = Path(output_path)
        self.output_path.mkdir(parents=True, exist_ok=True)
        self.downloaded_ids = set()
        self.is_running = True

    def load_downloaded_ids(self):
        """加载已下载ID，避免重复"""
        for file in self.output_path.glob("*.*"):
            match = re.match(r"(\d+)_", file.name)
            if match:
                self.downloaded_ids.add(match.group(1))
        return len(self.downloaded_ids)

    def download_single(self, wallpaper_info: dict) -> bool:
        """下载单个壁纸预览图"""
        if not self.is_running:
            return False

        wallpaper_id = wallpaper_info["publishedfileid"]
        title = sanitize_filename(wallpaper_info["title"])
        
        if wallpaper_id in self.downloaded_ids:
            return False

        preview_url = wallpaper_info.get("preview_url")
        if not preview_url:
            return False

        try:
            response = requests.get(
                preview_url,
                stream=True,
                timeout=CONFIG["TIMEOUT"],
                headers=CONFIG["headers"],
                verify=False
            )
            response.raise_for_status()

            file_ext = Path(preview_url).suffix or ".png"
            filename = f"{wallpaper_id}_{title}{file_ext}"
            file_path = self.output_path / filename

            with open(file_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if not self.is_running:
                        return False
                    f.write(chunk)

            self.downloaded_ids.add(wallpaper_id)
            return True

        except Exception:
            return False

    def batch_download(self, wallpapers: list, log_callback=None) -> tuple:
        """批量下载（多线程）"""
        success = 0
        fail = 0

        if log_callback:
            log_callback("【提示】Steam API仅能下载预览图，原壁纸需订阅后本地提取！")
            log_callback(f"开始下载 {len(wallpapers)} 个壁纸预览图...")

        with ThreadPoolExecutor(max_workers=CONFIG["DOWNLOAD_THREADS"]) as executor:
            futures = {executor.submit(self.download_single, wp): wp for wp in wallpapers}
            
            for future in as_completed(futures):
                if not self.is_running:
                    break
                wallpaper = futures[future]
                wallpaper_id = wallpaper["publishedfileid"]
                title = sanitize_filename(wallpaper["title"])
                
                try:
                    if future.result():
                        success += 1
                        log_callback(f"✅ [{wallpaper_id}] {title} - 下载完成")
                    else:
                        fail += 1
                        log_callback(f"❌ [{wallpaper_id}] {title} - 下载失败/已跳过")
                except Exception as e:
                    fail += 1
                    log_callback(f"❌ [{wallpaper_id}] {title} - 异常：{str(e)[:20]}")

        return success, fail

    def stop(self):
        """停止下载"""
        self.is_running = False

# ------------------------ UI类（双系统样式兼容） ------------------------
class WallpaperCrawlerUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Wallpaper Engine 壁纸爬取工具")
        self.root.geometry("800x600")
        self.root.resizable(False, False)
        self.root.configure(bg="#f5f5f5")

        self.is_running = False
        self.downloader = None

        # 跨系统样式配置
        self.style = ttk.Style()
        self._setup_style()

        self._create_widgets()

    def _setup_style(self):
        """适配Windows/Mac的UI样式"""
        # Mac优先用aqua主题，Windows用clam主题
        self.style.theme_use("aqua" if SYSTEM == "Darwin" else "clam")
        
        # 按钮样式（跨系统兼容）
        btn_bg = "#4a86e8" if SYSTEM == "Windows" else "#007aff"
        btn_active_bg = "#3a76d8" if SYSTEM == "Windows" else "#0066cc"
        
        self.style.configure("Primary.TButton", 
                            background=btn_bg, 
                            foreground="white",
                            font=("Microsoft YaHei" if SYSTEM == "Windows" else "Arial", 10, "bold"),
                            padding=8)
        self.style.map("Primary.TButton",
                      background=[("active", btn_active_bg)])
        
        # 通用样式
        self.style.configure("Title.TLabel",
                            font=("Microsoft YaHei" if SYSTEM == "Windows" else "Arial", 12, "bold"),
                            foreground="#333333")
        self.style.configure("Normal.TLabel",
                            font=("Microsoft YaHei" if SYSTEM == "Windows" else "Arial", 10),
                            foreground="#666666")
        self.style.configure("Custom.TEntry",
                            font=("Microsoft YaHei" if SYSTEM == "Windows" else "Arial", 10),
                            padding=5)
        self.style.configure("Custom.Horizontal.TProgressbar",
                            troughcolor="#e0e0e0",
                            background=btn_bg)

    def _create_widgets(self):
        """创建UI组件（跨系统布局）"""
        # 标题区域
        title_frame = ttk.Frame(self.root)
        title_frame.pack(fill="x", padx=20, pady=15)
        title_label = ttk.Label(title_frame, text="Wallpaper Engine 壁纸爬取工具", style="Title.TLabel")
        title_label.pack(pady=10)

        # 配置区域
        config_frame = ttk.Frame(self.root)
        config_frame.pack(fill="x", padx=20, pady=5)
        
        # API Key
        api_frame = ttk.Frame(config_frame)
        api_frame.pack(fill="x", padx=15, pady=10)
        ttk.Label(api_frame, text="Steam API Key：", style="Normal.TLabel").grid(row=0, column=0, sticky="w", padx=5)
        self.api_key_var = tk.StringVar(value=CONFIG["STEAM_API_KEY"])
        api_entry = ttk.Entry(api_frame, textvariable=self.api_key_var, style="Custom.TEntry", width=40)
        api_entry.grid(row=0, column=1, padx=5)

        # 搜索配置
        search_frame = ttk.Frame(config_frame)
        search_frame.pack(fill="x", padx=15, pady=10)
        ttk.Label(search_frame, text="搜索关键词：", style="Normal.TLabel").grid(row=0, column=0, sticky="w", padx=5)
        self.keyword_var = tk.StringVar(value="原神")
        keyword_entry = ttk.Entry(search_frame, textvariable=self.keyword_var, style="Custom.TEntry", width=30)
        keyword_entry.grid(row=0, column=1, padx=5)
        
        ttk.Label(search_frame, text="爬取数量：", style="Normal.TLabel").grid(row=0, column=2, sticky="w", padx=15)
        self.page_size_var = tk.IntVar(value=10)
        page_size_entry = ttk.Entry(search_frame, textvariable=self.page_size_var, style="Custom.TEntry", width=10)
        page_size_entry.grid(row=0, column=3, padx=5)

        # 输出路径
        path_frame = ttk.Frame(config_frame)
        path_frame.pack(fill="x", padx=15, pady=10)
        ttk.Label(path_frame, text="输出路径：", style="Normal.TLabel").grid(row=0, column=0, sticky="w", padx=5)
        self.output_path_var = tk.StringVar(value=CONFIG["DEFAULT_OUTPUT"])
        path_entry = ttk.Entry(path_frame, textvariable=self.output_path_var, style="Custom.TEntry", width=40)
        path_entry.grid(row=0, column=1, padx=5)
        ttk.Button(path_frame, text="选择", command=self._select_output_path).grid(row=0, column=2, padx=5)

        # 操作按钮
        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(fill="x", padx=20, pady=15)
        self.start_btn = ttk.Button(btn_frame, text="开始爬取下载", style="Primary.TButton", command=self._start_crawl)
        self.start_btn.pack(side="left", padx=10)
        self.stop_btn = ttk.Button(btn_frame, text="停止", state="disabled", command=self._stop_crawl)
        self.stop_btn.pack(side="left", padx=10)

        # 进度条
        self.progress_bar = ttk.Progressbar(self.root, style="Custom.Horizontal.TProgressbar", mode="indeterminate")
        self.progress_bar.pack(fill="x", padx=20, pady=5)

        # 日志区域
        log_frame = ttk.Frame(self.root)
        log_frame.pack(fill="both", expand=True, padx=20, pady=5)
        ttk.Label(log_frame, text="爬取日志", style="Normal.TLabel").pack(anchor="w", padx=15, pady=10)
        self.log_text = scrolledtext.ScrolledText(log_frame, width=90, height=20, font=("Consolas" if SYSTEM == "Windows" else "Monaco", 9))
        self.log_text.pack(fill="both", expand=True, padx=15, pady=10)
        self.log_text.configure(bg="#fafafa", fg="#333333", borderwidth=0)

    def _select_output_path(self):
        """选择输出路径"""
        path = select_directory("选择壁纸保存目录")
        if path:
            self.output_path_var.set(path)

    def _log(self, msg):
        """日志输出"""
        self.log_text.insert(tk.END, f"[{threading.current_thread().name}] {msg}\n")
        self.log_text.see(tk.END)
        self.root.update_idletasks()

    def _start_crawl(self):
        """开始爬取"""
        if self.is_running:
            messagebox.showwarning("提示", "爬取任务已在运行中！")
            return

        api_key = self.api_key_var.get().strip()
        keyword = self.keyword_var.get().strip()
        page_size = self.page_size_var.get()
        output_path = self.output_path_var.get().strip()

        if not api_key:
            messagebox.showerror("错误", "请填写Steam API Key！")
            return
        if not keyword:
            messagebox.showerror("错误", "请填写搜索关键词！")
            return
        if page_size <= 0 or page_size > 20:
            messagebox.showerror("错误", "爬取数量请设置为1-20之间！")
            return
        if not output_path:
            messagebox.showerror("错误", "请选择输出路径！")
            return

        self.is_running = True
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.progress_bar.start(10)
        self.log_text.delete(1.0, tk.END)
        self._log("===== 开始爬取任务 =====")

        def crawl_task():
            try:
                crawler = SteamWorkshopCrawler(api_key)
                self.downloader = WallpaperDownloader(output_path)
                
                downloaded_count = self.downloader.load_downloaded_ids()
                self._log(f"已加载 {downloaded_count} 个已下载壁纸ID")

                self._log(f"正在搜索关键词：{keyword}")
                wallpapers = crawler.search_wallpapers(keyword, page_size)
                
                if not wallpapers:
                    self._log("未搜索到符合条件的壁纸")
                    return

                self._log(f"搜索到 {len(wallpapers)} 个有效壁纸")

                success, fail = self.downloader.batch_download(wallpapers, self._log)
                
                self._log(f"\n===== 爬取完成 ======")
                self._log(f"成功下载：{success} 个")
                self._log(f"失败/跳过：{fail} 个")
                self._log(f"文件保存路径：{output_path}")

            except Exception as e:
                self._log(f"任务异常：{str(e)}")
            finally:
                self.is_running = False
                self.start_btn.config(state="normal")
                self.stop_btn.config(state="disabled")
                self.progress_bar.stop()

        threading.Thread(target=crawl_task, name="CrawlThread", daemon=True).start()

    def _stop_crawl(self):
        """停止爬取"""
        if self.downloader:
            self.downloader.stop()
        self.is_running = False
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.progress_bar.stop()
        self._log("已手动停止爬取任务")

# ------------------------ 程序入口 ------------------------
def main():
    root = tk.Tk()
    app = WallpaperCrawlerUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()