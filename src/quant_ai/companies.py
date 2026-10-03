"""기업 마스터 — 종목 하나의 '신분증'. 검색·관심종목·포트폴리오·뉴스·종목 화면이 모두 여기서 같은 이름·로고·거래소를 받는다.

필드: symbol · name(한글) · name_en · country · exchange · currency · website · sector · industry · isin · logo
  - 주요 종목(국내 시총 상위 · 많이 찾는 미국 종목·ETF)은 아래 표에 직접 적어 둔다 → 네트워크·DB 가 없어도 같은 값.
  - 그 밖의 종목은 DB(instruments: 이름·업종·통화)에서 채운다 → identity() 는 어떤 종목이든 항상 같은 모양을 돌려준다.
  - logo: 'bundled' (프로젝트에 들어 있는 로고 — assets/logos) · 'cache' (받아 둔 로고) · 'fetch' (아직 없음 → 받는 중/기본 아이콘)
ISIN 은 체크 숫자를 검사한다 (잘못 적은 값은 None). 국내 보통주는 종목코드로 계산한다.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

LOGO_DIR = Path(__file__).resolve().parent / "assets" / "logos"


@dataclass(frozen=True)
class Company:
    symbol: str
    name: str
    name_en: str | None = None
    country: str = "KR"
    exchange: str = "KOSPI"
    currency: str = "KRW"
    website: str | None = None
    sector: str | None = None
    industry: str | None = None
    isin: str | None = None


def isin_ok(isin: str | None) -> bool:
    """ISIN 체크 숫자 검사 (Luhn — 글자는 A=10 … Z=35 로 바꿔서)."""
    if not isin or len(isin) != 12 or not isin[:2].isalpha() or not isin[-1].isdigit():
        return False
    digits = "".join(str(int(c, 36)) for c in isin[:-1].upper())
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            d = d - 9 if d > 9 else d
        total += d
    return (10 - total % 10) % 10 == int(isin[-1])


def kr_isin(code: str) -> str | None:
    """국내 보통주 ISIN = KR7 + 종목코드 + 00 + 체크 숫자 (우선주 등 끝자리가 0 이 아닌 코드는 규칙이 달라 계산하지 않음)."""
    if not (len(code) == 6 and code.isdigit() and code.endswith("0")):
        return None
    for c in "0123456789":
        if isin_ok(f"KR7{code}00{c}"):
            return f"KR7{code}00{c}"
    return None


# 국내: (코드, 이름, 영문, 거래소, 업종, 세부 업종, 홈페이지)
_KR = [
    ("005930", "삼성전자", "Samsung Electronics", "KOSPI", "IT", "반도체·전자", "samsung.com"),
    ("005935", "삼성전자우", "Samsung Electronics (Pref.)", "KOSPI", "IT", "반도체·전자", "samsung.com"),
    ("000660", "SK하이닉스", "SK hynix", "KOSPI", "IT", "반도체", "skhynix.com"),
    ("373220", "LG에너지솔루션", "LG Energy Solution", "KOSPI", "산업재", "2차전지", "lgensol.com"),
    ("207940", "삼성바이오로직스", "Samsung Biologics", "KOSPI", "헬스케어", "바이오 위탁생산", "samsungbiologics.com"),
    ("005380", "현대차", "Hyundai Motor", "KOSPI", "경기소비재", "자동차", "hyundai.com"),
    ("000270", "기아", "Kia", "KOSPI", "경기소비재", "자동차", "kia.com"),
    ("068270", "셀트리온", "Celltrion", "KOSPI", "헬스케어", "바이오의약품", "celltrion.com"),
    ("005490", "POSCO홀딩스", "POSCO Holdings", "KOSPI", "소재", "철강", "posco-holdings.com"),
    ("035420", "NAVER", "NAVER", "KOSPI", "커뮤니케이션", "인터넷", "navercorp.com"),
    ("035720", "카카오", "Kakao", "KOSPI", "커뮤니케이션", "인터넷", "kakaocorp.com"),
    ("051910", "LG화학", "LG Chem", "KOSPI", "소재", "화학", "lgchem.com"),
    ("006400", "삼성SDI", "Samsung SDI", "KOSPI", "IT", "2차전지", "samsungsdi.com"),
    ("105560", "KB금융", "KB Financial Group", "KOSPI", "금융", "은행", "kbfg.com"),
    ("055550", "신한지주", "Shinhan Financial Group", "KOSPI", "금융", "은행", "shinhangroup.com"),
    ("012330", "현대모비스", "Hyundai Mobis", "KOSPI", "경기소비재", "자동차 부품", "mobis.co.kr"),
    ("028260", "삼성물산", "Samsung C&T", "KOSPI", "산업재", "건설·상사", "samsungcnt.com"),
    ("066570", "LG전자", "LG Electronics", "KOSPI", "경기소비재", "가전", "lg.com"),
    ("003550", "LG", "LG Corp.", "KOSPI", "산업재", "지주회사", "lgcorp.com"),
    ("096770", "SK이노베이션", "SK Innovation", "KOSPI", "에너지", "정유·배터리", "skinnovation.com"),
    ("017670", "SK텔레콤", "SK Telecom", "KOSPI", "커뮤니케이션", "통신", "sktelecom.com"),
    ("030200", "KT", "KT", "KOSPI", "커뮤니케이션", "통신", "kt.com"),
    ("015760", "한국전력", "KEPCO", "KOSPI", "유틸리티", "전력", "kepco.co.kr"),
    ("032830", "삼성생명", "Samsung Life", "KOSPI", "금융", "보험", "samsunglife.com"),
    ("086790", "하나금융지주", "Hana Financial Group", "KOSPI", "금융", "은행", "hanafn.com"),
    ("316140", "우리금융지주", "Woori Financial Group", "KOSPI", "금융", "은행", "woorifg.com"),
    ("033780", "KT&G", "KT&G", "KOSPI", "필수소비재", "담배", "ktng.com"),
    ("009150", "삼성전기", "Samsung Electro-Mechanics", "KOSPI", "IT", "전자부품", "samsungsem.com"),
    ("018260", "삼성에스디에스", "Samsung SDS", "KOSPI", "IT", "IT 서비스", "samsungsds.com"),
    ("010130", "고려아연", "Korea Zinc", "KOSPI", "소재", "비철금속", "koreazinc.co.kr"),
    ("011200", "HMM", "HMM", "KOSPI", "산업재", "해운", "hmm21.com"),
    ("259960", "크래프톤", "Krafton", "KOSPI", "커뮤니케이션", "게임", "krafton.com"),
    ("323410", "카카오뱅크", "KakaoBank", "KOSPI", "금융", "은행", "kakaobank.com"),
    ("377300", "카카오페이", "Kakao Pay", "KOSPI", "금융", "결제", "kakaopay.com"),
    ("036570", "엔씨소프트", "NCSOFT", "KOSPI", "커뮤니케이션", "게임", "ncsoft.com"),
    ("010950", "S-Oil", "S-Oil", "KOSPI", "에너지", "정유", "s-oil.com"),
    ("012450", "한화에어로스페이스", "Hanwha Aerospace", "KOSPI", "산업재", "방산·항공", "hanwhaaerospace.com"),
    ("042660", "한화오션", "Hanwha Ocean", "KOSPI", "산업재", "조선", "hanwhaocean.com"),
    ("000810", "삼성화재", "Samsung Fire & Marine", "KOSPI", "금융", "보험", "samsungfire.com"),
    ("034220", "LG디스플레이", "LG Display", "KOSPI", "IT", "디스플레이", "lgdisplay.com"),
    ("011070", "LG이노텍", "LG Innotek", "KOSPI", "IT", "전자부품", "lginnotek.com"),
    ("051900", "LG생활건강", "LG H&H", "KOSPI", "필수소비재", "화장품", "lghnh.com"),
    ("032640", "LG유플러스", "LG Uplus", "KOSPI", "커뮤니케이션", "통신", "lguplus.com"),
    ("003490", "대한항공", "Korean Air", "KOSPI", "산업재", "항공", "koreanair.com"),
    ("352820", "하이브", "HYBE", "KOSPI", "커뮤니케이션", "엔터테인먼트", "hybecorp.com"),
    ("010140", "삼성중공업", "Samsung Heavy Industries", "KOSPI", "산업재", "조선", "samsungshi.com"),
    ("016360", "삼성증권", "Samsung Securities", "KOSPI", "금융", "증권", "samsungpop.com"),
    ("329180", "HD현대중공업", "HD Hyundai Heavy Industries", "KOSPI", "산업재", "조선", "hd.com"),
    ("267250", "HD현대", "HD Hyundai", "KOSPI", "산업재", "지주회사", "hd.com"),
    ("034020", "두산에너빌리티", "Doosan Enerbility", "KOSPI", "산업재", "발전 설비", "doosanenerbility.com"),
    ("064350", "현대로템", "Hyundai Rotem", "KOSPI", "산업재", "방산·철도", "hyundai-rotem.co.kr"),
    ("006800", "미래에셋증권", "Mirae Asset Securities", "KOSPI", "금융", "증권", "miraeasset.com"),
    ("021240", "코웨이", "Coway", "KOSPI", "경기소비재", "생활가전", "coway.com"),
    ("247540", "에코프로비엠", "EcoPro BM", "KOSDAQ", "산업재", "2차전지 소재", "ecopro.co.kr"),
    ("086520", "에코프로", "EcoPro", "KOSDAQ", "산업재", "2차전지 소재", "ecopro.co.kr"),
    ("196170", "알테오젠", "Alteogen", "KOSDAQ", "헬스케어", "바이오", "alteogen.com"),
    ("028300", "HLB", "HLB", "KOSDAQ", "헬스케어", "바이오", "hlbpharma.com"),
    ("042700", "한미반도체", "Hanmi Semiconductor", "KOSPI", "IT", "반도체 장비", "hanmisemi.com"),
    ("263750", "펄어비스", "Pearl Abyss", "KOSDAQ", "커뮤니케이션", "게임", "pearlabyss.com"),
    ("293490", "카카오게임즈", "Kakao Games", "KOSDAQ", "커뮤니케이션", "게임", "kakaogames.com"),
    ("035900", "JYP Ent.", "JYP Entertainment", "KOSDAQ", "커뮤니케이션", "엔터테인먼트", "jype.com"),
    ("041510", "에스엠", "SM Entertainment", "KOSDAQ", "커뮤니케이션", "엔터테인먼트", "smentertainment.com"),
]

# 미국: (티커, 이름, 영문, 거래소, 업종, 세부 업종, 홈페이지, ISIN)
_US = [
    ("NVDA", "엔비디아", "NVIDIA", "NASDAQ", "IT", "반도체", "nvidia.com", "US67066G1040"),
    ("AAPL", "애플", "Apple", "NASDAQ", "IT", "하드웨어", "apple.com", "US0378331005"),
    ("MSFT", "마이크로소프트", "Microsoft", "NASDAQ", "IT", "소프트웨어", "microsoft.com", "US5949181045"),
    ("GOOGL", "알파벳(구글)", "Alphabet Class A", "NASDAQ", "커뮤니케이션", "인터넷", "abc.xyz", "US02079K3059"),
    ("GOOG", "알파벳(구글) C", "Alphabet Class C", "NASDAQ", "커뮤니케이션", "인터넷", "abc.xyz", "US02079K1079"),
    ("AMZN", "아마존", "Amazon", "NASDAQ", "경기소비재", "전자상거래·클라우드", "amazon.com", "US0231351067"),
    ("META", "메타", "Meta Platforms", "NASDAQ", "커뮤니케이션", "인터넷", "meta.com", "US30303M1027"),
    ("TSLA", "테슬라", "Tesla", "NASDAQ", "경기소비재", "전기차", "tesla.com", "US88160R1014"),
    ("AVGO", "브로드컴", "Broadcom", "NASDAQ", "IT", "반도체", "broadcom.com", "US11135F1012"),
    ("TSM", "TSMC", "Taiwan Semiconductor (ADR)", "NYSE", "IT", "반도체 위탁생산", "tsmc.com", "US8740391003"),
    ("AMD", "AMD", "Advanced Micro Devices", "NASDAQ", "IT", "반도체", "amd.com", "US0079031078"),
    ("NFLX", "넷플릭스", "Netflix", "NASDAQ", "커뮤니케이션", "스트리밍", "netflix.com", "US64110L1061"),
    ("PLTR", "팔란티어", "Palantir", "NASDAQ", "IT", "소프트웨어", "palantir.com", "US69608A1088"),
    ("INTC", "인텔", "Intel", "NASDAQ", "IT", "반도체", "intel.com", "US4581401001"),
    ("QCOM", "퀄컴", "Qualcomm", "NASDAQ", "IT", "반도체", "qualcomm.com", "US7475251036"),
    ("MU", "마이크론", "Micron Technology", "NASDAQ", "IT", "메모리 반도체", "micron.com", "US5951121038"),
    ("ASML", "ASML", "ASML Holding", "NASDAQ", "IT", "반도체 장비", "asml.com", None),
    ("ARM", "ARM", "Arm Holdings (ADR)", "NASDAQ", "IT", "반도체 설계", "arm.com", None),
    ("SMCI", "슈퍼마이크로", "Super Micro Computer", "NASDAQ", "IT", "서버", "supermicro.com", None),
    ("ORCL", "오라클", "Oracle", "NYSE", "IT", "소프트웨어", "oracle.com", "US68389X1054"),
    ("CRM", "세일즈포스", "Salesforce", "NYSE", "IT", "소프트웨어", "salesforce.com", "US79466L3024"),
    ("ADBE", "어도비", "Adobe", "NASDAQ", "IT", "소프트웨어", "adobe.com", "US00724F1012"),
    ("COST", "코스트코", "Costco", "NASDAQ", "필수소비재", "할인점", "costco.com", "US22160K1051"),
    ("WMT", "월마트", "Walmart", "NYSE", "필수소비재", "할인점", "walmart.com", "US9311421039"),
    ("JPM", "JP모건", "JPMorgan Chase", "NYSE", "금융", "은행", "jpmorganchase.com", "US46625H1005"),
    ("V", "비자", "Visa", "NYSE", "금융", "결제", "visa.com", "US92826C8394"),
    ("MA", "마스터카드", "Mastercard", "NYSE", "금융", "결제", "mastercard.com", "US57636Q1040"),
    ("KO", "코카콜라", "Coca-Cola", "NYSE", "필수소비재", "음료", "coca-colacompany.com", "US1912161007"),
    ("PEP", "펩시코", "PepsiCo", "NASDAQ", "필수소비재", "음료·식품", "pepsico.com", "US7134481081"),
    ("DIS", "디즈니", "Walt Disney", "NYSE", "커뮤니케이션", "미디어", "thewaltdisneycompany.com", "US2546871060"),
    ("NKE", "나이키", "Nike", "NYSE", "경기소비재", "의류·신발", "nike.com", "US6541061031"),
    ("BRK-B", "버크셔 해서웨이", "Berkshire Hathaway B", "NYSE", "금융", "지주·보험", "berkshirehathaway.com", "US0846707026"),
    ("LLY", "일라이 릴리", "Eli Lilly", "NYSE", "헬스케어", "제약", "lilly.com", "US5324571083"),
    ("NVO", "노보 노디스크", "Novo Nordisk (ADR)", "NYSE", "헬스케어", "제약", "novonordisk.com", None),
    ("UNH", "유나이티드헬스", "UnitedHealth", "NYSE", "헬스케어", "건강보험", "unitedhealthgroup.com", "US91324P1021"),
    ("XOM", "엑슨모빌", "Exxon Mobil", "NYSE", "에너지", "석유", "exxonmobil.com", "US30231G1022"),
    ("COIN", "코인베이스", "Coinbase", "NASDAQ", "금융", "가상자산 거래소", "coinbase.com", "US19260Q1076"),
    ("MSTR", "스트래티지", "Strategy (MicroStrategy)", "NASDAQ", "IT", "소프트웨어·비트코인", "strategy.com", None),
    ("IONQ", "아이온큐", "IonQ", "NYSE", "IT", "양자컴퓨터", "ionq.com", None),
    ("UBER", "우버", "Uber", "NYSE", "산업재", "모빌리티", "uber.com", "US90353T1007"),
    ("BABA", "알리바바", "Alibaba (ADR)", "NYSE", "경기소비재", "전자상거래", "alibabagroup.com", None),
    ("SPY", "S&P500 ETF (SPY)", "SPDR S&P 500 ETF", "NYSE Arca", "ETF", "지수 ETF", "ssga.com", "US78462F1030"),
    ("QQQ", "나스닥100 ETF (QQQ)", "Invesco QQQ", "NASDAQ", "ETF", "지수 ETF", "invesco.com", "US46090E1038"),
    ("SOXX", "반도체 ETF (SOXX)", "iShares Semiconductor ETF", "NASDAQ", "ETF", "업종 ETF", "ishares.com", None),
    ("TQQQ", "나스닥 3배 (TQQQ)", "ProShares UltraPro QQQ", "NASDAQ", "ETF", "레버리지 ETF", "proshares.com", None),
    ("SCHD", "배당 ETF (SCHD)", "Schwab US Dividend Equity ETF", "NYSE Arca", "ETF", "배당 ETF", "schwab.com", None),
]


@lru_cache(maxsize=1)
def master() -> dict[str, Company]:
    out: dict[str, Company] = {}
    for code, name, en, ex, sec, ind, web in _KR:
        out[code] = Company(code, name, en, "KR", ex, "KRW", web, sec, ind, kr_isin(code))
    for sym, name, en, ex, sec, ind, web, isin in _US:
        out[sym] = Company(sym, name, en, "US", ex, "USD", web, sec, ind, isin if isin_ok(isin) else None)
    return out


@lru_cache(maxsize=1)
def bundled() -> dict[str, str]:
    """프로젝트에 들어 있는 로고 목록 {종목: 원본}."""
    p = LOGO_DIR / "index.json"
    return json.loads(p.read_text()) if p.exists() else {}


def bundled_logo(sym: str) -> bytes | None:
    f = LOGO_DIR / f"{sym.upper()}.svg"
    return f.read_bytes() if sym.upper() in bundled() and f.exists() else None


def is_etf(sym: str, name: str | None = None) -> bool:
    c = master().get(sym)
    return bool(c and c.sector == "ETF") or bool(name and "ETF" in name.upper())


def identity(app, sym: str, name: str | None = None) -> dict:
    """어떤 종목이든 같은 모양 — 화면은 이것 하나로 로고·이름·티커·거래소를 그린다."""
    sym = (sym or "").strip().upper() if not (sym or "").strip().isdigit() else sym.strip()
    c = master().get(sym)
    d = asdict(c) if c else {"symbol": sym, "name": name or sym, "name_en": None, "country": "KR" if sym[:1].isdigit() else "US",
                              "exchange": None, "currency": "KRW" if sym[:1].isdigit() else "USD", "website": None,
                              "sector": None, "industry": None, "isin": kr_isin(sym) if sym[:1].isdigit() else None}
    if not c and app is not None:
        try:
            from sqlalchemy import select

            from .data.db import session_scope
            from .data.models import Instrument
            with session_scope(app.engine) as s:
                row = s.scalars(select(Instrument).where(Instrument.symbol == sym)).first()
                if row:
                    if row.name and row.name != sym:
                        d["name"] = row.name
                    d["sector"] = d["sector"] or row.sector
                    d["currency"] = row.currency or d["currency"]
                    if row.market in ("KOSPI", "KOSDAQ", "KONEX"):
                        d["exchange"] = row.market
        except Exception:  # noqa: BLE001, S110 - DB 가 없어도 기본값으로
            pass
    if not c and d["country"] == "US":
        from .data.global_stocks import global_name
        d["name"] = global_name(sym) or d["name"]
    d["logo"] = "bundled" if sym in bundled() else "fetch"
    d["logo_url"] = f"/api/logo/{sym}"
    d["etf"] = is_etf(sym, d["name"])
    return d


__all__ = ["Company", "master", "identity", "bundled", "bundled_logo", "isin_ok", "kr_isin", "is_etf"]
