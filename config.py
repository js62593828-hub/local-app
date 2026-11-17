"""
설정 파일 — 네이버 검색광고 API 인증정보를 환경변수 또는 .env 파일에서 읽어옵니다.
절대 이 파일에 직접 키를 적지 마세요.

우선순위: 이미 등록된 시스템/사용자 환경변수가 있으면 그 값을 그대로 쓰고,
없는 값만 이 폴더의 .env 파일에서 채워 넣습니다. (탐색기 더블클릭 실행 시
윈도우 환경변수가 바로 반영 안 되는 경우가 있어서, .env 쪽이 더 확실합니다.)
"""
import os


def _load_dotenv_into_environ(path: str = None) -> None:
    """
    같은 폴더의 .env 파일을 읽어 os.environ에 채워 넣습니다.
    - 이미 os.environ에 값이 있는 키는 건드리지 않습니다(시스템 환경변수 우선).
    - 형식: 한 줄에 KEY=VALUE. '#'으로 시작하는 줄은 주석으로 무시.
    - 값 앞뒤에 따옴표(' 또는 ")가 있으면 벗겨냅니다.
    - .env 파일이 없으면 조용히 넘어갑니다(오류 아님).
    """
    env_path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        pass  # .env를 못 읽어도 프로그램은 계속 동작(환경변수만으로 시도)


_load_dotenv_into_environ()

NAVER_CUSTOMER_ID = os.environ.get("NAVER_CUSTOMER_ID", "")
NAVER_API_KEY = os.environ.get("NAVER_API_KEY", "")       # 액세스라이선스
NAVER_SECRET_KEY = os.environ.get("NAVER_SECRET_KEY", "") # 비밀키

NAVER_API_BASE_URL = "https://api.naver.com"

# ---------- NAVER API HUB (NCP) — 뉴스검색·검색어트렌드·쇼핑인사이트 ----------
# 기존 NAVER_API_KEY(광고 키워드도구, 개발자센터)와는 완전히 별개의 새 플랫폼입니다.
# 인증 헤더 이름도 다릅니다 (X-NCP-APIGW-API-KEY-ID / X-NCP-APIGW-API-KEY).
NAVER_HUB_CLIENT_ID = os.environ.get("NAVER_HUB_CLIENT_ID", "")
NAVER_HUB_CLIENT_SECRET = os.environ.get("NAVER_HUB_CLIENT_SECRET", "")
NAVER_HUB_CONFIGURED = bool(NAVER_HUB_CLIENT_ID and NAVER_HUB_CLIENT_SECRET)

# 실제 API 키가 없으면 자동으로 목(mock) 모드로 동작합니다.
USE_MOCK = not (NAVER_CUSTOMER_ID and NAVER_API_KEY and NAVER_SECRET_KEY)

# 어떤 값이 비어있는지 바로 알 수 있도록 진단 정보를 남겨둡니다 (app.py에서 표시).
MISSING_NAVER_VARS = [
    name
    for name, value in [
        ("NAVER_CUSTOMER_ID", NAVER_CUSTOMER_ID),
        ("NAVER_API_KEY", NAVER_API_KEY),
        ("NAVER_SECRET_KEY", NAVER_SECRET_KEY),
    ]
    if not value
]

# ---------- 워드프레스 REST API 자동발행 ----------
WORDPRESS_URL = os.environ.get("WORDPRESS_URL", "").rstrip("/")
WORDPRESS_USERNAME = os.environ.get("WORDPRESS_USERNAME", "")
WORDPRESS_APP_PASSWORD = os.environ.get("WORDPRESS_APP_PASSWORD", "")

WORDPRESS_CONFIGURED = bool(WORDPRESS_URL and WORDPRESS_USERNAME and WORDPRESS_APP_PASSWORD)

MISSING_WORDPRESS_VARS = [
    name
    for name, value in [
        ("WORDPRESS_URL", WORDPRESS_URL),
        ("WORDPRESS_USERNAME", WORDPRESS_USERNAME),
        ("WORDPRESS_APP_PASSWORD", WORDPRESS_APP_PASSWORD),
    ]
    if not value
]

