"""Вся логика игры и рейтинга. Очки считает ТОЛЬКО сервер."""
import hashlib
import hmac
import json
import os
import random
import re
import secrets
import sqlite3
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl

from words import WORDS

W = [l.split("=", 1) for l in WORDS.strip().split("\n")]
MEAN = {w: m for w, m in W}
KEYS = [w for w, _ in W]
PRON = {"берЕт": "бэрЭт"}      # слова, которые голос читает неправильно
PTS = [1, 2, 3, 5]             # очки за слово на уровнях 1-4
CAP = 300                      # максимум очков в день
TTL = 2400                     # сессия живёт 40 минут

_path = os.getenv("DB_PATH", "data/rating.db")
os.makedirs(os.path.dirname(_path) or ".", exist_ok=True)
DB = sqlite3.connect(_path, check_same_thread=False)
DB.executescript(
    "create table if not exists users(id integer primary key, name text, points integer default 0);"
    "create table if not exists daily(uid integer, day text, points integer, primary key(uid, day));"
)
SESS, RATE = {}, {}


def disp(w):
    return "".join("ё" if c == "Ё" else c.lower() + "\u0301" if c.isupper() else " " if c == "_" else c for c in w)


def plain(w):
    return w.lower().replace("_", " ")


def day():
    return datetime.now(timezone(timedelta(hours=3))).strftime("%Y-%m-%d")


def verify(init, token):
    """Проверка подписи Telegram (initData). Подделать без токена бота нельзя."""
    try:
        data = dict(parse_qsl(init, keep_blank_values=True))
        h = data.pop("hash")
        s = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
        key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(hmac.new(key, s.encode(), hashlib.sha256).hexdigest(), h):
            return None
        if time.time() - int(data["auth_date"]) > 86400:
            return None
        return json.loads(data["user"])
    except Exception:
        return None


def today(uid):
    r = DB.execute("select points from daily where uid=? and day=?", (uid, day())).fetchone()
    return r[0] if r else 0


def me(u, b=None):
    name = (u.get("first_name") or "Аноним")[:20]
    DB.execute("insert into users(id,name) values(?,?) on conflict(id) do update set name=excluded.name", (u["id"], name))
    DB.commit()
    pts = DB.execute("select points from users where id=?", (u["id"],)).fetchone()[0]
    rank = DB.execute("select count(*)+1 from users where points>?", (pts,)).fetchone()[0] if pts else None
    return {"name": name, "points": pts, "rank": rank, "today": today(u["id"]), "cap": CAP}


def dictionary(u, b=None):
    return {"words": [[disp(w), MEAN[w], disp(PRON.get(w, w))] for w in KEYS]}


def start(u, b):
    lv, n = b.get("level"), b.get("n")
    if lv not in (0, 1, 2, 3) or n not in (10, 25, 50):
        return {"error": "bad"}
    now = time.time()
    for k in [k for k, s in SESS.items() if s["uid"] == u["id"] or now - s["t0"] > TTL]:
        del SESS[k]
    words = random.sample(KEYS, min(n, len(KEYS)))
    sid = secrets.token_urlsafe(12)
    SESS[sid] = dict(uid=u["id"], level=lv, words=words, i=0, open=False, t0=now, ok=0, gained=0, wrong=[])
    return {"sid": sid, "n": len(words)}


def _sess(u, b):
    s = SESS.get(b.get("sid"))
    if not s or s["uid"] != u["id"] or time.time() - s["t0"] > TTL:
        return None
    return s


def question(u, b):
    s = _sess(u, b)
    if not s:
        return {"error": "expired"}
    if not s["open"]:
        w = s["words"][s["i"]]
        pw, lv = plain(w), s["level"]
        have = sorted(set(pw.replace(" ", "")))
        letters = have + random.sample([c for c in "аеиоуыснтрлкв" if c not in have], 3)
        random.shuffle(letters)
        q = {"i": s["i"], "n": len(s["words"]), "level": lv, "letters": letters, "space": " " in pw}
        if lv < 2:
            q["text"] = disp(w)
        if lv != 1:
            q["meaning"] = MEAN[w]
        if lv == 2:
            q["speak"] = disp(PRON.get(w, w))
        s["q"], s["open"], s["served"] = q, True, time.time()
    return s["q"]


def answer(u, b):
    s = _sess(u, b)
    if not s:
        return {"error": "expired"}
    dq = RATE.setdefault(u["id"], deque(maxlen=40))
    now = time.time()
    if len(dq) == 40 and now - dq[0] < 60:
        return {"error": "rate"}
    dq.append(now)
    if not s["open"]:
        return {"error": "order"}
    s["open"] = False
    w = s["words"][s["i"]]
    pw = plain(w)
    a = re.sub(r"\s+", " ", str(b.get("answer", ""))).strip().lower()[:40]
    fast = now - s["served"] < 0.8 + 0.2 * len(pw)   # человек так быстро не соберёт слово
    ok = a == pw and not fast
    gain = 0
    if ok:
        gain = min(PTS[s["level"]], max(0, CAP - today(u["id"])))
        s["ok"] += 1
        s["gained"] += gain
        if gain:
            DB.execute("update users set points=points+? where id=?", (gain, u["id"]))
            DB.execute("insert into daily values(?,?,?) on conflict(uid,day) do update set points=points+?",
                       (u["id"], day(), gain, gain))
            DB.commit()
    else:
        s["wrong"].append({"text": disp(w), "meaning": MEAN[w]})
    s["i"] += 1
    r = {"correct": ok, "pts": gain, "right": disp(w), "fast": fast and a == pw}
    if s["i"] >= len(s["words"]):
        r.update(done=True, n=len(s["words"]), ok=s["ok"], gained=s["gained"], wrong=s["wrong"],
                 capped=s["ok"] > 0 and s["gained"] < s["ok"] * PTS[s["level"]])
        del SESS[b["sid"]]
    return r


def top(u, b):
    if b.get("tab") == "day":
        q = ("select u.id,u.name,d.points from daily d join users u on u.id=d.uid "
             "where d.day=? and d.points>0 order by d.points desc limit 20")
        rows = DB.execute(q, (day(),)).fetchall()
    else:
        rows = DB.execute("select id,name,points from users where points>0 order by points desc limit 20").fetchall()
    return {"rows": [{"name": n, "points": p, "me": i == u["id"]} for i, n, p in rows]}
