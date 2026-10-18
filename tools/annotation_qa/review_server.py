"""Local review server (View A): triage detected issues as real / false alarm."""

from __future__ import annotations

import hashlib
import html as _html
import json
import logging
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from annotation_qa.data import ORIGINALS_DIR, REPO_ROOT
from annotation_qa.issues import (CALIBRATION_N, Issue, calibration_sample,  # noqa: F401
                                  calibration_status, load_issues_tolerant, read_marks,
                                  write_marks)
from annotation_qa import comments as cm, locator, zoom
from annotation_qa import progress as pg
from annotation_qa import reverts as rv
from annotation_qa.rounds import merged_tags, round_files
from annotation_qa.exclude import read_excluded
from annotation_qa.viewb import build_rows, render_crop, select_rows, viewb_html

log = logging.getLogger(__name__)
SCALABEL = "http://localhost:8686"
LOCAL_HOSTS = {"127.0.0.1", "localhost"}


class ReviewState:
    def __init__(self, out: Path, repo_root: Path, originals_dir: Path):
        self.out, self.repo_root, self.originals = Path(out), Path(repo_root), Path(originals_dir)
        self.lock = threading.Lock()
        self._frame_idx: dict[str, dict[str, int]] = {}
        self._task_size: dict[str, tuple[int, bool]] = {}

    def issues(self, t: str | None = None) -> dict[str, list[Issue]]:
        res = {}
        for p in sorted(self.out.glob("issues_*.jsonl")):
            name = p.stem[len("issues_"):]
            if t is None or name == t:
                res[name] = load_issues_tolerant(p)
        return res

    def types(self) -> dict[str, int]:
        return {k: len(v) for k, v in self.issues().items() if v}

    def _frames(self, stem: str) -> dict[str, int]:
        if stem not in self._frame_idx:
            idx: dict[str, int] = {}
            for p in (self.out / "work" / f"{stem}.json", self.originals / f"{stem}.json"):
                if p.exists():
                    try:
                        frames = json.loads(p.read_text()).get("frames", [])
                        idx = {f.get("name"): i for i, f in reversed(list(enumerate(frames)))}
                    except Exception as e:  # noqa: BLE001
                        log.warning("cannot read export %s: %s", p, e)
                        continue
                    break
            self._frame_idx[stem] = idx
        return self._frame_idx[stem]

    def _project(self, project: str) -> tuple[int, bool]:
        if project not in self._task_size:
            d = self.repo_root / "local-data/scalabel/projects" / project
            size = 0
            try:
                size = int(json.loads((d / "project.json").read_text())["config"]["taskSize"])
            except Exception:  # noqa: BLE001
                pass
            self._task_size[project] = (size, d.is_dir())
        return self._task_size[project]

    def card(self, issue: Issue, type_counts: dict[tuple[str, str], int],
             comments: dict | None = None) -> dict:
        project = issue.export_stem.split("_export_")[0]
        size, found = self._project(project)
        i = self._frames(issue.export_stem).get(issue.frame_name)
        if i is not None and size > 0:
            task, frame_no = i // size, i % size + 1
        else:
            task, frame_no = 0, None
        d = asdict(issue)
        d.update(
            key=issue.key,
            others=type_counts.get((issue.export_stem, issue.frame_name), 1) - 1,
            frame_no=frame_no,
            project=project,
            project_found=found,
            link=f"{SCALABEL}/label?project_name={project}&task_index={task}" + (
                f"&item={frame_no}&label={quote(str(issue.label_id), safe='')}"
                f"&x={int(round(issue.anchor[0]))}&y={int(round(issue.anchor[1]))}"
                if frame_no else ""),
            frame_link=f"{SCALABEL}/label?project_name={project}&task_index={task}" + (
                f"&item={frame_no}" if frame_no else ""),
            locator="/locator?key=" + quote(issue.key, safe=""),
            comment=(comments or {}).get(issue.key, {}).get("text", ""),
            n=0, group_size=1, other_types=0, overview="",
            crop=f"/crops/{issue.issue_type}/"
                 f"{hashlib.sha1(issue.key.encode()).hexdigest()[:12]}.jpg",
        )
        return d

    @staticmethod
    def grouped(mine: list[Issue]) -> list[list[Issue]]:
        """Issues of one type grouped by frame: most issues first, then highest confidence.

        Inside a group: confidence desc. A card's number is its 1-based place in its group;
        the frame overview numbers its circles with the same list."""
        groups: dict[tuple[str, str], list[Issue]] = {}
        for i in mine:
            groups.setdefault((i.export_stem, i.frame_name), []).append(i)
        for g in groups.values():
            g.sort(key=lambda i: (-i.confidence, i.key))
        return sorted(groups.values(), key=lambda g: (
            -len(g), -g[0].confidence, g[0].export_stem, g[0].frame_name))

    def frame_issues(self, t: str, stem: str, frame: str) -> list[Issue]:
        for g in self.grouped(self.issues(t).get(t, [])):
            if (g[0].export_stem, g[0].frame_name) == (stem, frame):
                return g
        return []

    def find_issue(self, key: str) -> Issue | None:
        t = key.rsplit("|", 1)[-1]
        return next((i for i in self.issues(t).get(t, []) if i.key == key), None)

    def cards(self, t: str) -> list[dict]:
        allv = [i for v in self.issues().values() for i in v]
        counts: dict[tuple[str, str], int] = {}
        for i in allv:
            counts[(i.export_stem, i.frame_name)] = counts.get((i.export_stem, i.frame_name), 0) + 1
        res = []
        comments = cm.read_comments(self.out)
        excluded = read_excluded(self.out)
        for g in self.grouped(self.issues(t).get(t, [])):
            ov = ("/locator/frame?" + "&".join(f"{k}={quote(v, safe='')}" for k, v in (
                ("type", t), ("stem", g[0].export_stem), ("frame", g[0].frame_name))))
            for n, i in enumerate(g, 1):
                d = self.card(i, counts, comments)
                d["auto_fix_rejected"] = i.key in excluded
                d.update(n=n, group_size=len(g), overview=ov,
                         other_types=counts[(i.export_stem, i.frame_name)] - len(g))
                res.append(d)
        return res

    def calibration(self, t: str) -> list[dict]:
        """The fixed calibration sample of type ``t`` (the one `fix` is gated on)."""
        comments = cm.read_comments(self.out)
        return [dict(self.card(i, {}, comments), n=n) for n, i in enumerate(
            calibration_sample(self.out, t, self.issues(t).get(t, [])), 1)]

    def calibration_status(self) -> dict[str, dict]:
        """type -> answered k/n, real, precision and PASS / FAIL / NOT CALIBRATED."""
        marks = read_marks(self.out / "marks.json")
        return {t: calibration_status(self.out, t, v, marks) for t, v in self.issues().items() if v}

    def save(self, new: dict[str, str]) -> int:
        with self.lock:
            marks = read_marks(self.out / "marks.json")
            marks.update(new)
            write_marks(self.out / "marks.json", marks)
        return len(new)

    def set_comment(self, key: str, text: str) -> int:
        """Save (or, when blank, delete) the comment of an issue; returns the comment count."""
        if len(text) > cm.MAX_LEN:
            raise ValueError(f"comment longer than {cm.MAX_LEN} characters")
        issue = self.find_issue(key)
        if issue is None:
            raise ValueError("unknown issue key")
        with self.lock:
            comments = cm.read_comments(self.out)
            if text.strip():
                comments[key] = cm.entry(issue, text)
            else:
                comments.pop(key, None)
            cm.write_comments(self.out, comments)
            return len(comments)

    def set_revert(self, tag: str, frame: str, label: str, revert: bool) -> bool:
        """Record (or undo) a View B revert; ValueError unless the change exists in that round."""
        rnd = next((r for r in round_files(self.out) if r["tag"] == tag), None)
        if rnd is None:
            raise ValueError(f"unknown round {tag!r}")
        try:
            changes = json.loads(rnd["changes"].read_text())
        except (OSError, ValueError) as e:
            raise ValueError(f"cannot read changes of {tag}: {e}")
        if frame not in changes:
            raise ValueError(f"unknown frame {frame!r} in round {tag}")
        if label not in changes[frame]:
            raise ValueError(f"unknown label {label!r} in frame {frame!r} of round {tag}")
        if (rnd["stem"], tag) in merged_tags(self.out):
            raise ValueError(f"round {tag} is already merged; a revert only applies at merge time")
        with self.lock:
            return rv.set_revert(self.out, tag, frame, label, revert)

    def _round_frames(self, tag: str) -> tuple[dict, bool]:
        """(round record, merged?) with the set of frame names in ``frames``; ValueError if unknown."""
        rnd = next((r for r in round_files(self.out) if r["tag"] == tag), None)
        if rnd is None:
            raise ValueError(f"unknown round {tag!r}")
        names: set[str] = set()
        for k in ("changes", "items"):
            try:
                d = json.loads(rnd[k].read_text())
            except (OSError, ValueError) as e:
                raise ValueError(f"cannot read {k} of {tag}: {e}")
            names |= set(d) if k == "changes" else {f["name"] for f in d.get("frames", [])}
        return {"frames": names, **rnd}, (rnd["stem"], tag) in merged_tags(self.out)

    def set_progress(self, tag: str, frame: str, reviewed: bool) -> bool:
        rnd, merged = self._round_frames(tag)
        if frame not in rnd["frames"]:
            raise ValueError(f"unknown frame {frame!r} in round {tag}")
        if merged:
            raise ValueError(f"round {tag} is already merged; all its frames count as reviewed")
        with self.lock:
            return pg.set_reviewed(self.out, tag, frame, reviewed)

    def progress(self, tag: str) -> dict:
        """{frame: {reviewed, time?}} for a round; every frame of a merged round is reviewed."""
        rnd, merged = self._round_frames(tag)
        if merged:
            return {f: {"reviewed": True, "merged": True} for f in sorted(rnd["frames"])}
        return pg.read_progress(self.out).get(tag) or {}

    def comments_html(self) -> str:
        comments = cm.read_comments(self.out)
        by_type: dict[str, list[tuple[str, dict]]] = {}
        for k, c in comments.items():
            by_type.setdefault(c.get("issue_type", "?"), []).append((k, c))
        e = _html.escape
        parts = []
        for t in sorted(by_type):
            parts.append(f"<h2>{e(t)} ({len(by_type[t])})</h2>")
            for k, c in sorted(by_type[t], key=lambda kc: (
                    kc[1].get("export_stem", ""), kc[1].get("frame_name", ""),
                    str(kc[1].get("label_id", "")))):
                issue = self.find_issue(k)
                card = self.card(issue, {}) if issue else None
                img = (f'<img src="{e(card["crop"], quote=True)}">' if card else "")
                link = (f' &middot; <a href="{e(card["link"], quote=True)}" target="_blank">'
                        f'Open in Scalabel</a>' if card else " (issue no longer detected)")
                parts.append(
                    f'<div class="card col">{img}<div><b>{e(str(c.get("export_stem", "")))}</b>'
                    f' &middot; frame {e(str(c.get("frame_name", "")))}'
                    f' &middot; label {e(str(c.get("label_id", "")))}{link}'
                    f'<div style="white-space:pre-wrap;margin-top:6px">{e(str(c.get("text", "")))}'
                    f'</div><small>{e(str(c.get("updated", "")))}</small></div></div>')
        body = "".join(parts) or "<p style='margin:16px'>No comments yet.</p>"
        return (f'<!doctype html><html><head><meta charset="utf-8"><title>Comments</title>'
                f'<style>{CSS}</style></head><body><header><a href="/">&larr; back</a> &nbsp; '
                f'<b>Comments ({len(comments)})</b></header>{body}</body></html>')

    def bulk(self, t: str, min_conf: float, dry_run: bool = False) -> int:
        """Mark every issue of ``t`` with confidence >= ``min_conf`` real (false marks stay).

        ``dry_run`` only counts them, so the page can ask for confirmation first."""
        with self.lock:
            marks = read_marks(self.out / "marks.json")
            n = 0
            for i in self.issues(t).get(t, []):
                if i.confidence >= min_conf and marks.get(i.key) != "false":
                    marks[i.key] = "real"
                    n += 1
            if not dry_run:
                write_marks(self.out / "marks.json", marks)
        return n


