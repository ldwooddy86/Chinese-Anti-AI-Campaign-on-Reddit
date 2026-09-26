#!/usr/bin/env python3
"""DALABA (大喇叭, "the big loudspeaker"): GHOST LOOM's official-voice connector.

DALABA listens to what the Chinese party-state says in public and in its own name: state-media
RSS feeds, the Foreign Ministry's regular press conferences, and the uploads of state outlets'
official YouTube channels. It codes every item for the AI seams GHOST LOOM tracks on Reddit and
writes one JSON pack that the dashboard's CHINA tab loads (State voice panel, "Load pack").

Why official voices only. GHOST LOOM asks whether what surfaces in Reddit's anti-AI communities
tracks what Beijing says, on what clock, and through which accounts. The overt track, what the
state says under its own name, is the baseline for that comparison. The covert track is found on
Reddit itself, with the General Ludd collector. Neither question needs the domestic Chinese
internet.

What it will not do. DALABA does not read Chinese social media or messaging platforms: Weibo,
WeChat, QQ, Douyin, Kuaishou, Xiaohongshu (RedNote), Bilibili, Zhihu, Tieba, Toutiao, Douban,
DingTalk, Feishu (Lark) and the rest. A denylist refuses those hosts at every hop, including
redirects and followed links, and it outranks the allowlist. Those platforms carry private
people's speech under real-name registration and state monitoring, their terms bar automated
collection, and the messaging apps are private correspondence. The dashboard profiles each of
them instead.

Standard library only. Python 3.9 or newer. Runs on your machine. Nothing leaves it except HTTP
requests to the listed publishers (and to the YouTube Data API if you supply a key).

Setup (once)
  python dalaba.py init  --out ./statevoice      writes statevoice/dalaba_sources.json
  put an email address or URL in "contact" in that file (it goes in the User-Agent)
  python dalaba.py check --out ./statevoice      tests every enabled source

Run
  python dalaba.py pull  --out ./statevoice                  fetch, code, archive, write the pack
  python dalaba.py pull  --out ./statevoice --follow         also read AI-relevant pages for excerpts
  python dalaba.py pull  --out ./statevoice --youtube-key K  add state-outlet YouTube uploads
                                                             (or set DALABA_YT_KEY)
  python dalaba.py pack  --out ./statevoice --days 28        rebuild the pack from the archive
  python dalaba.py recode --out ./statevoice                 re-run coding after a codebook change
  python dalaba.py selftest                                  offline checks, no network

Feeds hold only their latest 20 to 50 items, a day or two for the busiest. Run pull every six
hours to keep an unbroken record; the pack flags gaps where pulls were too far apart.
  cron:   17 */6 * * *  cd /path/to/folder && python3 dalaba.py pull --out ./statevoice --follow
  Windows Task Scheduler: the same command, repeating every 6 hours.

Then load statevoice/statevoice_YYYYMMDD.json in GHOST LOOM: CHINA tab, State voice, Load pack.

Codes are topics, not stance. A DC hit means an item raised data centers and power or water; it
does not say which side the item took. The dashboard reads the codes that way and so should you.
"""
from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import gzip
import hashlib
import html
import http.cookiejar
import json
import os
import random
import re
import shutil
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

VERSION = "1.0.0"
TOOL = "DALABA"
KIND = "ghostloom-statevoice"
UTC = dt.timezone.utc
BJT = dt.timezone(dt.timedelta(hours=8), "UTC+8")
CONFIG = "dalaba_sources.json"
ARCHIVE = "dalaba_archive.jsonl"
PULLS = "dalaba_pulls.jsonl"
MAX_BYTES = 6_000_000
STALE_DAYS = 30

try:  # never crash a Windows console on a stray character
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except (AttributeError, ValueError):
    pass


# =============================================================================== the guard
# Official publishers: party-state media, ministries, missions. Suffix match on the host.
ALLOW = {
    "cgtn.com": "CGTN (China Media Group)",
    "cctv.com": "CCTV (China Media Group)",
    "cctvplus.com": "CCTV+ (China Media Group video agency)",
    "cri.cn": "China Radio International (China Media Group)",
    "globaltimes.cn": "Global Times (People's Daily group)",
    "chinadaily.com.cn": "China Daily",
    "chinadailyhk.com": "China Daily Hong Kong",
    "xinhuanet.com": "Xinhua News Agency",
    "news.cn": "Xinhua News Agency",
    "people.com.cn": "People's Daily",
    "people.cn": "People's Daily",
    "ecns.cn": "China News Service",
    "chinanews.com.cn": "China News Service",
    "qstheory.cn": "Qiushi (CCP Central Committee journal)",
    "chinamil.com.cn": "PLA Daily",
    "81.cn": "China Military Online (PLA)",
    "fmprc.gov.cn": "Ministry of Foreign Affairs",
    "mfa.gov.cn": "Ministry of Foreign Affairs",
    "china-embassy.gov.cn": "PRC embassy",
    "china-mission.gov.cn": "PRC mission",
    "gov.cn": "PRC government body",
}

# Chinese social media, messaging and user-content hosts. Checked first; always wins.
DENY = {
    "weibo.com": "Weibo", "weibo.cn": "Weibo", "t.cn": "Weibo short links", "sinaimg.cn": "Weibo media",
    "qq.com": "Tencent: WeChat, QQ, Qzone, WeCom, Tencent News comments",
    "wechat.com": "WeChat", "weixin.com": "WeChat", "meeting.tencent.com": "Tencent Meeting",
    "douyin.com": "Douyin", "iesdouyin.com": "Douyin share links", "douyinvod.com": "Douyin video",
    "tiktok.com": "TikTok (user platform; its research API is gated to approved researchers)",
    "xiaohongshu.com": "Xiaohongshu (RedNote)", "xhslink.com": "Xiaohongshu share links",
    "xhscdn.com": "Xiaohongshu media", "rednote.com": "RedNote",
    "bilibili.com": "Bilibili", "b23.tv": "Bilibili share links", "biligame.com": "Bilibili",
    "zhihu.com": "Zhihu", "zhimg.com": "Zhihu media",
    "tieba.baidu.com": "Baidu Tieba", "zhidao.baidu.com": "Baidu Zhidao (user Q&A)",
    "baijiahao.baidu.com": "Baijiahao (creator accounts)",
    "kuaishou.com": "Kuaishou", "kwai.com": "Kwai", "gifshow.com": "Kuaishou", "kuaishou.cn": "Kuaishou",
    "toutiao.com": "Toutiao (user accounts and comments)", "ixigua.com": "Xigua Video",
    "douban.com": "Douban", "hupu.com": "Hupu", "acfun.cn": "AcFun", "lofter.com": "Lofter",
    "dingtalk.com": "DingTalk (workplace messaging)", "feishu.cn": "Feishu (workplace messaging)",
    "larksuite.com": "Lark", "larkoffice.com": "Lark",
    "yy.com": "YY live streaming", "huya.com": "Huya live streaming", "douyu.com": "Douyu live streaming",
    "immomo.com": "Momo", "soulapp.cn": "Soul", "okjike.com": "Jike",
    "xueqiu.com": "Xueqiu (Snowball)", "maimai.cn": "Maimai",
}
# Forum, blog, microblog and comment subdomains, even on allowlisted publishers.
DENY_LABELS = {"bbs", "bbs1", "bbs2", "forum", "forums", "club", "blog", "blogs", "t", "weibo",
               "tieba", "comment", "comments"}

# Official YouTube channels of state outlets. Others need an official_basis (see init help).
STATE_CHANNELS = {
    "@cgtn": "CGTN", "@cgtnamerica": "CGTN America", "@cgtneurope": "CGTN Europe",
    "@cgtnafrica": "CGTN Africa", "@newchinatv": "New China TV (Xinhua)",
    "@cctvvideonewsagency": "CCTV Video News Agency",
}


class Refused(Exception):
    def __init__(self, url, reason):
        super().__init__(reason)
        self.url, self.reason = redact(url), reason


class FetchError(Exception):
    def __init__(self, url, reason):
        super().__init__(reason)
        self.url, self.reason = redact(url), reason


def redact(url):
    return re.sub(r"([?&]key=)[^&]+", r"\1***", url or "")


def host_of(url):
    try:
        h = urllib.parse.urlsplit(url).hostname or ""
    except ValueError:
        return ""
    return h.lower().rstrip(".")


def _suffix(host, dom):
    return host == dom or host.endswith("." + dom)


def guard(url, purpose="fetch"):
    """Return (ok, reason, label). The denylist is checked first and always wins."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False, "unparseable URL", ""
    if parts.scheme not in ("http", "https"):
        return False, f"scheme {parts.scheme!r} is not allowed", ""
    host = host_of(url)
    if not host:
        return False, "no host", ""
    for dom, why in DENY.items():
        if _suffix(host, dom):
            return False, (f"denylist: {dom} ({why}). DALABA does not read Chinese social media "
                           "or messaging platforms"), ""
    if purpose == "youtube-api":
        if host == "www.googleapis.com" and parts.path.startswith("/youtube/v3/"):
            return True, "", "YouTube Data API"
        return False, "YouTube calls go only to www.googleapis.com/youtube/v3/", ""
    if host.split(".")[0] in DENY_LABELS:
        return False, f"denylist: {host.split('.')[0]}.* host (forum, blog, microblog or comments)", ""
    best = ""
    for dom in ALLOW:
        if _suffix(host, dom) and len(dom) > len(best):
            best = dom
    if best:
        return True, "", ALLOW[best]
    return False, f"not on the official-publisher allowlist: {host}", ""


# =============================================================================== HTTP
class Resp:
    __slots__ = ("url", "status", "ctype", "body")

    def __init__(self, url, status, ctype, body):
        self.url, self.status, self.ctype, self.body = url, status, ctype or "", body or b""

    @property
    def text(self):
        return decode(self.body, self.ctype)


class Guarded(urllib.request.HTTPRedirectHandler):
    """Every redirect hop must pass the guard, or the request dies with Refused."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        purpose = getattr(req, "dalaba_purpose", "fetch")
        newurl = urllib.parse.urljoin(req.full_url, newurl)
        ok, why, _ = guard(newurl, purpose)
        if not ok:
            raise Refused(newurl, "redirect refused, " + why)
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            new.dalaba_purpose = purpose
        return new


def _decompress(body, enc):
    try:
        if "gzip" in enc or body[:2] == b"\x1f\x8b":
            return gzip.decompress(body)
        if "deflate" in enc:
            try:
                return zlib.decompress(body)
            except zlib.error:
                return zlib.decompress(body, -zlib.MAX_WBITS)
    except (OSError, zlib.error, EOFError):
        pass
    return body


CHARSET_RX = re.compile(rb"(?:charset|encoding)\s*=\s*[\"']?([A-Za-z0-9_\-]+)", re.I)


def decode(body, ctype=""):
    if body.startswith(b"\xef\xbb\xbf"):
        return body[3:].decode("utf-8", "replace")
    cs = None
    m = re.search(r"charset=([\w\-]+)", ctype or "", re.I)
    if m:
        cs = m.group(1)
    if not cs:
        m = CHARSET_RX.search(body[:2048])
        if m:
            cs = m.group(1).decode("ascii", "ignore")
    cs = (cs or "utf-8").lower()
    if cs in ("gb2312", "gbk", "x-gbk", "gb_2312-80", "cp936"):
        cs = "gb18030"
    try:
        return body.decode(cs)
    except (LookupError, UnicodeDecodeError):
        for alt in ("utf-8", "gb18030"):
            try:
                return body.decode(alt)
            except UnicodeDecodeError:
                continue
        return body.decode("utf-8", "replace")


