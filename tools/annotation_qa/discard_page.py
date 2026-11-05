"""The /discard review page: one card per candidate image (occlusion.py), answered Discard / Keep.

Each card shows the road strip with its labels in their class colours and the covered stretches
(canopy / deep shadow on both sides of a line) as magenta dots, the covered share, the image's
lat/lon with Maps and Street View, and Open in Scalabel into the candidates' own project.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import quote

import cv2
import numpy as np

from annotation_qa import occlusion as oc
from annotation_qa.data import REPO_ROOT
from annotation_qa.geo import default_index as geo_index

sys.path.insert(0, str(REPO_ROOT / "scalabel" / "tools"))
from draw_poly2d import COLORS, DEFAULT_COLOR, poly_to_points  # noqa: E402

SCALABEL = "http://localhost:8686"
TASK_SIZE = 50  # scalabel_project.TASK_SIZE
MAX_SIDE = 1100  # px of the rendered card image


def _key(c: dict) -> str:
    return f"{c['stem']}|{c['name']}"


def _project(out: Path) -> str | None:
    try:
        return json.loads((Path(out) / "discard" / "project.json").read_text())["project"]
    except (OSError, ValueError, KeyError):
        return None


def items(out: Path) -> dict:
    data = oc.read_candidates(out)
    answers = oc.read_answers(out)
    project = _project(out)
    cards = []
    for rank, c in enumerate(data.get("candidates") or []):
        k = _key(c)
        link = None
        if project:
            link = (f"{SCALABEL}/label?project_name={quote(project, safe='')}"
                    f"&task_index={rank // TASK_SIZE}&item={rank % TASK_SIZE + 1}")
        cards.append({"rank": rank, "key": k, "stem": c["stem"], "name": c["name"],
                      "image": c["name"].rsplit("/", 1)[-1],
                      "line_cover": c["line_cover"], "strip_cover": c["strip_cover"],
                      "link": link, "geo": geo_index().lookup(c["name"]),
                      "img": f"/discard/img?i={rank}",
                      "answer": (answers.get(k) or {}).get("value", ""),
                      "comment": (answers.get(k) or {}).get("comment", "")})
    return {"project": project, "min_cover": data.get("min_cover"), "measured": data.get("measured"),
            "cards": cards}


def render(out: Path, rank: int) -> Path | None:
    """JPEG of candidate ``rank``: the road strip, labels in class colours, covered dots."""
    cands = oc.read_candidates(out).get("candidates") or []
    if not 0 <= rank < len(cands):
        return None
    c = cands[rank]
    h = hashlib.sha1(f"{_key(c)}|{len(c['marks'])}|v1".encode()).hexdigest()[:16]
    path = Path(out) / "discard" / "img" / f"{h}.jpg"
    if path.exists():
        return path
    img = cv2.imread(str(REPO_ROOT / "local-data" / c["name"]))
    if img is None:
        return None
    frame = None
    work = Path(out) / "work" / f"{c['stem']}.json"
    for f in json.loads(work.read_text())["frames"]:
        if f["name"] == c["name"]:
            frame = f
            break
    lw = max(2, int(round(max(img.shape[:2]) / 900)))
    for lab in (frame or {}).get("labels") or []:
        col = COLORS.get(lab.get("category"), DEFAULT_COLOR)
        for poly in lab.get("poly2d") or []:
            P = np.round(poly_to_points(poly)).astype(np.int32)
            if len(P) >= 2:
                cv2.polylines(img, [P], bool(poly.get("closed")), col, lw, cv2.LINE_AA)
    for x, y in c["marks"]:
        cv2.circle(img, (int(x), int(y)), lw + 2, (255, 0, 255), -1, cv2.LINE_AA)
    ys, xs = np.where(img.max(axis=2) > 8)
    if len(xs):
        pad = 20
        img = img[max(ys.min() - pad, 0):ys.max() + pad, max(xs.min() - pad, 0):xs.max() + pad]
    hh, ww = img.shape[:2]
    s = min(1.0, MAX_SIDE / max(hh, ww))
    if s < 1:
        img = cv2.resize(img, (int(ww * s), int(hh * s)), interpolation=cv2.INTER_AREA)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), img, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return path


HTML = """<!doctype html><html><head><meta charset="utf-8"><title>Discard review</title>
<style>
body{font:14px/1.4 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#f4f4f4;color:#222}
header{position:sticky;top:0;z-index:5;background:#fff;border-bottom:1px solid #ddd;padding:8px 14px;display:flex;gap:14px;align-items:center;flex-wrap:wrap}
.card{background:#fff;margin:12px;padding:10px 12px;border-radius:6px;border-left:6px solid #ccc;display:flex;gap:14px;align-items:flex-start}
.card.discard{border-left-color:#c62828}.card.keep{border-left-color:#2e7d32}.card.cur{outline:3px solid #1e88e5}
.card img{max-width:70vw;max-height:78vh;background:#000;cursor:zoom-in;border-radius:3px}
.card img.full{max-width:none;max-height:none;cursor:zoom-out}
.meta{min-width:300px;max-width:420px}
.num{display:inline-block;background:#333;color:#fff;border-radius:10px;padding:0 8px;margin-right:6px}
.big button{font-size:16px;padding:8px 16px;margin:8px 6px 0 0;border:0;border-radius:5px;color:#fff;cursor:pointer}
.d{background:#c62828}.k{background:#2e7d32}
.geo .ll{user-select:all;font-family:ui-monospace,Menlo,monospace;background:#f3f3f3;padding:0 3px;border-radius:3px}
textarea{width:100%;margin-top:8px;font:inherit}
.muted{color:#777}.bad{color:#c62828;font-weight:600}.ok{color:#2e7d32;font-weight:600}
</style></head><body>
<header><b>Discard review: trees / deep shadow</b><span id="count"></span>
<span class="muted">Y = discard · N = keep · J / K = next / previous · click an image to zoom ·
magenta dots = lines under canopy or shadow</span></header>
<div id="cards"></div>
<script>
const $=s=>document.querySelector(s);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let D=null,cur=0;
async function post(b){const r=await fetch('/discard/answer',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});
 if(!r.ok)throw new Error(await r.text());return r.json();}
function count(){const c=D.cards,a=c.filter(x=>x.answer).length,d=c.filter(x=>x.answer==='discard').length;
 $('#count').innerHTML=`answered <b>${a} / ${c.length}</b> · discard <span class="bad">${d}</span> · keep <span class="ok">${a-d}</span>`+(a===c.length?' <b class="ok">All done - thank you!</b>':'');}
function geo(g){return g?` · <span class="geo">&#128205; <span class="ll">${g.lat}, ${g.lon}</span> · <a href="${esc(g.maps)}" target="_blank">Maps</a> · <a href="${esc(g.streetview)}" target="_blank">Street View</a></span>`:'';}
function card(c,i){return `<div class="card ${esc(c.answer)}" id="c${i}"><img loading="lazy" src="${esc(c.img)}" onclick="this.classList.toggle('full')">
<div class="meta"><div><span class="num">${i+1}</span><b>${esc(c.image)}</b></div>
<div class="muted">${esc(c.stem)}</div>
<div><b>${Math.round(c.line_cover*100)}%</b> of the labelled lines under canopy / shadow (road strip ${Math.round(c.strip_cover*100)}%)</div>
<div>${c.link?`<a href="${esc(c.link)}" target="_blank">Open in Scalabel</a>`:'<span class="muted">no project</span>'}${geo(c.geo)}</div>
<div class="big"><button class="d" onclick="answer(${i},'discard')">Discard (Y)</button><button class="k" onclick="answer(${i},'keep')">Keep (N)</button></div>
<textarea rows="2" placeholder="Comment (optional)" onblur="comment(${i},this.value)">${esc(c.comment)}</textarea></div></div>`;}
async function answer(i,v){const c=D.cards[i];try{await post({key:c.key,value:v});}catch(e){alert('not saved: '+e.message);return;}
 c.answer=v;$('#c'+i).className='card '+v;count();go(i+1);}
async function comment(i,t){const c=D.cards[i];if((c.comment||'')===t)return;try{await post({key:c.key,comment:t});c.comment=t;}catch(e){alert('comment not saved');}}
function go(i){if(!D.cards.length)return;cur=Math.max(0,Math.min(D.cards.length-1,i));
 document.querySelectorAll('.card').forEach(e=>e.classList.remove('cur'));const el=$('#c'+cur);if(!el)return;
 el.classList.add('cur');el.scrollIntoView({block:'start',behavior:'smooth'});window.scrollBy(0,-60);}
document.addEventListener('keydown',e=>{if(e.target.tagName==='TEXTAREA')return;const k=e.key.toLowerCase();
 if(k==='y')answer(cur,'discard');else if(k==='n')answer(cur,'keep');else if(k==='j')go(cur+1);else if(k==='k')go(cur-1);});
(async()=>{D=await (await fetch('/discard/items')).json();
 if(!D.cards.length){$('#cards').innerHTML='<p style="margin:16px">No candidates: run <code>python3 -m annotation_qa occlusion --project NAME</code></p>';return;}
 $('#cards').innerHTML=D.cards.map(card).join('');count();go(D.cards.findIndex(c=>!c.answer));})();
</script></body></html>"""
