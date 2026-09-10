"""Work_Walk_Agent.ipynb의 Tool 구현을 실제 API로 호출해 확인하는 스모크 테스트.
LangChain/LLM 없이 Tool 함수만 검증합니다 (빠르고 토큰 비용 없음).

실행: python test_agent.py
각 Tool을 순서대로 호출하고 [PASS]/[FAIL]로 결과를 출력한다. 어느 하나가 실패해도
나머지 Tool은 계속 테스트한다 (한 API 키 문제 때문에 전체 테스트가 멈추지 않도록).
"""
import os
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

# 순수 계산 로직(외부 API 불필요)은 utils.py에 모아두고 여기서는 가져다 쓰기만 한다
from utils import KST, calc_time_budget, straight_distance, latlon_to_grid

# .env에 있는 KAKAO_API_KEY / T1_API_KEY / KPA_API_KEY / OPENAI_API_KEY 등을 환경변수로 로드
# override=True: 이미 셸에 같은 이름의 환경변수가 있어도 .env 값으로 덮어쓴다
load_dotenv(override=True)

# 카카오 로컬 API는 인증키를 HTTP 헤더의 Authorization에 "KakaoAK {키}" 형식으로 넣는다
# (①주소→좌표 ②카페검색 ③공원검색 ⑥좌표→주소, 4개 Tool이 이 헤더 하나를 공유한다)
KAKAO_HEADERS = {"Authorization": f"KakaoAK {os.environ['KAKAO_API_KEY']}"}
# TMAP은 인증키를 HTTP 헤더 "appKey"에 그대로 넣는다 (카카오와 헤더 이름/형식이 다름)
T1_HEADERS = {"appKey": os.environ["T1_API_KEY"]}
# 기상청 API허브는 헤더가 아니라 URL 쿼리 파라미터 "authKey"로 인증한다 (요청마다 params에 직접 포함)
KPA_AUTH_KEY = os.environ["KPA_API_KEY"]
# OpenRouteService는 헤더 "Authorization"에 키를 그대로 넣는다 (카카오처럼 "KakaoAK" 같은 접두사 없음)
ORS_HEADERS = {"Authorization": os.environ["OPENROUTE_API_KEY"], "Content-Type": "application/json"}


def geocode(address: str) -> dict:
    """주소 -> 좌표 변환 (카카오 로컬 "주소 검색" API).
    응답의 x=경도, y=위도이며, 이후 모든 Tool 호출의 출발점(회사 위치)으로 쓰인다.
    """
    url = "https://dapi.kakao.com/v2/local/search/address.json"
    resp = requests.get(url, headers=KAKAO_HEADERS, params={"query": address})
    resp.raise_for_status()  # 4xx/5xx면 여기서 바로 예외를 던져서 호출부의 try/except가 잡게 한다
    doc = resp.json()["documents"][0]  # 검색 결과 중 가장 정확도 높은 첫 번째 주소만 사용
    return {"x": float(doc["x"]), "y": float(doc["y"]), "address_name": doc["address_name"]}


def find_cafes(x: float, y: float, radius_m: int = 300) -> list:
    """좌표 주변 카페 검색 (카카오 로컬 "카테고리 검색" API, CE7=카페).
    카페는 카카오가 제공하는 전용 카테고리 코드가 있어서 키워드가 아니라 코드로 검색한다
    (엉뚱한 "카페"라는 이름의 다른 업종이 섞이지 않도록 정확도를 높이는 방식).
    """
    url = "https://dapi.kakao.com/v2/local/search/category.json"
    params = {"category_group_code": "CE7", "x": x, "y": y, "radius": radius_m, "sort": "distance"}
    resp = requests.get(url, headers=KAKAO_HEADERS, params=params)
    resp.raise_for_status()
    docs = resp.json()["documents"]
    # 장기 기억(Long-term memory)에는 좌표/주소를 저장하면 안 되는 카카오 정책 때문에
    # place_id(장소 고유 ID)만 남기고, 좌표는 필요할 때마다 다시 검색하는 걸 전제로 한다
    return [
        {"name": d["place_name"], "x": float(d["x"]), "y": float(d["y"]), "distance_m": int(d["distance"]), "place_id": d["id"]}
        for d in docs
    ]


