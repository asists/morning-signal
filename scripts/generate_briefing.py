#!/usr/bin/env python3
"""Generate a static Morning Signal briefing for GitHub Pages.

The script is designed for GitHub Actions. Secrets are read from environment
variables and only the generated JSON files are written to public/data.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_DIR = ROOT / "public"
DATA_DIR = PUBLIC_DIR / "data"
HISTORY_DIR = DATA_DIR / "history"
LATEST_FILE = DATA_DIR / "latest.json"
INDEX_FILE = DATA_DIR / "index.json"
SAMPLE_FILE = DATA_DIR / "sample.json"
KST = ZoneInfo("Asia/Seoul")
SCRIPT_VERSION = "1.1.1"

NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID", "").strip()
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite").strip()
NEWS_HOURS = int(os.getenv("NEWS_HOURS", "72"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "25"))
MAX_ARTICLES = int(os.getenv("MAX_ARTICLES", "70"))
MAX_GEMINI_ARTICLES = int(os.getenv("MAX_GEMINI_ARTICLES", "40"))

NEWS_QUERIES: list[tuple[str, str]] = [
    ("금리·채권", "금리"),
    ("금리·채권", "연준"),
    ("환율·수급", "환율"),
    ("증시", "코스피"),
    ("증시", "미국 증시"),
    ("AI·반도체", "반도체"),
    ("AI·반도체", "인공지능"),
    ("산업", "산업 경제"),
    ("원자재", "국제유가"),
    ("정책·거시", "경제 정책"),
    ("지정학", "국제 정세"),
]

PUBLISHER_ALIASES = {
    "yna.co.kr": "연합뉴스",
    "hankyung.com": "한국경제",
    "mk.co.kr": "매일경제",
    "edaily.co.kr": "이데일리",
    "sedaily.com": "서울경제",
    "newsis.com": "뉴시스",
    "fnnews.com": "파이낸셜뉴스",
    "mt.co.kr": "머니투데이",
    "chosun.com": "조선일보",
    "joongang.co.kr": "중앙일보",
    "donga.com": "동아일보",
    "reuters.com": "Reuters",
    "bloomberg.com": "Bloomberg",
    "cnbc.com": "CNBC",
    "wsj.com": "WSJ",
    "ft.com": "Financial Times",
}


def now_kst() -> datetime:
    return datetime.now(tz=KST)


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    temp.replace(path)


def clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalized_title(title: str) -> str:
    title = clean_text(title).lower()
    title = re.sub(r"\[[^\]]+\]|\([^)]*속보[^)]*\)", " ", title)
    title = re.sub(r"\b(속보|종합|단독|영상|그래픽|포토)\b", " ", title)
    title = re.sub(r"[^0-9a-z가-힣]", "", title)
    return title[:160]


def parse_naver_date(value: str) -> datetime:
    parsed = parsedate_to_datetime(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))
    return parsed.astimezone(KST)


def infer_publisher(url: str) -> str:
    match = re.search(r"https?://(?:www\.)?([^/]+)", url or "")
    if not match:
        return "원문"
    domain = match.group(1).lower()
    for suffix, name in PUBLISHER_ALIASES.items():
        if domain.endswith(suffix):
            return name
    return domain.removeprefix("news.").split(".")[0].upper()


def request_with_retry(method: str, url: str, *, attempts: int = 3, **kwargs: Any) -> requests.Response:
    """일시적인 네트워크·서버 오류만 짧게 재시도합니다."""
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = requests.request(method, url, **kwargs)
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < attempts:
                    time.sleep(1.5 * attempt)
                    continue
            return response
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(1.5 * attempt)
                continue
            raise
    if last_error:
        raise last_error
    raise RuntimeError("API 요청 재시도에 실패했습니다.")


def fetch_naver_group(category: str, query: str) -> list[dict[str, Any]]:
    response = request_with_retry(
        "GET",
        "https://naverapihub.apigw.ntruss.com/search/v1/news",
        params={"query": query, "display": 40, "start": 1, "sort": "date"},
        headers={
            "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
            "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET,
            "User-Agent": "MorningSignalPages/1.0",
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    result: list[dict[str, Any]] = []
    for item in response.json().get("items", []):
        try:
            published = parse_naver_date(item.get("pubDate", ""))
        except Exception:
            continue
        url = item.get("originallink") or item.get("link") or ""
        result.append(
            {
                "category": category,
                "title": clean_text(item.get("title")),
                "description": clean_text(item.get("description")),
                "link": url,
                "naver_link": item.get("link") or "",
                "published_at": published.isoformat(),
                "publisher": infer_publisher(url),
            }
        )
    return result


def dedupe_articles(articles: list[dict[str, Any]], hours: int | None) -> list[dict[str, Any]]:
    if hours is not None:
        cutoff = now_kst() - timedelta(hours=hours)
        articles = [a for a in articles if datetime.fromisoformat(a["published_at"]) >= cutoff]
    articles = sorted(articles, key=lambda a: a["published_at"], reverse=True)

    unique: list[dict[str, Any]] = []
    seen_links: set[str] = set()
    seen_titles: set[str] = set()
    for article in articles:
        link_key = re.sub(r"[?#].*$", "", article["link"])
        title_key = normalized_title(article["title"])
        if not title_key or link_key in seen_links or title_key in seen_titles:
            continue
        seen_links.add(link_key)
        seen_titles.add(title_key)
        article = dict(article)
        article["id"] = len(unique) + 1
        unique.append(article)
        if len(unique) >= MAX_ARTICLES:
            break
    return unique


def collect_news() -> tuple[list[dict[str, Any]], list[str]]:
    """최근 72시간을 우선 사용하고, 부족하면 7일·전체 결과 순으로 자동 확장합니다."""
    articles: list[dict[str, Any]] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=len(NEWS_QUERIES)) as pool:
        futures = {
            pool.submit(fetch_naver_group, category, query): f"{category}/{query}"
            for category, query in NEWS_QUERIES
        }
        for future in as_completed(futures):
            label = futures[future]
            try:
                articles.extend(future.result())
            except Exception as exc:
                errors.append(f"{label}: {type(exc).__name__}")

    if not articles:
        errors.append("네이버 뉴스 검색 결과를 가져오지 못했습니다.")
        return [], errors

    recent = dedupe_articles(articles, NEWS_HOURS)
    if len(recent) >= 3:
        return recent, errors

    weekly = dedupe_articles(articles, 24 * 7)
    if len(weekly) >= 3:
        errors.append(f"최근 {NEWS_HOURS}시간 뉴스가 부족해 최근 7일 범위로 확장했습니다.")
        return weekly, errors

    newest = dedupe_articles(articles, None)
    if newest:
        errors.append(f"고유 뉴스가 {len(newest)}건뿐이어서 확보된 기사만 사용했습니다.")
    return newest, errors


def fetch_yahoo_metric(symbol: str, label: str, kind: str = "number", divisor: float = 1.0) -> dict[str, Any] | None:
    try:
        response = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}",
            params={"range": "5d", "interval": "1d"},
            headers={"User-Agent": "Mozilla/5.0 MorningSignalPages/1.0"},
            timeout=14,
        )
        response.raise_for_status()
        result = response.json()["chart"]["result"][0]
        meta = result.get("meta", {})
        value = meta.get("regularMarketPrice")
        previous = meta.get("chartPreviousClose") or meta.get("previousClose")
        if value is None:
            closes = [x for x in result.get("indicators", {}).get("quote", [{}])[0].get("close", []) if x is not None]
            value = closes[-1] if closes else None
            previous = closes[-2] if len(closes) > 1 else previous
        if value is None:
            return None
        value = float(value) / divisor
        previous = float(previous) / divisor if previous else None
        change_pct = ((value - previous) / previous * 100) if previous else None
        if kind == "percent":
            value_text = f"{value:.2f}%"
        elif kind == "won":
            value_text = f"{value:,.1f}원"
        elif kind == "oil":
            value_text = f"${value:,.2f}"
        else:
            value_text = f"{value:,.2f}"
        return {
            "label": label,
            "value": value_text,
            "change": f"{change_pct:+.2f}%" if change_pct is not None else "",
            "tone": "positive" if (change_pct or 0) >= 0 else "negative",
            "source": "Yahoo Finance",
            "as_of": now_kst().isoformat(),
        }
    except Exception:
        return None


def fetch_fear_greed() -> dict[str, Any] | None:
    try:
        response = requests.get(
            "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
            headers={"User-Agent": "Mozilla/5.0 MorningSignalPages/1.0", "Accept": "application/json"},
            timeout=14,
        )
        response.raise_for_status()
        fg = response.json().get("fear_and_greed", {})
        score = float(fg["score"])
        previous = fg.get("previous_close")
        diff = score - float(previous) if previous is not None else None
        rating = str(fg.get("rating", "")).replace("_", " ").lower()
        rating_ko = {
            "extreme fear": "극단적 공포",
            "fear": "공포",
            "neutral": "중립",
            "greed": "탐욕",
            "extreme greed": "극단적 탐욕",
        }.get(rating, rating or "심리")
        return {
            "label": "공포·탐욕",
            "value": f"{score:.0f} · {rating_ko}",
            "change": f"{diff:+.0f}" if diff is not None else "",
            "tone": "positive" if score >= 55 else "negative" if score <= 45 else "neutral",
            "source": "CNN",
            "as_of": now_kst().isoformat(),
        }
    except Exception:
        return None


def collect_market_metrics() -> list[dict[str, Any]]:
    specs = [
        ("^VIX", "VIX", "number", 1.0),
        ("^GSPC", "S&P 500", "number", 1.0),
        ("^IXIC", "NASDAQ", "number", 1.0),
        ("^KS11", "KOSPI", "number", 1.0),
        ("^KQ11", "KOSDAQ", "number", 1.0),
        ("^TNX", "미 10년물", "percent", 10.0),
        ("DX-Y.NYB", "달러인덱스", "number", 1.0),
        ("KRW=X", "원/달러", "won", 1.0),
        ("CL=F", "WTI", "oil", 1.0),
    ]
    metrics: list[dict[str, Any]] = []
    fg = fetch_fear_greed()
    if fg:
        metrics.append(fg)
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(fetch_yahoo_metric, *spec) for spec in specs]
        for future in futures:
            item = future.result()
            if item:
                metrics.append(item)
    order = {name: i for i, name in enumerate(["공포·탐욕", "VIX", "S&P 500", "NASDAQ", "KOSPI", "KOSDAQ", "미 10년물", "달러인덱스", "원/달러", "WTI"])}
    metrics.sort(key=lambda m: order.get(m["label"], 99))
    return metrics


def compact_articles(articles: list[dict[str, Any]]) -> str:
    blocks = []
    for article in articles:
        blocks.append(
            f"[{article['id']}] ({article['category']}) {article['title']}\n"
            f"요약: {article['description'][:320]}\n"
            f"발행: {article['published_at']} / 매체: {article['publisher']}"
        )
    return "\n\n".join(blocks)


def history_context(limit: int = 30) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not HISTORY_DIR.exists():
        return rows
    for path in sorted(HISTORY_DIR.glob("*.json"))[-limit:]:
        data = read_json(path) or {}
        if (data.get("meta") or {}).get("mode") == "sample":
            continue
        rows.append(
            {
                "date": path.stem,
                "headline": data.get("headline", ""),
                "change": data.get("change_from_yesterday", ""),
                "stories": [story.get("title", "") for story in data.get("stories", [])],
                "stance": (data.get("stance") or {}).get("title", ""),
            }
        )
    return rows


def briefing_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "headline": {"type": "string"},
            "deck": {"type": "string"},
            "highlight": {"type": "string"},
            "change_from_yesterday": {"type": "string"},
            "stories": {
                "type": "array", "minItems": 3, "maxItems": 3,
                "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {
                        "category": {"type": "string"},
                        "impact": {"type": "string", "enum": ["높음", "보통", "낮음"]},
                        "title": {"type": "string"},
                        "summary": {"type": "string"},
                        "why": {"type": "string"},
                        "korea": {"type": "string"},
                        "investor_view": {"type": "string"},
                        "source_ids": {"type": "array", "minItems": 1, "maxItems": 5, "items": {"type": "integer"}},
                    },
                    "required": ["category", "impact", "title", "summary", "why", "korea", "investor_view", "source_ids"],
                },
            },
            "overnight_summary": {"type": "string"},
            "overnight_facts": {
                "type": "array", "minItems": 3, "maxItems": 5,
                "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {"label": {"type": "string"}, "value": {"type": "string"}},
                    "required": ["label", "value"],
                },
            },
            "stance": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"}, "summary": {"type": "string"},
                    "attitude": {"type": "string"}, "interest": {"type": "string"},
                    "caution": {"type": "string"}, "execution": {"type": "string"},
                },
                "required": ["title", "summary", "attitude", "interest", "caution", "execution"],
            },
            "schedule": {
                "type": "array", "maxItems": 6,
                "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {"time": {"type": "string"}, "event": {"type": "string"}, "note": {"type": "string"}},
                    "required": ["time", "event", "note"],
                },
            },
            "weekly": {
                "type": "object", "additionalProperties": False,
                "properties": {"title": {"type": "string"}, "summary": {"type": "string"}, "themes": {"type": "array", "maxItems": 4, "items": {"type": "string"}}},
                "required": ["title", "summary", "themes"],
            },
            "monthly": {
                "type": "object", "additionalProperties": False,
                "properties": {"title": {"type": "string"}, "summary": {"type": "string"}, "watch": {"type": "array", "maxItems": 4, "items": {"type": "string"}}},
                "required": ["title", "summary", "watch"],
            },
            "risks": {"type": "array", "minItems": 2, "maxItems": 4, "items": {"type": "string"}},
        },
        "required": ["headline", "deck", "highlight", "change_from_yesterday", "stories", "overnight_summary", "overnight_facts", "stance", "schedule", "weekly", "monthly", "risks"],
    }


def safe_api_error(exc: Exception) -> str:
    """API 키·요청 URL을 제외하고 상태 코드와 짧은 오류명만 남깁니다."""
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        status = exc.response.status_code
        code = "HTTP_ERROR"
        try:
            body = exc.response.json()
            error = body.get("error") or {}
            code = clean_text(error.get("status") or error.get("code") or code)
        except Exception:
            pass
        code = re.sub(r"[^0-9A-Za-z_.-]", "_", code)[:80]
        return f"HTTP {status} · {code}"
    return type(exc).__name__


def call_gemini(articles: list[dict[str, Any]], metrics: list[dict[str, Any]], previous: dict[str, Any] | None, history: list[dict[str, Any]]) -> dict[str, Any]:
    previous_context: dict[str, Any] | str = "없음"
    if previous and (previous.get("meta") or {}).get("mode") != "sample":
        previous_context = {
            "headline": previous.get("headline"),
            "change": previous.get("change_from_yesterday"),
            "stories": [s.get("title") for s in previous.get("stories", [])],
            "stance": (previous.get("stance") or {}).get("title"),
        }

    schema_text = json.dumps(briefing_schema(), ensure_ascii=False)
    prompt = f"""
