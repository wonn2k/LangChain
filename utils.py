"""보조 함수 모음 - 외부 API 호출 없이 순수 계산만 하는 함수들.

Work_Walk_Agent.ipynb와 test_agent.py가 공통으로 import해서 씁니다.
설계서(Agent_설계서_4조) 상 "API 없이 Python으로 만들 Tool" 항목에 해당합니다.
"""
from datetime import datetime, timezone, timedelta
import math

# 기상청/카카오/TMAP 응답은 모두 한국 표준시(KST) 기준이므로 서버 로케일과 무관하게 KST로 고정한다
KST = timezone(timedelta(hours=9))


def calc_time_budget(end_time: str, start_time: str = None, coffee_min: int = 0) -> int:
    """점심 시작·끝 시간으로 지금부터 산책 가능한 시간(분)을 계산합니다. 카페 등 체류 시간은 차감합니다.

    Args:
        end_time: 점심 종료 시각 "HH:MM"
        start_time: 점심 시작 시각 "HH:MM". 없으면 현재 시각을 사용
        coffee_min: 카페 등 체류 예정 시간(분)
    """
    now = datetime.now(KST)
    if start_time is None:
        # 시작 시각을 안 주면 "지금 당장 출발"로 간주한다 (설계서: 끝 시간만 입력하는 케이스)
        start = now
    else:
        h, m = map(int, start_time.split(":"))
        # 입력된 시작 시각이 이미 지나버린 경우(예: 12:00 입력했는데 지금이 12:20)
        # 과거 시각으로 계산하면 가용 시간이 실제보다 커지므로, 현재 시각과 비교해 더 늦은 쪽을 쓴다
        start = max(now.replace(hour=h, minute=m, second=0, microsecond=0), now)
    eh, em = map(int, end_time.split(":"))
    end = now.replace(hour=eh, minute=em, second=0, microsecond=0)
    # (끝 - 시작)을 분 단위로 바꾸고 체류 시간을 뺀다. 이미 시간이 다 지났으면 음수가 나올 수 있어 0으로 clamp
    return max(int((end - start).total_seconds() // 60) - coffee_min, 0)


def straight_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    """두 좌표 간 직선거리(m)를 반환합니다. TMAP 도보 경로 API 실패 시 대체 계산용입니다.

    Args:
        lat1: 출발지 위도
        lon1: 출발지 경도
        lat2: 도착지 위도
        lon2: 도착지 경도
    """
    R = 6371000  # 지구 평균 반지름 (m)
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    # 하버사인(Haversine) 공식: 구면 위 두 점 사이의 대원거리(great-circle distance)를 구하는 표준식
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return int(2 * R * math.asin(math.sqrt(a)))


def latlon_to_grid(lat: float, lon: float) -> tuple:
    """위경도를 기상청 초단기예보 격자 좌표(nx, ny)로 변환합니다.

    기상청 예보 격자는 위경도 좌표계가 아니라 람베르트 정각원추도법(Lambert Conformal Conic)
    기반의 5km 단위 정수 격자라서, check_weather를 호출하기 전 반드시 이 변환을 거쳐야 한다.
    Agent가 직접 부를 이유는 없는 내부 헬퍼라 @tool로 등록하지 않는다.
    """
    # 기상청이 공식 문서로 배포하는 격자 변환 상수: 지구 반경(km), 격자 간격(km),
    # 표준위도 1·2, 기준점 경도·위도, 기준점의 격자상 X·Y 좌표
    RE, GRID, SLAT1, SLAT2, OLON, OLAT, XO, YO = 6371.00877, 5.0, 30.0, 60.0, 126.0, 38.0, 43, 136
    DEGRAD = math.pi / 180.0
    re = RE / GRID
    slat1, slat2 = SLAT1 * DEGRAD, SLAT2 * DEGRAD
    olon, olat = OLON * DEGRAD, OLAT * DEGRAD

    # 원추도법의 축척계수(sn)와 축척 보정값(sf) 계산
    sn = math.tan(math.pi * 0.25 + slat2 * 0.5) / math.tan(math.pi * 0.25 + slat1 * 0.5)
    sn = math.log(math.cos(slat1) / math.cos(slat2)) / math.log(sn)
    sf = math.tan(math.pi * 0.25 + slat1 * 0.5)
    sf = math.pow(sf, sn) * math.cos(slat1) / sn
    # 기준점(OLON, OLAT)까지의 극좌표 반지름(ro)
    ro = re * sf / math.pow(math.tan(math.pi * 0.25 + olat * 0.5), sn)

    # 입력 좌표(lat, lon)까지의 극좌표 반지름(ra)과 각도(theta)
    ra = re * sf / math.pow(math.tan(math.pi * 0.25 + lat * DEGRAD * 0.5), sn)
    theta = lon * DEGRAD - olon
    # 경도차를 -180~180도 범위로 정규화 (날짜변경선 부근 계산 오류 방지)
    if theta > math.pi:
        theta -= 2.0 * math.pi
    if theta < -math.pi:
        theta += 2.0 * math.pi
    theta *= sn

    # 극좌표(ra, theta)를 직교좌표(nx, ny)로 변환하고, 기준점 오프셋(XO, YO)을 더한다
    nx = int(ra * math.sin(theta) + XO + 0.5)
    ny = int(ro - ra * math.cos(theta) + YO + 0.5)
    return nx, ny
