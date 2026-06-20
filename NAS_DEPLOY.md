# NAS 배포 메모

`b2b_excel`과 같은 Docker Compose 방식으로 실행합니다.

## 1. NAS로 업로드

`review_crawl` 폴더 전체를 NAS의 원하는 경로에 업로드합니다.

예:

```bash
/volume1/docker/review_crawl
```

## 2. 실행

NAS 터미널에서 프로젝트 폴더로 이동한 뒤 실행합니다.

```bash
cd /volume1/docker/review_crawl
docker compose up -d --build
```

## 3. 접속

기본 설정은 리버스 프록시 연결을 전제로 합니다.

```yaml
ports:
  - "127.0.0.1:8502:8502"
```

NAS 내부 리버스 프록시에서 `http://127.0.0.1:8502`로 연결하면 됩니다. 이 웹앱은 일반 HTTP 요청으로 상태를 갱신하므로 WebSocket 사용자 지정 헤더가 없어도 화면이 동작합니다.

LAN에서 바로 접속하려면 `docker-compose.yml`을 아래처럼 바꿉니다.

```yaml
ports:
  - "8502:8502"
```

그 다음 다시 실행합니다.

```bash
docker compose up -d --build
```

## 4. 저장 파일

수집된 엑셀 파일은 프로젝트의 `output` 폴더에 저장됩니다.

```bash
./output
```

웹 화면 왼쪽 사이드바에서도 최근 저장 파일을 다시 다운로드할 수 있습니다.

## 5. 주의사항

- NAS Docker에서 브라우저를 실행하므로 메모리를 여유 있게 둬야 합니다.
- `docker-compose.yml`에 `shm_size: "1gb"`를 넣어 브라우저 탭이 갑자기 죽는 문제를 줄였습니다.
- 네이버 페이지 구조가 바뀌면 수집이 실패할 수 있습니다. 이 경우 수집 로그를 보고 버튼/파서 셀렉터를 수정해야 합니다.
