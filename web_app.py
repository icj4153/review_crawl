from __future__ import annotations

import os
import re
import threading
from datetime import datetime
from pathlib import Path

import streamlit as st

from crawler import (
    NaverReviewCrawler,
    is_supported_naver_store_url,
    normalize_naver_store_product_url,
)
from excel_writer import save_reviews_xlsx


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = Path(os.getenv("REVIEW_OUTPUT_DIR", str(BASE_DIR / "output")))


def safe_filename(value: str, default: str = "naver_reviews") -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", value).strip().strip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or default


def output_name(product_name: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = safe_filename(product_name[:50] if product_name else "naver_reviews")
    return f"{timestamp}_{name}.xlsx"


def list_recent_files(limit: int = 20) -> list[Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(
        OUTPUT_DIR.glob("*.xlsx"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return files[:limit]


st.set_page_config(page_title="네이버 리뷰 수집", layout="wide")

st.markdown(
    """
    <style>
    .block-container { padding-top: 2rem; max-width: 1120px; }
    div[data-testid="stMetricValue"] { font-size: 1.35rem; }
    textarea { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("네이버 스토어 리뷰 수집")
st.caption("스마트스토어/브랜드스토어 상품 URL을 입력하면 리뷰를 수집해 엑셀로 저장합니다.")

with st.sidebar:
    st.subheader("최근 저장 파일")
    recent_files = list_recent_files()
    if not recent_files:
        st.caption("아직 저장된 엑셀 파일이 없습니다.")
    for file_path in recent_files:
        with file_path.open("rb") as fp:
            st.download_button(
                label=file_path.name,
                data=fp.read(),
                file_name=file_path.name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"recent-{file_path.name}-{file_path.stat().st_mtime_ns}",
                use_container_width=True,
            )

with st.form("review_collect_form"):
    url = st.text_area(
        "수집 URL",
        height=120,
        placeholder="https://smartstore.naver.com/.../products/... 또는 https://brand.naver.com/.../products/...",
    )
    max_reviews = st.number_input("최대 리뷰 수", min_value=1, max_value=10000, value=80, step=10)
    submitted = st.form_submit_button("수집 시작", type="primary", use_container_width=False)

log_placeholder = st.empty()
result_placeholder = st.empty()

if submitted:
    url = url.strip()
    if not url:
        st.warning("수집할 URL을 입력하세요.")
        st.stop()
    if not is_supported_naver_store_url(url):
        st.warning("네이버 스마트스토어 또는 브랜드스토어 상품 URL을 입력하세요.")
        st.stop()

    normalized_url = normalize_naver_store_product_url(url)
    logs: list[str] = []

    def log(message: str) -> None:
        logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")
        log_placeholder.code("\n".join(logs[-120:]), language="text")

    with st.status("리뷰 수집 중입니다. 브라우저를 닫거나 새로고침하지 마세요.", expanded=True) as status:
        st.write(f"대상 URL: `{normalized_url}`")
        try:
            crawler = NaverReviewCrawler(
                browser_mode="chromium",
                headless=True,
                log=log,
            )
            reviews = crawler.collect(normalized_url, int(max_reviews), threading.Event())
            if not reviews:
                status.update(label="수집된 리뷰가 없습니다.", state="error", expanded=True)
                st.stop()

            product_name = reviews[0].product_name or "naver_reviews"
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            xlsx_path = OUTPUT_DIR / output_name(product_name)
            save_reviews_xlsx(reviews, xlsx_path)

            with_images = sum(1 for review in reviews if review.image_urls)
            status.update(label=f"수집 완료: {len(reviews)}건", state="complete", expanded=False)

            col1, col2, col3 = st.columns(3)
            col1.metric("수집 리뷰", f"{len(reviews):,}건")
            col2.metric("이미지 URL 포함", f"{with_images:,}건")
            col3.metric("저장 파일", xlsx_path.name)

            with xlsx_path.open("rb") as fp:
                result_placeholder.download_button(
                    "엑셀 다운로드",
                    data=fp.read(),
                    file_name=xlsx_path.name,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    type="primary",
                )

            st.dataframe(
                [
                    {
                        "작성자": review.writer,
                        "작성일": review.created_at,
                        "평점": review.rating,
                        "이미지 URL 수": len(review.image_urls),
                        "리뷰": review.content,
                    }
                    for review in reviews[:50]
                ],
                use_container_width=True,
                hide_index=True,
            )
        except Exception as exc:
            status.update(label="수집 실패", state="error", expanded=True)
            st.error(str(exc))
