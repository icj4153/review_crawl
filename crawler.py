from __future__ import annotations

import hashlib
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import urlopen

try:
    from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover - surfaced as a runtime dependency error.
    PlaywrightTimeoutError = Exception
    Page = Any
    sync_playwright = None


LogCallback = Callable[[str], None]
SUPPORTED_NAVER_STORE_HOSTS = {"smartstore.naver.com", "brand.naver.com"}


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip().lstrip("\ufeff")
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception:
        return


load_env_file(Path(__file__).resolve().parent / ".env")


@dataclass(slots=True)
class Review:
    review_no: str
    product_no: str
    product_name: str
    writer: str
    created_at: str
    rating: str
    content: str
    option: str = ""
    image_urls: list[str] = field(default_factory=list)


def resolve_edge_path() -> str | None:
    candidates = [
        os.path.join(os.environ.get("ProgramFiles", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
        "/usr/bin/microsoft-edge",
        "/usr/bin/microsoft-edge-stable",
        "/opt/microsoft/msedge/msedge",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def default_profile_dir() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    return root / "NaverSmartStoreReviewCollector" / "edge_profile"


def extract_product_no(url: str) -> str:
    match = re.search(r"/products/(\d+)", url)
    if match:
        return match.group(1)

    query = parse_qs(urlparse(url).query)
    for key in ("productNo", "product_no", "n_mall_pid"):
        value = query.get(key)
        if value:
            return value[0]
    return ""


def _normalized_host(url: str) -> str:
    parsed = urlparse(url.strip())
    return parsed.netloc.lower().split("@")[-1].split(":")[0]


def is_supported_naver_store_url(url: str) -> bool:
    stripped = url.strip()
    parsed = urlparse(stripped)
    host = _normalized_host(stripped)
    return host in SUPPORTED_NAVER_STORE_HOSTS and bool(re.search(r"^/[^/?#]+/products/\d+", parsed.path))


def normalize_naver_store_product_url(url: str) -> str:
    stripped = url.strip()
    parsed = urlparse(stripped)
    host = _normalized_host(stripped)
    match = re.search(r"^/([^/?#]+)/products/(\d+)", parsed.path)
    if host in SUPPORTED_NAVER_STORE_HOSTS and match:
        return f"https://{host}/{match.group(1)}/products/{match.group(2)}"
    return stripped


def normalize_smartstore_product_url(url: str) -> str:
    return normalize_naver_store_product_url(url)


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_cdp(port: int, timeout_seconds: float = 20) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as response:
                return response.status == 200
        except Exception:
            time.sleep(0.25)
    return False


def normalize_date(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        number = int(value)
        try:
            if number > 10_000_000_000:
                number = number / 1000
            return datetime.fromtimestamp(number).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return str(value)

    text = str(value).strip()
    if not text:
        return ""
    text = text.replace("T", " ").replace("Z", "")
    text = re.sub(r"\.\d+", "", text)
    text = re.sub(r"([+-]\d{2}:?\d{2})$", "", text).strip()

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y.%m.%d. %H:%M:%S", "%Y.%m.%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    return text


def _first_value(data: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return ""


def _option_text_from_value(value: Any) -> str:
    parts: list[str] = []

    def visit(node: Any) -> None:
        if node in (None, ""):
            return
        if isinstance(node, (str, int, float)):
            text = str(node).strip()
            if text:
                parts.append(text)
            return
        if isinstance(node, list):
            for item in node:
                visit(item)
            return
        if isinstance(node, dict):
            label = str(
                _first_value(node, ("name", "optionName", "optionTitle", "optionTypeName", "groupName", "label"))
            ).strip()
            value_text = str(
                _first_value(node, ("value", "optionValue", "optionValueName", "valueName", "text", "contents"))
            ).strip()
            if label and value_text and label != value_text:
                parts.append(f"{label}: {value_text}")
                return
            if value_text:
                parts.append(value_text)
                return
            if label:
                parts.append(label)
                return
            for key, item in node.items():
                if "option" in key.lower():
                    visit(item)

    visit(value)
    return " / ".join(dict.fromkeys(parts))


def _collect_image_urls(value: Any) -> list[str]:
    urls: list[str] = []

    def visit(node: Any) -> None:
        if isinstance(node, str):
            if node.startswith("http") and (
                "phinf.pstatic.net" in node
                or "shop-phinf.pstatic.net" in node
                or re.search(r"\.(jpe?g|png|gif|webp)(\?|$)", node, re.I)
            ):
                urls.append(node)
            return
        if isinstance(node, list):
            for item in node:
                visit(item)
            return
        if isinstance(node, dict):
            for key, item in node.items():
                if isinstance(item, str) and key.lower() in {
                    "url",
                    "imageurl",
                    "attachurl",
                    "thumbnailurl",
                    "originurl",
                }:
                    visit(item)
                elif "image" in key.lower() or "photo" in key.lower() or "attach" in key.lower():
                    visit(item)

    visit(value)
    return list(dict.fromkeys(urls))


def _looks_like_review_dict(data: dict[str, Any]) -> bool:
    lower_keys = {key.lower() for key in data}
    has_id = bool(lower_keys & {"reviewno", "id", "reviewid", "reviewseq", "mallreviewno"})
    has_text = bool(
        lower_keys
        & {
            "contents",
            "content",
            "reviewcontents",
            "reviewcontent",
            "reviewtext",
            "comment",
        }
    )
    has_rating = bool(lower_keys & {"score", "rating", "starpoint", "reviewscore", "purchasepoint"})
    return has_id and (has_text or has_rating)


def _extract_review_from_dict(data: dict[str, Any], fallback_product_no: str, fallback_product_name: str) -> Review | None:
    if not _looks_like_review_dict(data):
        return None

    review_no = str(
        _first_value(data, ("reviewNo", "reviewId", "reviewSeq", "mallReviewNo", "id", "no"))
    ).strip()
    if not review_no:
        return None

    content = str(
        _first_value(
            data,
            (
                "contents",
                "content",
                "reviewContents",
                "reviewContent",
                "reviewText",
                "comment",
            ),
        )
    ).strip()

    writer = str(
        _first_value(
            data,
            (
                "writerMemberId",
                "maskedWriterMemberId",
                "writerId",
                "writer",
                "nickname",
                "writerNickname",
                "memberId",
                "userId",
            ),
        )
    ).strip()

    created_at = normalize_date(
        _first_value(
            data,
            (
                "createDate",
                "createdDate",
                "createdAt",
                "registerYmdt",
                "registerDateTime",
                "registerDate",
                "regDate",
                "writeDate",
                "reviewDate",
                "modDate",
            ),
        )
    )

    rating = str(
        _first_value(data, ("score", "rating", "starPoint", "reviewScore", "purchasePoint"))
    ).strip()
    option = _option_text_from_value(
        _first_value(
            data,
            (
                "optionName",
                "productOptionName",
                "productOption",
                "optionContents",
                "optionContent",
                "optionText",
                "option",
                "options",
                "selectedOptions",
                "purchaseOption",
                "purchaseOptions",
                "orderOption",
                "orderOptions",
                "itemOptionName",
                "optionValue",
                "optionValueName",
            ),
        )
    )

    product_no = str(_first_value(data, ("productNo", "originProductNo", "itemNo")) or fallback_product_no).strip()
    product_name = str(_first_value(data, ("productName", "productTitle", "itemName")) or fallback_product_name).strip()

    return Review(
        review_no=review_no,
        product_no=product_no,
        product_name=product_name,
        writer=writer,
        created_at=created_at,
        rating=rating,
        content=content,
        option=option,
        image_urls=_collect_image_urls(data),
    )


def extract_reviews_from_json(payload: Any, fallback_product_no: str, fallback_product_name: str) -> list[Review]:
    reviews: list[Review] = []
    seen: set[str] = set()

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            review = _extract_review_from_dict(node, fallback_product_no, fallback_product_name)
            if review and review.review_no not in seen:
                seen.add(review.review_no)
                reviews.append(review)
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(payload)
    return reviews


class NaverReviewCrawler:
    def __init__(
        self,
        *,
        profile_dir: Path | None = None,
        edge_path: str | None = None,
        browser_mode: str = "edge",
        headless: bool = False,
        screenshot_path: Path | None = None,
        screenshot_interval_seconds: float = 1.0,
        log: LogCallback | None = None,
    ) -> None:
        self.profile_dir = profile_dir or default_profile_dir()
        self.edge_path = edge_path or resolve_edge_path()
        self.browser_mode = browser_mode
        self.headless = headless
        self.screenshot_path = screenshot_path
        self.screenshot_interval_seconds = screenshot_interval_seconds
        self.log = log or (lambda message: None)
        self._reviews: dict[str, Review] = {}
        self._product_name = ""
        self._product_no = ""
        self._lock = threading.Lock()
        self._last_screenshot_at = 0.0
        self._screenshot_error_logged = False

    @property
    def reviews(self) -> list[Review]:
        with self._lock:
            return list(self._reviews.values())

    def _log(self, message: str) -> None:
        self.log(message)

    def _capture_screenshot(self, page: Page, *, force: bool = False) -> None:
        if not self.screenshot_path:
            return
        now = time.monotonic()
        if not force and now - self._last_screenshot_at < self.screenshot_interval_seconds:
            return
        try:
            self.screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(
                path=str(self.screenshot_path),
                type="jpeg",
                quality=74,
                full_page=False,
                timeout=3_000,
            )
            self._last_screenshot_at = now
        except Exception as exc:
            if not self._screenshot_error_logged:
                self._log(f"브라우저 화면 캡처 실패: {exc}")
                self._screenshot_error_logged = True

    def _add_reviews(self, reviews: list[Review], max_reviews: int) -> int:
        added = 0
        with self._lock:
            for review in reviews:
                if len(self._reviews) >= max_reviews:
                    break
                if review.review_no in self._reviews:
                    continue
                self._reviews[review.review_no] = review
                added += 1
        return added

    def collect(self, url: str, max_reviews: int, stop_event: threading.Event) -> list[Review]:
        if sync_playwright is None:
            raise RuntimeError("playwright가 설치되어 있지 않습니다. 'python -m pip install -r requirements.txt'를 먼저 실행하세요.")
        if self.browser_mode == "edge" and not self.edge_path:
            raise RuntimeError("Microsoft Edge 실행 파일을 찾을 수 없습니다.")

        self._reviews.clear()
        self._last_screenshot_at = 0.0
        self._screenshot_error_logged = False
        if self.screenshot_path:
            try:
                self.screenshot_path.unlink(missing_ok=True)
            except Exception:
                pass
        target_url = normalize_naver_store_product_url(url)
        if target_url != url.strip():
            self._log(f"입력 URL을 기본 상품 URL로 정리했습니다: {target_url}")

        self._product_no = extract_product_no(target_url)

        with sync_playwright() as playwright:
            if self.browser_mode == "chromium":
                self._collect_with_chromium(playwright, target_url, max_reviews, stop_event)
            else:
                self._collect_with_edge(playwright, target_url, max_reviews, stop_event)

        return self.reviews[:max_reviews]

    def _collect_from_page(self, page: Page, target_url: str, max_reviews: int, stop_event: threading.Event) -> None:
        self._login_before_collect_if_configured(page, target_url)
        self._log("상품 페이지로 바로 이동 중...")
        page.goto(target_url, wait_until="domcontentloaded", timeout=60_000)
        self._capture_screenshot(page, force=True)
        self._recover_login_redirect(page, target_url)
        self._retry_access_limited_page(page, target_url)
        self._dismiss_popups(page)
        self._product_name = self._read_product_name(page)
        if self._product_name:
            self._log(f"상품명: {self._product_name}")
        self._capture_screenshot(page, force=True)
        self._open_full_review_list_from_screen(page, stop_event)
        self._capture_screenshot(page, force=True)
        self._crawl_visible_screen_reviews(page, max_reviews, stop_event)
        if len(self.reviews) < max_reviews:
            self._log("화면에서 더 이상 새 리뷰가 보이지 않아 현재 수집분으로 종료합니다.")

    def _collect_with_edge(self, playwright: Any, target_url: str, max_reviews: int, stop_event: threading.Event) -> None:
        if self.edge_path and not self.edge_path.lower().endswith(".exe"):
            self._collect_with_linux_edge(playwright, target_url, max_reviews, stop_event)
            return

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        runtime_profile_dir = Path(tempfile.mkdtemp(prefix="edge_run_", dir=str(self.profile_dir)))

        self._log(f"Edge 실행: {self.edge_path}")
        self._log(f"전용 프로필: {runtime_profile_dir}")

        port = find_free_port()
        initial_url = f"https://nid.naver.com/nidlogin.login?url={quote(target_url, safe='')}"
        process = subprocess.Popen(
            [
                self.edge_path,
                f"--remote-debugging-port={port}",
                f"--user-data-dir={runtime_profile_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                initial_url,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        browser = None
        try:
            if not wait_for_cdp(port):
                raise RuntimeError("Edge 디버그 연결 포트가 열리지 않았습니다.")

            browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            context = browser.contexts[0]
            page = context.pages[0] if context.pages else context.new_page()
            try:
                page.wait_for_load_state("domcontentloaded", timeout=15_000)
            except Exception:
                pass
            self._collect_from_page(page, target_url, max_reviews, stop_event)
        finally:
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except Exception:
                    process.kill()
            shutil.rmtree(runtime_profile_dir, ignore_errors=True)

    def _collect_with_linux_edge(self, playwright: Any, target_url: str, max_reviews: int, stop_event: threading.Event) -> None:
        self._log(f"Microsoft Edge 실행: {self.edge_path}")
        browser = playwright.chromium.launch(
            executable_path=self.edge_path,
            headless=self.headless,
            args=[
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        try:
            context = browser.new_context(
                locale="ko-KR",
                viewport={"width": 1280, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0"
                ),
            )
            page = context.new_page()
            self._collect_from_page(page, target_url, max_reviews, stop_event)
        finally:
            try:
                browser.close()
            except Exception:
                pass

    def _collect_with_chromium(self, playwright: Any, target_url: str, max_reviews: int, stop_event: threading.Event) -> None:
        mode_label = "화면 보기" if not self.headless else "headless"
        self._log(f"Chromium {mode_label} 브라우저 실행 중...")
        browser = playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )
        try:
            context = browser.new_context(
                locale="ko-KR",
                viewport={"width": 1280, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()
            self._collect_from_page(page, target_url, max_reviews, stop_event)
        finally:
            try:
                browser.close()
            except Exception:
                pass

    def _recover_login_redirect(self, page: Page, target_url: str) -> None:
        if "nid.naver.com" not in page.url:
            return

        self._log("로그인 페이지로 이동되어 네이버 자동 로그인을 시도합니다.")
        if self._login_to_naver(page, target_url):
            self._log("네이버 로그인 완료. 상품 URL로 다시 접속합니다.")
            page.goto(target_url, wait_until="domcontentloaded", timeout=60_000)
            if "nid.naver.com" not in page.url:
                return

        self._log("자동 로그인 후에도 로그인 페이지가 표시되어 기본 상품 URL로 다시 접속합니다.")
        page.goto(target_url, wait_until="domcontentloaded", timeout=60_000)
        if "nid.naver.com" in page.url:
            raise RuntimeError(
                "네이버 로그인 페이지에서 벗어나지 못했습니다. 보안확인, 2단계 인증, 캡차 또는 계정 보호 화면이 표시되었을 수 있습니다."
            )

        self._log("기본 상품 URL 접속 확인. 로그인 없이 수집을 계속합니다.")

    def _login_before_collect_if_configured(self, page: Page, target_url: str) -> None:
        if not self._naver_credentials():
            return
        login_url = f"https://nid.naver.com/nidlogin.login?mode=form&url={quote(target_url, safe='')}"
        self._log("네이버 로그인 정보가 설정되어 상품 접속 전에 먼저 로그인합니다.")
        page.goto(login_url, wait_until="domcontentloaded", timeout=60_000)
        self._capture_screenshot(page, force=True)

        if "nid.naver.com" not in page.url:
            self._log("이미 네이버 로그인 상태입니다.")
            return

        if not self._login_to_naver(page, target_url):
            raise RuntimeError(
                "네이버 자동 로그인을 완료하지 못했습니다. 브라우저 화면에서 추가 인증 또는 보안확인 화면을 확인하세요."
            )

    def _naver_credentials(self) -> tuple[str, str] | None:
        login_id = os.getenv("NAVER_LOGIN_ID", "").strip()
        password = os.getenv("NAVER_LOGIN_PASSWORD", "")
        if login_id and password:
            return login_id, password
        return None

    def _login_to_naver(self, page: Page, target_url: str) -> bool:
        credentials = self._naver_credentials()
        if not credentials:
            self._log("네이버 로그인 정보가 설정되어 있지 않아 자동 로그인을 건너뜁니다.")
            return False

        login_id, password = credentials
        try:
            page.locator("#id").wait_for(state="visible", timeout=15_000)
            id_input = page.locator("#id")
            pw_input = page.locator("#pw")

            id_input.click(timeout=5_000)
            page.keyboard.press("Control+A")
            page.keyboard.type(login_id, delay=35)
            pw_input.click(timeout=5_000)
            page.keyboard.press("Control+A")
            page.keyboard.type(password, delay=35)

            clicked = False
            for selector in ("#log\\.login", "button[type='submit']", ".btn_login"):
                try:
                    button = page.locator(selector).first
                    if button.count():
                        button.click(timeout=5_000)
                        clicked = True
                        break
                except Exception:
                    continue
            if not clicked:
                page.keyboard.press("Enter")

            try:
                page.wait_for_load_state("domcontentloaded", timeout=20_000)
            except Exception:
                pass
            page.wait_for_timeout(2_000)

            if "nid.naver.com" not in page.url:
                return True

            body_text = ""
            try:
                body_text = page.locator("body").inner_text(timeout=2_000)
            except Exception:
                pass
            if any(keyword in body_text for keyword in ("2단계", "OTP", "보안", "캡차", "자동입력", "인증")):
                self._log("네이버 추가 인증 또는 보안확인 화면이 표시되었습니다.")
            else:
                self._log("네이버 자동 로그인 후에도 로그인 페이지가 유지됩니다.")
            self._capture_screenshot(page, force=True)
            return False
        except Exception as exc:
            self._log(f"네이버 자동 로그인 실패: {exc}")
            self._capture_screenshot(page, force=True)
            return False

    def _is_access_limited_page(self, page: Page) -> bool:
        try:
            text = page.locator("body").inner_text(timeout=1_500)
        except Exception:
            return False
        return (
            "동시에 접속하는 이용자 수가 많거나" in text
            or "인터넷 네트워크 상태가 불안정하여 접속이 불가합니다" in text
            or "잠시 후 다시 접속해 주시기 바랍니다" in text
        )

    def _retry_access_limited_page(self, page: Page, target_url: str) -> None:
        for attempt in range(1, 4):
            if not self._is_access_limited_page(page):
                return
            self._capture_screenshot(page, force=True)
            self._log(f"네이버 접속 제한 화면이 표시되었습니다. 주소창 URL 재입력 방식으로 재시도 {attempt}/3")
            page.wait_for_timeout(3_000)
            page.goto(target_url, wait_until="domcontentloaded", timeout=60_000)
            self._capture_screenshot(page, force=True)

        if self._is_access_limited_page(page):
            raise RuntimeError(
                "네이버 접속 제한 화면이 계속 표시됩니다. NAS 서버 IP 또는 현재 네트워크에서 네이버 접속이 제한된 상태입니다. "
                "브라우저 화면 보기 패널에서 차단 화면을 확인할 수 있습니다."
            )

    def _dismiss_popups(self, page: Page) -> None:
        labels = ["닫기", "오늘 하루 보지 않기", "확인"]
        for label in labels:
            try:
                locator = page.get_by_text(label, exact=True)
                if locator.count():
                    locator.first.click(timeout=1_000)
            except Exception:
                pass

    def _read_product_name(self, page: Page) -> str:
        selectors = [
            "h3",
            "h2",
            "[class*=ProductInfo] h3",
            "[class*=product_info] h3",
            "meta[property='og:title']",
        ]
        for selector in selectors:
            try:
                if selector.startswith("meta"):
                    value = page.locator(selector).first.get_attribute("content", timeout=1_000)
                else:
                    value = page.locator(selector).first.inner_text(timeout=1_000)
                if value and value.strip():
                    return re.sub(r"\s+", " ", value).strip()
            except Exception:
                continue
        return ""

    def _open_full_review_list_from_screen(self, page: Page, stop_event: threading.Event) -> None:
        self._log("리뷰 탭 버튼을 누르는 중...")
        tab_clicked = False
        for _ in range(20):
            if stop_event.is_set():
                return
            clicked = page.evaluate(
                """() => {
                    const textOf = (el) => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                    const items = Array.from(document.querySelectorAll(
                        "button[data-shp-inventory='tab'][data-shp-area='tab.select'], button.oOJYvFw3Y5"
                    )).filter((el) => /^리뷰\\s*[\\d,]*/.test(textOf(el)));
                    items.sort((a, b) => textOf(a).length - textOf(b).length);
                    const target = items[0];
                    if (!target) return false;
                    target.scrollIntoView({ block: 'center' });
                    target.click();
                    return true;
                }"""
            )
            if clicked:
                tab_clicked = True
                page.wait_for_timeout(1_800)
                self._capture_screenshot(page, force=True)
                break
            page.evaluate("window.scrollBy(0, Math.floor(window.innerHeight * 0.45))")
            page.wait_for_timeout(800)
            self._capture_screenshot(page)

        if not tab_clicked:
            self._log("리뷰 탭 버튼을 찾지 못했습니다. 현재 화면 기준으로 계속 진행합니다.")

        self._log("리뷰 전체보기 버튼을 누르는 중...")
        for _ in range(24):
            if stop_event.is_set():
                return
            clicked = page.evaluate(
                """() => {
                    const textOf = (el) => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                    const items = Array.from(document.querySelectorAll(
                        "button[data-shp-area='sprvrpre.more'], button.YlvE2juz4n"
                    )).filter((el) => textOf(el).includes('리뷰 전체보기'));
                    items.sort((a, b) => textOf(a).length - textOf(b).length);
                    const target = items[0];
                    if (!target) return false;
                    target.scrollIntoView({ block: 'center' });
                    target.click();
                    return true;
                }"""
            )
            if clicked:
                page.wait_for_timeout(2_500)
                self._capture_screenshot(page, force=True)
                self._log("리뷰 전체보기 화면을 열었습니다.")
                return
            page.evaluate("window.scrollBy(0, Math.floor(window.innerHeight * 0.55))")
            page.wait_for_timeout(800)
            self._capture_screenshot(page)

        self._log("리뷰 전체보기 버튼을 찾지 못해 현재 상품 화면에 보이는 리뷰만 수집합니다.")

    def _crawl_visible_screen_reviews(self, page: Page, max_reviews: int, stop_event: threading.Event) -> None:
        stagnant_turns = 0
        previous_count = -1
        turn = 0
        max_turns = max(1200, min(max_reviews * 4, 30000))
        recovery_logs: set[int] = set()

        while turn < max_turns:
            turn += 1
            if stop_event.is_set():
                self._log("중지 요청을 확인했습니다.")
                return
            if len(self.reviews) >= max_reviews:
                self._log("요청한 리뷰 수에 도달했습니다.")
                return

            reviews = self._extract_reviews_from_screen_text(page)
            added = self._add_reviews(reviews, max_reviews)
            if added:
                self._log(f"화면에서 리뷰 {added}건 수집됨 (총 {len(self.reviews)}건)")
            self._capture_screenshot(page)

            current_count = len(self.reviews)
            if current_count == previous_count:
                stagnant_turns += 1
            else:
                stagnant_turns = 0
            previous_count = current_count

            if stagnant_turns in {12, 30, 50} and stagnant_turns not in recovery_logs:
                recovery_logs.add(stagnant_turns)
                self._log("새 리뷰가 잠시 보이지 않아 스크롤 회복을 시도합니다.")
                if self._recover_review_screen_scroll(page):
                    page.wait_for_timeout(600)
                    self._capture_screenshot(page)
                    continue

            if stagnant_turns >= 80:
                if self._recover_review_screen_scroll(page):
                    stagnant_turns = 50
                    page.wait_for_timeout(900)
                    self._capture_screenshot(page)
                    continue
                return

            moved = self._scroll_review_screen(page, aggressive=stagnant_turns >= 8)
            if not moved:
                stagnant_turns += 2
            page.wait_for_timeout(220 if stagnant_turns < 8 else 420)
            self._capture_screenshot(page)

        self._log(f"스크롤 안전 한도({max_turns}회)에 도달해 현재 수집분으로 종료합니다.")

    def _scroll_review_screen(self, page: Page, *, aggressive: bool = False) -> bool:
        before_state = self._review_scroll_state(page)
        result = page.evaluate(
            """(aggressive) => {
                const textOf = (el) => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                const isVisible = (el) => {
                    const rect = el.getBoundingClientRect();
                    return rect.width >= 320 && rect.height >= 180 && rect.bottom > 0 && rect.top < window.innerHeight;
                };
                const scrollables = Array.from(document.querySelectorAll('*'))
                    .filter((el) => {
                        const style = getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        return (style.overflowY === 'auto' || style.overflowY === 'scroll')
                            && el.scrollHeight > el.clientHeight + 80
                            && rect.width >= 360
                            && rect.height >= 220;
                    })
                    .map((el) => {
                        const rect = el.getBoundingClientRect();
                        const remain = el.scrollHeight - el.clientHeight - el.scrollTop;
                        const text = textOf(el).slice(0, 3000);
                        let score = remain;
                        if (isVisible(el)) score += 100000;
                        if (text.includes('평점') || text.includes('리뷰가 도움이 되었나요?')) score += 200000;
                        if (text.includes('작성일') || text.includes('최신순')) score += 60000;
                        if (rect.left >= -20 && rect.right <= window.innerWidth + 20) score += 20000;
                        return { el, score, remain };
                    })
                    .sort((a, b) => {
                        return b.score - a.score;
                    });
                const targets = scrollables.map((item) => item.el);
                const root = document.scrollingElement || document.documentElement;
                if (root && !targets.includes(root)) targets.push(root);
                const amount = Math.max(
                    aggressive ? 950 : 420,
                    Math.floor(window.innerHeight * (aggressive ? 0.92 : 0.68))
                );
                let moved = false;
                const tried = [];
                for (const target of targets.slice(0, aggressive ? 5 : 3)) {
                    const before = target.scrollTop || 0;
                    target.scrollBy(0, amount);
                    target.dispatchEvent(new WheelEvent('wheel', { deltaY: amount, bubbles: true, cancelable: true }));
                    const after = target.scrollTop || 0;
                    tried.push({ before, after, height: target.scrollHeight, client: target.clientHeight });
                    if (after > before) {
                        moved = true;
                        break;
                    }
                }
                window.dispatchEvent(new WheelEvent('wheel', { deltaY: amount, bubbles: true, cancelable: true }));
                return { moved, tried };
            }""",
            aggressive,
        )
        if result and result.get("moved"):
            return True

        try:
            page.mouse.wheel(0, 1400 if aggressive else 850)
            page.wait_for_timeout(120)
        except Exception:
            pass
        after_state = self._review_scroll_state(page)
        return bool(after_state and after_state != before_state)

    def _review_scroll_state(self, page: Page) -> str:
        try:
            return page.evaluate(
                """() => {
                    const root = document.scrollingElement || document.documentElement;
                    const scrollables = Array.from(document.querySelectorAll('*'))
                        .filter((el) => {
                            const style = getComputedStyle(el);
                            return (style.overflowY === 'auto' || style.overflowY === 'scroll')
                                && el.scrollHeight > el.clientHeight + 80;
                        })
                        .sort((a, b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight))
                        .slice(0, 8);
                    const parts = [];
                    if (root) parts.push(`root:${Math.round(root.scrollTop)}:${root.scrollHeight}:${root.clientHeight}`);
                    for (const el of scrollables) {
                        const rect = el.getBoundingClientRect();
                        parts.push(`${Math.round(el.scrollTop)}:${el.scrollHeight}:${el.clientHeight}:${Math.round(rect.top)}:${Math.round(rect.bottom)}`);
                    }
                    return parts.join('|');
                }"""
            )
        except Exception:
            return ""

    def _recover_review_screen_scroll(self, page: Page) -> bool:
        clicked = False
        try:
            clicked = bool(
                page.evaluate(
                    """() => {
                        const textOf = (el) => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                        const isVisible = (el) => {
                            const rect = el.getBoundingClientRect();
                            const style = getComputedStyle(el);
                            return style.visibility !== 'hidden'
                                && style.display !== 'none'
                                && rect.width > 0
                                && rect.height > 0
                                && rect.bottom > 0
                                && rect.top < window.innerHeight;
                        };
                        const candidates = Array.from(document.querySelectorAll('button, a'))
                            .filter((el) => {
                                if (!isVisible(el)) return false;
                                const text = textOf(el);
                                if (!text || text.includes('리뷰 전체보기')) return false;
                                return /^(더보기|다음|다음 페이지|페이지 다음)$/.test(text)
                                    || text === '>';
                            });
                        const target = candidates[0];
                        if (!target) return false;
                        target.scrollIntoView({ block: 'center' });
                        target.click();
                        return true;
                    }"""
                )
            )
        except Exception:
            clicked = False
        if clicked:
            self._log("정체 구간에서 더보기/다음 버튼을 눌렀습니다.")
            page.wait_for_timeout(1_200)
            return True

        moved = False
        for _ in range(4):
            if self._scroll_review_screen(page, aggressive=True):
                moved = True
            page.wait_for_timeout(220)
        return moved

    def _extract_reviews_from_screen_text(self, page: Page) -> list[Review]:
        body_text = page.evaluate("() => document.body ? document.body.innerText : ''")
        image_groups = self._extract_review_image_groups_from_screen(page)
        image_groups_by_key: dict[tuple[str, str], list[list[str]]] = {}
        for group in image_groups:
            key = (group["writer"], group["date"])
            image_groups_by_key.setdefault(key, []).append(group["image_urls"])

        lines = [line.strip() for line in body_text.splitlines() if line.strip()]
        starts: list[tuple[int, str, str, str]] = []
        writer_date = re.compile(r"(?P<writer>[A-Za-z0-9_.-]+\*{2,})\s*(?P<date>\d{2}\.\d{2}\.\d{2}\.)")

        for index, line in enumerate(lines):
            rating = ""
            target = ""
            if re.fullmatch(r"(?:★\s*)?[1-5](?:한달사용)?", line) and index + 1 < len(lines):
                rating = line[0]
                if not rating.isdigit():
                    rating = re.search(r"[1-5]", line).group(0)
                target = lines[index + 1]
            elif line == "평점" and index + 2 < len(lines) and re.fullmatch(r"(?:★\s*)?[1-5](?:한달사용)?", lines[index + 1]):
                rating = re.search(r"[1-5]", lines[index + 1]).group(0)
                target = lines[index + 2]
            else:
                inline = re.match(r"^(?:평점\s*)?(?P<rating>[1-5])(?:한달사용)?\s*(?P<rest>[A-Za-z0-9_.-]+\*{2,}\s*\d{2}\.\d{2}\.\d{2}\.)", line)
                if inline:
                    rating = inline.group("rating")
                    target = inline.group("rest")

            match = writer_date.search(target)
            if match:
                starts.append((index, rating, match.group("writer"), match.group("date")))
                continue

            match = writer_date.search(line)
            if match:
                previous = lines[index - 1] if index > 0 else ""
                rating_match = re.search(r"[1-5]", previous)
                starts.append((index, rating_match.group(0) if rating_match else rating, match.group("writer"), match.group("date")))

        reviews: list[Review] = []
        for position, (start, rating, writer, date_text) in enumerate(starts):
            next_start = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
            raw_chunk = lines[start + 1 : next_start]
            if raw_chunk and writer_date.search(raw_chunk[0]):
                raw_chunk = raw_chunk[1:]
            if raw_chunk and raw_chunk[0] == "평점":
                raw_chunk = raw_chunk[2:]
            raw_chunk = self._trim_screen_review_chunk(raw_chunk)

            photo_count = 0
            option_lines: list[str] = []
            cleaned_lines: list[str] = []
            skip_next_photo_number = False
            for line in raw_chunk:
                if line == "사진/비디오 수":
                    skip_next_photo_number = True
                    continue
                if skip_next_photo_number and re.fullmatch(r"\d+", line):
                    photo_count = int(line)
                    skip_next_photo_number = False
                    continue
                if line in {"리뷰 전체보기", "Q&A", "Q&A 전체보기", "스토어 PICK", "도움말"}:
                    continue
                if line in {"신고", "리뷰가 도움이 되었나요?"} or line.startswith("도움돼요"):
                    continue
                if re.fullmatch(r"(?:[1-5]?재구매(?:한달사용)?|[1-5]?한달사용|재구매)", line):
                    continue
                if self._looks_like_screen_review_option(line, has_content=bool(cleaned_lines)):
                    option_lines.append(self._clean_screen_review_option(line))
                    continue
                if "구매자거주인원" in line or "식이관심사" in line:
                    continue
                if ("크기차" in line or "신선함" in line or "과숙정도" in line) and len(line) < 120:
                    continue
                if line.startswith("[") and ":" in line and len(line) < 180:
                    continue
                if ":" in line and len(line) < 80 and not re.search(r"[.!?。ㅠㅎ]", line):
                    continue
                if re.fullmatch(r"\d+", line):
                    continue
                cleaned_lines.append(line)

            content = " ".join(cleaned_lines).strip()
            content = re.sub(r"\s+", " ", content)
            content = re.sub(
                r"\s*(더보기|접기|이미지 펼치기|이미지 접기|이 구매자의 처음 리뷰 보기|이 구매자의 한달사용 리뷰 보기)\s*",
                " ",
                content,
            )
            content = self._strip_screen_chrome(content)
            content = re.sub(r"\s*[1-5]?재구매(?:한달사용)?\s*$", "", content)
            content = re.sub(r"\s*[1-5]?(?:재구매)?한달사용\s*$", "", content)
            content = re.sub(r"\s+", " ", content).strip()
            if not content or len(content) < 2:
                continue

            assigned_images = self._pop_screen_review_images(image_groups_by_key, writer, date_text)
            option = " / ".join(dict.fromkeys(line for line in option_lines if line))

            created_at = self._screen_date_to_full_date(date_text)
            review_no = self._screen_review_no(writer, created_at, content, option)
            reviews.append(
                Review(
                    review_no=review_no,
                    product_no=self._product_no,
                    product_name=self._product_name,
                    writer=writer,
                    created_at=created_at,
                    rating=rating,
                    content=content,
                    option=option,
                    image_urls=assigned_images,
                )
            )

        return reviews

    def _extract_review_image_groups_from_screen(self, page: Page) -> list[dict[str, Any]]:
        groups = page.evaluate(
            r"""() => {
                const writerDateRe = /([A-Za-z0-9_.-]+\*{2,})\s*(\d{2}\.\d{2}\.\d{2}\.)/;
                const imageUrlFromStyle = (style) => {
                    const urls = [];
                    const re = /url\(["']?([^"')]+)["']?\)/g;
                    let match;
                    while ((match = re.exec(style || ''))) urls.push(match[1]);
                    return urls;
                };
                const imageUrls = (el) => {
                    const urls = [];
                    el.querySelectorAll('img, source').forEach((node) => {
                        const currentSrc = node.currentSrc || '';
                        if (currentSrc) urls.push(currentSrc);
                        ['src', 'data-src', 'data-original', 'srcset', 'data-srcset'].forEach((attr) => {
                            const value = node.getAttribute(attr);
                            if (!value) return;
                            String(value).split(',').forEach((part) => {
                                const url = part.trim().split(/\s+/)[0];
                                if (url) urls.push(url);
                            });
                        });
                    });
                    el.querySelectorAll('*').forEach((node) => {
                        imageUrlFromStyle(getComputedStyle(node).backgroundImage).forEach((url) => urls.push(url));
                    });
                    return Array.from(new Set(urls
                        .filter((src) => src.includes('checkout.phinf'))
                        .map((src) => src.replace(/\?.*$/, ''))));
                };
                const candidates = Array.from(document.querySelectorAll('li, article, section, div'))
                    .map((el) => {
                        const text = el.innerText || '';
                        const match = text.match(writerDateRe);
                        if (!match) return null;
                        const allMatches = text.match(new RegExp(writerDateRe.source, 'g')) || [];
                        if (allMatches.length !== 1) return null;
                        const rect = el.getBoundingClientRect();
                        if (rect.width < 260 || rect.height < 40 || text.length < 20 || text.length > 1800) return null;
                        const urls = imageUrls(el);
                        if (!urls.length) return null;
                        return {
                            writer: match[1],
                            date: match[2],
                            image_urls: urls,
                            top: rect.top + window.scrollY,
                            area: rect.width * rect.height,
                            text_len: text.length,
                        };
                    })
                    .filter(Boolean)
                    .sort((a, b) => a.text_len - b.text_len || a.area - b.area);

                const selected = [];
                const seen = new Set();
                for (const candidate of candidates) {
                    const key = `${candidate.writer}|${candidate.date}|${candidate.image_urls.join('|')}`;
                    if (seen.has(key)) continue;
                    seen.add(key);
                    selected.push(candidate);
                }
                selected.sort((a, b) => a.top - b.top || a.text_len - b.text_len);
                return selected.map(({writer, date, image_urls}) => ({writer, date, image_urls}));
            }"""
        )
        if not isinstance(groups, list):
            return []
        normalized: list[dict[str, Any]] = []
        for group in groups:
            if not isinstance(group, dict):
                continue
            writer = str(group.get("writer") or "").strip()
            date = str(group.get("date") or "").strip()
            urls = group.get("image_urls") or []
            if not writer or not date or not isinstance(urls, list):
                continue
            clean_urls = [str(url).strip() for url in urls if str(url).strip()]
            clean_urls = list(dict.fromkeys(clean_urls))
            if clean_urls:
                normalized.append({"writer": writer, "date": date, "image_urls": clean_urls})
        return normalized

    def _pop_screen_review_images(
        self,
        image_groups_by_key: dict[tuple[str, str], list[list[str]]],
        writer: str,
        date_text: str,
    ) -> list[str]:
        groups = image_groups_by_key.get((writer, date_text))
        if not groups:
            return []
        return groups.pop(0)

    def _looks_like_screen_review_option(self, line: str, *, has_content: bool) -> bool:
        text = re.sub(r"\s+", " ", line).strip()
        if not text or len(text) > 260:
            return False
        if self._is_screen_chrome_line(text):
            return False
        if re.search(r"[.!?。ㅠㅎ]{2,}", text):
            return False

        normalized = text.replace("：", ":")
        option_markers = (
            "옵션",
            "선택옵션",
            "선택 옵션",
            "구매옵션",
            "구매 옵션",
            "주문옵션",
            "주문 옵션",
        )
        if any(marker in normalized for marker in option_markers):
            if not has_content or ":" in normalized:
                return True
            return bool(re.match(r"^\[?(?:선택\s*)?(?:구매\s*)?(?:주문\s*)?옵션\b", normalized))

        if has_content or ":" not in normalized:
            return False

        key, value = self._split_screen_review_option(normalized)
        if not key or not value or len(key) > 80 or len(value) > 150:
            return False
        if key in {"평점", "작성일", "리뷰", "사진/비디오 수"}:
            return False
        survey_keywords = {
            "크기",
            "크기차",
            "신선함",
            "과숙정도",
            "당도",
            "맛",
            "품질",
            "가격",
            "배송",
            "포장",
            "향",
            "식감",
            "만족도",
            "구매자거주인원",
            "식이관심사",
        }
        if key in survey_keywords:
            return False
        return self._has_screen_review_option_key(key)

    def _has_screen_review_option_key(self, key: str) -> bool:
        option_key_keywords = (
            "선택",
            "옵션",
            "상품",
            "제품",
            "품목",
            "구성",
            "종류",
            "색상",
            "컬러",
            "사이즈",
            "크기",
            "용량",
            "중량",
            "수량",
            "개수",
            "맛",
            "향",
            "타입",
            "세트",
            "포장",
        )
        return any(keyword in key for keyword in option_key_keywords)

    def _split_screen_review_option(self, text: str) -> tuple[str, str]:
        normalized = text.replace("：", ":")
        if ":" not in normalized:
            return "", ""
        key, value = [part.strip() for part in normalized.rsplit(":", 1)]
        if "/" in key:
            key = key.rsplit("/", 1)[-1].strip()
        key = re.sub(r"^[^\w가-힣\[]+", "", key).strip()
        return key, value

    def _clean_screen_review_option(self, line: str) -> str:
        text = re.sub(r"\s+", " ", line).strip()
        text = text.replace("：", ":")
        text = re.sub(r"^\[?(?:선택\s*)?옵션\]?\s*[:：]?\s*", "", text).strip()
        key, value = self._split_screen_review_option(text)
        if key and value and self._has_screen_review_option_key(key):
            return value
        return text

    def _trim_screen_review_chunk(self, lines: list[str]) -> list[str]:
        trimmed: list[str] = []
        for line in lines:
            if self._is_screen_chrome_line(line):
                break
            trimmed.append(line)
        return trimmed

    def _is_screen_chrome_line(self, line: str) -> bool:
        exact_markers = {
            "닫기",
            "본문으로 바로가기",
            "네이버",
            "네이버플러스 스토어 홈",
            "사용자 링크",
            "로그인",
            "서비스",
            "검색어를 입력해주세요",
            "알림받기",
            "이전",
            "다음",
        }
        if line in exact_markers:
            return True
        if re.fullmatch(r"리뷰\s*[\d,]+", line):
            return True
        prefixes = (
            "관심고객수",
            "포토&동영상",
            "AI 리뷰요약",
            "내 맞춤정보",
            "판매자정보",
            "상품정보",
            "상품문의",
            "구매정보",
            "관련상품",
            "함께 구매한",
        )
        return line.startswith(prefixes)

    def _strip_screen_chrome(self, content: str) -> str:
        patterns = [
            r"\s+닫기\s+본문으로 바로가기.*$",
            r"\s+본문으로 바로가기.*$",
            r"\s+네이버\s+네이버플러스\s+스토어\s+홈.*$",
            r"\s+다음\s+포토&동영상.*$",
            r"\s+포토&동영상\s+[\d,]+.*$",
            r"\s+리뷰\s+[\d,]+\s+AI 리뷰요약.*$",
        ]
        for pattern in patterns:
            content = re.sub(pattern, "", content)
        return content

    def _screen_date_to_full_date(self, value: str) -> str:
        match = re.match(r"(\d{2})\.(\d{2})\.(\d{2})\.", value)
        if not match:
            return value
        year = 2000 + int(match.group(1))
        return f"{year:04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d} 00:00:00"

    def _screen_review_no(self, writer: str, created_at: str, content: str, option: str = "") -> str:
        digest = hashlib.sha1(f"{writer}|{created_at}|{option}|{content}".encode("utf-8")).hexdigest()[:16]
        return f"screen-{digest}"
