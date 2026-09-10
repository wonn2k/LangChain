# Work Walk Agent - Tool 문서

직장인 점심 산책 코스 추천 Agent(설계서: `Agent_설계서_4조.docx.pdf`)의 Tool 구현 부분 문서입니다.

## 파일 구성

| 파일 | 역할 |
|---|---|
| `utils.py` | API 호출이 필요 없는 순수 계산 함수 (`calc_time_budget`, `straight_distance`, `latlon_to_grid`) |
| `test_agent.py` | 9개 Tool을 실제 API로 호출해 확인하는 스모크 테스트 (`python test_agent.py`) |
| `Work_Walk_Agent.ipynb` | Tool을 `@tool`로 등록하고 `create_agent`로 묶은 실행 노트북 |

## 필요한 API 키 (.env)

| 환경변수 | 발급처 | 사용하는 Tool |
|---|---|---|
| `KAKAO_API_KEY` | [developers.kakao.com](https://developers.kakao.com) → 내 애플리케이션 → REST API 키 (제품 설정에서 **카카오맵** 활성화 필요) | geocode, find_cafes, find_parks, reverse_geocode |
| `T1_API_KEY` | [openapi.sk.com](https://openapi.sk.com) → appKey (앱에 **보행자 경로안내** 상품 추가 필요) | walk_route |
| `KPA_API_KEY` | [apihub.kma.go.kr](https://apihub.kma.go.kr) → 마이페이지 인증키 (**단기예보 > 동네예보(초단기실황·초단기예보·단기예보) 조회** 활용신청 필요) | check_weather |
| `OPENROUTE_API_KEY` | [openrouteservice.org](https://openrouteservice.org/dev/#/signup) | walk_route_roundtrip |
| `OPENAI_API_KEY` | [platform.openai.com](https://platform.openai.com) | Agent 모델(`gpt-5-mini`) |

## Tool 목록

### 1. `geocode(address: str) -> dict`
- **API**: 카카오 로컬 - 주소 검색 (`GET /v2/local/search/address.json`)
- **인증**: 헤더 `Authorization: KakaoAK {KAKAO_API_KEY}`
- **반환**: `{"x": 경도, "y": 위도, "address_name": str}`
- **역할**: 회사 주소 등 텍스트 주소를 좌표로 변환. 이후 모든 Tool의 출발점이 되므로 가장 먼저 호출.

### 2. `find_cafes(x: float, y: float, radius_m: int = 300) -> list`
- **API**: 카카오 로컬 - 카테고리 검색, `category_group_code=CE7`(카페) (`GET /v2/local/search/category.json`)
- **반환**: `[{"name", "x", "y", "distance_m", "place_id"}, ...]` (거리순 정렬)
- **역할**: 좌표 주변 카페 검색. 비 오는 날은 Agent가 `radius_m`을 줄여서(예: 300m) 호출하는 판단을 함.
- **주의**: 카카오 정책상 좌표/주소를 DB에 영구 저장 금지 → 장기 기억엔 `place_id`만 남길 것.

### 3. `find_parks(x: float, y: float, radius_m: int = 500) -> list`
- **API**: 카카오 로컬 - 키워드 검색, `query="공원"` (`GET /v2/local/search/keyword.json`)
- **반환**: find_cafes와 동일한 형태
- **역할**: 공원은 전용 카테고리 코드가 없어 키워드로 검색.

### 4. `walk_route(start_x, start_y, end_x, end_y) -> dict`
- **API**: TMAP 보행자 경로 (`POST /tmap/routes/pedestrian?version=1`)
- **인증**: 헤더 `appKey: {T1_API_KEY}`
- **반환**: `{"total_distance_m": int, "total_time_sec": int}`
- **역할**: 출발-도착 두 좌표 간 실제 도보 거리/시간 계산. 경유지가 있는 왕복 코스는 구간별로 나눠 호출 후 합산.
- **주의**: 요청은 GET이 아니라 **POST + JSON body**. 시작/도착 좌표가 사실상 같으면(거리 0m) 400 에러.

### 5. `walk_route_roundtrip(start_x, start_y, length_m: int = 1000) -> dict`
- **API**: OpenRouteService 도보 경로, `options.round_trip` (`POST /v2/directions/foot-walking/geojson`)
- **인증**: 헤더 `Authorization: {OPENROUTE_API_KEY}` (접두사 없이 키만)
- **반환**: `{"total_distance_m": int, "total_time_sec": int}`
- **역할**: 목적지 없이 "출발점 + 원하는 거리"만으로 순환(왕복) 코스 생성. "그냥 20분만 걷고 싶다" 같은 목적 없는 요청에 사용.

### 6. `check_weather(x: float, y: float) -> dict`
- **API**: 기상청 API허브 - 동네예보 초단기예보 (`GET VilageFcstInfoService_2.0/getUltraSrtFcst`)
- **인증**: URL 쿼리 파라미터 `authKey={KPA_API_KEY}` (헤더 아님)
- **반환**: `{"precipitation_type": "0"~"4", "temperature_c": float}`
  - 강수형태 코드: `0`=없음, `1`=비, `2`=비/눈, `3`=눈, `4`=소나기
- **역할**: 비/폭염 여부를 판단해 산책 모드(`walk` vs `shortest_indoor`) 전환의 근거로 사용.
- **주의**: 내부적으로 위경도를 `latlon_to_grid`(기상청 격자 변환)로 바꿔서 조회. 기준시각은 "지금-45분"으로 잡아 발표 완료된 가장 최근 회차를 안전하게 조회.

### 7. `reverse_geocode(x: float, y: float) -> str`
- **API**: 카카오 로컬 - 좌표to주소 (`GET /v2/local/geo/coord2address.json`, geocode와 같은 키/헤더)
- **반환**: 도로명주소 문자열 (없으면 지번주소)
- **역할**: 최종 답변에서 "○○로 12"처럼 사람이 읽는 주소로 안내할 때 사용.

### 8. `calc_time_budget(end_time: str, start_time: str = None, coffee_min: int = 0) -> int`
- **API 불필요** (순수 함수, `utils.py`)
- **반환**: 산책 가능 시간(분)
- **역할**: 점심 시작/끝 시각(또는 끝 시각만)으로 지금부터 걸을 수 있는 시간을 계산. 카페 등 체류 시간은 차감.

### 9. `straight_distance(lat1, lon1, lat2, lon2) -> int`
- **API 불필요** (순수 함수, `utils.py`, 하버사인 공식)
- **반환**: 두 좌표 간 직선거리(m)
- **역할**: `walk_route`(TMAP) 실패 시 대체 추정치로 사용.

## 좌표 규약 (중요)

- 카카오·TMAP·OpenRouteService 모두 **x=경도, y=위도** (WGS84)로 통일.
- 기상청만 예외적으로 위경도가 아닌 자체 격자(nx, ny) 좌표를 쓰며, `latlon_to_grid(lat, lon)`으로 변환해서 넘겨야 함.

## 테스트

```bash
python test_agent.py
```
LLM/LangChain 없이 9개 Tool을 실제 API로 순서대로 호출해 `[PASS]`/`[FAIL]`로 결과를 출력합니다 (토큰 비용 없음).