def _handler(state: ReviewState):
    class H(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quiet
            pass

        def _send(self, body: bytes, ctype="application/json", code=200):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(json.dumps(obj).encode(), code=code)

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            t = (q.get("type") or [""])[0]
            if u.path == "/":
                self._send(index_html(state.types(), len(cm.read_comments(state.out))).encode(), "text/html; charset=utf-8")
            elif u.path == "/marks":
                self._json(read_marks(state.out / "marks.json"))
            elif u.path == "/comments":
                self._json(cm.read_comments(state.out))
            elif u.path == "/comments/view":
                self._send(state.comments_html().encode(), "text/html; charset=utf-8")
            elif u.path == "/issues":
                self._json(state.cards(t))
            elif u.path == "/calibrate/items":
                self._json(state.calibration(t))
            elif u.path == "/calibration":
                self._json(state.calibration_status())
            elif u.path == "/calibrate":
                self._send(calibrate_html(t, len(cm.read_comments(state.out))).encode(), "text/html; charset=utf-8")
            elif u.path == "/viewb":
                self._send(viewb_html(CSS).encode(), "text/html; charset=utf-8")
            elif u.path == "/viewb/rows":
                self._json(build_rows(state.out, state.repo_root))
            elif u.path == "/viewb/view":
                self._json(select_rows(build_rows(state.out, state.repo_root),
                                       (q.get("round") or [""])[0], (q.get("show") or ["needs"])[0],
                                       pg.read_progress(state.out)))
            elif u.path == "/viewb/progress":
                try:
                    self._json(state.progress((q.get("round") or [""])[0]))
                except ValueError as e:
                    self._json({"error": str(e)}, 400)
            elif u.path == "/viewb/reverts":
                tag = (q.get("round") or [""])[0]
                self._json(rv.round_reverts(state.out, tag) if tag else rv.read_reverts(state.out))
            elif u.path == "/viewb/crop":
                p = render_crop(state.out, state.repo_root, *(
                    (q.get(k) or [""])[0] for k in ("stem", "frame", "label", "round", "side")))
                if p is not None and p.is_file():
                    self._send(p.read_bytes(), "image/jpeg")
                else:
                    self._send(b"not found", "text/plain", 404)
            elif u.path == "/zoom":
                issue = state.find_issue((q.get("key") or [""])[0])
                if issue is None:
                    self._send(b"not found", "text/plain", 404)
                    return
                try:
                    p = zoom.render_zoom(state.out, state.repo_root, state.originals, issue,
                                         (q.get("raw") or [""])[0] == "1")
                except Exception as e:  # noqa: BLE001
                    log.warning("zoom render failed for %s: %s", issue.key, e)
                    p = None
                if p is not None and p.is_file():
                    self._send(p.read_bytes(), "image/jpeg")
                else:
                    self._send(b"not found", "text/plain", 404)
            elif u.path in ("/locator", "/locator/frame"):
                if u.path == "/locator":
                    issue = state.find_issue((q.get("key") or [""])[0])
                    grp, width = ([issue] if issue else []), 260
                    marks = [(i.anchor, None) for i in grp]
                else:
                    grp = state.frame_issues(t, (q.get("stem") or [""])[0],
                                             (q.get("frame") or [""])[0])
                    width = 700
                    marks = [(i.anchor, str(n)) for n, i in enumerate(grp, 1)]
                body = None
                if grp:
                    try:
                        body = locator.render(state.out, state.repo_root, state.originals,
                                              grp[0].export_stem, grp[0].frame_name, marks, width)
                    except Exception as e:  # noqa: BLE001
                        log.warning("locator failed: %s", e)
                if body:
                    self._send(body, "image/jpeg")
                else:
                    self._send(b"not found", "text/plain", 404)
            elif u.path.startswith("/crops/"):
                base = (state.out / "crops").resolve()
                p = (base / unquote(u.path[len("/crops/"):])).resolve()
                if base in p.parents and p.is_file():
                    self._send(p.read_bytes(), "image/jpeg")
                else:
                    self._send(b"not found", "text/plain", 404)
            else:
                self._send(b"not found", "text/plain", 404)

        def do_POST(self):
            host = (self.headers.get("Host") or "").strip().lower()
            host = host[1:].split("]")[0] if host.startswith("[") else host.rsplit(":", 1)[0]
            if host not in LOCAL_HOSTS:  # a page on another site must not post marks
                self._json({"error": "forbidden host"}, 403)
                return
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype != "application/json":
                self._json({"error": "Content-Type must be application/json"}, 415)
                return
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                if self.path == "/marks":
                    if not isinstance(body, dict) or any(
                            v not in ("real", "false") for v in body.values()):
                        raise ValueError("expected {key: 'real'|'false'}")
                    self._json({"saved": state.save(body)})
                elif self.path == "/comments":
                    if not isinstance(body, dict) or not isinstance(body.get("key"), str) \
                            or not isinstance(body.get("text"), str):
                        raise ValueError("expected {key: str, text: str}")
                    self._json({"saved": True, "count": state.set_comment(body["key"], body["text"])})
                elif self.path == "/viewb/revert":
                    if not isinstance(body, dict) or not all(
                            isinstance(body.get(k), str) for k in ("round", "frame", "label")) \
                            or not isinstance(body.get("revert"), bool):
                        raise ValueError("expected {round: str, frame: str, label: str, revert: bool}")
                    self._json({"reverted": state.set_revert(
                        body["round"], body["frame"], body["label"], body["revert"])})
                elif self.path == "/viewb/progress":
                    if not isinstance(body, dict) or not all(
                            isinstance(body.get(k), str) for k in ("round", "frame")) \
                            or not isinstance(body.get("reviewed"), bool):
                        raise ValueError("expected {round: str, frame: str, reviewed: bool}")
                    self._json({"reviewed": state.set_progress(
                        body["round"], body["frame"], body["reviewed"])})
                elif self.path == "/marks/bulk":
                    dry = bool(body.get("dry_run"))
                    n = state.bulk(body["issue_type"], float(body["min_confidence"]), dry)
                    self._json({"count": n} if dry else {"saved": n})
                else:
                    self._send(b"not found", "text/plain", 404)
            except (ValueError, KeyError, TypeError) as e:
                self._json({"error": str(e)}, 400)

    return H


def make_server(out: Path, port: int = 8790, repo_root: Path = REPO_ROOT,
                originals_dir: Path = ORIGINALS_DIR) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port),
                               _handler(ReviewState(out, repo_root, originals_dir)))