class Fetcher:
    """Polite, guarded HTTP: truthful User-Agent, robots.txt, per-host pacing, retries, cookies."""

    def __init__(self, contact="", delay=2.0, timeout=30, offline=None):
        self.ua = f"GHOST-LOOM-DALABA/{VERSION} (official-media research; contact: {contact or 'not set'})"
        self.delay, self.timeout, self.offline = float(delay), timeout, offline
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar), Guarded())
        self._last, self._robots = {}, {}
        self.refused = []

    def note(self, exc):
        self.refused.append({"url": exc.url, "reason": exc.reason})

    def _pace(self, host, delay):
        if self.offline is not None:
            return
        wait = self._last.get(host, 0.0) + delay - time.monotonic()
        if wait > 0:
            time.sleep(wait + random.uniform(0, 0.4))
        self._last[host] = time.monotonic()

    def robots_ok(self, url):
        parts = urllib.parse.urlsplit(url)
        key = f"{parts.scheme}://{host_of(url)}"
        if key not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self._raw(key + "/robots.txt", robots=False)
                if r.status in (401, 403):
                    rp.disallow_all = True
                elif r.status >= 400:
                    rp.allow_all = True
                else:
                    rp.parse(r.text.splitlines())
            except Refused:
                rp.disallow_all = True
            except (FetchError, OSError, ValueError):
                rp.allow_all = True
            self._robots[key] = rp
        return self._robots[key].can_fetch(self.ua, url)

    def get(self, url, purpose="fetch", accept=None):
        return self._raw(url, purpose=purpose, accept=accept)

    def get_json(self, url, purpose="youtube-api"):
        r = self._raw(url, purpose=purpose, accept="application/json", delay=0.2)
        try:
            data = json.loads(r.text) if r.body else {}
        except ValueError:
            data = {}
        if r.status != 200:
            msg = ((data.get("error") or {}).get("message") if isinstance(data, dict) else "") or f"http {r.status}"
            raise FetchError(url, redact(msg))
        return data

    def _offline(self, url):
        hit = self.offline.get(url)
        if hit is None:
            return Resp(url, 404, "text/plain", b"not found")
        status, ctype, body = hit
        if status in (301, 302, 303, 307, 308):  # body carries the Location
            nxt = urllib.parse.urljoin(url, body.decode() if isinstance(body, bytes) else body)
            ok, why, _ = guard(nxt, "fetch")
            if not ok:
                raise Refused(nxt, "redirect refused, " + why)
            return self._offline(nxt)
        return Resp(url, status, ctype, body if isinstance(body, bytes) else body.encode("utf-8"))

    def _raw(self, url, purpose="fetch", robots=True, accept=None, delay=None):
        ok, why, _ = guard(url, purpose)
        if not ok:
            raise Refused(url, why)
        if robots and purpose == "fetch" and not self.robots_ok(url):
            raise Refused(url, "robots.txt disallows this path for DALABA")
        if self.offline is not None:
            return self._offline(url)
        host = host_of(url)
        headers = {"User-Agent": self.ua, "Accept-Encoding": "gzip, deflate",
                   "Accept": accept or "*/*", "Accept-Language": "en;q=0.9, zh;q=0.8"}
        last = None
        for attempt in range(4):
            self._pace(host, self.delay if delay is None else delay)
            req = urllib.request.Request(url, headers=headers)
            req.dalaba_purpose = purpose
            try:
                with self.opener.open(req, timeout=self.timeout) as r:
                    body = r.read(MAX_BYTES + 1)[:MAX_BYTES]
                    body = _decompress(body, (r.headers.get("Content-Encoding") or "").lower())
                    return Resp(r.geturl(), r.status, r.headers.get("Content-Type", ""), body)
            except Refused:
                raise
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                    ra = (e.headers.get("Retry-After") if e.headers else "") or ""
                    wait = min(60, int(ra)) if ra.isdigit() else 2 ** (attempt + 1)
                    time.sleep(wait + random.uniform(0, 1))
                    last = e
                    continue
                try:
                    body = e.read()
                except (OSError, AttributeError):
                    body = b""
                return Resp(url, e.code, e.headers.get("Content-Type", "") if e.headers else "", body)
            except (urllib.error.URLError, OSError) as e:
                last = e
                if attempt < 3:
                    time.sleep(2 ** (attempt + 1))
                    continue
        raise FetchError(url, str(getattr(last, "reason", last)))


# =============================================================================== text and dates
TAG_RX = re.compile(r"<[^>]+>")


def strip_html(s):
    s = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1\s*>", " ", s or "")
    s = re.sub(r"(?i)<br\s*/?>|</p\s*>|</div\s*>|</li\s*>|</h\d\s*>", "\n", s)
    s = TAG_RX.sub(" ", s)
    return html.unescape(s).replace(" ", " ")


def squash(s):
    return re.sub(r"\s+", " ", s or "").strip()


def clip(s, n):
    s = squash(s)
    if len(s) <= n:
        return s
    cut = s[: n - 1]
    if " " in cut[-40:]:
        cut = cut.rsplit(" ", 1)[0]
    return cut + "…"


def canon(url):
    p = urllib.parse.urlsplit(url.strip())
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True)
         if not k.lower().startswith("utm_") and k.lower() not in ("spm", "from", "share", "s_trans")]
    host = (p.hostname or "").lower()
    netloc = host + (f":{p.port}" if p.port else "")
    return urllib.parse.urlunsplit(((p.scheme or "https").lower(), netloc, p.path or "/",
                                    urllib.parse.urlencode(q), ""))


MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july",
                                      "august", "september", "october", "november", "december"], 1)}
EN_DATE_RX = re.compile(r"\b(" + "|".join(MONTHS) + r")\s+(\d{1,2}),?\s+(\d{4})\b", re.I)
NUM_DATE_RX = re.compile(r"(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?"
                         r"(?:[ T]+(\d{1,2}):(\d{2})(?::(\d{2}))?)?")
URL_DATE_RX = re.compile(r"(?:/|t)(20\d{2})[-_/]?(\d{2})[-_/]?(\d{2})(?=[/_.])")
URL_MONTH_RX = re.compile(r"/(20\d{2})(\d{2})/(?=\d)")
ABBR = {k[:3]: v for k, v in MONTHS.items()}
META_DATE_RX = re.compile(r"<meta\b[^>]*?(?:property|name|itemprop)\s*=\s*[\"'](?:article:published_time|og:published_time"
                          r"|publishdate|pubdate|publish_date|datepublished|dc\.date(?:\.issued)?)[\"'][^>]*>", re.I)
CONTENT_RX = re.compile(r"content\s*=\s*[\"']([^\"']+)", re.I)
PUBLISHED_RX = re.compile(r"Published:?\s*([A-Z][a-z]{2,8})\.?\s+(\d{1,2}),\s*(\d{4})"
                          r"(?:\s+(\d{1,2}):(\d{2})\s*([AP]M))?", re.I)


def _mk(y, mo, d, hh=12, mm=0, ss=0):
    try:
        return dt.datetime(int(y), int(mo), int(d), int(hh), int(mm), int(ss), tzinfo=BJT).astimezone(UTC)
    except ValueError:
        return None


def date_from_text(s):
    """Dates written in titles and URLs. No time given means noon Beijing, flagged date-only."""
    m = EN_DATE_RX.search(s or "")
    if m:
        d = _mk(m.group(3), MONTHS[m.group(1).lower()], m.group(2))
        if d:
            return d, "date-only"
    m = NUM_DATE_RX.search(s or "")
    if m:
        if m.group(4):
            d = _mk(m.group(1), m.group(2), m.group(3), m.group(4), m.group(5), m.group(6) or 0)
            if d:
                return d, "assumed-utc8"
        d = _mk(m.group(1), m.group(2), m.group(3))
        if d:
            return d, "date-only"
    return None, "unparsed"


def date_from_url(url):
    """Full dates in URLs; a URL that carries only a month returns quality 'month:YYYY-MM'."""
    m = URL_DATE_RX.search(url or "")
    if m:
        d = _mk(m.group(1), m.group(2), m.group(3))
        if d:
            return d, "url"
    m = URL_MONTH_RX.search(url or "")
    if m and 1 <= int(m.group(2)) <= 12:
        return None, f"month:{m.group(1)}-{m.group(2)}"
    return None, "missing"


def date_from_page(page):
    """Publication time from an article page: meta tags first, then a 'Published:' line."""
    for m in META_DATE_RX.finditer(page or ""):
        c = CONTENT_RX.search(m.group(0))
        if c:
            d, q = parse_date(c.group(1))
            if d:
                return d, "page"
    m = PUBLISHED_RX.search(page or "")
    if m:
        mo = ABBR.get(m.group(1)[:3].lower())
        if mo:
            hh, mm = int(m.group(4) or 12), int(m.group(5) or 0)
            if m.group(6):
                hh = hh % 12 + (12 if m.group(6).upper() == "PM" else 0)
            d = _mk(m.group(3), mo, m.group(2), hh, mm)
            if d:
                return d, "page"
    return None, "missing"


def parse_date(s):
    """Feed timestamps. Return (aware UTC datetime or None, quality)."""
    s = (s or "").strip()
    if not s:
        return None, "missing"
    if re.search(r"[A-Za-z]{3},?\s+\d{1,2}\s+[A-Za-z]{3}\s+\d{4}|\d{1,2}\s+[A-Za-z]{3}\s+\d{4}\s+\d", s):
        try:
            d = email.utils.parsedate_to_datetime(s)
            if d is not None:
                if d.tzinfo is None:
                    return d.replace(tzinfo=BJT).astimezone(UTC), "assumed-utc8"
                return d.astimezone(UTC), "feed"
        except (TypeError, ValueError, IndexError, OverflowError):
            pass
    iso = s[:-1] + "+00:00" if s.endswith(("Z", "z")) else s
    iso = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", iso)
    m = re.search(r"\.(\d+)", iso)
    if m:
        iso = iso[:m.start(1)] + (m.group(1) + "000000")[:6] + iso[m.end(1):]
    try:
        d = dt.datetime.fromisoformat(iso)
        if d.tzinfo is None:
            return d.replace(tzinfo=BJT).astimezone(UTC), ("assumed-utc8" if ("T" in iso or " " in iso) else "date-only")
        return d.astimezone(UTC), "feed"
    except ValueError:
        pass
    return date_from_text(s)


def iso(d):
    return d.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if d else None


def from_iso(s):
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC) if s else None


def bj_day(d):
    return d.astimezone(BJT).strftime("%Y-%m-%d") if d else None