# ---------- 워드프레스 브라우저 자동화 발행 (REST API가 호스팅사 보안 시스템에 막힐 때의 대안) ----------
# 애플리케이션 비밀번호가 아니라, 사람이 로그인할 때 쓰는 진짜 아이디/비밀번호를 씁니다.
WORDPRESS_ADMIN_USERNAME = os.environ.get("WORDPRESS_ADMIN_USERNAME", "")
WORDPRESS_ADMIN_PASSWORD = os.environ.get("WORDPRESS_ADMIN_PASSWORD", "")

WORDPRESS_BROWSER_CONFIGURED = bool(WORDPRESS_URL and WORDPRESS_ADMIN_USERNAME and WORDPRESS_ADMIN_PASSWORD)

MISSING_WORDPRESS_BROWSER_VARS = [
    name
    for name, value in [
        ("WORDPRESS_URL", WORDPRESS_URL),
        ("WORDPRESS_ADMIN_USERNAME", WORDPRESS_ADMIN_USERNAME),
        ("WORDPRESS_ADMIN_PASSWORD", WORDPRESS_ADMIN_PASSWORD),
    ]
    if not value
]

# ---------- 숍 워드프레스(쿠팡파트너스용, 인포랩스 하위 사이트) REST API 자동발행 ----------
# 롱텀 블로그와 완전히 다른 워드프레스 사이트라, 접속 정보도 따로 둡니다.
SHOP_WORDPRESS_URL = os.environ.get("SHOP_WORDPRESS_URL", "").rstrip("/")
SHOP_WORDPRESS_USERNAME = os.environ.get("SHOP_WORDPRESS_USERNAME", "")
SHOP_WORDPRESS_APP_PASSWORD = os.environ.get("SHOP_WORDPRESS_APP_PASSWORD", "")

SHOP_WORDPRESS_CONFIGURED = bool(SHOP_WORDPRESS_URL and SHOP_WORDPRESS_USERNAME and SHOP_WORDPRESS_APP_PASSWORD)

# 숍 워드프레스 상품 끝에 나가는 "싼세계 배너 이미지"의 주소 (https://… 로 시작해야 적용됩니다).
# 2026-10-15 쿠팡 운영정책 검토 후 배너 사용 중단(빈 값 = 쿠팡 코드만 / 쿠팡 주소면 주황 버튼)
SHOP_WP_CTA_BANNER_URL = ""

MISSING_SHOP_WORDPRESS_VARS = [
    name
    for name, value in [
        ("SHOP_WORDPRESS_URL", SHOP_WORDPRESS_URL),
        ("SHOP_WORDPRESS_USERNAME", SHOP_WORDPRESS_USERNAME),
        ("SHOP_WORDPRESS_APP_PASSWORD", SHOP_WORDPRESS_APP_PASSWORD),
    ]
    if not value
]

# ---------- 구글 서치콘솔 (성과 루프, 읽기 전용) ----------
# 서비스 계정 키 파일 경로와 사이트 주소. 키 파일 내용은 코드·로그·화면 어디에도 출력하지 않습니다.
GSC_KEY_FILE = os.environ.get("GSC_KEY_FILE", "gsc_key.json")
GSC_SITE_LONGTERM = os.environ.get("GSC_SITE_LONGTERM", "")
GSC_SITE_SSANSEGAE = os.environ.get("GSC_SITE_SSANSEGAE", "")

# ---------- 네이버 블로그 자동발행 (네이버 아이디로 로그인 + 블로그 글쓰기 API) ----------
NAVER_LOGIN_CLIENT_ID = os.environ.get("NAVER_LOGIN_CLIENT_ID", "")
NAVER_LOGIN_CLIENT_SECRET = os.environ.get("NAVER_LOGIN_CLIENT_SECRET", "")
NAVER_LOGIN_REDIRECT_URI = os.environ.get(
    "NAVER_LOGIN_REDIRECT_URI", "http://127.0.0.1:5000/naver-oauth-callback"
)

