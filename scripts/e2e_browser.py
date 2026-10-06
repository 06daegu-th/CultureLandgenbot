"""브라우저 E2E — 실제 화면에서: 홈 → 검색(삼성전자·엔비디아) → 종목 페이지 → 로고가 진짜 그려졌는지 · JS 오류 없는지.
v25: 쉬운 화면(기본)은 토스식 화면(toss.js) — 종목 머리는 .tsk-id / 장 상태 줄은 .tsk-when · 탭 6개 · 뒤로 가기도 본다.

쓰는 법 (서버를 켠 뒤):  python scripts/e2e_browser.py [http://127.0.0.1:8050]
필요: pip install playwright && playwright install chromium   (CI 에는 브라우저가 없어 pytest 대신 이 스크립트로 둔다)
끝에 '통과/실패' 를 출력하고, 실패가 있으면 종료 코드 1.
"""

from __future__ import annotations

import asyncio
import os
import sys


async def main(base: str) -> int:
    from playwright.async_api import async_playwright
    fails: list[str] = []
    async with async_playwright() as p:
        exe = os.environ.get("QUANT_E2E_CHROME")
        b = await p.chromium.launch(**({"executable_path": exe} if exe else {}))
        pg = await (await b.new_context(viewport={"width": 1280, "height": 900})).new_page()
        errs: list[str] = []
        pg.on("pageerror", lambda e: errs.append(str(e)[:160]))
        await pg.goto(f"{base}/#dashboard")
        for word in ("AI가 본 오늘의 종목", "오늘의 주요 이벤트", "내 보유 종목 현황", "오늘 확인할 것", "오늘 주의할 것"):
            try:
                await pg.get_by_text(word).first.wait_for(timeout=20000)  # 처음 계산은 몇 초 걸릴 수 있다
            except Exception:  # noqa: BLE001
                fails.append(f"홈에 '{word}' 없음 (20초 안에 안 나타남)")
        for q, sym in (("삼성전자", "005930"), ("엔비디아", "NVDA")):
            await pg.fill("#search", q)
            await pg.wait_for_timeout(1200)
            item = pg.locator(f'#search-results a[data-sym="{sym}"]')
            if not await item.count():
                fails.append(f"검색 '{q}' → {sym} 없음")
                continue
            ok = await item.locator("img.lg").evaluate("i => i.complete && i.naturalWidth > 0")
            if not ok:
                fails.append(f"검색 결과 {sym} 로고가 그려지지 않음")
            await item.click()
            await pg.wait_for_timeout(3500)
            toss = await pg.locator(".tsk-id").count() > 0
            head, meta = (".tsk-id img.lg", ".tsk-when") if toss else ("#sh-id img.lg", "#sh-meta")
            if not await pg.locator(head).first.evaluate("i => i.complete && i.naturalWidth > 0"):
                fails.append(f"{sym} 종목 머리 로고가 그려지지 않음")
            if not (await pg.locator(meta).inner_text()).strip():
                fails.append(f"{sym} 장 상태·시간 줄이 비어 있음")
            if toss:
                for k in ("chart", "news", "earn", "ai", "sum"):
                    await pg.locator(f'#tsk-tabs button[data-k="{k}"]').click()
                    await pg.wait_for_timeout(1500)
                    if "불러오지 못했어요" in await pg.locator("#tsk-body").inner_text():
                        fails.append(f"{sym} '{k}' 탭을 불러오지 못함")
                await pg.locator(".t-back").click()
                await pg.wait_for_timeout(1500)
                if "#analysis/" in pg.url:
                    fails.append(f"{sym} 뒤로 가기가 동작하지 않음")
        fails += [f"JS 오류: {e}" for e in errs]
        await b.close()
    print("\n".join(fails) if fails else "통과 — 홈 · 검색 · 종목(탭·뒤로) · 로고 · JS 오류 없음")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8050")))