당신은 한국 주식 투자자를 위한 Morning Signal 편집자다.
아래 제공된 뉴스 제목·요약과 시장 지표만 근거로 한국어 아침 브리핑을 작성하라.

핵심 원칙:
- 같은 사건의 반복 보도는 하나의 핵심 이슈로 묶는다.
- 사실과 해석을 분리하고, 기사에 없는 사실·수치·일정·종목 전망은 만들지 않는다.
- 투자 관점은 매수·매도 추천이 아니라 확인해야 할 조건, 수급 경로, 유리·불리한 업종과 위험 신호로 쓴다.
- 국내 증시 영향은 가능하면 금리 → 달러·원화 → 외국인 수급 → 업종 순으로 설명한다.
- source_ids는 반드시 제공 기사 ID 중에서만 고른다.
- headline은 짧고 단정한 결론, deck은 최대 2문장, highlight는 deck에 실제 포함된 짧은 구절로 쓴다.
- 오늘 일정은 기사 안에서 날짜·시간이 명확히 확인될 때만 넣고, 불명확하면 빈 배열로 둔다.
- risks는 오늘의 기준 시나리오를 깨뜨릴 수 있는 반대 요인을 쓴다.
- weekly는 최근 7일 데이터가 부족하면 확보된 기간만 분석했다고 명시한다.
- monthly는 최근 30일 데이터가 부족하면 데이터 축적 중임을 명시한다.
- 반드시 JSON 객체 하나만 출력하고, 아래 JSON Schema의 키와 자료형을 지킨다.