# =============================================================================== the codebook
_AI = r"(?:(?<![a-z0-9])a\.?i\.?(?![a-z0-9])|artificial intelligence)"
_AIZ = r"(?:人工智能|(?<![a-z])ai(?![a-z])|大模型)"
_DC = r"data ?cent(?:er|re)s?"
CODEBOOK = {
    "version": "2026.09.26-sv3",
    "note": "Topic detectors. A hit says an item raised the topic, never which side it took.",
    "gate": {"AI": [
        _AI,
        r"\b(?:machine learning|deep learning|neural networks?|large (?:language )?models?|llms?|foundation models?"
        r"|chatbots?|chatgpt|openai|anthropic|deepseek|qwen|kimi|doubao|ernie bot|hunyuan|agi|algorithms?"
        r"|computing power|semiconductors?|chips?|chipmakers?|gpus?|nvidia|huawei ascend|robot(?:s|ics)?"
        r"|humanoids?|embodied intelligence|intelligent computing|smart manufacturing)\b",
        _DC,
        r"人工智能|大模型|算力|数据中心|芯片|半导体|英伟达|深度求索|通义千问|豆包|文心一言|智能体|机器人|具身智能|生成式|智算",
    ]},
    "seams": {
        "DC": {"label": "Data centers drain power, water and wallets", "rx": [
            _DC + r".{0,80}\b(?:electric\w*|power|grid|water|energy|bills?|utilit\w*|rates|emissions|carbon|noise"
                  r"|land|farmland|protest\w*|opposition|backlash|resist\w*)\b",
            r"\b(?:electric\w*|power|grid|energy|water|utility|utilities|protests?|opposition|backlash|residents"
            r"|moratorium)\b.{0,80}" + _DC,
            _AI + r".{0,60}\b(?:electricity|power|energy|grid)\b.{0,40}\b(?:squeeze|shortage|strain\w*|crunch|bills?"
                  r"|prices?|costs?|demand|crisis)\b",
            r"\bpjm\b|\bcapacity auction\b",
            r"数据中心.{0,30}(?:电|水|能耗|排放|居民)|(?:电价|电费|电网|用电|缺电|电力).{0,30}(?:数据中心|人工智能|ai)|算力.{0,20}(?:耗电|能耗)",
        ]},
        "MIL": {"label": "AI is a war and surveillance machine", "rx": [
            _AI + r".{0,50}\b(?:military|militar\w+|weapon\w*|warfare|battlefield|targeting (?:systems?|software|data|decisions?)"
                  r"|kill chains?|lethal|surveillance|pentagon|drones?|killing|arms race)\b",
            r"\b(?:military|missile|air)? ?strikes?\b.{0,40}" + _DC + r"|" + _DC + r".{0,40}\b(?:military|strikes?|legitimate target|attack\w*)\b",
            r"\b(?:military|pentagon|weaponi[sz]\w*|warfare|arms race)\b.{0,50}" + _AI,
            r"\bpalantir\b|\bproject (?:nimbus|maven)\b",
            _AIZ + r".{0,20}(?:军事|武器|战争|战场|监控|五角大楼|无人机)|(?:军事|武器化|五角大楼).{0,20}" + _AIZ + r"|军备竞赛",
        ]},
        "BUB": {"label": "AI is a bubble and the US will pay for it", "rx": [
            r"\bai bubble\b|\bai boom\b.{0,40}\b(?:fails?|hollow|illusion|mirage|bust)\b",
            r"\bbubble\b.{0,60}(?:" + _AI + r"|\btech\b|\bnvidia\b|" + _DC + r")",
            _AI + r".{0,60}\b(?:bubble|overhyped|hype|overinvest\w*|unsustainable|burn\w* cash|circular (?:deals|financing))\b",
            r"(?:人工智能|ai|大模型|英伟达|科技股|算力).{0,20}泡沫|泡沫.{0,20}(?:人工智能|ai|科技|英伟达)",
        ]},
        "TRUST": {"label": "Western AI is biased, captured or unsafe", "rx": [
            r"\b(?:chatgpt|openai|gemini|claude|copilot|grok|western ai|us ai|american ai|silicon valley)\b.{0,60}"
            r"\b(?:propaganda|bias\w*|censor\w*|spy\w*|surveillance|leak\w*|compromised|privacy|misinformation"
            r"|disinformation|hallucinat\w*|unsafe|monopol\w*|breach\w*)\b",
            r"(?:chatgpt|openai|美国(?:的)?(?:人工智能|ai)|西方(?:的)?(?:人工智能|ai)).{0,30}(?:偏见|审查|泄露|隐私|监控|虚假信息|垄断|安全隐患)",
        ]},
        "JOBS": {"label": "AI is taking jobs and livelihoods", "rx": [
            r"(?:" + _AI + r"|\bautomation\b|\brobots?\b).{0,60}\b(?:jobs?|employment|unemploy\w*|layoffs?|laid off"
            r"|workers|livelihoods?|replac\w+)\b",
            r"\b(?:layoffs?|laid off|job (?:losses|cuts))\b.{0,60}(?:" + _AI + r"|\bautomation\b)",
            r"(?:人工智能|ai|自动化|机器人).{0,30}(?:就业|失业|裁员|岗位|取代|替代)|(?:裁员|失业).{0,30}(?:人工智能|ai)",
        ]},
        "ART": {"label": "AI art is theft and slop is flooding the internet", "rx": [
            r"\bai[\s-]?(?:generated|art|images?|videos?|content)\b.{0,60}\b(?:copyright\w*|theft|infring\w*|artists?"
            r"|plagiari\w*|slop|label\w*)\b",
            r"\b(?:copyright\w*|artists?)\b.{0,60}" + _AI + r".{0,30}\b(?:train\w*|scrap\w*|theft|infring\w*)\b",
            r"\bai slop\b",
            r"(?:ai|人工智能)(?:生成|绘画|作品).{0,30}(?:版权|侵权|艺术家|标识|抄袭)|版权.{0,20}(?:人工智能|ai|大模型)",
        ]},
        "DOOM": {"label": "AI could end humanity", "rx": [
            r"(?:" + _AI + r"|\bagi\b|\bsuperintelligence\b).{0,50}\b(?:extinction|existential|end of humanity|out of control"
            r"|loss of control|doom\w*|catastroph\w*)\b",
            r"\bpause ai\b",
            r"(?:人工智能|ai|超级智能).{0,20}(?:失控|灭绝|生存风险|末日)",
        ]},
        "BOY": {"label": "Boycott and reject AI", "rx": [
            r"\b(?:boycott\w*|reject\w*)\b.{0,30}(?:" + _AI + r"|\bgenai\b|\bchatgpt\b)",
            r"\banti[\s-]?ai\b",
            r"\b(?:public|workers|artists|residents|consumers)\b.{0,30}\b(?:reject\w*|push\w* back|backlash|revolt)\b.{0,30}" + _AI,
            r"\bbacklash (?:against|to) " + _AI,
            r"抵制.{0,10}(?:人工智能|ai)|反(?:对)?(?:人工智能|ai)",
        ]},
    },
    "prc": {
        "AI_PRC_POSITIVE": {"label": "Chinese AI promoted", "rx": [
            r"\b(?:deepseek|qwen|kimi|moonshot ai|zhipu|minimax|doubao|ernie bot|hunyuan|huawei ascend)\b",
            r"\bchinese (?:ai|models?|open[\s-]source)\b|\bchina'?s (?:ai|artificial intelligence|open[\s-]source|large models?)\b",
            r"深度求索|通义千问|月之暗面|智谱|豆包|文心一言|混元|昇腾|国产大模型|中国(?:的)?(?:人工智能|ai|大模型)",
        ]},
        "EXPORT_CONTROLS": {"label": "Export controls and chip curbs", "rx": [
            r"\bexport controls?\b|\bchip (?:bans?|curbs|restrictions)\b|\bentity list\b|\bh20\b|\bh200\b|\bchips act\b",
            r"\btech(?:nology|nological)? blockade\b|\bsmall yard,? high fence\b|\bdecoupl\w*\b",
            r"出口管制|实体清单|芯片禁令|断供|科技封锁|小院高墙|脱钩",
        ]},
        "TECH_TARIFFS": {"label": "Tariffs framed as tech domination", "rx": [
            r"\btariffs?\b.{0,60}\b(?:tech\w*|chips?|semiconductors?|ai|artificial intelligence)\b",
            r"\b(?:tech\w*|chips?|semiconductors?)\b.{0,40}\btariffs?\b|\bsection 232\b",
            r"关税.{0,20}(?:芯片|半导体|科技|技术)|(?:芯片|半导体).{0,20}关税",
        ]},
        "US_DECLINE": {"label": "US decline", "rx": [
            r"\b(?:america|the us|usa|united states|the west)\b.{0,20}\b(?:is|are)\b.{0,15}\b(?:collapsing|finished|dying"
            r"|in decline|declining)\b",
            r"\bdeclining empire\b|\bfailed state\b|\blate[\s-]stage empire\b|\bdecline of (?:the )?(?:us|united states|america|west)\b",
            r"美国.{0,10}(?:衰落|衰败|没落|衰退)|霸权衰落",
        ]},
        "PRC_OFFICIAL_PHRASING": {"label": "Official PRC phrasing", "rx": [
            r"\bcold[\s-]war (?:mentality|script|playbook|thinking|logic)\b|\bzero[\s-]sum (?:game|mentality|thinking|rivalry)\b|\bhegemon(?:y|ism|ic)\b",
            r"\bso[\s-]called (?:human rights|democracy|freedom|free world)\b|\binternal affairs\b|\bhurt(?:s|ing)? the feelings\b",
            r"\bwin[\s-]win\b|\bshared future\b|\bchina threat theory\b|\bcolou?r revolutions?\b|\bsmear(?:ing|s)? (?:campaign against )?china\b",
            r"\bweaponi[sz]\w* (?:trade|technology|tech|economic|economy|chips?)\b|\btech(?:nological)? hegemony\b",
            r"\bpoliticiz\w+ (?:trade|technology|tech|economic)\b|\b(?:overstretch|generaliz)\w* the concept of national security\b",
            r"冷战思维|零和博弈|霸权主义|科技霸权|泛化国家安全|政治化|武器化|合作共赢|人类命运共同体|互利共赢",
        ]},
        "AI_GOVERNANCE": {"label": "China as AI governance leader", "rx": [
            r"\bglobal ai governance\b|\bai governance\b|\bworld ai cooperation organi[sz]ation\b|\bwaico\b",
            r"\bai capacity[\s-]building\b|\bglobal (?:governance|development) initiative\b|\bai for (?:good|all)\b",
            r"\binclusive\b.{0,30}" + _AI + r"|\bglobal south\b.{0,40}" + _AI + r"|" + _AI + r".{0,40}\bglobal south\b",
            r"\b(?:intelligence|digital|ai) divide\b|\bai plus\b|\bai\+",
            r"全球人工智能治理|人工智能全球治理|世界人工智能合作组织|人工智能能力建设|全球治理倡议|全球发展倡议|智能鸿沟|数字鸿沟|人工智能\+|ai\+",
        ]},
        "AI_COOPERATION": {"label": "US-China AI cooperation", "rx": [
            r"\b(?:china|chinese|beijing|sino)[\s,/-]+(?:and\s+(?:the\s+)?)?(?:us|u\.s\.|united states|america|american|washington)\b.{0,60}"
            r"(?:" + _AI + r").{0,40}\b(?:cooperat\w*|collaborat\w*|dialogue|partnership|incident line)\b",
            r"(?:" + _AI + r").{0,30}\b(?:cooperat\w*|collaborat\w*|dialogue)\b.{0,20}\b(?:between|with)\b.{0,20}"
            r"\b(?:china|chinese|beijing|the us|u\.s\.|united states|america|washington)\b",
            r"\b(?:us|u\.s\.|united states|america|washington)[\s,/-]+(?:and\s+)?(?:china|beijing)\b.{0,60}(?:" + _AI + r").{0,40}"
            r"\b(?:cooperat\w*|collaborat\w*|dialogue)\b",
            r"\b(?:china|chinese|sino)[\s,/-]*(?:and\s+(?:the\s+)?)?(?:us|u\.s\.|american)\b.{0,20}\b(?:cooperat\w*|collaborat\w*|dialogue)\b.{0,30}(?:" + _AI + r")",
            r"中美.{0,20}(?:人工智能|ai).{0,20}(?:合作|对话)|(?:人工智能|ai).{0,20}中美.{0,10}(?:合作|对话)",
        ]},
        "POWER_CONTRAST": {"label": "China's ample power vs US grid strain", "rx": [
            r"\bchina'?s?\b.{0,60}\b(?:power|electricity|energy|grid)\b.{0,40}\b(?:abundan\w*|surplus|cheap|clean|green|ample|advantage)\b",
            r"\b(?:ample|abundant|cheap|surplus) (?:power|electricity|energy)\b",
            r"\b(?:us|u\.s\.|america'?s?|american)\b.{0,40}\b(?:grid|power|electricity)\b.{0,40}\b(?:strain\w*|shortage\w*|squeeze|crisis"
            r"|bottleneck|can'?t keep up)\b",
            r"中国.{0,20}(?:电力|能源|绿电).{0,20}(?:充足|优势|富余|便宜)|美国.{0,20}(?:电网|电力).{0,20}(?:紧张|短缺|瓶颈|危机)",
        ]},
        "SAFETY_AS_CONTAINMENT": {"label": "US AI safety framed as containment", "rx": [
            r"\bsafety\b.{0,80}\b(?:contain\w*|suppress\w*|protectionis\w*|pretext|smokescreen|hegemon\w*|monopol\w*)\b",
            r"\b(?:contain\w*|suppress\w*)\b.{0,60}\bsafety\b",
            r"安全.{0,20}(?:借口|幌子|遏制|打压)|(?:遏制|打压).{0,20}(?:人工智能|ai).{0,20}安全",
        ]},
        "COLD_WAR_FRAME": {"label": "Cold War or containment framing", "rx": [
            r"\bcold[\s-]war\b|\bzero[\s-]sum\b|\bhegemon\w*\b|\bcontainment\b|\bcontain(?:ing)? china\b|\bsmall yard,? high fence\b",
            r"新冷战|冷战|零和|霸权|遏制|小院高墙",
        ]},
        "AI_US_TARGET": {"label": "US AI firms named", "rx": [
            r"\b(?:openai|sam altman|anthropic|nvidia|silicon valley|big tech|stargate|xai|grok|chatgpt|microsoft|google|meta)\b",
            r"\b(?:us|american) tech (?:giants|companies|firms)\b",
            r"奥尔特曼|英伟达|硅谷|美国科技(?:巨头|公司|企业)|星际之门|微软|谷歌|马斯克",
        ]},
    },
}


def _compile(rx_list):
    return re.compile("|".join(f"(?:{p})" for p in rx_list), re.I | re.S)


class Coder:
    def __init__(self, book=None):
        self.book = book or CODEBOOK
        self.version = self.book["version"]
        self.gate = _compile(self.book["gate"]["AI"])
        self.seams = {k: _compile(v["rx"]) for k, v in self.book["seams"].items()}
        self.prc = {k: _compile(v["rx"]) for k, v in self.book["prc"].items()}

    @staticmethod
    def norm(text):
        t = unicodedata.normalize("NFKC", text or "").replace("’", "'").replace("‘", "'")
        t = re.sub("[‐-―−]", "-", t)
        return squash(t)  # patterns are case-insensitive; keeping case makes hit snippets readable

    @staticmethod
    def _snip(t, m):
        a, b = max(0, m.start() - 40), min(len(t), m.end() + 40)
        return ("…" if a else "") + t[a:b].strip() + ("…" if b < len(t) else "")

    def is_ai(self, text):
        return bool(self.gate.search(self.norm(text)))

    def code(self, *parts):
        t = self.norm(" \n ".join(p for p in parts if p))
        res = {"ai": False, "seams": [], "prc": [], "hits": {}}
        m = self.gate.search(t)
        if m:
            res["ai"] = True
            res["hits"]["AI"] = self._snip(t, m)
            for k, rx in self.seams.items():
                mm = rx.search(t)
                if mm:
                    res["seams"].append(k)
                    res["hits"][k] = self._snip(t, mm)
        for k, rx in self.prc.items():
            mm = rx.search(t)
            if mm:
                res["prc"].append(k)
                res["hits"][k] = self._snip(t, mm)
        return res


# =============================================================================== parsers
def _local(tag):
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


ITEM_RX = re.compile(r"<(item|entry)\b[^>]*>(.*?)</\1\s*>", re.I | re.S)