def serve(out: Path, port: int = 8790, page: str = "/") -> None:
    srv = make_server(out, port)
    print(f"review server: http://localhost:{srv.server_address[1]}{page}  (Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


CSS = """
body{font:14px system-ui,sans-serif;margin:0;background:#f4f4f6;color:#222}
header{background:#fff;padding:8px 16px;border-bottom:1px solid #ddd;position:sticky;top:0;z-index:2}
.tab{display:inline-block;padding:6px 12px;margin-right:4px;border-radius:6px;cursor:pointer;background:#e8e8ee}
.tab.on{background:#2b5fd9;color:#fff}
.bar{margin-top:8px}
.card{background:#fff;margin:12px 16px;padding:12px;border-radius:8px;display:flex;gap:16px;
 border-left:8px solid #ccc}
.card.real{border-color:#d33}.card.false{border-color:#2a9d4a}
.card img{max-width:640px;max-height:480px;object-fit:contain;background:#000}
.card.col{flex-direction:column;gap:8px}
.card.col img.crop{width:100%;max-width:1400px;max-height:none;height:auto;cursor:zoom-in}
.card.col .row{display:flex;gap:16px;align-items:flex-start}
.card.col img.loc{width:260px;max-width:260px;max-height:none;flex:none}
.ghead{margin:20px 16px 0;padding:10px 12px;background:#fff;border-radius:8px;border-top:4px solid #2b5fd9}
.ghead img{display:block;margin-top:8px;width:700px;max-width:100%;max-height:none}
.num{display:inline-block;min-width:22px;text-align:center;background:#d33;color:#fff;border-radius:11px;
 padding:1px 6px;font-weight:bold;margin-right:6px}
#lb{display:none;position:fixed;inset:0;background:rgba(0,0,0,.92);z-index:10;overflow:auto;color:#eee}
#lb .bar2{position:sticky;top:0;background:#111;padding:8px 16px;z-index:1}
#lb .bar2 button,#lb .bar2 a{margin-right:8px;font-size:13px;padding:6px 10px;background:#333;color:#fff;
 border-radius:6px;text-decoration:none;display:inline-block}
#lb img{display:block;margin:8px auto;max-width:96vw;max-height:calc(100vh - 90px);object-fit:contain;cursor:zoom-in}
#lb img.full{max-width:none;max-height:none;cursor:zoom-out}
.meta{max-width:520px}.meta div{margin:3px 0}
.badge{background:#f90;color:#fff;border-radius:4px;padding:1px 6px;font-size:12px}
button{padding:8px 14px;border:0;border-radius:6px;cursor:pointer;font-size:14px}
.r{background:#d33;color:#fff}.f{background:#2a9d4a;color:#fff}
.cmt{display:block;width:100%;max-width:520px;margin-top:8px;font:13px system-ui,sans-serif}
.sv{font-size:11px;color:#2a9d4a;margin-left:6px}
.big button{font-size:20px;padding:18px 28px;margin-right:10px}
"""

INDEX_JS = """
const TABS=%s;let cur=null,marks={},items=[],shown=0;const PAGE=100;
const $=s=>document.querySelector(s);
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function post(u,b){const r=await fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});
 let j={};try{j=await r.json();}catch(e){}
 if(!r.ok){alert('Not saved: '+(j.error||r.status));throw new Error('save failed');}return j;}
function calText(s){if(!s)return '';const p=s.precision==null?'-':s.precision.toFixed(2);
 return `${s.status} (answered ${s.answered}/${s.n}, real ${s.real}, precision ${p})`;}
async function calib(){const st=await (await fetch('/calibration')).json();
 document.querySelectorAll('.tab').forEach(e=>{const s=st[e.dataset.t];e.querySelector('.cal').textContent=s?' · '+s.status:'';});
 $('#cal').innerHTML=cur&&st[cur]?'Calibration of '+esc(cur)+': <b>'+esc(calText(st[cur]))+'</b> &mdash; fix needs PASS (precision &ge; 2/3 on &ge; '+st[cur].need+' answered) <a href="/calibrate?type='+encodeURIComponent(cur)+'">calibrate</a>':'';}
async function show(t){cur=t;document.querySelectorAll('.tab').forEach(e=>e.classList.toggle('on',e.dataset.t==t));
 marks=await (await fetch('/marks')).json();items=await (await fetch('/issues?type='+encodeURIComponent(t))).json();shown=0;
 $('#cards').innerHTML='';more();calib();}
function more(){const end=Math.min(items.length,shown+PAGE);let h='';
 for(let i=shown;i<end;i++)h+=card(items[i],i);shown=end;$('#cards').insertAdjacentHTML('beforeend',h);
 $('#more').style.display=shown<items.length?'':'none';$('#more').textContent='show more ('+(items.length-shown)+' left)';}
function ghead(it){return `<div class="ghead"><b>${esc(it.frame_name)}</b> &middot; ${esc(it.export_stem)}<br>
 ${it.group_size} issues in this frame (this type) &middot; ${it.other_types} other types
 &middot; <a href="${it.frame_link}" target="_blank">Open frame in Scalabel</a>
 <img loading="lazy" src="${it.overview}"></div>`;}
function card(it,i){const m=marks[it.key]||'';
 return (it.n==1?ghead(it):'')+`<div class="card col ${m}" id="c${i}"><img class="crop" loading="lazy" src="${it.crop}" onclick="lbOpen(${i})">
 <div class="row"><img class="loc" loading="lazy" src="${it.locator}"><div class="meta">
 <div><span class="num">${it.n}</span><b>${esc(it.export_stem)}</b></div><div>frame ${esc(it.frame_name)}${it.frame_no?' (frame '+it.frame_no+' in task)':''}</div>
 <div>label ${esc(it.label_id)} &middot; ${esc(it.category)} &middot; conf ${it.confidence.toFixed(2)}</div>
 <div>${esc(it.reason)}</div><div>this frame has ${it.others} other issues</div>
 ${it.hand_fix?'<div><span class="badge">draw by hand</span></div>':''}
 ${it.auto_fix_rejected?'<div><span class="badge" style="background:#888">auto-fix rejected</span></div>':''}
 <div><a href="${it.link}" target="_blank">Open in Scalabel</a>${it.project_found?'':' (project not found)'}</div>
 <div style="margin-top:8px"><button class="r" onclick="mark(${i},'real')">Real</button>
 <button class="f" onclick="mark(${i},'false')">False alarm</button></div>${cmtBox(it,i)}</div></div></div>`;}
async function mark(i,v){const it=items[i];try{await post('/marks',{[it.key]:v});}catch(e){return;}
 marks[it.key]=v;$('#c'+i).className='card col '+v;calib();}
async function bulk(){const c=parseFloat($('#conf').value);if(isNaN(c))return;
 let d;try{d=await post('/marks/bulk',{issue_type:cur,min_confidence:c,dry_run:true});}catch(e){return;}
 if(!d.count){$('#msg').textContent='nothing to mark';return;}
 if(!confirm('Mark '+d.count+' '+cur+' issues with confidence >= '+c+' as real?'))return;
 let r;try{r=await post('/marks/bulk',{issue_type:cur,min_confidence:c});}catch(e){return;}
 await show(cur);$('#msg').textContent='marked '+r.saved+' as real';}
document.addEventListener('DOMContentLoaded',()=>{if(TABS.length)show(TABS[0][0]);});
"""


LB_HTML = """<div id="lb"><div class="bar2"><b id="lbcap"></b> &nbsp;
<button onclick="lbRawToggle()" id="lbraw">raw only (R)</button><button onclick="lbOpenSc()">Open in Scalabel (O)</button>
<span id="lbans"></span><button onclick="lbClose()">close (Esc)</button>
<span id="lbhelp" style="font-size:12px"></span><br>
<textarea id="lbcm" class="cmt" rows="2" placeholder="Comment for Claude (optional)&hellip;"
 oninput="cmtInput(lbI,this.value)" onblur="if(lbI>=0)cmtSave(lbI)"></textarea><span class="sv" id="lbsv"></span></div><div id="lbmsg" style="text-align:center;padding:8px"></div>
<img id="lbimg" alt=""></div>"""

LB_JS = """
let lbI=-1,lbRaw=false;
function lbShow(i){lbI=i;const it=items[i];const im=$('#lbimg');$('#lb').style.display='block';
 $('#lbmsg').textContent='rendering (the first view of a crop can take a few seconds)...';im.style.visibility='hidden';
 im.onload=()=>{$('#lbmsg').textContent='';im.style.visibility='visible';};
 im.onerror=()=>{$('#lbmsg').textContent='could not render this crop';};
 im.src='/zoom?key='+encodeURIComponent(it.key)+(lbRaw?'&raw=1':'');
 $('#lbcap').textContent=(i+1)+' / '+items.length+' \u00b7 '+it.category+' \u00b7 label '+it.label_id+' \u00b7 '+it.reason+(marks[it.key]?' \u00b7 ['+marks[it.key]+']':'');
 $('#lbraw').textContent=lbRaw?'labelled (R)':'raw only (R)';
 $('#lbhelp').textContent=(typeof LB_ANSWER==='function'?'Y = YES, N = NO, ':'')+'\u2190/\u2192 move, click image = actual size';
 $('#lbcm').value=it.comment||'';$('#lbsv').textContent='';
 const c=document.getElementById('c'+i);if(c)c.scrollIntoView({block:'center'});}
function lbOpen(i){lbShow(i);}
function lbClose(){lbI=-1;$('#lb').style.display='none';$('#lbimg').removeAttribute('src');}
function lbRawToggle(){if(lbI>=0){lbRaw=!lbRaw;lbShow(lbI);}}
function lbOpenSc(){if(lbI>=0)window.open(items[lbI].link,'_blank');}
$('#lbimg').onclick=e=>e.target.classList.toggle('full');
async function lbAnswer(i,v){try{await LB_ANSWER(i,v);}catch(e){return false;}return true;}
const cmtT={};
function cmtBox(it,i){return `<div><textarea class="cmt" id="cm${i}" rows="2" placeholder="Comment for Claude (optional)\u2026"
 oninput="cmtInput(${i},this.value)" onblur="cmtSave(${i})">${esc(it.comment)}</textarea><span class="sv" id="sv${i}"></span></div>`;}
function cmtInput(i,v){if(items[i].cmtSaved===undefined)items[i].cmtSaved=items[i].comment||'';items[i].comment=v;clearTimeout(cmtT[i]);cmtT[i]=setTimeout(()=>cmtSave(i),600);
 for(const id of ['cm'+i,'lbcm']){const t=document.getElementById(id);if(t&&t.value!==v&&(id!=='lbcm'||lbI===i))t.value=v;}}
async function cmtSave(i){clearTimeout(cmtT[i]);const it=items[i];if(!it||it.cmtSaved===it.comment)return;
 const v=it.comment;
 try{const r=await fetch('/comments',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:it.key,text:v})});
  if(!r.ok)throw new Error(r.status);const j=await r.json();it.cmtSaved=v;
  const n=document.getElementById('ncm');if(n)n.textContent=j.count;
  for(const id of ['sv'+i,lbI===i?'lbsv':'']){const s=id&&document.getElementById(id);if(s){s.textContent='saved';setTimeout(()=>{if(s.textContent==='saved')s.textContent='';},1500);}}
 }catch(err){for(const id of ['sv'+i,'lbsv']){const s=document.getElementById(id);if(s)s.textContent='NOT saved';}}}
document.addEventListener('keydown',async e=>{
 if(e.ctrlKey||e.metaKey||e.altKey||/^(INPUT|TEXTAREA|SELECT)$/.test((e.target.tagName||'')))return;
 const k=e.key.toLowerCase(),open=lbI>=0;
 if(open){
  if(k==='escape')lbClose();
  else if(k==='arrowleft'&&lbI>0){e.preventDefault();lbShow(lbI-1);}
  else if(k==='arrowright'&&lbI<items.length-1){e.preventDefault();lbShow(lbI+1);}
  else if(k==='r')lbRawToggle();
  else if(k==='o')lbOpenSc();
  else if((k==='y'||k==='n')&&typeof LB_ANSWER==='function'){
   if(await lbAnswer(lbI,k==='y'?'real':'false')){if(lbI<items.length-1)lbShow(lbI+1);else lbClose();}}
 }else if((k==='y'||k==='n')&&typeof LB_ANSWER==='function'){
  const f=items.findIndex(it=>!marks[it.key]);if(f<0)return;
  if(await lbAnswer(f,k==='y'?'real':'false')){const nx=items.findIndex(it=>!marks[it.key]);
   const c=document.getElementById('c'+(nx<0?f:nx));if(c)c.scrollIntoView({block:'center',behavior:'smooth'});}}
});
"""


def index_html(types: dict[str, int], n_comments: int = 0) -> str:
    tabs = "".join(f'<span class="tab" data-t="{t}" onclick="show(\'{t}\')">{t} ({n})'
                   f'<span class="cal"></span></span>' for t, n in types.items())
    calib = " ".join(f'<a href="/calibrate?type={t}">{t}</a>' for t in types)
    js = INDEX_JS % json.dumps([[t, n] for t, n in types.items()])
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Annotation QA review</title>
<style>{CSS}</style></head><body><header>{tabs}
<div class="bar">Mark all &ge; <input id="conf" size="4" value="0.8"> as real
<button class="r" onclick="bulk()">Apply to this tab</button> <span id="msg"></span>
&nbsp;|&nbsp; Calibrate: {calib} &nbsp;|&nbsp; <a href="/viewb">View B (final review)</a>
&nbsp;|&nbsp; <a href="/comments/view">Comments (<span id="ncm">{n_comments}</span>)</a></div>
<div class="bar" id="cal"></div></header>
<div id="cards"></div><div style="margin:16px"><button id="more" onclick="more()">show more</button></div>
{LB_HTML}<script>{js}{LB_JS}</script></body></html>"""


CAL_JS = """
const T=%s;const $=s=>document.querySelector(s);let items=[],marks={};
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function count(){const k=items.filter(i=>marks[i.key]).length;$('#prog').textContent='answered '+k+' / '+items.length;
 $('#done').style.display=(items.length&&k==items.length)?'':'none';
 const s=(await (await fetch('/calibration')).json())[T];if(!s){$('#status').textContent='';return;}
 const p=s.precision==null?'-':s.precision.toFixed(2);
 $('#status').textContent=`${s.status}: real ${s.real} of ${s.answered} answered, precision ${p} (PASS needs >= 2/3 with >= ${s.need} answered)`;}
async function answer(i,v){const it=items[i];
 const r=await fetch('/marks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({[it.key]:v})});
 if(!r.ok){alert('Not saved ('+r.status+'); try again');throw new Error('save failed');}
 marks[it.key]=v;$('#c'+i).className='card col '+v;count();}
window.LB_ANSWER=answer;
(async()=>{marks=await (await fetch('/marks')).json();items=await (await fetch('/calibrate/items?type='+T)).json();
 $('#cards').innerHTML=items.map((it,i)=>`<div class="card col ${marks[it.key]||''}" id="c${i}">
 <img class="crop" loading="lazy" src="${it.crop}" onclick="lbOpen(${i})"><div class="row">
 <img class="loc" loading="lazy" src="${it.locator}"><div><div><span class="num">${i+1}</span>${esc(it.reason)}</div>
 <div>${esc(it.category)} &middot; ${esc(it.frame_name)} &middot; label ${esc(it.label_id)}
 &middot; <a href="${it.link}" target="_blank">Open in Scalabel</a></div>
 <div class="big"><button class="r" onclick="answer(${i},'real')">YES &mdash; real mistake (Y)</button>
 <button class="f" onclick="answer(${i},'false')">NO &mdash; false alarm (N)</button></div>${cmtBox(it,i)}</div></div></div>`).join('');count();})();
"""


def calibrate_html(t: str, n_comments: int = 0) -> str:
    safe = "".join(c for c in t if c.isalnum() or c == "_")
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Calibrate {safe}</title>
<style>{CSS}</style></head><body><header><a href="/">&larr; back</a> &nbsp; Calibrate: {safe}
&nbsp; <a href="/comments/view">Comments (<span id="ncm">{n_comments}</span>)</a>
&nbsp; <b id="prog"></b> &nbsp; <b id="status"></b> <b id="done" style="display:none;color:#2a9d4a">All done - thank you!</b></header>
<div id="cards"></div>{LB_HTML}<script>{CAL_JS % json.dumps(safe)}{LB_JS}</script></body></html>"""