NAVER_BLOG_CONFIGURED = bool(NAVER_LOGIN_CLIENT_ID and NAVER_LOGIN_CLIENT_SECRET)

MISSING_NAVER_BLOG_VARS = [
    name
    for name, value in [
        ("NAVER_LOGIN_CLIENT_ID", NAVER_LOGIN_CLIENT_ID),
        ("NAVER_LOGIN_CLIENT_SECRET", NAVER_LOGIN_CLIENT_SECRET),
    ]
    if not value
]

# ---------- 네이버 블로그 브라우저 자동화 (공식 API가 개인 개발자에게 막혀있어서 이 방식으로) ----------
NAVER_BLOG_ID = os.environ.get("NAVER_BLOG_ID", "")
NAVER_LOGIN_ID = os.environ.get("NAVER_LOGIN_ID", "")
NAVER_LOGIN_PASSWORD = os.environ.get("NAVER_LOGIN_PASSWORD", "")

NAVER_BLOG_BROWSER_CONFIGURED = bool(NAVER_BLOG_ID and NAVER_LOGIN_ID and NAVER_LOGIN_PASSWORD)

MISSING_NAVER_BLOG_BROWSER_VARS = [
    name
    for name, value in [
        ("NAVER_BLOG_ID", NAVER_BLOG_ID),
        ("NAVER_LOGIN_ID", NAVER_LOGIN_ID),
        ("NAVER_LOGIN_PASSWORD", NAVER_LOGIN_PASSWORD),
    ]
    if not value
]

# ---------- 화면에서 숨길 기능 목록 ----------
# 코드는 남겨 두고 메뉴·버튼·섹션만 감춥니다. 다시 켜려면 이 목록에서 이름을 지우세요.
#   "plan_export"          : 주간 승인 화면의 "기획 자료 만들기" 버튼 (weekly_plan_export)
#   "weekly_final_export"  : 주간 승인 화면의 "주간 최종 내보내기" 버튼
#   "manual_promo"         : 글 편집 ⑤ 수동 홍보 체크리스트 + 주간 승인 화면의 "오늘 할 일" 배너
#                            (2026-09 쓰레드 계정 정지 뒤로 계속 숨겨 둔 상태 그대로)
HIDDEN_FEATURES = ["plan_export", "weekly_final_export", "manual_promo"]

# ---------- N데스크피커(radar_naver) "함께 보면 좋은 글" 기준 날짜 ----------
# 이 날짜(포함) 이후에 발행 표시된 글만 "함께 보면 좋은 글" 후보가 됩니다.
RADAR_RELATED_SINCE = "2026-11-23"

# ---------- N데스크피커(radar_naver) 최종본 줄 띄움 · 블로그 카테고리 ----------
# 일반 문단을 어떻게 줄바꿈할지: "sentence"(문장마다, 기본) / "two_sentences"(두 문장마다) / "off"(그대로).
RADAR_LINE_BREAK = "sentence"
# 메모에 "카테고리:" 줄이 없을 때 쓰는 표: 갈래(그 주 plan.json 의 갈래) → 네이버 블로그 카테고리 이름.
# 지금은 갈래 이름을 그대로 적어 두었습니다. 실제 블로그 카테고리 이름이 다르면 오른쪽 값만 바꾸세요.
RADAR_CATEGORY_MAP = {
    "충전·전원": "충전·전원",
    "음향": "음향",
    "입력": "입력",
    "책상꾸미기·정리": "책상꾸미기·정리",
    "조명": "조명",
    "모니터·노트북": "모니터·노트북",
    "몸편한것": "몸편한것",
    "겨울제철": "겨울제철",
}