def _uncdata(s):
    return re.sub(r"<!\[CDATA\[(.*?)\]\]>", lambda m: m.group(1), s or "", flags=re.S)


def _field(block, names):
    for n in names:
        m = re.search(rf"<(?:[\w-]+:)?{n}\b[^>]*>(.*?)</(?:[\w-]+:)?{n}\s*>", block, re.I | re.S)
        if m:
            return _uncdata(m.group(1)).strip()
    return ""


def _lenient(t):
    out = []
    for m in ITEM_RX.finditer(t):
        b = m.group(2)
        link = _field(b, ["link"])
        if not link:
            m2 = re.search(r"<link\b[^>]*href=[\"']([^\"']+)", b, re.I)
            link = m2.group(1) if m2 else ""
        out.append({"title": html.unescape(_field(b, ["title"])), "link": html.unescape(link),
                    "date": _field(b, ["pubDate", "published", "updated", "date"]),
                    "summary": html.unescape(_field(b, ["description", "summary", "encoded", "content"])),
                    "guid": _field(b, ["guid", "id"])})
    return out


def parse_feed(text):
    """RSS 2.0, RSS 1.0 (RDF) and Atom. Falls back to a lenient scan for broken XML."""
    t = re.sub(r"^\s*<\?xml[^>]*\?>", "", (text or "").lstrip("﻿")).strip()
    try:
        root = ET.fromstring(t)
    except ET.ParseError:
        return _lenient(t), "lenient"
    out = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        rec = {"title": "", "link": "", "date": "", "summary": "", "content": "", "guid": ""}
        for ch in el:
            n, txt = _local(ch.tag), (ch.text or "").strip()
            if n == "title":
                rec["title"] = "".join(ch.itertext()).strip()
            elif n == "link":
                href = ch.get("href")
                if href:
                    if ch.get("rel", "alternate") == "alternate" or not rec["link"]:
                        rec["link"] = href
                elif txt:
                    rec["link"] = txt
            elif n in ("pubdate", "published", "date", "issued"):
                rec["date"] = rec["date"] if (rec["date"] and n == "date") else txt
            elif n in ("updated", "modified") and not rec["date"]:
                rec["date"] = txt
            elif n in ("description", "summary"):
                rec["summary"] = "".join(ch.itertext()).strip()
            elif n in ("encoded", "content"):
                rec["content"] = "".join(ch.itertext()).strip()
            elif n in ("guid", "id"):
                rec["guid"] = txt
        rec["summary"] = rec["summary"] or rec.pop("content")
        rec.pop("content", None)
        out.append(rec)
    return out, {"rss": "rss2", "feed": "atom", "rdf": "rss1"}.get(_local(root.tag), _local(root.tag))


A_RX = re.compile(r"<a\b[^>]*?href\s*=\s*[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a\s*>", re.I | re.S)


def parse_index(page, base, match=".", url_match=None, title_dates=False):
    """Links on a listing page. match filters titles, url_match filters URLs. Dates come from the URL;
    press-conference titles may also carry them (title_dates)."""
    mrx = re.compile(match or ".", re.I)
    urx = re.compile(url_match, re.I) if url_match else None
    seen, out = {}, []
    for href, inner in A_RX.findall(page or ""):
        title = squash(strip_html(inner))
        url = canon(urllib.parse.urljoin(base, href.strip()))
        if urx and not urx.search(url):
            continue
        if url in seen:  # the same link often appears twice, once around an image
            if title and not out[seen[url]]["title"] and mrx.search(title):
                out[seen[url]]["title"] = title
            continue
        if not title or not mrx.search(title):
            continue
        d, q = date_from_url(url)
        if d is None and title_dates:
            d2, q2 = date_from_text(title)
            if d2:
                d, q = d2, q2
        seen[url] = len(out)
        out.append({"title": title, "url": url, "date": d, "date_q": q})
    return out


def paragraphs(page, min_len=30):
    """Body text as paragraphs. Uses <p> blocks unless the page keeps its text in line-broken divs."""
    body = re.sub(r"(?is)<head\b.*?</head\s*>", " ", page or "")
    body = re.sub(r"(?is)<(script|style|noscript|nav|header|footer|aside|form)\b.*?</\1\s*>", " ", body)

    def keep(chunks):
        out = []
        for p in chunks:
            t = squash(strip_html(p))
            if len(t) >= min_len and t not in out:
                out.append(t)
        return out

    a = keep(re.findall(r"(?is)<p\b[^>]*>(.*?)</p\s*>", body))
    b = keep(strip_html(body).split("\n"))
    return a if sum(map(len, a)) >= 0.6 * sum(map(len, b)) else b


def qa_excerpts(paras, coder, max_n=6, max_len=900):
    """Press conference exchanges that touch AI: the question and its answer together."""
    spans = []
    for i, p in enumerate(paras):
        if not coder.is_ai(p):
            continue
        a, b = i, i
        if p.rstrip().endswith("?") and i + 1 < len(paras):
            b = i + 1
        elif i > 0 and paras[i - 1].rstrip().endswith("?"):
            a = i - 1
        if spans and a <= spans[-1][1] + 0:
            spans[-1] = (spans[-1][0], max(spans[-1][1], b))
        else:
            spans.append((a, b))
    return [clip(" / ".join(paras[a:b + 1]), max_len) for a, b in spans[:max_n]]


SPEAKER_RX = re.compile(r"Spokesperson\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})(?:'s|’s)")


# =============================================================================== items
NOCODE = {"ai": False, "seams": [], "prc": [], "hits": {}}
CARRY = ("excerpts", "ai", "seams", "prc", "hits", "followed", "n_paras", "date", "day", "date_q", "month", "speaker")
# Headlines about power prices are read even when they do not name AI: the data-center seam lives there.
FOLLOW_EXTRA = re.compile(r"\b(?:electricity|power (?:bills?|prices?|grid|shortages?)|grid|utilit(?:y|ies)"
                          r"|energy prices?)\b|电价|电费|电网|用电", re.I)


def make_item(src, kind, title, url, d, q, summary, excerpts, coder, code=True):
    key = re.sub(r"^https?://", "", url) if url else f"{src['outlet']}|{title}|{iso(d) or ''}"
    it = {"id": hashlib.sha1(key.encode("utf-8")).hexdigest()[:16], "outlet": src["outlet"],
          "source": src["id"], "lang": src.get("lang", "en"), "kind": kind, "date": iso(d),
          "day": bj_day(d), "date_q": q, "title": title, "url": url, "summary": summary,
          "excerpts": list(excerpts)}
    if q.startswith("month:"):
        it["month"], it["date_q"] = q[6:], "month"
    it.update(coder.code(title, summary, *excerpts) if code else dict(NOCODE))
    return it


def carry_over(it, k):
    """Keep what an earlier pull learned by reading the page (excerpts, coding, page date)."""
    if k and k.get("followed"):
        for f in CARRY:
            if f in k:
                it[f] = k[f]
        if not k.get("month"):
            it.pop("month", None)
    return it


def _span(items):
    ds = sorted(i["date"] for i in items if i.get("date"))
    return (ds[0], ds[-1]) if ds else (None, None)


def wants_follow(it, coder):
    return it["ai"] or bool(FOLLOW_EXTRA.search(coder.norm(it["title"] + " " + (it.get("summary") or ""))))


def follow_articles(fx, coder, items, known, limit, max_paras=3):
    n = 0
    for it in items:
        if it["kind"] != "article" or not it["url"] or not wants_follow(it, coder):
            continue
        k = known.get(it["id"])
        if k and k.get("followed"):
            carry_over(it, k)
            continue
        if n >= limit:
            continue
        n += 1
        try:
            r = fx.get(it["url"], accept="text/html,*/*;q=0.8")
        except Refused as e:
            fx.note(e)
            continue
        except FetchError:
            continue
        if r.status != 200:
            continue
        page = r.text
        if not it.get("date"):
            d, q = date_from_page(page)
            if d:
                it.update(date=iso(d), day=bj_day(d), date_q=q)
                it.pop("month", None)
        head = coder.norm(it["title"]).lower()[:60]
        ex = [clip(p, 500) for p in paragraphs(page)
              if coder.is_ai(p) and not coder.norm(p).lower().startswith(head)][:max_paras]
        if ex:
            it["excerpts"] = ex
            it.update(coder.code(it["title"], it["summary"], *ex))
        it["followed"] = True


def pull_rss(fx, coder, src, known, follow, follow_max):
    r = fx.get(src["url"], accept="application/rss+xml, application/atom+xml, application/xml;q=0.9, "
                                  "text/xml;q=0.8, */*;q=0.5")
    log = {"http": r.status, "final_url": redact(r.url)}
    if r.status != 200:
        log["status"] = f"http {r.status}"
        return [], log
    raw, flavor = parse_feed(r.text)
    items = []
    for rec in raw:
        title = squash(strip_html(rec["title"]))
        link = (rec["link"] or rec["guid"] or "").strip()
        if not title and not link:
            continue
        url = canon(urllib.parse.urljoin(r.url, link)) if link else ""
        d, q = parse_date(rec["date"])
        if d is None:
            d, q = date_from_url(url)
        items.append(make_item(src, "article", title, url, d, q, clip(strip_html(rec["summary"]), 600), [], coder))
    if follow:
        follow_articles(fx, coder, items, known, follow_max)
    oldest, newest = _span(items)
    log.update(status="ok" if items else "empty", flavor=flavor, n=len(items), ai=sum(i["ai"] for i in items),
               oldest=oldest, newest=newest)
    if newest and from_iso(newest) < dt.datetime.now(UTC) - dt.timedelta(days=STALE_DAYS):
        log["status"] = "stale"
    return items, log


def pull_index(fx, coder, src, known, since, follow, follow_max):
    """Listing pages. Press-conference mode reads every transcript in the window; article mode codes the
    headlines and, with --follow, reads the pages whose headlines touch AI, chips, data centers or power."""
    presser = src.get("mode") == "presser"
    r = fx.get(src["url"], accept="text/html,*/*;q=0.8")
    log = {"http": r.status, "final_url": redact(r.url)}
    if r.status != 200:
        log["status"] = f"http {r.status}"
        return [], log
    entries = parse_index(r.text, r.url, src.get("match") or ".", src.get("url_match"), title_dates=presser)
    items, n = [], 0
    for e in entries:
        if not presser:
            it = make_item(src, "article", e["title"], e["url"], e["date"], e["date_q"], "", [], coder)
            items.append(carry_over(it, known.get(it["id"])))
            continue
        it = make_item(src, "presser", e["title"], e["url"], e["date"], e["date_q"], "", [], coder, code=False)
        sp = SPEAKER_RX.search(e["title"])
        if sp:
            it["speaker"] = sp.group(1)
        k = known.get(it["id"])
        if k and k.get("followed"):
            items.append(carry_over(it, k))
            continue
        if e["date"] and e["date"] >= since and n < follow_max:
            n += 1
            try:
                page = fx.get(e["url"], accept="text/html,*/*;q=0.8")
                if page.status == 200:
                    paras = paragraphs(page.text)
                    ex = qa_excerpts(paras, coder)
                    it["excerpts"], it["n_paras"] = ex, len(paras)
                    it.update(coder.code(*ex) if ex else dict(NOCODE))
                    it["followed"] = True
            except Refused as ex_:
                fx.note(ex_)
            except FetchError:
                pass
        items.append(it)
    if not presser and follow:
        follow_articles(fx, coder, items, known, follow_max)
    oldest, newest = _span(items)
    log.update(status="ok" if items else "empty", flavor="presser" if presser else "index", n=len(items),
               ai=sum(i["ai"] for i in items), oldest=oldest, newest=newest)
    return items, log


YT_API = "https://www.googleapis.com/youtube/v3/"


def yt_url(endpoint, **params):
    return YT_API + endpoint + "?" + urllib.parse.urlencode(params)


def yt_allowed(ch):
    h = (ch.get("handle") or "").strip()
    if not h.startswith("@"):
        return False, "handle must start with @"
    if h.lower() in STATE_CHANNELS:
        return True, ""
    basis = ch.get("official_basis") or ""
    ok, why, _ = guard(basis) if basis else (False, "", "")
    if ok:
        return True, ""
    return False, ("not a built-in state-outlet channel; add official_basis: a page on an allowlisted "
                   "publisher domain that links this channel")