def find_parks(x: float, y: float, radius_m: int = 500) -> list:
    """좌표 주변 공원 검색 (카카오 로컬 "키워드 검색" API).
    공원은 카페(CE7) 같은 전용 카테고리 코드가 없으므로, 키워드 "공원"으로 검색한다.
    """
    url = "https://dapi.kakao.com/v2/local/search/keyword.json"
    params = {"query": "공원", "x": x, "y": y, "radius": radius_m, "sort": "distance"}
    resp = requests.get(url, headers=KAKAO_HEADERS, params=params)
    resp.raise_for_status()
    docs = resp.json()["documents"]
    return [
        {"name": d["place_name"], "x": float(d["x"]), "y": float(d["y"]), "distance_m": int(d["distance"]), "place_id": d["id"]}
        for d in docs
    ]


def walk_route(start_x: float, start_y: float, end_x: float, end_y: float) -> dict:
    """출발-도착 좌표 간 도보 경로의 총 거리(m)·소요시간(초) 계산 (TMAP 보행자 경로 API).
    다른 Tool과 달리 GET이 아니라 POST로 JSON 바디를 보낸다.
    회사->카페->회사처럼 경유지가 있는 왕복 코스는 이 함수를 구간별로 나눠 호출한 뒤 합산한다.
    """
    url = "https://apis.openapi.sk.com/tmap/routes/pedestrian?version=1"
    body = {
        "startX": start_x, "startY": start_y,
        "endX": end_x, "endY": end_y,
        "startName": "출발", "endName": "도착",  # TMAP 필수 파라미터(경로 안내 문구에 쓰이지만 값 자체는 임의)
        "reqCoordType": "WGS84GEO", "resCoordType": "WGS84GEO",  # 입출력 모두 위경도 좌표계 사용 지정
    }
    resp = requests.post(url, headers=T1_HEADERS, json=body)
    resp.raise_for_status()
    # 응답은 GeoJSON FeatureCollection이고, 첫 feature(출발점 요약 지점)의 properties에
    # 경로 전체의 총 거리/시간이 들어있다 - 나머지 feature들은 구간별 세부 좌표라 여기선 안 씀
    props = resp.json()["features"][0]["properties"]
    return {"total_distance_m": int(props["totalDistance"]), "total_time_sec": int(props["totalTime"])}


def walk_route_roundtrip(start_x: float, start_y: float, length_m: int = 1000) -> dict:
    """출발지에서 원하는 거리만큼 걷고 다시 출발지로 돌아오는 순환(왕복) 도보 코스를 계산합니다.
    목적지를 정하지 않고 "그냥 20분만 걷고 싶다"처럼 목적지가 없는 요청에 사용하세요 (OpenRouteService round_trip).
    walk_route(TMAP)는 출발-도착 두 점이 필요하지만, 이 Tool은 출발점 하나와 원하는 거리만 있으면 된다.
    """
    url = "https://api.openrouteservice.org/v2/directions/foot-walking/geojson"
    body = {
        "coordinates": [[start_x, start_y]],  # round_trip은 출발점 좌표 하나만 필요 (도착점 없음)
        "options": {"round_trip": {"length": length_m, "points": 3}},  # points: 순환 경로를 만드는 경유점 개수(많을수록 더 도는 모양)
    }
    resp = requests.post(url, headers=ORS_HEADERS, json=body)
    resp.raise_for_status()
    # TMAP과 달리 실제 이동거리/시간은 properties.summary에 바로 들어있다 (segments[0]과 값 동일)
    summary = resp.json()["features"][0]["properties"]["summary"]
    return {"total_distance_m": int(summary["distance"]), "total_time_sec": int(summary["duration"])}


