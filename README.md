# 네이버 스토어 리뷰 수집기

네이버 스마트스토어/브랜드스토어 상품 리뷰를 수집해 엑셀 파일로 저장하는 도구입니다.

두 가지 방식으로 사용할 수 있습니다.

- Windows 데스크톱 앱: `app.py` 또는 빌드된 exe
- NAS 웹 서비스: `web_app.py` 또는 Docker Compose

## 설치

```powershell
python -m pip install -r requirements.txt
python -m playwright install chromium
```

Windows 데스크톱 앱은 설치된 Microsoft Edge를 사용합니다. NAS 웹 서비스는 Playwright Chromium을 headless로 사용합니다.

## Windows 앱 실행

```powershell
python app.py
```

1. 네이버 스마트스토어 또는 브랜드스토어 상품 URL을 입력합니다.
2. 최대 리뷰 수를 지정합니다.
3. `실행`을 누르면 Edge가 앱 전용 임시 프로필로 열립니다.
4. 앱이 광고/추적 파라미터를 제거한 기본 상품 URL로 자동 접속합니다.
5. `리뷰` 탭과 `리뷰 전체보기` 버튼을 누른 뒤 화면 리뷰를 수집합니다.
6. 수집이 끝나면 `저장`을 눌러 `.xlsx` 파일을 저장합니다.

## 웹 서비스 실행

로컬 테스트:

```powershell
streamlit run web_app.py --server.port 8502
```

Docker/NAS 실행:

```bash
docker compose up -d --build
```

기본 포트는 `8502`입니다. `docker-compose.yml`은 `127.0.0.1:8502`로 바인딩되어 있으므로, NAS 리버스 프록시를 붙이거나 LAN에서 바로 열려면 포트 바인딩을 `8502:8502`로 바꾸면 됩니다.

## GitHub Actions CI/CD

`main` 브랜치에 push하면 `.github/workflows/ci-cd.yml`이 Python smoke test, Docker build, NAS 배포를 실행합니다.

NAS 배포 설정은 [CI_CD_NAS_DEPLOY.md](CI_CD_NAS_DEPLOY.md)를 참고하세요.

## 저장 형식

엑셀 컬럼은 아래 순서로 저장합니다.

`리뷰번호, 상품번호, 상품이름, 작성자, 작성일, 평점, 리뷰, 첨부파일`

`첨부파일` 컬럼에는 화면에서 확인된 이미지 URL을 쉼표로 연결해 저장합니다. 별도 이미지 폴더나 이미지 파일은 생성하지 않습니다. 화면 수집 모드에서는 네이버가 화면에 표시하지 않는 실제 리뷰번호 대신 `screen-...` 형식의 내부 식별자를 사용합니다.

## EXE 빌드

```powershell
.\build.ps1
```

빌드 결과는 `dist\NaverSmartStoreReviewCollector.exe`에 생성됩니다.