JSON Schema:
{schema_text}

시장 지표:
{json.dumps(metrics, ensure_ascii=False)}

이전 브리핑:
{json.dumps(previous_context, ensure_ascii=False)}

최근 일간 브리핑 누적 요약:
{json.dumps(history, ensure_ascii=False)}

기사 목록:
{compact_articles(articles[:MAX_GEMINI_ARTICLES])}
""".strip()

    response = request_with_retry(
        "POST",
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": GEMINI_API_KEY,
        },
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 7000,
                "responseMimeType": "application/json",
            },
        },
        timeout=120,
    )
    response.raise_for_status()
    payload = response.json()
    candidates = payload.get("candidates") or []
    if not candidates:
        raise RuntimeError("Gemini 응답에 후보가 없습니다.")
    parts = candidates[0].get("content", {}).get("parts", [])
    output_text = "".join(part.get("text", "") for part in parts)
    if not output_text:
        raise RuntimeError("Gemini가 분석 JSON을 반환하지 않았습니다.")
    analysis = json.loads(output_text)
    if not isinstance(analysis, dict):
        raise RuntimeError("Gemini 분석 결과가 JSON 객체가 아닙니다.")
    return analysis


def fallback_analysis(articles: list[dict[str, Any]]) -> dict[str, Any]:
    selected: list[dict[str, Any]] = []
    categories: set[str] = set()
    for article in articles:
        if article["category"] in categories and len(selected) < 2:
            continue
        selected.append(article)
        categories.add(article["category"])
        if len(selected) == 3:
            break
    while len(selected) < 3:
        selected.append(articles[len(selected) % len(articles)])
    stories = []
    for article in selected:
        stories.append({
            "category": article["category"], "impact": "보통", "title": article["title"],
            "summary": article["description"] or "관련 보도가 이어지고 있습니다.",
            "why": "시장 가격과 수급이 이 뉴스에 실제로 반응하는지 확인할 필요가 있습니다.",
            "korea": "환율과 외국인 수급, 관련 업종의 상대 강도를 함께 확인하세요.",
            "investor_view": "단일 기사만 보고 추격하지 말고 거래량과 후속 보도를 확인하는 편이 좋습니다.",
            "source_ids": [article["id"]],
        })
    return {
        "headline": "실제 뉴스 수집, AI 분석 대기",
        "deck": "최신 뉴스는 수집했지만 Gemini 분석에 실패해 기본 요약으로 표시합니다. 다음 자동 실행에서 다시 분석합니다.",
        "highlight": "기본 요약으로 표시합니다",
        "change_from_yesterday": "AI 분석이 복구되면 이전 브리핑과의 변화가 다시 표시됩니다.",
        "stories": stories,
        "overnight_summary": "수집 시점 기준으로 가장 최근의 주요 이슈를 우선 배치했습니다.",
        "overnight_facts": [{"label": "수집 기사", "value": f"{len(articles)}건"}, {"label": "분석 상태", "value": "기본 요약"}, {"label": "다음 갱신", "value": "내일 08:00"}],
        "stance": {"title": "확인 우선", "summary": "AI 분석이 일시적으로 불안정하므로 원문과 가격 반응을 우선 확인하세요.", "attitude": "추격보다 사실 확인", "interest": "반복 보도가 늘어난 이슈", "caution": "단일 기사 기반 테마", "execution": "외국인 수급과 거래량이 뉴스 방향과 일치하는지 확인하세요."},
        "schedule": [],
        "weekly": {"title": "이번 주 흐름", "summary": "정상 분석 데이터가 누적되면 주간 흐름을 제공합니다.", "themes": []},
        "monthly": {"title": "이번 달 흐름", "summary": "정상 분석 데이터가 누적되면 월간 구조 변화를 제공합니다.", "watch": []},
        "risks": ["Gemini 분석이 일시적으로 실패했습니다.", "뉴스와 실제 시장 반응이 다를 수 있습니다."],
    }


def enrich_sources(analysis: dict[str, Any], articles: list[dict[str, Any]]) -> None:
    by_id = {a["id"]: a for a in articles}
    for story in analysis.get("stories", []):
        ids = story.pop("source_ids", [])
        sources = []
        for source_id in ids:
            article = by_id.get(source_id)
            if not article:
                continue
            sources.append({
                "id": source_id,
                "publisher": article["publisher"],
                "title": article["title"],
                "url": article["link"],
                "published_at": article["published_at"],
            })
        story["sources"] = sources
        story["source_count"] = len(sources)


def update_index() -> None:
    rows: list[dict[str, Any]] = []
    for path in sorted(HISTORY_DIR.glob("*.json"), reverse=True):
        data = read_json(path) or {}
        rows.append({
            "date": path.stem,
            "title": data.get("headline", path.stem),
            "mode": (data.get("meta") or {}).get("mode", "unknown"),
        })
    write_json(INDEX_FILE, {"updated_at": now_kst().isoformat(), "dates": rows[:365]})


def write_sample_mode(message: str) -> None:
    sample = read_json(SAMPLE_FILE)
    if not sample:
        raise RuntimeError("sample.json 파일이 없습니다.")
    sample["meta"] = {
        **(sample.get("meta") or {}),
        "mode": "sample",
        "generated_at": now_kst().isoformat(),
        "message": message,
        "model": GEMINI_MODEL,
    }
    write_json(LATEST_FILE, sample)
    update_index()



def redact_secrets(value: Any) -> Any:
    secrets = [secret for secret in (NAVER_CLIENT_ID, NAVER_CLIENT_SECRET, GEMINI_API_KEY) if secret]
    if isinstance(value, dict):
        return {key: redact_secrets(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, str):
        redacted = value
        for secret in secrets:
            redacted = redacted.replace(secret, "[REDACTED]")
        return redacted
    return value


def has_valid_previous(previous: dict[str, Any] | None) -> bool:
    return bool(previous and len(previous.get("stories", [])) == 3 and previous.get("headline"))


def generate(force_sample: bool = False) -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    previous = read_json(LATEST_FILE)

    if force_sample or not (NAVER_CLIENT_ID and NAVER_CLIENT_SECRET):
        write_sample_mode("GitHub Secrets에 네이버 API 키를 넣으면 실제 뉴스로 전환됩니다.")
        return read_json(LATEST_FILE) or {}

    articles, collection_errors = collect_news()
    generated_at = now_kst()

    if not articles:
        if has_valid_previous(previous):
            stale = json.loads(json.dumps(previous, ensure_ascii=False))
            stale["meta"] = {
                **(stale.get("meta") or {}),
                "mode": "stale",
                "generated_at": generated_at.isoformat(),
                "message": "새 뉴스를 가져오지 못해 직전 브리핑을 유지합니다.",
                "collection_warnings": collection_errors,
                "error": "네이버 뉴스 수집 실패",
            }
            stale = redact_secrets(stale)
            write_json(LATEST_FILE, stale)
            update_index()
            return stale
        write_sample_mode("새 뉴스를 가져오지 못해 샘플 브리핑을 표시합니다.")
        return read_json(LATEST_FILE) or {}

    metrics = collect_market_metrics()
    analysis_error = ""
    if len(articles) < 3:
        analysis_error = f"고유 뉴스가 {len(articles)}건뿐이어서 기본 요약을 사용했습니다."
        analysis = fallback_analysis(articles)
        mode = "news-only"
    elif GEMINI_API_KEY:
        try:
            analysis = call_gemini(articles, metrics, previous, history_context())
            mode = "live"
        except Exception as exc:
            analysis_error = f"Gemini 분석 실패: {safe_api_error(exc)}"
            analysis = fallback_analysis(articles)
            mode = "news-only"
    else:
        analysis_error = "GEMINI_API_KEY가 설정되지 않아 기본 요약을 사용했습니다."
        analysis = fallback_analysis(articles)
        mode = "news-only"

    enrich_sources(analysis, articles)
    payload: dict[str, Any] = {
        "meta": {
            "mode": mode,
            "generated_at": generated_at.isoformat(),
            "article_count": len(articles),
            "model": GEMINI_MODEL,
            "message": "실제 뉴스와 Gemini 분석이 연결되었습니다." if mode == "live" else "실제 뉴스는 연결됐지만 AI 분석은 기본 요약으로 표시됩니다.",
            "collection_warnings": collection_errors,
            "error": analysis_error,
        },
        **analysis,
        "metrics": metrics,
    }
    payload = redact_secrets(payload)
    date_key = generated_at.strftime("%Y-%m-%d")
    write_json(LATEST_FILE, payload)
    write_json(HISTORY_DIR / f"{date_key}.json", payload)
    update_index()
    return payload


def validate_payload(payload: dict[str, Any]) -> None:
    required = ["meta", "headline", "deck", "stories", "stance", "weekly", "monthly"]
    missing = [key for key in required if key not in payload]
    if missing:
        raise RuntimeError(f"생성 결과에 필수 항목이 없습니다: {', '.join(missing)}")
    if len(payload.get("stories", [])) != 3:
        raise RuntimeError("핵심 뉴스는 정확히 3개여야 합니다.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", action="store_true", help="API를 호출하지 않고 샘플 모드로 생성")
    parser.add_argument("--validate-only", action="store_true", help="현재 latest.json만 검증")
    args = parser.parse_args()
    try:
        if args.validate_only:
            payload = read_json(LATEST_FILE) or {}
        else:
            payload = generate(force_sample=args.sample)
        validate_payload(payload)
        meta = payload.get("meta", {})
        print(json.dumps({
            "ok": True,
            "script_version": SCRIPT_VERSION,
            "mode": meta.get("mode"),
            "generated_at": meta.get("generated_at"),
            "article_count": meta.get("article_count"),
            "headline": payload.get("headline"),
        }, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"Morning Signal 생성 실패: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