def check_weather(x: float, y: float) -> dict:
    """좌표의 초단기예보(강수형태, 기온) 조회 (기상청 API허브 VilageFcstInfoService_2.0).
    비/폭염 여부를 판단해 산책 모드(walk vs shortest_indoor)를 정하는 근거로 쓴다.
    """
    # 기상청은 위경도가 아니라 격자(nx, ny)로 조회하므로 먼저 변환한다 (y=위도, x=경도 순서 주의)
    nx, ny = latlon_to_grid(y, x)
    # 초단기예보는 매시 30분에 발표되고, 발표 후 10분 뒤부터 조회 가능하다.
    # "지금 - 45분"을 기준시로 잡으면 항상 이미 발표가 끝난 가장 최근 회차를 안전하게 가리킨다.
    base = datetime.now(KST) - timedelta(minutes=45)
    url = "https://apihub.kma.go.kr/api/typ02/openApi/VilageFcstInfoService_2.0/getUltraSrtFcst"
    params = {
        "authKey": KPA_AUTH_KEY, "pageNo": 1, "numOfRows": 60, "dataType": "JSON",
        "base_date": base.strftime("%Y%m%d"), "base_time": base.strftime("%H30"),
        "nx": nx, "ny": ny,
    }
    resp = requests.get(url, params=params)
    resp.raise_for_status()
    items = resp.json()["response"]["body"]["items"]["item"]
    # 응답은 (여러 예보 시각) x (여러 카테고리: PTY/T1H/POP/...) 조합이 한 리스트에 다 섞여 온다.
    # 가장 이른 예보 시각(첫 item의 fcstTime) 한 시점만 골라 카테고리별 값을 딕셔너리로 재구성한다.
    first_time = items[0]["fcstTime"]
    values = {item["category"]: item["fcstValue"] for item in items if item["fcstTime"] == first_time}
    return {
        "precipitation_type": values.get("PTY"),  # 강수형태 코드: 0=없음, 1=비, 2=비/눈, 3=눈, 4=소나기
        "temperature_c": float(values.get("T1H", 0)),  # 기온(섭씨)
    }


def reverse_geocode(x: float, y: float) -> str:
    """좌표 -> 사람이 읽는 주소 변환 (카카오 로컬 "좌표to주소" API, ①geocode와 같은 키/헤더 사용).
    최종 답변에서 "○○로 12"처럼 장소를 안내할 때 쓴다.
    """
    url = "https://dapi.kakao.com/v2/local/geo/coord2address.json"
    resp = requests.get(url, headers=KAKAO_HEADERS, params={"x": x, "y": y})
    resp.raise_for_status()
    doc = resp.json()["documents"][0]
    road = doc.get("road_address")  # 도로명주소가 있으면 우선 사용
    return road["address_name"] if road else doc["address"]["address_name"]  # 없으면 지번주소로 대체


def _check(name: str, fn):
    """fn()을 실행해 결과를 [PASS]로 출력하고, 예외가 나면 [FAIL]로 출력한 뒤 None을 반환한다.
    하나의 Tool이 실패해도 나머지 테스트가 이어지도록 여기서 예외를 흡수한다.
    """
    try:
        result = fn()
        print(f"[PASS] {name}: {result}")
        return result
    except Exception as e:
        print(f"[FAIL] {name}: {e}")
        return None


def main():
    print("=== Work Walk Tool 스모크 테스트 ===")
    address = "경기 성남시 분당구 판교역로 231"
    fallback_xy = (127.1086, 37.4019)  # geocode 자체가 실패해도 나머지 Tool은 테스트할 수 있게 판교역 좌표로 대체

    # 1) geocode가 다른 모든 Tool의 출발 좌표를 만들어주므로 제일 먼저 호출한다
    loc = _check("geocode", lambda: geocode(address))
    x, y = (loc["x"], loc["y"]) if loc else fallback_xy

    # 2) 위에서 구한 좌표로 카페/공원/날씨/역지오코딩을 각각 검증
    cafes = _check("find_cafes", lambda: find_cafes(x, y))
    _check("find_parks", lambda: find_parks(x, y))
    _check("check_weather", lambda: check_weather(x, y))
    _check("reverse_geocode", lambda: reverse_geocode(x, y))

    # 3) walk_route는 출발지와 "다른 위치"의 목적지가 있어야 의미 있는 경로가 나온다.
    #    카페 검색 결과 중 거리 0m(출발지와 동일 좌표)인 항목은 TMAP이 경로를 못 찾아 400을 반환하므로 제외
    walkable_cafes = [c for c in (cafes or []) if c["distance_m"] > 0]
    if walkable_cafes:
        cafe = walkable_cafes[0]
        _check("walk_route", lambda: walk_route(x, y, cafe["x"], cafe["y"]))
    else:
        print("[SKIP] walk_route: 거리 0m가 아닌 카페 결과 없음")

    # 4) walk_route_roundtrip은 목적지 없이 출발점 하나로만 호출한다
    _check("walk_route_roundtrip", lambda: walk_route_roundtrip(x, y, 1000))

    # 5) API 호출이 필요 없는 순수 함수도 같은 방식으로 확인 (네트워크 없이 항상 성공해야 정상)
    _check("calc_time_budget", lambda: calc_time_budget("23:59", "00:01", 10))
    _check("straight_distance", lambda: straight_distance(37.5665, 126.9780, 37.4979, 127.0276))
    print("=== 완료 ===")


if __name__ == "__main__":
    main()
