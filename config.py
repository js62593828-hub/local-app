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