def pull_youtube(fx, coder, ch, key, since, max_pages=6):
    src = {"id": ch["id"], "outlet": ch["outlet"], "lang": ch.get("lang", "en")}
    j = fx.get_json(yt_url("channels", part="snippet,contentDetails", forHandle=ch["handle"], key=key))
    if not j.get("items"):
        return [], {"status": "handle not found", "http": 200}
    c = j["items"][0]
    uploads = c["contentDetails"]["relatedPlaylists"]["uploads"]
    log = {"http": 200, "channel_title": c["snippet"].get("title", ""), "channel_id": c.get("id", "")}
    items, token, pages, old = [], None, 0, 0
    while pages < max_pages:
        params = dict(part="snippet,contentDetails", playlistId=uploads, maxResults=50, key=key)
        if token:
            params["pageToken"] = token
        j = fx.get_json(yt_url("playlistItems", **params))
        pages += 1
        for v in j.get("items", []):
            cd, sn = v.get("contentDetails", {}), v.get("snippet", {})
            d, q = parse_date(cd.get("videoPublishedAt") or sn.get("publishedAt") or "")
            if d is None or d < since:
                old += 1
                continue
            vid = cd.get("videoId") or (sn.get("resourceId") or {}).get("videoId", "")
            it = make_item(src, "video", squash(sn.get("title", "")), f"https://www.youtube.com/watch?v={vid}",
                           d, q, clip(sn.get("description", ""), 600), [], coder)
            items.append(it)
        token = j.get("nextPageToken")
        if not token or old >= 10:
            break
    oldest, newest = _span(items)
    log.update(status="ok", flavor="youtube", n=len(items), ai=sum(i["ai"] for i in items), oldest=oldest,
               newest=newest, pages=pages)
    return items, log


# =============================================================================== config, archive, pack
_CMG = "China Media Group, under the CCP Central Propaganda Department"
_CDG = "China Daily Group, under the CCP Central Propaganda Department"
_PD = "People's Daily, organ of the CCP Central Committee"
_XH = "Xinhua News Agency, a ministry-level State Council body"
_GT = "Global Times, published by the People's Daily group"
_GOV = "State Council of the PRC"
_CNS = "China News Service, under the CCP United Front Work Department"
_U_CD = r"chinadaily\.com\.cn/a/\d{6}/\d{2}/WS"
_U_PDE = r"people\.(?:cn|com\.cn)/n3/\d{4}/\d{4}/c\d+-\d+\.html"
_U_PDZ = r"people\.com\.cn/n1/\d{4}/\d{4}/c\d+-\d+\.html"
_U_XH = r"(?:news\.cn|xinhuanet\.com)/(?:[a-z]+/)?\d{8}/[0-9a-f]{32}/c\.html"
_U_GOV = r"gov\.cn/.+/\d{6}/\d{2}/content_WS"
_U_CNS = r"ecns\.cn/.+/\d{4}-\d{2}-\d{2}/detail-"
_U_GT = r"globaltimes\.cn/page/\d{6}/\d+\.shtml"


def _rss(id_, outlet, owner, url, status, lang="en", enabled=True):
    return {"id": id_, "outlet": outlet, "owner": owner, "type": "rss", "lang": lang, "url": url,
            "enabled": enabled, "status": status}


def _ix(id_, outlet, owner, url, url_match, status, lang="en", enabled=True, **kw):
    d = {"id": id_, "outlet": outlet, "owner": owner, "type": "html-index", "lang": lang, "url": url,
         "enabled": enabled, "status": status}
    if url_match:
        d["url_match"] = url_match
    d.update(kw)
    return d


DEFAULT_CONFIG = {
    "tool": TOOL, "version": VERSION,
    "_help": [
        "contact: an email address or URL publishers can use to reach you. It goes in the User-Agent.",
        "sources: RSS or Atom feeds (type rss) and link-list pages (type html-index) on official PRC publisher domains.",
        "Every URL, redirect and followed link must pass the guard. The denylist of Chinese social media and messaging hosts outranks the allowlist.",
        "status records what was verified and when. Run check after every edit.",
        "youtube: official channels of state outlets through the YouTube Data API v3: titles, descriptions, publish times, no comments. A handle outside the built-in list needs official_basis, a page on an allowlisted publisher domain that links the channel.",
    ],
    "contact": "",
    "delay_seconds": 2.0,
    "sources": [
        _rss("cgtn-world", "CGTN", _CMG, "https://www.cgtn.com/subscribe/rss/section/world.xml", "verified 2026-09-26 UTC: RSS 2.0, 50 items with descriptions"),
        _rss("cgtn-china", "CGTN", _CMG, "https://www.cgtn.com/subscribe/rss/section/china.xml", "verified 2026-09-26 UTC: RSS 2.0, 50 items"),
        _rss("cgtn-business", "CGTN", _CMG, "https://www.cgtn.com/subscribe/rss/section/business.xml", "verified 2026-09-26 UTC: RSS 2.0, 50 items; US, China, AI and trade"),
        _ix("mfa-pressers", "PRC Foreign Ministry", "Ministry of Foreign Affairs", "https://www.fmprc.gov.cn/eng/xw/fyrbt/lxjzh/", None,
            "verified 2026-09-26 UTC: 8 regular press conferences listed; needs cookies, which DALABA keeps", mode="presser", match="regular press conference", follow_max=12),
        _ix("chinadaily-tech", "China Daily", _CDG, "https://www.chinadaily.com.cn/business/tech", _U_CD, "verified 2026-09-26 UTC: 35 dated links"),
        _ix("chinadaily-opinion", "China Daily", _CDG, "https://www.chinadaily.com.cn/opinion", _U_CD, "verified 2026-09-26 UTC: 45 dated links"),
        _ix("chinadaily-world", "China Daily", _CDG, "https://www.chinadaily.com.cn/world", _U_CD, "candidate: same link pattern as the verified China Daily pages"),
        _ix("peoplesdaily-scitech", "People's Daily", _PD, "http://en.people.cn/202936/index.html", _U_PDE, "verified 2026-09-26 UTC: 26 dated links"),
        _ix("peoplesdaily-opinion", "People's Daily", _PD, "http://en.people.cn/90780/index.html", _U_PDE, "verified 2026-09-26 UTC: 26 dated links"),
        _ix("xinhua-latest", "Xinhua", _XH, "https://english.news.cn/list/latestnews.htm", _U_XH, "verified 2026-09-26 UTC: 20 dated links, all from the last day"),
        _ix("xinhua-world", "Xinhua", _XH, "https://english.news.cn/world/index.htm", _U_XH, "verified 2026-09-26 UTC: 44 dated links"),
        _ix("govcn-news", "State Council (gov.cn)", _GOV, "https://english.www.gov.cn/news/", _U_GOV, "verified 2026-09-26 UTC: 16 dated links"),
        _ix("govcn-policies", "State Council (gov.cn)", _GOV, "https://english.www.gov.cn/policies/", _U_GOV, "verified 2026-09-26 UTC: 12 dated links"),
        _ix("ecns-scitech", "China News Service", _CNS, "https://www.ecns.cn/news/sci-tech/", _U_CNS, "verified 2026-09-26 UTC: 37 dated links"),
        _ix("ecns-voices", "China News Service", _CNS, "https://www.ecns.cn/voices/", _U_CNS, "verified 2026-09-26 UTC: 24 dated links"),
        _ix("globaltimes-opinion", "Global Times", _GT, "https://www.globaltimes.cn/opinion/", _U_GT, "verified 2026-09-26 UTC: 210 links dated to the month only; --follow reads the page date"),
        _ix("globaltimes-world", "Global Times", _GT, "https://www.globaltimes.cn/world/index.html", _U_GT, "verified 2026-09-26 UTC: 103 links dated to the month only"),
        _ix("globaltimes-business", "Global Times", _GT, "https://www.globaltimes.cn/business/index.html", _U_GT, "candidate: same link pattern as the verified Global Times pages"),
        _ix("peoplesdaily-zh-world", "People's Daily (Chinese)", _PD, "http://world.people.com.cn/", _U_PDZ, "verified 2026-09-26 UTC: 88 dated links", lang="zh"),
        _ix("peoplesdaily-zh-opinion", "People's Daily (Chinese)", _PD, "http://opinion.people.com.cn/", _U_PDZ, "verified 2026-09-26 UTC: 41 dated links", lang="zh"),
        _ix("xinhua-zh-world", "Xinhua (Chinese)", _XH, "http://www.news.cn/world/index.html", _U_XH, "verified 2026-09-26 UTC: 74 dated links", lang="zh"),
        _ix("xinhua-zh-tech", "Xinhua (Chinese)", _XH, "http://www.xinhuanet.com/tech/", _U_XH, "verified 2026-09-26 UTC: 29 dated links", lang="zh"),
        _rss("globaltimes-rss", "Global Times", _GT, "https://www.globaltimes.cn/rss/outbrain.xml", "disabled: answers, but carries a recommendation set from Nov 2025 to Aug 2026, not current news", enabled=False),
        _rss("xinhua-rss", "Xinhua", _XH, "https://english.news.cn/rss/worldrss.xml", "disabled: answers, but its newest item is from January 2018", enabled=False),
        _rss("chinadaily-rss", "China Daily", _CDG, "https://www.chinadaily.com.cn/rss/world_rss.xml", "disabled: 404 on 2026-09-25", enabled=False),
        _rss("peoplesdaily-rss", "People's Daily", _PD, "http://en.people.cn/rss/90001.xml", "disabled: 404 on 2026-09-25", enabled=False),
    ],
    "youtube": [
        {"id": "yt-cgtn", "outlet": "CGTN", "handle": "@CGTN", "enabled": True, "status": "candidate: confirm with check --youtube-key"},
        {"id": "yt-cgtn-america", "outlet": "CGTN America", "handle": "@CGTNAmerica", "enabled": True, "status": "candidate"},
        {"id": "yt-cgtn-europe", "outlet": "CGTN Europe", "handle": "@CGTNEurope", "enabled": True, "status": "candidate"},
        {"id": "yt-cgtn-africa", "outlet": "CGTN Africa", "handle": "@CGTNAfrica", "enabled": True, "status": "candidate"},
        {"id": "yt-newchinatv", "outlet": "New China TV (Xinhua)", "handle": "@NewChinaTV", "enabled": True, "status": "candidate"},
        {"id": "yt-cctv-vna", "outlet": "CCTV Video News Agency", "handle": "@CCTVVideoNewsAgency", "enabled": True, "status": "candidate"},
    ],
}


def load_config(out):
    p = Path(out) / CONFIG
    if not p.exists():
        sys.exit(f"No {CONFIG} in {out}. Run: python dalaba.py init --out {out}")
    cfg = json.loads(p.read_text(encoding="utf-8"))
    good, bad = [], []
    for s in cfg.get("sources", []):
        if not s.get("enabled", True):
            continue
        if s.get("type") not in ("rss", "html-index") or not s.get("id") or not s.get("outlet") or not s.get("url"):
            bad.append((s.get("id", "?"), "needs id, outlet, url and type rss or html-index"))
            continue
        ok, why, _ = guard(s["url"])
        (good if ok else bad).append(s if ok else (s["id"], "refused by the guard: " + why))
    yts = []
    for c in cfg.get("youtube", []):
        if not c.get("enabled", True):
            continue
        ok, why = yt_allowed(c)
        (yts if ok else bad).append(c if ok else (c.get("id", "?"), why))
    return cfg, good, yts, bad


def load_archive(out):
    recs = {}
    p = Path(out) / ARCHIVE
    if p.exists():
        with p.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    r = json.loads(line)
                    recs[r["id"]] = r
    return recs


def save_archive(out, recs):
    p = Path(out) / ARCHIVE
    tmp = p.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for r in sorted(recs.values(), key=lambda r: (r.get("date") or "", r["id"])):
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(p)


def _tkey(r):
    t = re.sub(r"\W+", " ", (r.get("title") or "").lower()).strip()
    when = r.get("day") or r.get("month")
    return (r.get("outlet"), when, t) if len(t) > 12 and when else None


def merge(recs, items, now):
    """Add a pull to the archive. The same story under a second URL (a mirror host) is dropped."""
    seen_titles = {}
    for rid, r in recs.items():
        k = _tkey(r)
        if k:
            seen_titles[k] = rid
    new = 0
    for it in items:
        old = recs.get(it["id"])
        if old is None:
            k = _tkey(it)
            if k and k in seen_titles and seen_titles[k] != it["id"]:
                continue
            it["first_seen"] = it["last_seen"] = iso(now)
            recs[it["id"]] = it
            if k:
                seen_titles[k] = it["id"]
            new += 1
            continue
        it["first_seen"], it["last_seen"] = old.get("first_seen", iso(now)), iso(now)
        if old.get("followed") and not it.get("followed"):
            carry_over(it, old)
        recs[it["id"]] = it
    return new


def read_pulls(out):
    p = Path(out) / PULLS
    rows = []
    if p.exists():
        with p.open(encoding="utf-8") as fh:
            rows = [json.loads(x) for x in fh if x.strip()]
    return rows


