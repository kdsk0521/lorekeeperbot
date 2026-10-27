// [2026-10-23 C73 M7a] 코드 앱 HTML 화면 — 샌드박스 iframe 안에서만 돈다. 규칙 conventions/activity_ui.md §5e.
//   부모(app.js)가 sandbox="allow-scripts" 로만 띄운다 → 이 문서는 불투명 origin 이라 세션 · 부모 DOM · 저장소에 손이 안 닿는다.
//   하는 일: ① 부모가 준 앱 HTML 을 지울 것 지우고 이 문서에 넣기 ② data-lk-act 누르기를 부모에 알리기
//            ③ 확인 줄(data-lk-confirm) ④ 누른 결과의 소리(audio[data-lk-play]) ⑤ 높이 알리기.
//   로직은 Lua(서버) 몫 — 앱 HTML 안의 script · on* 속성은 지운다.
(function () {
  "use strict";
  var DROP = ["script", "iframe", "frame", "frameset", "object", "embed", "base", "meta", "link", "noscript"];
  var URL_ATTRS = ["href", "src", "srcset", "poster", "action", "formaction", "xlink:href", "background"];
  var busy = false;

  var base = document.createElement("style");
  base.textContent = "html,body{margin:0;padding:0;background:transparent;color:#dbdee1;" +
    "font:14px/1.5 system-ui,-apple-system,'Segoe UI','Apple SD Gothic Neo','Malgun Gothic',sans-serif}" +
    "[data-lk-act]{cursor:pointer}body.lk-busy [data-lk-act]{opacity:.6;pointer-events:none}" +
    ".lk-confirm{position:sticky;bottom:0;display:flex;gap:8px;flex-wrap:wrap;align-items:center;padding:8px;" +
    "background:#2b2d31;border-top:1px solid #3f4147}.lk-confirm span{flex:1 1 100%;color:#949ba4;font-size:13px}" +
    ".lk-confirm button{flex:1;min-height:44px;border-radius:6px;border:1px solid #3f4147;background:#383a40;color:#dbdee1;font:inherit}" +
    ".lk-confirm button.ok{background:#5865f2;border-color:#5865f2;color:#fff}";
  document.head.appendChild(base);

  function post(msg) { try { window.parent.postMessage(msg, "*"); } catch (e) { /* 부모가 없으면 할 일도 없다 */ } }

  function safeUrl(v) {
    var s = String(v || "").trim().toLowerCase();
    return s === "" || s.charAt(0) === "#" || s.indexOf("data:") === 0;   // 바깥 주소 0 — 소리 · 그림은 data URI(§5e)
  }

  function clean(doc) {
    DROP.forEach(function (tag) {
      Array.prototype.slice.call(doc.querySelectorAll(tag)).forEach(function (n) { n.parentNode.removeChild(n); });
    });
    Array.prototype.slice.call(doc.querySelectorAll("*")).forEach(function (node) {
      Array.prototype.slice.call(node.attributes).forEach(function (a) {
        var n = a.name.toLowerCase();
        if (n.indexOf("on") === 0) { node.removeAttribute(a.name); return; }
        if (URL_ATTRS.indexOf(n) >= 0 && !safeUrl(a.value)) node.removeAttribute(a.name);
      });
    });
  }

  function render(html, play) {
    var doc = new DOMParser().parseFromString(String(html || ""), "text/html");
    clean(doc);
    Array.prototype.slice.call(document.head.querySelectorAll("[data-lk-app]")).forEach(function (n) { n.parentNode.removeChild(n); });
    Array.prototype.slice.call(doc.head.querySelectorAll("style")).forEach(function (st) {
      var c = document.importNode(st, true);
      c.setAttribute("data-lk-app", "1");
      document.head.appendChild(c);
    });
    var body = document.body;
    while (body.firstChild) body.removeChild(body.firstChild);
    ["class", "style"].forEach(function (k) {
      var v = doc.body.getAttribute(k);
      if (v === null) body.removeAttribute(k); else body.setAttribute(k, v);
    });
    Array.prototype.slice.call(doc.body.childNodes).forEach(function (n) { body.appendChild(document.importNode(n, true)); });
    setBusy(false);
    if (play) {
      var a = document.querySelector("audio[data-lk-play]");
      if (a && typeof a.play === "function") { try { var p = a.play(); if (p && p.catch) p.catch(function () {}); } catch (e) { /* 자동 재생 거절 */ } }
    }
    size();
  }

  function setBusy(on) {
    busy = !!on;
    if (busy) document.body.classList.add("lk-busy"); else document.body.classList.remove("lk-busy");
  }

  function readData(el) {
    var raw = el.getAttribute("data-lk-data");
    if (!raw) return {};
    try {
      var v = JSON.parse(raw);
      return (v && typeof v === "object" && !Array.isArray(v)) ? v : { value: v };
    } catch (e) {
      return { value: raw };
    }
  }

  function send(id, data) {
    if (busy) return;
    setBusy(true);
    post({ type: "lk-act", id: String(id).slice(0, 64), data: data });
  }

  function confirmBar(text, onOk) {
    var old = document.querySelector(".lk-confirm");
    if (old) old.parentNode.removeChild(old);
    var bar = document.createElement("div");
    bar.className = "lk-confirm";
    var t = document.createElement("span");
    t.textContent = text;
    var ok = document.createElement("button");
    ok.type = "button"; ok.className = "ok"; ok.textContent = "확인";
    var no = document.createElement("button");
    no.type = "button"; no.textContent = "취소";
    ok.addEventListener("click", function (e) { e.stopPropagation(); bar.parentNode.removeChild(bar); onOk(); });
    no.addEventListener("click", function (e) { e.stopPropagation(); bar.parentNode.removeChild(bar); size(); });
    bar.appendChild(t); bar.appendChild(ok); bar.appendChild(no);
    document.body.appendChild(bar);
    size();
  }

  document.addEventListener("click", function (e) {
    var el = e.target && e.target.closest ? e.target.closest("[data-lk-act]") : null;
    if (!el) return;
    e.preventDefault();
    var id = el.getAttribute("data-lk-act");
    if (!id) return;
    var data = readData(el);
    var q = el.getAttribute("data-lk-confirm");
    if (q) { confirmBar(q, function () { send(id, data); }); return; }
    send(id, data);
  });

  var lastH = 0;
  function size() {
    var h = Math.ceil(Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0));
    if (h !== lastH) { lastH = h; post({ type: "lk-size", h: h }); }
  }
  if (window.ResizeObserver) { try { new ResizeObserver(size).observe(document.documentElement); } catch (e) { /* 없어도 render 가 알린다 */ } }

  window.addEventListener("message", function (e) {
    if (e.source !== window.parent) return;        // 부모 말만 듣는다
    var m = e.data;
    if (!m || typeof m !== "object") return;
    if (m.type === "lk-html" && typeof m.html === "string") render(m.html, !!m.play);
    else if (m.type === "lk-done") setBusy(false);
  });

  post({ type: "lk-ready" });
})();