# ---------- N데스크피커(radar_naver) 최종본 모양 (⑧) ----------
# 쇼핑커넥트 필수 표기 이미지(글자 대신 이미지로 넣음). 같은 이름 뒤에 .jpg 가 한 번 더 붙은 파일(윈도우 숨김 확장자)도 찾아 씁니다.
RADAR_NOTICE_IMAGE = "static/radar/notice_shoppingconnect.jpg"
# 강조 모양: **글자** → 굵게 + 이 글자색 / ==글자== → 이 배경색 + 굵게
RADAR_EM_BOLD_COLOR = "#8A6A1F"
RADAR_EM_HL_BG = "#FFF1B8"
# 본문 정렬: "center"(가운데, 기본) / "justify"(양쪽 맞춤, 예전 방식). 표 칸 안 글자는 항상 왼쪽.
RADAR_ALIGN = "center"
# naver_upload 로 복사할 때 사진·GIF 한 변 크기(정사각형). 비율이 다르면 자르지 않고 흰색 여백으로 채움. 필수 표기 이미지는 제외.
RADAR_UPLOAD_SIZE = 800
# 본문 사진(📷 NN_사진n)만 프레임을 씌움: 전체 800×800, 바깥 바탕 RADAR_FRAME_BG, 안쪽 사진 칸은 사방 PAD px 안쪽·모서리 RADIUS px 둥글게,
# 사진은 자르지 않고 칸 안에 맞추고 남는 곳은 FILL. False 면 예전 방식(흰 여백 800×800). 표지·GIF·필수 표기 이미지는 해당 없음.
RADAR_PHOTO_FRAME = True
RADAR_FRAME_BG = "#F4F1EC"
RADAR_FRAME_PAD = 27
RADAR_FRAME_RADIUS = 16
RADAR_FRAME_FILL = "#FFFFFF"

# ---------- N데스크피커(radar_naver) 최종본 ⑪: 필수 표기 크기·자동 강조 ----------
# 필수 표기 이미지는 네이버에서 원래 크기로 보여 글자가 본문보다 커 보이므로, naver_upload 로 복사할 때 줄여서 넣는다.
# RADAR_NOTICE_TEXT_PX: 이미지 속 한글 글자 줄 높이(윗끝~아랫끝)가 이 px 이 되게 가로·세로를 같이 줄임(본문 15px 보다 약간 작게 14).
# RADAR_NOTICE_WIDTH: 숫자를 주면 그 가로(px)로 맞춤(둘 다 있으면 WIDTH 우선). None 이면 안 씀.
RADAR_NOTICE_TEXT_PX = 14
RADAR_NOTICE_WIDTH = None
# 숫자+단위 자동 강조(굵게+RADAR_EM_BOLD_COLOR). False 면 끔.
RADAR_AUTO_EM_NUMBERS = True
RADAR_AUTO_EM_UNITS = ["mAh", "Wh", "W", "V", "A", "g", "kg", "mm", "cm", "m", "인치", "Hz", "dB", "%", "원", "만원", "점",
                       "시간", "분", "초", "개", "매", "포트", "단", "년"]
# 결론 문장 자동 형광펜: 원고의 ==강조==(길이 상관없음)가 RADAR_AUTO_HL_MIN 곳 미만일 때만, 마지막 ## 소제목 아래 첫 문장을 형광펜으로. False 면 끔.
RADAR_AUTO_HL_CONCLUSION = True
RADAR_AUTO_HL_MIN = 2
# ---------- N데스크피커(radar_naver) 최종본 ⑫: 가격 확인 배너 ----------
# 각 제휴 링크(naver.me 줄) 바로 위에 "네이버에서 가격 확인하기" 배너 이미지(📷 줄)를 넣음. 흰 글씨/검정 글씨를 번갈아.
# 이미지: static/radar/banner_white.png · banner_black.png (이름 뒤에 .png 가 한 번 더 붙어도 찾음). 가로 RADAR_BANNER_WIDTH px 로 비율 그대로 줄여 naver_upload 에 복사.
RADAR_BANNER = True
RADAR_BANNER_START = "white"
RADAR_BANNER_WIDTH = 430