def build_pack(out, days, now, keep_all=False, coder=None, synthetic=False):
    coder = coder or Coder()
    recs = load_archive(out)
    pulls = read_pulls(out)
    end = now.astimezone(BJT).date()
    start = end - dt.timedelta(days=days - 1)
    win = [r for r in recs.values() if r.get("day") and start.isoformat() <= r["day"] <= end.isoformat()]
    months = {start.strftime("%Y-%m"), end.strftime("%Y-%m")}
    month_only = {}
    for r in recs.values():
        if not r.get("day") and r.get("month") in months:
            month_only[r["outlet"]] = month_only.get(r["outlet"], 0) + 1
    undated = sum(1 for r in recs.values() if not r.get("day") and not r.get("month"))
    volume, by_out, by_seam, by_prc = {}, {}, {}, {}
    for r in win:
        v = volume.setdefault(r["outlet"], {}).setdefault(r["day"], [0, 0])
        v[0] += 1
        o = by_out.setdefault(r["outlet"], {"items": 0, "ai": 0})
        o["items"] += 1
        if r.get("ai"):
            v[1] += 1
            o["ai"] += 1
            for s in r.get("seams", []):
                by_seam[s] = by_seam.get(s, 0) + 1
            for c in r.get("prc", []):
                by_prc[c] = by_prc.get(c, 0) + 1
    items = sorted((r for r in win if r.get("ai") or keep_all), key=lambda r: r.get("date") or "", reverse=True)
    t0 = dt.datetime.combine(start, dt.time(0, 0), tzinfo=BJT).astimezone(UTC)
    srcs, refused = {}, {}
    for p in pulls:
        for x in p.get("refused", []):
            refused[x["url"]] = x["reason"]
        if from_iso(p["time"]) < t0:
            continue
        s = srcs.setdefault(p["source"], {"id": p["source"], "outlet": p.get("outlet", ""), "type": p.get("type", ""),
                                          "pulls": 0, "ok": 0, "gaps": [], "_prev": None})
        s["pulls"] += 1
        s.update(last_pull=p["time"], last_status=p.get("status"), last_http=p.get("http"))
        s.setdefault("first_pull", p["time"])
        if p.get("status") in ("ok", "stale"):
            s["ok"] += 1
            prev = s["_prev"]
            if prev and p.get("oldest") and p["oldest"] > prev and p.get("type") != "youtube":
                s["gaps"].append([prev, p["oldest"]])
            s["_prev"] = p["time"]
    for s in srcs.values():
        s.pop("_prev", None)
        s["gaps"] = s["gaps"][-20:]
    notes = [
        "Codes are topics, not stance: a hit says an item raised the topic, not which side it took.",
        "Days are Beijing dates (UTC+8). Volume counts every item seen; items lists AI-relevant items only"
        + (" (keep-all: every item)." if keep_all else "."),
        "Feeds hold their latest 20 to 50 items. A gap means a pull came after the previous pull's items had "
        "scrolled off; items published inside a gap may be missing.",
        "DALABA reads official publishers only. It does not read Chinese social media or messaging platforms.",
    ]
    notes.append("Listing-page sources are coded on headlines; with --follow, pages whose headlines touch AI, "
                 "chips, data centers or power prices are read and recoded on their text.")
    if month_only:
        notes.append("Some URLs carry only a month (Global Times). Unread items from those outlets are counted in "
                     "month_only, not in daily volume, so their daily AI share runs high.")
    if undated:
        notes.append(f"{undated} archived items have no usable date and sit outside every window.")
    pack = {
        "kind": KIND, "tool": TOOL, "version": VERSION, "synthetic": bool(synthetic), "generated": iso(now),
        "window": {"days": days, "start": start.isoformat(), "end": end.isoformat(), "tz": "UTC+8 (Beijing) dates"},
        "codebook": {"version": coder.version,
                     "seams": {k: v["label"] for k, v in coder.book["seams"].items()},
                     "prc": {k: v["label"] for k, v in coder.book["prc"].items()}},
        "sources": sorted(srcs.values(), key=lambda s: s["id"]),
        "volume": volume,
        "counts": {"items": len(win), "ai": sum(1 for r in win if r.get("ai")), "by_outlet": by_out,
                   "by_seam": by_seam, "by_prc": by_prc, "month_only": month_only},
        "items": items,
        "refused": [{"url": u, "reason": r} for u, r in sorted(refused.items())][:200],
        "notes": notes,
    }
    path = Path(out) / f"statevoice_{now.astimezone(UTC).strftime('%Y%m%d')}.json"
    path.write_text(json.dumps(pack, ensure_ascii=False, indent=1), encoding="utf-8")
    return path, pack


# =============================================================================== commands
def cmd_init(args):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    p = out / CONFIG
    if p.exists() and not args.force:
        print(f"{p} exists; left alone (use --force to overwrite).")
    else:
        p.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {p}")
    print("next: put your email or URL in \"contact\", then run: python dalaba.py check --out", args.out)


def _fmt(s):
    return (s or "").replace("T", " ")[:16] or "-"


def cmd_check(args):
    cfg, srcs, yts, bad = load_config(args.out)
    fx = Fetcher(cfg.get("contact", ""), cfg.get("delay_seconds", 2.0))
    coder = Coder()
    print(f"{TOOL} {VERSION} check. Guard: {len(DENY)} denied platform domains, {len(ALLOW)} publisher domains.")
    if not cfg.get("contact"):
        print("  note: contact is empty; publishers see 'contact: not set' in the User-Agent.")
    since = dt.datetime.now(UTC) - dt.timedelta(days=14)
    for s in srcs:
        try:
            if s["type"] == "rss":
                items, log = pull_rss(fx, coder, s, {}, False, 0)
            else:
                items, log = pull_index(fx, coder, s, {}, since, False, 1)
            print(f"  {log['status']:<9} {s['id']:<18} {log.get('flavor', ''):<7} {log.get('n', 0):>3} items  "
                  f"AI {log.get('ai', 0):>3}  newest {_fmt(log.get('newest'))}  http {log.get('http')}")
        except Refused as e:
            print(f"  refused   {s['id']:<18} {e.reason}")
        except FetchError as e:
            print(f"  failed    {s['id']:<18} {e.reason}")
    key = args.youtube_key
    for c in yts:
        if not key:
            print(f"  skipped   {c['id']:<18} no YouTube key (--youtube-key or DALABA_YT_KEY)")
            continue
        try:
            j = fx.get_json(yt_url("channels", part="snippet", forHandle=c["handle"], key=key))
            if j.get("items"):
                print(f"  ok        {c['id']:<18} {c['handle']} is '{j['items'][0]['snippet'].get('title', '')}'")
            else:
                print(f"  not found {c['id']:<18} {c['handle']}")
        except (Refused, FetchError) as e:
            print(f"  failed    {c['id']:<18} {e.reason}")
    for sid, why in bad:
        print(f"  skipped   {sid:<18} {why}")
    for x in fx.refused:
        print(f"  refused   {x['url']}  {x['reason']}")


def cmd_pull(args, fx=None, now=None):
    cfg, srcs, yts, bad = load_config(args.out)
    only = set(filter(None, (args.only or "").split(",")))
    if only:
        srcs = [s for s in srcs if s["id"] in only]
        yts = [c for c in yts if c["id"] in only]
    fx = fx or Fetcher(cfg.get("contact", ""), cfg.get("delay_seconds", 2.0))
    now = now or dt.datetime.now(UTC)
    since = now - dt.timedelta(days=args.days)
    coder = Coder()
    recs = load_archive(args.out)
    rows, total_new = [], 0
    jobs = [("src", s) for s in srcs] + [("yt", c) for c in yts if args.youtube_key]
    for kind, s in jobs:
        before = len(fx.refused)
        log = {"source": s["id"], "outlet": s["outlet"], "time": iso(now)}
        try:
            if kind == "yt":
                log["type"] = "youtube"
                items, extra = pull_youtube(fx, coder, s, args.youtube_key, since)
            elif s["type"] == "rss":
                log["type"] = "rss"
                items, extra = pull_rss(fx, coder, s, recs, args.follow, args.follow_max)
            else:
                log["type"] = "html-index"
                fmax = int(s.get("follow_max", 12)) if s.get("mode") == "presser" else args.follow_max
                items, extra = pull_index(fx, coder, s, recs, since, args.follow, fmax)
            log.update(extra)
        except Refused as e:
            items = []
            log.update(status="refused", reason=e.reason)
            fx.note(e)
        except FetchError as e:
            items = []
            log.update(status="failed", reason=e.reason)
        new = merge(recs, items, now)
        total_new += new
        log["new"] = new
        log["refused"] = fx.refused[before:]
        rows.append(log)
        print(f"  {log.get('status', '?'):<9} {s['id']:<18} {log.get('n', 0):>3} items {new:>3} new  AI "
              f"{log.get('ai', 0):>3}  newest {_fmt(log.get('newest'))}")
    save_archive(args.out, recs)
    with (Path(args.out) / PULLS).open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    for sid, why in bad:
        print(f"  skipped   {sid:<18} {why}")
    if yts and not args.youtube_key:
        print(f"  skipped   {len(yts)} YouTube channels (no key)")
    ai_n = sum(1 for r in recs.values() if r.get("ai"))
    print(f"archive: {len(recs)} items, {ai_n} AI-relevant, {total_new} new this pull")
    if not args.no_pack:
        path, pack = build_pack(args.out, args.days, now, args.keep_all, coder)
        print(f"pack: {path} ({pack['counts']['items']} items in {args.days} days, {pack['counts']['ai']} AI)")
    return rows


def cmd_pack(args):
    path, pack = build_pack(args.out, args.days, dt.datetime.now(UTC), args.keep_all)
    print(f"pack: {path} ({pack['counts']['items']} items in {args.days} days, {pack['counts']['ai']} AI)")


def cmd_recode(args):
    coder = Coder()
    recs = load_archive(args.out)
    for r in recs.values():
        if r["kind"] == "presser":
            r.update(coder.code(*r.get("excerpts", [])) if r.get("excerpts") else dict(NOCODE))
        else:
            r.update(coder.code(r.get("title", ""), r.get("summary", ""), *r.get("excerpts", [])))
    save_archive(args.out, recs)
    print(f"recoded {len(recs)} items with codebook {coder.version}")


