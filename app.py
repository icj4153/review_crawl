from __future__ import annotations

import queue
import re
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from crawler import NaverReviewCrawler, Review, is_supported_naver_store_url
from excel_writer import save_reviews_xlsx


APP_TITLE = "네이버 스토어 리뷰 수집"


def safe_filename(value: str, default: str = "file") -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", value).strip().strip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or default


class ReviewCollectorApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("720x560")
        self.minsize(660, 520)

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.reviews: list[Review] = []
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None

        self._build_ui()
        self._poll_log_queue()
        self.log("프로그램 사용 준비가 되었습니다.")

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        url_frame = ttk.LabelFrame(self, text="수집 URL")
        url_frame.grid(row=0, column=0, sticky="nsew", padx=14, pady=(12, 8))
        url_frame.columnconfigure(0, weight=1)

        self.url_text = tk.Text(url_frame, height=4, wrap="none")
        self.url_text.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        url_scroll_y = ttk.Scrollbar(url_frame, orient="vertical", command=self.url_text.yview)
        url_scroll_y.grid(row=0, column=1, sticky="ns", pady=10)
        url_scroll_x = ttk.Scrollbar(url_frame, orient="horizontal", command=self.url_text.xview)
        url_scroll_x.grid(row=1, column=0, sticky="ew", padx=10)
        self.url_text.configure(yscrollcommand=url_scroll_y.set, xscrollcommand=url_scroll_x.set)

        options = ttk.Frame(self)
        options.grid(row=1, column=0, sticky="ew", padx=14, pady=4)
        options.columnconfigure(3, weight=1)

        ttk.Label(options, text="최대 리뷰수(상품당):").grid(row=0, column=0, sticky="w")
        self.max_reviews_var = tk.IntVar(value=80)
        self.max_reviews_spin = ttk.Spinbox(
            options,
            from_=1,
            to=10000,
            textvariable=self.max_reviews_var,
            width=8,
        )
        self.max_reviews_spin.grid(row=0, column=1, sticky="w", padx=(8, 18))

        self.status_var = tk.StringVar(value="대기")
        ttk.Label(options, textvariable=self.status_var).grid(row=0, column=2, sticky="w")

        buttons = ttk.Frame(self)
        buttons.grid(row=2, column=0, sticky="ew", padx=14, pady=(8, 12))
        buttons.columnconfigure(0, weight=1)

        self.save_button = ttk.Button(buttons, text="저장", command=self.save_reviews, state="disabled")
        self.save_button.grid(row=0, column=1, padx=(0, 8))

        self.run_button = ttk.Button(buttons, text="실행", command=self.start_collect)
        self.run_button.grid(row=0, column=2, padx=8)

        self.stop_button = ttk.Button(buttons, text="중지", command=self.stop_collect, state="disabled")
        self.stop_button.grid(row=0, column=3, padx=(8, 0))

        log_frame = ttk.Frame(self)
        log_frame.grid(row=3, column=0, sticky="nsew", padx=14, pady=(0, 14))
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self.log_text = tk.Text(
            log_frame,
            bg="black",
            fg="#00ffff",
            insertbackground="#00ffff",
            wrap="word",
            state="disabled",
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=log_scroll.set)

    def log(self, message: str) -> None:
        self.log_queue.put(message)

    def _poll_log_queue(self) -> None:
        while True:
            try:
                message = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_text.configure(state="normal")
            self.log_text.insert("end", message + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        self.after(100, self._poll_log_queue)

    def start_collect(self) -> None:
        if self.worker and self.worker.is_alive():
            return

        url = self.url_text.get("1.0", "end").strip()
        if not url:
            messagebox.showwarning(APP_TITLE, "수집할 URL을 입력하세요.")
            return
        if not is_supported_naver_store_url(url):
            messagebox.showwarning(APP_TITLE, "네이버 스마트스토어/브랜드스토어 상품 URL을 입력하세요.")
            return

        try:
            max_reviews = int(self.max_reviews_var.get())
            if max_reviews <= 0:
                raise ValueError
        except Exception:
            messagebox.showwarning(APP_TITLE, "최대 리뷰수는 1 이상의 숫자여야 합니다.")
            return

        self.reviews = []
        self.stop_event.clear()
        self._set_running(True)
        self.log("수집을 시작합니다.")

        self.worker = threading.Thread(
            target=self._collect_worker,
            args=(url, max_reviews),
            daemon=True,
        )
        self.worker.start()

    def _collect_worker(self, url: str, max_reviews: int) -> None:
        try:
            crawler = NaverReviewCrawler(log=self.log)
            reviews = crawler.collect(url, max_reviews, self.stop_event)
            self.reviews = reviews
            self.log(f"수집 완료: {len(reviews)}건")
        except Exception as exc:
            self.log(f"오류: {exc}")
        finally:
            self.after(0, lambda: self._set_running(False))

    def _set_running(self, running: bool) -> None:
        self.status_var.set("수집 중" if running else f"대기 - 수집 {len(self.reviews)}건")
        self.run_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")
        self.save_button.configure(state="normal" if self.reviews and not running else "disabled")

    def stop_collect(self) -> None:
        self.stop_event.set()
        self.stop_button.configure(state="disabled")
        self.log("중지를 요청했습니다. 현재 처리 중인 단계가 끝나면 멈춥니다.")

    def save_reviews(self) -> None:
        if not self.reviews:
            messagebox.showinfo(APP_TITLE, "저장할 리뷰가 없습니다.")
            return

        default_name = self._default_save_name()
        output_path = filedialog.asksaveasfilename(
            title="엑셀 저장",
            defaultextension=".xlsx",
            initialfile=default_name,
            filetypes=[("Excel Workbook", "*.xlsx")],
        )
        if not output_path:
            return

        xlsx_path = Path(output_path)

        try:
            save_reviews_xlsx(self.reviews, xlsx_path)
            self.log(f"엑셀 저장 완료: {xlsx_path}")
            messagebox.showinfo(APP_TITLE, "저장이 완료되었습니다.")
        except Exception as exc:
            self.log(f"저장 오류: {exc}")
            messagebox.showerror(APP_TITLE, f"저장 중 오류가 발생했습니다.\n{exc}")

    def _default_save_name(self) -> str:
        product_name = self.reviews[0].product_name if self.reviews else "naver_reviews"
        product_name = re.sub(r"\[[^\]]+\]", "", product_name).strip()
        product_name = safe_filename(product_name[:40], "naver_reviews")
        return f"{product_name}.xlsx"


if __name__ == "__main__":
    app = ReviewCollectorApp()
    app.mainloop()