# =============================================================================== selftest
RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>World</title>
<item><title><![CDATA[America's AI boom is powering an electricity squeeze]]></title>
<link>https://news.cgtn.com/news/2026-09-24/AI-boom-1ab/p.html?UTM_Source=cgtn&amp;UTM_Medium=rss&amp;UTM_Campaign=World</link>
<pubDate>Thu, 24 Sep 2026 08:30:00 GMT</pubDate>
<description><![CDATA[<p>US households face higher power bills as data centers strain the PJM grid, and critics warn of an AI bubble.</p>]]></description></item>
<item><title>China's action plan for global AI governance wins support across the Global South</title>
<link>https://news.cgtn.com/news/2026-09-23/AI-governance-2de/p.html</link>
<pubDate>Wed, 23 Sep 2026 02:00:00 GMT</pubDate>
<description>Countries welcomed the World AI Cooperation Organization and China's open-source models.</description></item>
<item><title>China's mooncakes get a fresh twist</title><link>https://news.cgtn.com/news/2026-09-24/mooncakes-3gh/p.html</link>
<pubDate>Thu, 24 Sep 2026 10:00:00 GMT</pubDate><description>Bakers try new fillings.</description></item>
</channel></rss>"""

ATOM_SAMPLE = """<feed xmlns="http://www.w3.org/2005/Atom"><title>Opinion</title>
<entry><title type="html">Wall Street braces as the &lt;b&gt;AI bubble&lt;/b&gt; wobbles</title>
<link rel="alternate" href="https://www.globaltimes.cn/page/202609/1300001.shtml"/><updated>2026-09-22T09:00:00Z</updated>
<summary>Nvidia and OpenAI face hard questions.</summary></entry>
<entry><title>Autumn tourism booms</title><link href="https://www.globaltimes.cn/page/202609/1300002.shtml"/>
<published>2026-09-21T09:00:00+08:00</published><summary>Travel is up.</summary></entry></feed>"""

RDF_SAMPLE = """<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/"
 xmlns:dc="http://purl.org/dc/elements/1.1/"><channel rdf:about="x"><title>X</title></channel>
<item rdf:about="https://english.news.cn/20260920/abc/c.html"><title>Spokesperson: US export controls on chips reflect a Cold War mentality</title>
<link>https://english.news.cn/20260920/abc/c.html</link><dc:date>2026-09-20T08:00:00+08:00</dc:date>
<description>The entity list and chip bans hurt global supply chains.</description></item></rdf:RDF>"""

BROKEN_RSS = """<rss version="2.0"><channel><item><title>AI & jobs: automation is replacing factory workers</title>
<link>https://www.chinadaily.com.cn/a/202609/19/WS1.html</link><pubDate>2026-09-19 10:00:00</pubDate>
<description>Unions & scholars weigh in.</description></item></channel></rss>"""

ZH_RSS = """<rss version="2.0"><channel><item><title>美国人工智能热潮推高电价 数据中心耗电引发居民不满</title>
<link>http://www.people.com.cn/n1/2026/0918/c1002-1.html</link><pubDate>2026年09月18日 09:30</pubDate>
<description>英伟达和OpenAI面临泡沫质疑。</description></item></channel></rss>"""

MFA_INDEX = """<html><body><ul class="list">
<li><a href="./202609/t20260924_11800001.html" target="_blank">Foreign Ministry Spokesperson Lin Jian's Regular Press Conference on September 24, 2026</a><span>2026-09-24</span></li>
<li><a href="./202609/t20260923_11800002.html">Foreign Ministry Spokesperson Lin Jian's Regular Press Conference on September 23, 2026</a></li>
<li><a href="https://weibo.com/mfa">Weibo</a></li><li><a href="/eng/">Home</a></li></ul></body></html>"""

MFA_P1 = """<html><body><div class="content">
<p>CCTV: The US has expanded export controls on AI chips again. What is China's comment?</p>
<p>Lin Jian: The US has overstretched the concept of national security and weaponized tech and trade issues. This is a Cold War mentality. China firmly opposes it.</p>
<p>AFP: A question about the typhoon season this week in the south?</p>
<p>Lin Jian: I would refer you to the competent authorities for the details on that.</p>
<p>Bloomberg: Some US officials say data center protests are a China PR operation. Your response?</p>
<p>Lin Jian: We have no knowledge of that. China never interferes in other countries' internal affairs.</p>
</div></body></html>"""

MFA_P2 = """<html><body><div class="content">
<p>Reuters: Can you share details of the minister's upcoming trip to Europe next month?</p>
<p>Lin Jian: We will release information in due course. Please stay tuned for the announcement.</p>
<p>Kyodo: Is there any update on the fishing vessel issue raised last week?</p>
</div></body></html>"""

CD_ROBOTS = "User-agent: *\nDisallow: /a/\n"

GT_INDEX = """<div><a href="https://www.globaltimes.cn/page/202609/1371340.shtml"><img src="x.jpg"></a>
<a class="new_title_ml" href="https://www.globaltimes.cn/page/202609/1371340.shtml">US data centers push power bills up as AI bubble fears mount: Global Times editorial</a>
<a href="https://www.globaltimes.cn/page/202609/1371081.shtml">Autumn festival markets draw crowds</a>
<a href="/opinion/editorial/index.html">Editorial</a></div>"""

GT_ARTICLE = """<html><head><meta name="twitter:title" content="x"></head><body><div class="article_right">
<span class="pub_time">Published: Sep 25, 2026 11:28 PM</span>
<div class="article_content">American households are paying more for electricity as AI data centers soak up power from the PJM grid.<br><br>
Critics in Washington warn that the AI bubble will leave ordinary taxpayers holding the bill for years.<br><br>
Meanwhile the autumn harvest continues across the northern provinces with record yields reported.<br></div>
<p>Photo: VCG</p></div></body></html>"""

CD_INDEX = """<a href="//www.chinadaily.com.cn/a/202609/24/WS6ab48113e4b06d4aa055ff09.html">Cartoon: Smaller screens, thicker glasses</a>
<a href="//www.chinadaily.com.cn/a/202609/22/WS6ab1d790e4b06d4aa055f673.html">Power prices climb as the grid strains in Virginia</a>
<a href="https://www.chinadaily.com.cn/opinion/editorials">Editorials</a>"""


def _offline_web():
    web = {
        "https://www.cgtn.com/rss/world.xml": (200, "application/rss+xml; charset=utf-8", RSS_SAMPLE),
        "https://www.globaltimes.cn/rss/atom.xml": (200, "application/atom+xml", ATOM_SAMPLE),
        "https://english.news.cn/rss/rdf.xml": (200, "application/rdf+xml", RDF_SAMPLE),
        "https://www.chinadaily.com.cn/rss/broken.xml": (200, "text/xml", BROKEN_RSS),
        "https://www.chinadaily.com.cn/robots.txt": (200, "text/plain", CD_ROBOTS),
        "http://www.people.com.cn/rss/zh.xml": (200, "text/xml; charset=gb2312", ZH_RSS.encode("gb18030")),
        "https://www.fmprc.gov.cn/eng/xw/fyrbt/lxjzh/": (302, "", "/eng/xw/fyrbt/lxjzh/index.html"),
        "https://www.fmprc.gov.cn/eng/xw/fyrbt/lxjzh/index.html": (200, "text/html", MFA_INDEX),
        "https://www.fmprc.gov.cn/eng/xw/fyrbt/lxjzh/202609/t20260924_11800001.html": (200, "text/html", MFA_P1),
        "https://www.fmprc.gov.cn/eng/xw/fyrbt/lxjzh/202609/t20260923_11800002.html": (200, "text/html", MFA_P2),
        "https://news.cgtn.com/news/2026-09-24/AI-boom-1ab/p.html": (200, "text/html", "<p>short</p><p>Analysts say US data centers will need far more electricity, and AI demand is pushing power bills up for households across Virginia.</p><p>Other text about the weather in Beijing this week and nothing else.</p><p>A final paragraph about sports results from the weekend games.</p>"),
        "https://www.cgtn.com/rss/redirect.xml": (302, "", "https://weibo.com/cgtn"),
        "https://www.globaltimes.cn/opinion/": (200, "text/html", GT_INDEX),
        "https://www.globaltimes.cn/page/202609/1371340.shtml": (200, "text/html", GT_ARTICLE),
        "https://www.chinadaily.com.cn/opinion": (200, "text/html", CD_INDEX),
        yt_url("channels", part="snippet,contentDetails", forHandle="@CGTN", key="TESTKEY"): (200, "application/json", json.dumps(
            {"items": [{"id": "UC_TEST_CHANNEL", "snippet": {"title": "Test channel"},
                        "contentDetails": {"relatedPlaylists": {"uploads": "UU_TEST_UPLOADS"}}}]})),
        yt_url("playlistItems", part="snippet,contentDetails", playlistId="UU_TEST_UPLOADS", maxResults=50, key="TESTKEY"): (200, "application/json", json.dumps(
            {"items": [
                {"snippet": {"title": "Why America's AI power crunch is hitting households", "description": "Data centers and electricity bills."},
                 "contentDetails": {"videoId": "vid00000001", "videoPublishedAt": "2026-09-24T12:00:00Z"}},
                {"snippet": {"title": "Panda cubs meet the public", "description": "Cute."},
                 "contentDetails": {"videoId": "vid00000002", "videoPublishedAt": "2026-09-23T12:00:00Z"}},
                {"snippet": {"title": "Old upload", "description": "An AI story from August."},
                 "contentDetails": {"videoId": "vid00000003", "videoPublishedAt": "2026-08-01T12:00:00Z"}}]})),
    }
    return web


def cmd_selftest(args):
    fails, n = [], [0]

    def ok(cond, label):
        n[0] += 1
        if not cond:
            fails.append(label)

    # guard
    allow = ["https://www.cgtn.com/x", "https://news.cgtn.com/news/x", "https://english.news.cn/x",
             "https://www.fmprc.gov.cn/eng/", "https://un.china-mission.gov.cn/eng/", "http://en.people.cn/n3/x.html"]
    deny = ["https://weibo.com/u/1", "https://m.weibo.cn/status/1", "https://mp.weixin.qq.com/s/abc",
            "https://www.douyin.com/video/1", "https://www.xiaohongshu.com/explore/1", "https://xhslink.com/a",
            "https://www.bilibili.com/video/BV1", "https://www.zhihu.com/question/1", "https://tieba.baidu.com/p/1",
            "https://www.kuaishou.com/short-video/1", "https://www.toutiao.com/article/1", "https://www.dingtalk.com/",
            "https://www.feishu.cn/", "https://www.tiktok.com/@cgtn", "https://bbs1.people.com.cn/",
            "https://www.reddit.com/r/antiai", "https://example.com/", "ftp://www.cgtn.com/x", "https://www.youtube.com/@CGTN"]
    for u in allow:
        ok(guard(u)[0], f"guard should allow {u}")
    for u in deny:
        ok(not guard(u)[0], f"guard should refuse {u}")
    ok(guard(YT_API + "channels?x=1", "youtube-api")[0], "guard allows the YouTube API for youtube purpose")
    ok(not guard(YT_API + "channels?x=1")[0], "guard refuses the YouTube API for plain fetches")
    ok(not guard("https://weibo.com/x", "youtube-api")[0], "denylist outranks the youtube purpose")
    ok(yt_allowed({"handle": "@CGTN"})[0] and not yt_allowed({"handle": "@somecreator"})[0], "YouTube handle allowlist")
    ok(yt_allowed({"handle": "@x", "official_basis": "https://www.cgtn.com/about"})[0], "official_basis admits a handle")
    ok(not yt_allowed({"handle": "@x", "official_basis": "https://weibo.com/x"})[0], "official_basis on a denied host fails")
    h = Guarded()
    req = urllib.request.Request("https://www.cgtn.com/a")
    try:
        h.redirect_request(req, None, 302, "Found", {}, "https://weibo.com/x")
        ok(False, "redirect to Weibo must raise Refused")
    except Refused:
        ok(True, "")
    nxt = h.redirect_request(req, None, 302, "Found", {}, "https://news.cgtn.com/b")
    ok(nxt is not None and nxt.full_url == "https://news.cgtn.com/b", "redirect within publishers passes")

    # dates
    cases = [("Fri, 25 Sep 2026 13:59:51 GMT", "2026-09-25T13:59:51Z", "feed"),
             ("2026-09-20T08:00:00+08:00", "2026-09-20T00:00:00Z", "feed"),
             ("2026-09-22T09:00:00Z", "2026-09-22T09:00:00Z", "feed"),
             ("2026-09-19 10:00:00", "2026-09-19T02:00:00Z", "assumed-utc8"),
             ("2026年09月18日 09:30", "2026-09-18T01:30:00Z", "assumed-utc8"),
             ("2026-09-21T09:00:00.12345+0800", "2026-09-21T01:00:00Z", "feed"),
             ("", None, "missing"), ("garbage", None, "unparsed")]
    for raw, want, q in cases:
        d, qq = parse_date(raw)
        ok(iso(d) == want and qq == q, f"parse_date({raw!r}) gave {iso(d)} {qq}")
    d, q = date_from_text("Foreign Ministry Spokesperson Lin Jian's Regular Press Conference on September 24, 2026")
    ok(iso(d) == "2026-09-24T04:00:00Z" and q == "date-only", "date from a press-conference title")
    d, q = date_from_url("https://x.gov.cn/eng/202609/t20260924_1.html")
    ok(iso(d) == "2026-09-24T04:00:00Z" and q == "url", "date from a URL")

    # coding
    c = Coder()
    want = [("Data centers are pushing up electricity bills for US families", "DC"),
            ("The Pentagon's AI targeting systems", "MIL"), ("Fears of an AI bubble grow on Wall Street", "BUB"),
            ("ChatGPT accused of bias and censorship", "TRUST"), ("AI is replacing jobs for illustrators", "JOBS"),
            ("Artists sue over AI-generated images and copyright", "ART"),
            ("Scientists warn AI could lead to human extinction", "DOOM"),
            ("Consumers boycott generative AI products", "BOY"),
            ("DeepSeek and Qwen lead open AI models", "AI_PRC_POSITIVE"),
            ("US export controls on H20 chips", "EXPORT_CONTROLS"), ("New tariffs on semiconductors", "TECH_TARIFFS"),
            ("On AI, the United States is in decline", "US_DECLINE"),
            ("Chip curbs show a zero-sum game and Cold War mentality", "PRC_OFFICIAL_PHRASING"),
            ("China released the Global AI Governance Action Plan", "AI_GOVERNANCE"),
            ("Nvidia and OpenAI face questions", "AI_US_TARGET"),
            ("China, US reach trade consensus and hold first AI dialogue", "AI_COOPERATION"),
            ("Three areas with huge potential for Sino-US AI collaboration", "AI_COOPERATION"),
            ("AI growth is underwritten by ample power supply in China", "POWER_CONTRAST"),
            ("US calls for AI safety are a pretext to contain China", "SAFETY_AS_CONTAINMENT"),
            ("The US AI Cold\u2011War script", "PRC_OFFICIAL_PHRASING"),
            ("OpenAI breach of Australian healthcare records", "TRUST"),
            ("Military strikes on China's data centers? A dangerous delusion", "MIL"),
            ("中美在人工智能领域加强合作", "AI_COOPERATION"),
            ("US and China agree to an AI dialogue with an incident line", "AI_COOPERATION"),
            ("Targeting China's AI: US tech right unfolds Cold War playbook", "COLD_WAR_FRAME"),
            ("数据中心耗电推高电价", "DC"), ("人工智能泡沫正在破裂", "BUB"), ("美方滥用芯片出口管制", "EXPORT_CONTROLS"),
            ("芯片问题上的冷战思维", "PRC_OFFICIAL_PHRASING"), ("世界人工智能合作组织在上海成立", "AI_GOVERNANCE")]
    for text, code in want:
        r = c.code(text)
        ok(r["ai"] and (code in r["seams"] or code in r["prc"]), f"coding {text!r} should hit {code}, got {r}")
    for text in ("Mooncake sales rise in Shanghai and Dubai", "Autumn tourism booms"):
        r = c.code(text)
        ok(not r["ai"] and not r["seams"], f"{text!r} should not pass the AI gate")
    ok(c.code("AI技术")["ai"], "AI inside Chinese text passes the gate")
    ok("AI_COOPERATION" not in c.code("Companies from China, ASEAN showcase AI cooperation at the expo")["prc"], "China-ASEAN is not US-China")
    ok("COLD_WAR_FRAME" not in c.code("AI seen as new frontier for win-win cooperation")["prc"], "win-win is not Cold War framing")

    # parsers
    for sample, n_items in ((RSS_SAMPLE, 3), (ATOM_SAMPLE, 2), (RDF_SAMPLE, 1), (ZH_RSS, 1)):
        items, flavor = parse_feed(sample)
        ok(len(items) == n_items and flavor != "lenient", f"parse_feed {flavor} gave {len(items)} items")
    items, flavor = parse_feed(BROKEN_RSS)
    ok(flavor == "lenient" and len(items) == 1 and items[0]["title"].startswith("AI & jobs"), "lenient parser rescues bad XML")
    idx = parse_index(MFA_INDEX, "https://www.fmprc.gov.cn/eng/xw/fyrbt/lxjzh/index.html", "regular press conference")
    ok(len(idx) == 2 and idx[0]["url"].endswith("t20260924_11800001.html"), "press-conference index parsed")
    ex = qa_excerpts(paragraphs(MFA_P1), c)
    ok(len(ex) == 2 and "Cold War mentality" in ex[0] and "data center" in ex[1], f"Q&A excerpts: {ex}")
    ok(canon("https://news.cgtn.com/a/p.html?UTM_Source=cgtn&x=1#f") == "https://news.cgtn.com/a/p.html?x=1", "canonical URL")
    d, q = date_from_page(GT_ARTICLE)
    ok(iso(d) == "2026-09-25T15:28:00Z" and q == "page", f"date from a 'Published:' line gave {iso(d)}")
    d, q = date_from_page('<meta property="article:published_time" content="2026-09-24T10:00:00+08:00">')
    ok(iso(d) == "2026-09-24T02:00:00Z", "date from a meta tag")
    ok(date_from_url("https://www.globaltimes.cn/page/202609/1371340.shtml") == (None, "month:2026-09"), "month-only URL")
    ok(any("PJM grid" in x for x in paragraphs(GT_ARTICLE)), "line-broken article bodies are read")
    src0 = {"id": "x", "outlet": "People's Daily"}
    a = make_item(src0, "article", "Same story", "http://en.people.cn/n3/2026/0926/c90000-1.html", None, "missing", "", [], c)
    b = make_item(src0, "article", "Same story", "https://en.people.cn/n3/2026/0926/c90000-1.html", None, "missing", "", [], c)
    ok(a["id"] == b["id"], "http and https copies share an id")
    t0 = dt.datetime(2026, 9, 25, 12, tzinfo=UTC)
    m1 = make_item(src0, "article", "China and the US agree on strategic stability", "http://en.people.cn/n3/2026/0926/c90000-2.html", t0, "url", "", [], c)
    m2 = make_item(src0, "article", "China and the US agree on strategic stability", "http://english.people.com.cn/n3/2026/0926/c90000-2.html", t0, "url", "", [], c)
    arch = {}
    ok(merge(arch, [m1, m2], t0) == 1, "a mirror-host copy of the same story is dropped")
    ixs = parse_index(CD_INDEX, "https://www.chinadaily.com.cn/opinion", ".", _U_CD)
    ok(len(ixs) == 2 and ixs[1]["url"].startswith("https://www.chinadaily.com.cn/a/202609/22/"), f"url_match index {ixs}")

    # the full pipeline, offline
    tmp = Path(tempfile.mkdtemp(prefix="dalaba_selftest_"))
    try:
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        cfg["contact"] = "selftest@example.invalid"
        cfg["sources"] = [
            {"id": "t-cgtn", "outlet": "CGTN", "type": "rss", "url": "https://www.cgtn.com/rss/world.xml"},
            {"id": "t-gt", "outlet": "Global Times", "type": "rss", "url": "https://www.globaltimes.cn/rss/atom.xml"},
            {"id": "t-xh", "outlet": "Xinhua", "type": "rss", "url": "https://english.news.cn/rss/rdf.xml"},
            {"id": "t-cd", "outlet": "China Daily", "type": "rss", "url": "https://www.chinadaily.com.cn/rss/broken.xml"},
            {"id": "t-pd", "outlet": "People's Daily", "type": "rss", "lang": "zh", "url": "http://www.people.com.cn/rss/zh.xml"},
            {"id": "t-mfa", "outlet": "PRC Foreign Ministry", "type": "html-index", "url": "https://www.fmprc.gov.cn/eng/xw/fyrbt/lxjzh/",
             "mode": "presser", "match": "regular press conference", "follow_max": 12},
            {"id": "t-gtix", "outlet": "Global Times", "type": "html-index", "url": "https://www.globaltimes.cn/opinion/",
             "url_match": _U_GT},
            {"id": "t-cdix", "outlet": "China Daily", "type": "html-index", "url": "https://www.chinadaily.com.cn/opinion",
             "url_match": _U_CD},
            {"id": "t-redir", "outlet": "CGTN", "type": "rss", "url": "https://www.cgtn.com/rss/redirect.xml"},
            {"id": "t-weibo", "outlet": "Weibo", "type": "rss", "url": "https://weibo.com/rss"},
        ]
        cfg["youtube"] = [{"id": "t-yt", "outlet": "CGTN", "handle": "@CGTN"},
                          {"id": "t-yt-bad", "outlet": "Someone", "handle": "@somecreator"}]
        (tmp / CONFIG).write_text(json.dumps(cfg), encoding="utf-8")
        fx = Fetcher(cfg["contact"], offline=_offline_web())
        now = dt.datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
        ns = argparse.Namespace(out=str(tmp), follow=True, follow_max=25, youtube_key="TESTKEY", days=14,
                                no_pack=False, keep_all=False, only="")
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rows = cmd_pull(ns, fx=fx, now=now)
        st = {r["source"]: r for r in rows}
        ok(st["t-cgtn"]["status"] == "ok" and st["t-cgtn"]["n"] == 3 and st["t-cgtn"]["ai"] == 2, f"cgtn row {st['t-cgtn']}")
        ok(st["t-redir"]["status"] == "refused" and "weibo.com" in st["t-redir"]["reason"], "redirect to Weibo refused")
        ok("t-weibo" not in st, "a Weibo source never runs")
        ok("t-yt-bad" not in st, "a non-state YouTube handle never runs")
        ok(st["t-yt"]["n"] == 2 and st["t-yt"]["ai"] == 1, f"youtube row {st['t-yt']}")
        ok(st["t-mfa"]["n"] == 2 and st["t-mfa"]["ai"] == 1, f"mfa row {st['t-mfa']}")
        ok(any("robots.txt" in x["reason"] for x in st["t-cd"]["refused"]), "robots.txt respected on follow")
        ok(st["t-gtix"]["n"] == 2 and st["t-gtix"]["ai"] == 1, f"gt index row {st['t-gtix']}")
        ok(any("robots.txt" in x["reason"] for x in st["t-cdix"]["refused"]), "power-price headlines are followed")
        recs = load_archive(tmp)
        boom = [r for r in recs.values() if r["title"].startswith("America's AI boom")][0]
        ok(boom.get("followed") and boom["excerpts"] and "DC" in boom["seams"] and "BUB" in boom["seams"], f"followed article {boom}")
        ok("UTM" not in boom["url"] and boom["day"] == "2026-09-24", "canonical URL and Beijing day")
        pres = [r for r in recs.values() if r["kind"] == "presser" and r["ai"]][0]
        ok(pres.get("speaker") == "Lin Jian" and set(pres["prc"]) >= {"EXPORT_CONTROLS", "PRC_OFFICIAL_PHRASING"}
           and "DC" in pres["seams"], f"presser coding {pres}")
        zh = [r for r in recs.values() if r["lang"] == "zh"][0]
        ok(zh["ai"] and "DC" in zh["seams"] and "BUB" in zh["seams"], f"Chinese item coding {zh}")
        gt = [r for r in recs.values() if r["outlet"] == "Global Times" and r["kind"] == "article" and r.get("date_q") == "page"]
        ok(len(gt) == 1 and gt[0]["day"] == "2026-09-25" and "DC" in gt[0]["seams"] and gt[0]["excerpts"], f"GT page date {gt}")
        gtm = [r for r in recs.values() if r.get("month") == "2026-09"]
        ok(len(gtm) == 1 and gtm[0]["date"] is None, "unread month-only item keeps its month")
        with contextlib.redirect_stdout(buf):
            rows2 = cmd_pull(ns, fx=Fetcher(cfg["contact"], offline=_offline_web()), now=now + dt.timedelta(hours=6))
        ok(all(r.get("new", 0) == 0 for r in rows2 if r.get("status") == "ok"), "second pull adds nothing new")
        path, pack = build_pack(tmp, 14, now + dt.timedelta(hours=6))
        ok(pack["kind"] == KIND and pack["counts"]["ai"] == sum(1 for r in recs.values() if r.get("ai")
                                                               and r.get("day") and r["day"] >= "2026-09-12"), "pack counts")
        ok(all(i["ai"] for i in pack["items"]) and pack["volume"]["CGTN"]["2026-09-24"][0] >= 2, "pack items and volume")
        ok(any("weibo.com" in x["url"] for x in pack["refused"]), "pack lists refusals")
        ok(pack["counts"]["month_only"].get("Global Times") == 1, f"month_only {pack['counts']['month_only']}")
        ok(json.loads(path.read_text(encoding="utf-8"))["tool"] == TOOL, "pack file round-trips")
        late = now + dt.timedelta(hours=30)
        with (tmp / PULLS).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"source": "t-cgtn", "outlet": "CGTN", "type": "rss", "time": iso(late), "status": "ok",
                                 "http": 200, "n": 3, "oldest": iso(late - dt.timedelta(hours=4)), "refused": []}) + "\n")
        _, pack2 = build_pack(tmp, 14, late)
        g = [s for s in pack2["sources"] if s["id"] == "t-cgtn"][0]["gaps"]
        ok(len(g) == 1 and g[0][0] == iso(now + dt.timedelta(hours=6)), f"gap detection {g}")
        if args.write_demo:
            pack["synthetic"] = True
            pack["notes"].insert(0, "SYNTHETIC: built from DALABA's selftest samples. Not real state-media output.")
            Path(args.write_demo).write_text(json.dumps(pack, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"demo pack (synthetic): {args.write_demo}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if fails:
        print(f"selftest: {len(fails)} of {n[0]} checks FAILED")
        for f in fails:
            print("  - " + f)
        sys.exit(1)
    print(f"selftest: all {n[0]} checks passed (guard, redirects, robots, dates, codebook {CODEBOOK['version']}, "
          f"RSS 2.0, RSS 1.0, Atom, broken XML, GB18030, press conferences, YouTube, archive, pack)")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="dalaba", description="DALABA: GHOST LOOM's official-voice connector for "
                                 "PRC state media and government publishers. It never reads Chinese social media "
                                 "or messaging platforms.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def out(p):
        p.add_argument("--out", default="./statevoice", help="working folder: config, archive, packs")

    p = sub.add_parser("init", help="write the default source list")
    out(p)
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("check", help="test every enabled source")
    out(p)
    p.add_argument("--youtube-key", default=os.environ.get("DALABA_YT_KEY", ""))
    p = sub.add_parser("pull", help="fetch, code, archive and write the pack")
    out(p)
    p.add_argument("--follow", action="store_true", help="read AI-relevant article pages for excerpts")
    p.add_argument("--follow-max", type=int, default=25, help="pages to read per feed per pull")
    p.add_argument("--youtube-key", default=os.environ.get("DALABA_YT_KEY", ""))
    p.add_argument("--days", type=int, default=28, help="pack window in days")
    p.add_argument("--only", default="", help="comma-separated source ids")
    p.add_argument("--no-pack", action="store_true")
    p.add_argument("--keep-all", action="store_true", help="put every item in the pack, not only AI-relevant ones")
    p = sub.add_parser("pack", help="rebuild the pack from the archive")
    out(p)
    p.add_argument("--days", type=int, default=28)
    p.add_argument("--keep-all", action="store_true")
    p = sub.add_parser("recode", help="re-run coding over the archive")
    out(p)
    p = sub.add_parser("selftest", help="offline checks")
    p.add_argument("--write-demo", metavar="PATH", help="also write a SYNTHETIC demo pack for the dashboard loader")
    args = ap.parse_args(argv)
    {"init": cmd_init, "check": cmd_check, "pull": cmd_pull, "pack": cmd_pack, "recode": cmd_recode,
     "selftest": cmd_selftest}[args.cmd](args)


if __name__ == "__main__":
    main()
