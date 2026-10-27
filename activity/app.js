/* 로어키퍼 Activity 화면 — [2026-10-22 C54] 1단계(읽기 전용) · [C55] UI 규칙 적용
 *   [2026-10-23 C60 M2] 탭 줄은 서버 등록부(GET /api/tabs)에서 — 실패하면 index.html 의 옛 넷.
 *   [2026-10-23 C67 A1] 탭 줄 → 홈 + 앱 두 층(서랍 · 뒤로·홈·편집). 고치기 탭 → 앱마다 편집 모드(edit 고르개).
 *   [2026-10-23 C69 A3] 편지 · 소식 · 속마음 앱(렌더러 feed) · 홈 이번 턴 줄 · 앱 타일 배지(안 본 행 수).
 *   [2026-10-23 C72 A5] 부품 상자(렌더러 parts — 서버가 화면을 부품 목록으로 보낸다) · 선언 앱 → 모듈 앱.
 *   고치기 탭(편집): POST /api/act {action, args, rev} 한 길. 확인창 없음 · 낙관 갱신 0(응답의 data 로 다시 그린다)
 *   · 고친 뒤 영수증 한 줄 + ↶(방금 고친 것 하나 되돌리기). 판정·잠금·쓰기는 전부 서버 몫.
 *
 * 규칙 문서: conventions/activity_ui.md (화면을 고치기 전에 읽고, 고친 뒤 변경 기록에 한 줄).
 * 흐름: SDK 준비 → authorize(identify) → POST /api/session {code, channel_id} → 세션
 *       → GET /api/view/<탭> (Authorization: Bearer 세션) → 그리기.
 * 보안: 서버가 준 글은 전부 textContent·글자 노드로만 넣는다(HTML 문자열 삽입 0). 아이콘은 createElementNS 로 그린다.
 *       유일한 글자 꾸밈은 **굵게**(디스코드 표기) 하나.
 *
 * 아이콘: Lucide (https://lucide.dev) — ISC License, Copyright (c) 2026 Lucide Icons and Contributors.
 *   일부 아이콘은 Feather 유래 — MIT License, Copyright (c) 2013-present Cole Bemis.
 *   Permission to use, copy, modify, and/or distribute this software for any purpose with or without fee is hereby
 *   granted, provided that the above copyright notice and this permission notice appear in all copies.
 *   THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH REGARD TO THIS SOFTWARE.
 */
"use strict";
(function () {
  var CLIENT_ID = (document.querySelector('meta[name="discord-client-id"]') || {}).content || "";
  var ASSET_VER = (document.querySelector('meta[name="asset-ver"]') || {}).content || "0";   // [C73] frame.html 꼬리
  var view = document.getElementById("view");
  var sdk = null;
  var session = null;
  // [C67 A1] 탭 줄 → 홈 + 앱. route = null(홈) | 앱 tab. 아래 "앱 껍데기" 절.
  var currentPerson = null;

  // ---------- 아이콘 (Lucide 1.61.0 노드, 쓰는 것만 — 규칙 문서 §3 표와 같은 목록) ----------
  var ICONS = {"activity":[["path",{"d":"M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2"}]],"archive":[["rect",{"width":"20","height":"5","x":"2","y":"3","rx":"1"}],["path",{"d":"M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8"}],["path",{"d":"M10 12h4"}]],"users":[["path",{"d":"M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"}],["path",{"d":"M16 3.128a4 4 0 0 1 0 7.744"}],["path",{"d":"M22 21v-2a4 4 0 0 0-3-3.87"}],["circle",{"cx":"9","cy":"7","r":"4"}]],"scroll":[["path",{"d":"M15 12h-5"}],["path",{"d":"M15 8h-5"}],["path",{"d":"M19 17V5a2 2 0 0 0-2-2H4"}],["path",{"d":"M8 21h12a2 2 0 0 0 2-2v-1a1 1 0 0 0-1-1H11a1 1 0 0 0-1 1v1a2 2 0 1 1-4 0V5a2 2 0 1 0-4 0v2a1 1 0 0 0 1 1h3"}]],"refresh":[["path",{"d":"M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"}],["path",{"d":"M21 3v5h-5"}],["path",{"d":"M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"}],["path",{"d":"M8 16H3v5"}]],"user":[["path",{"d":"M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"}],["circle",{"cx":"12","cy":"7","r":"4"}]],"globe":[["circle",{"cx":"12","cy":"12","r":"10"}],["path",{"d":"M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"}],["path",{"d":"M2 12h20"}]],"handshake":[["path",{"d":"m11 17 2 2a1 1 0 1 0 3-3"}],["path",{"d":"m14 14 2.5 2.5a1 1 0 1 0 3-3l-3.88-3.88a3 3 0 0 0-4.24 0l-.88.88a1 1 0 1 1-3-3l2.81-2.81a5.79 5.79 0 0 1 7.06-.87l.47.28a2 2 0 0 0 1.42.25L21 4"}],["path",{"d":"m21 3 1 11h-2"}],["path",{"d":"M3 3 2 14l6.5 6.5a1 1 0 1 0 3-3"}],["path",{"d":"M3 4h8"}]],"clapperboard":[["path",{"d":"m12.296 3.464 3.02 3.956"}],["path",{"d":"M20.2 6 3 11l-.9-2.4c-.3-1.1.3-2.2 1.3-2.5l13.5-4c1.1-.3 2.2.3 2.5 1.3z"}],["path",{"d":"M3 11h18v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"}],["path",{"d":"m6.18 5.276 3.1 3.899"}]],"history":[["path",{"d":"M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"}],["path",{"d":"M3 3v5h5"}],["path",{"d":"M12 7v5l4 2"}]],"layers":[["path",{"d":"M12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83z"}],["path",{"d":"M2 12a1 1 0 0 0 .58.91l8.6 3.91a2 2 0 0 0 1.65 0l8.58-3.9A1 1 0 0 0 22 12"}],["path",{"d":"M2 17a1 1 0 0 0 .58.91l8.6 3.91a2 2 0 0 0 1.65 0l8.58-3.9A1 1 0 0 0 22 17"}]],"star":[["path",{"d":"M11.525 2.295a.53.53 0 0 1 .95 0l2.31 4.679a2.123 2.123 0 0 0 1.595 1.16l5.166.756a.53.53 0 0 1 .294.904l-3.736 3.638a2.123 2.123 0 0 0-.611 1.878l.882 5.14a.53.53 0 0 1-.771.56l-4.618-2.428a2.122 2.122 0 0 0-1.973 0L6.396 21.01a.53.53 0 0 1-.77-.56l.881-5.139a2.122 2.122 0 0 0-.611-1.879L2.16 9.795a.53.53 0 0 1 .294-.906l5.165-.755a2.122 2.122 0 0 0 1.597-1.16z"}]],"help":[["circle",{"cx":"12","cy":"12","r":"10"}],["path",{"d":"M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"}],["path",{"d":"M12 17h.01"}]],"map":[["path",{"d":"M20 10c0 4.993-5.539 10.193-7.399 11.799a1 1 0 0 1-1.202 0C9.539 20.193 4 14.993 4 10a8 8 0 0 1 16 0"}],["circle",{"cx":"12","cy":"10","r":"3"}]],"flag":[["path",{"d":"M4 22V4a1 1 0 0 1 .4-.8A6 6 0 0 1 8 2c3 0 5 2 7.333 2q2 0 3.067-.8A1 1 0 0 1 20 4v10a1 1 0 0 1-.4.8A6 6 0 0 1 16 16c-3 0-5-2-8-2a6 6 0 0 0-4 1.528"}]],"notebook":[["path",{"d":"M13.4 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-7.4"}],["path",{"d":"M2 6h4"}],["path",{"d":"M2 10h4"}],["path",{"d":"M2 14h4"}],["path",{"d":"M2 18h4"}],["path",{"d":"M21.378 5.626a1 1 0 1 0-3.004-3.004l-5.01 5.012a2 2 0 0 0-.506.854l-.837 2.87a.5.5 0 0 0 .62.62l2.87-.837a2 2 0 0 0 .854-.506z"}]],"mail":[["path",{"d":"m22 7-8.991 5.727a2 2 0 0 1-2.009 0L2 7"}],["rect",{"x":"2","y":"4","width":"20","height":"16","rx":"2"}]],"book":[["path",{"d":"M12 5v16"}],["path",{"d":"M20.001 19A2 2 0 0022 17V5a2 2 0 00-1.999-2L16 3.002A5 5 0 0012 5a5 5 0 00-4-2H4a2 2 0 00-2 2v12a2 2 0 001.999 2H8a5 5 0 014 2 5 5 0 014-2z"}]],"alert":[["path",{"d":"m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"}],["path",{"d":"M12 9v4"}],["path",{"d":"M12 17h.01"}]],"clock":[["circle",{"cx":"12","cy":"12","r":"10"}],["path",{"d":"M12 6v6l4 2"}]],"pencil":[["path",{"d":"M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z"}],["path",{"d":"m15 5 4 4"}]],"undo":[["path",{"d":"M9 14 4 9l5-5"}],["path",{"d":"M4 9h10.5a5.5 5.5 0 0 1 5.5 5.5a5.5 5.5 0 0 1-5.5 5.5H11"}]],"plus":[["path",{"d":"M5 12h14"}],["path",{"d":"M12 5v14"}]],"x":[["path",{"d":"M18 6 6 18"}],["path",{"d":"m6 6 12 12"}]],"check":[["path",{"d":"M20 6 9 17l-5-5"}]],"house":[["path",{"d":"M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8"}],["path",{"d":"M3 10a2 2 0 0 1 .709-1.528l7-5.999a2 2 0 0 1 2.582 0l7 5.999A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"}]],"arrow-left":[["path",{"d":"m12 19-7-7 7-7"}],["path",{"d":"M19 12H5"}]],"chevron-right":[["path",{"d":"m9 18 6-6-6-6"}]],"newspaper":[["path",{"d":"M15 18h-5"}],["path",{"d":"M18 14h-8"}],["path",{"d":"M4 22h16a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2H8a2 2 0 0 0-2 2v16a2 2 0 0 1-4 0v-9a2 2 0 0 1 2-2h2"}],["rect",{"width":"8","height":"4","x":"10","y":"6","rx":"1"}]],"brain":[["path",{"d":"M12 18V5"}],["path",{"d":"M15 13a4.17 4.17 0 01-3-4 4.17 4.17 0 01-3 4"}],["path",{"d":"M17.707 6.294A3 3 0 1012 5a3 3 0 10-5.707 1.294"}],["path",{"d":"M17.996 5.125a4 4 0 012.526 5.77"}],["path",{"d":"M18 18a4 4 0 002-7.465"}],["path",{"d":"M19.967 17.482a4 4 0 11-7.967.517 4 4 0 11-7.967-.517"}],["path",{"d":"M6 18a4 4 0 01-2-7.465"}],["path",{"d":"M6.004 5.125a4 4 0 00-2.526 5.77"}]],"blocks":[["path",{"d":"M10 22V7a1 1 0 0 0-1-1H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-5a1 1 0 0 0-1-1H2"}],["rect",{"x":"14","y":"2","width":"8","height":"8","rx":"1"}]],"chevron-down":[["path",{"d":"m6 9 6 6 6-6"}]],"orbit":[["path",{"d":"M20.341 6.484A10 10 0 0 1 10.266 21.85"}],["path",{"d":"M3.659 17.516A10 10 0 0 1 13.74 2.152"}],["circle",{"cx":"12","cy":"12","r":"3"}],["circle",{"cx":"19","cy":"5","r":"2"}],["circle",{"cx":"5","cy":"19","r":"2"}]],"target":[["circle",{"cx":"12","cy":"12","r":"10"}],["circle",{"cx":"12","cy":"12","r":"6"}],["circle",{"cx":"12","cy":"12","r":"2"}]]};
  var SVG_NS = "http://www.w3.org/2000/svg";
  function icon(name, size) {
    var s = document.createElementNS(SVG_NS, "svg");
    var px = String(size || 16);
    s.setAttribute("width", px); s.setAttribute("height", px);
    s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("fill", "none");
    s.setAttribute("stroke", "currentColor"); s.setAttribute("stroke-width", "2");
    s.setAttribute("stroke-linecap", "round"); s.setAttribute("stroke-linejoin", "round");
    s.setAttribute("aria-hidden", "true"); s.setAttribute("class", "icon");
    (ICONS[name] || ICONS.layers).forEach(function (node) {
      var n = document.createElementNS(SVG_NS, node[0]);
      Object.keys(node[1]).forEach(function (k) { n.setAttribute(k, node[1][k]); });
      s.appendChild(n);
    });
    return s;
  }

  // 서버 제목이 이모지로 시작하면(임베드 시절 머리) 떼고 아이콘으로 바꾼다 — 규칙 §3.
  var EMOJI_ICON = { "🧍": "user", "🌍": "globe", "🤝": "handshake", "📜": "clapperboard", "🎞": "history",
                     "💠": "layers", "💌": "mail", "📰": "scroll", "💭": "users", "📓": "notebook" };
  var LEAD_SYMBOL = /^[^\p{L}\p{N}\s]+\s*/u;
  function splitTitle(title) {
    var t = String(title || "");
    var m = t.match(LEAD_SYMBOL);
    if (!m) return { icon: null, text: t };
    var lead = m[0].trim(), name = null;
    Object.keys(EMOJI_ICON).forEach(function (e) { if (lead.indexOf(e) === 0) name = EMOJI_ICON[e]; });
    return { icon: name, text: t.slice(m[0].length) };
  }

  // ---------- DOM 도우미 ----------
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) appendRich(n, String(text));
    return n;
  }
  function appendRich(node, text) {          // **굵게** 만 살린다. 나머지는 글자 그대로.
    var parts = text.split("**");
    for (var i = 0; i < parts.length; i++) {
      if (!parts[i]) continue;
      if (i % 2 === 1 && i < parts.length - 1) {
        var b = document.createElement("b");
        b.textContent = parts[i];
        node.appendChild(b);
      } else {
        node.appendChild(document.createTextNode(parts[i]));
      }
    }
  }
  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); }
  function card(title, iconName) {
    var c = el("section", "card");
    if (title) {
      var h = el("h2", "card-title");
      h.appendChild(icon(iconName || "layers", 16));
      h.appendChild(el("span", null, title));
      c.appendChild(h);
    }
    return c;
  }
  // forcedIcon 이 있으면 그게 이긴다(보관함 장처럼 자리가 아이콘을 정하는 곳). 없으면 이모지 표 → 없으면 layers.
  function titledCard(rawTitle, forcedIcon) {
    var t = splitTitle(rawTitle);
    return card(t.text, forcedIcon || t.icon || "layers");
  }

  // 값 한 칸 — 줄마다 게이지면 막대로, 아니면 글자로(규칙 §5).
  var GAUGE = /^(.*?)\s*[▰▱]+\s*(\d+)\s*\/\s*(\d+)(.*)$/;
  function gaugeEl(label, n, max, tail) {
    var ratio = max > 0 ? Math.max(0, Math.min(1, n / max)) : 0;
    var g = el("div", "gauge");
    g.appendChild(el("span", "g-label", label.trim()));
    var track = el("span", "g-track");
    var fill = el("span", "g-fill " + (ratio >= 0.4 ? "ok" : ratio >= 0.15 ? "low" : "bad"));
    fill.style.width = (ratio * 100).toFixed(1) + "%";
    track.appendChild(fill);
    g.appendChild(track);
    g.appendChild(el("span", "g-num", n + "/" + max + (tail ? tail : "")));
    return g;
  }
  function valueEl(value) {
    var dd = el("dd");
    String(value === "​" ? "" : (value || "")).split("\n").forEach(function (line, i) {
      var m = line.match(GAUGE);
      if (m) { dd.appendChild(gaugeEl(m[1], Number(m[2]), Number(m[3]), m[4])); return; }
      var p = el("div", "v-line", line);
      dd.appendChild(p);
    });
    return dd;
  }
  function rows(container, list) {
    var dl = el("dl", "rows");
    (list || []).forEach(function (r) {
      var name = Array.isArray(r) ? r[0] : r.name;
      var value = Array.isArray(r) ? r[1] : r.value;
      var row = el("div", "row");
      row.appendChild(el("dt", null, name === "​" ? "" : name));
      row.appendChild(valueEl(value));
      dl.appendChild(row);
    });
    container.appendChild(dl);
  }
  function message(text, isError) {
    clear(view);
    var p = el("p", isError ? "state error" : "state");
    if (isError) p.appendChild(icon("alert", 16));
    p.appendChild(el("span", null, text));
    view.appendChild(p);
  }

  // ---------- 연결 ----------
  async function connect() {
    if (!window.DiscordSDKLib) throw new Error("SDK 파일을 못 불러왔어요");
    if (!sdk) {
      message("디스코드에 연결하는 중…");
      sdk = new window.DiscordSDKLib.DiscordSDK(CLIENT_ID);
      await sdk.ready();
      if (sdk.platform === "mobile") document.documentElement.classList.add("is-mobile");
    }
    if (!session) message("로그인하는 중…");
    var auth = await sdk.commands.authorize({
      client_id: CLIENT_ID, response_type: "code", state: "", prompt: "none", scope: ["identify"]
    });
    var r = await fetch("/api/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: auth.code, channel_id: sdk.channelId })
    });
    if (r.status === 403) throw new Error("이 채널을 볼 권한이 없어요");
    if (!r.ok) throw new Error("로그인 실패 (" + r.status + ")");
    var d = await r.json();
    session = d.session;
    // 화면엔 필요 없는 단계라 기다리지 않는다(첫 화면이 그만큼 빨리 뜬다).
    try { sdk.commands.authenticate({ access_token: d.access_token }).catch(function () {}); } catch (e) { /* 무시 */ }
  }

  async function api(path, retried) {
    var r = await fetch(path, { headers: { Authorization: "Bearer " + session } });
    if (r.status === 401 && !retried) {          // 세션 만료·봇 재시작 → 한 번만 다시 인증
      await connect();
      return api(path, true);
    }
    if (r.status === 404) return null;
    if (!r.ok) throw new Error("불러오기 실패 (" + r.status + ")");
    return r.json();
  }
  // [C60 M2c] 쓰기 — 상태 코드와 본문을 같이 돌려준다(409 도 본문에 새 데이터·안내가 있다).
  async function apiPost(path, body, retried) {
    var r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: "Bearer " + session },
      body: JSON.stringify(body)
    });
    if (r.status === 401 && !retried) { await connect(); return apiPost(path, body, true); }
    var d = null;
    try { d = await r.json(); } catch (e) { d = null; }
    return { status: r.status, body: d || {} };
  }

  // ---------- 탭: 상태 ----------
  async function showStatus() {
    var d = await api("/api/view/status");
    clear(view);
    var turn = d && d.turn;
    if (turn && turn.footer) {
      var f = el("p", "meta");
      f.appendChild(icon("clock", 14));
      f.appendChild(el("span", null, turn.footer));
      view.appendChild(f);
    }
    ((turn && turn.sections) || []).forEach(function (s) {
      if (!s[1] || !s[1].length) return;
      var c = titledCard(s[0]);
      rows(c, s[1]);
      view.appendChild(c);
    });
    if (turn && turn.comments && turn.comments.length) {
      var cc = el("section", "card");
      turn.comments.forEach(function (t) { cc.appendChild(el("p", "comment", t)); });
      view.appendChild(cc);
    }
    ((d && d.sections) || []).forEach(function (s) {
      if (!s[1] || !s[1].length) return;
      var c = card(s[0], "layers");
      rows(c, s[1]);
      view.appendChild(c);
    });
    if (!view.firstChild) message("아직 표시할 상태가 없어요.");
  }

  // ---------- 탭: 보관함 ----------
  var ARCHIVE_ICON = { notebook: "notebook", record: "history", journal: "book", mail: "mail",
                       relation: "handshake", empty: "archive" };
  async function showArchive() {
    var d = await api("/api/view/archive");
    clear(view);
    ((d && d.pages) || []).forEach(function (p) {
      if (p.key === "empty") return;
      var c = titledCard(p.title, ARCHIVE_ICON[p.key] || "archive");
      if (p.body) c.appendChild(el("pre", "body", p.body));
      if (p.rows && p.rows.length) rows(c, p.rows);
      view.appendChild(c);
    });
    if (!view.firstChild) message("아직 보관된 게 없어요.");
  }

  // ---------- 탭: 인물 ----------
  var KIND_ICON = { location: "map", faction: "flag" };
  async function showPeople() {
    var d = await api("/api/view/people");
    clear(view);
    var pages = (d && d.pages) || [];
    if (!pages.length) { message("아직 위키에 페이지가 없어요."); return; }
    var wrap = el("div", "people");
    var list = el("nav", "plist");
    var detail = el("div", "pdetail");
    pages.forEach(function (p) {
      var b = el("button", p.mine ? "mine" : null);
      b.type = "button";
      var ic = p.mine ? "star" : (KIND_ICON[p.kind] || "user");
      b.appendChild(icon(ic, 14));
      b.appendChild(el("span", null, p.name));
      b.addEventListener("click", function () {
        Array.prototype.forEach.call(list.children, function (x) { x.classList.remove("on"); });
        b.classList.add("on");
        currentPerson = p.page_id;
        showPerson(detail, p.page_id).catch(function (e) {
          clear(detail); detail.appendChild(el("p", "state error", String(e.message || e)));
        });
      });
      list.appendChild(b);
    });
    wrap.appendChild(list);
    wrap.appendChild(detail);
    view.appendChild(wrap);
    var pick = pages.find(function (p) { return p.page_id === currentPerson; }) || pages[0];
    var idx = pages.indexOf(pick);
    if (list.children[idx]) list.children[idx].click();
  }
  async function showPerson(detail, pageId) {
    clear(detail);
    detail.appendChild(el("p", "state", "불러오는 중…"));
    var d = await api("/api/view/people/" + encodeURIComponent(pageId));
    clear(detail);
    if (!d) { detail.appendChild(el("p", "state", "페이지가 없어요.")); return; }
    detail.appendChild(el("h1", "page-title", d.page.name));
    if (d.page.aliases && d.page.aliases.length) detail.appendChild(el("p", "meta", "별칭 " + d.page.aliases.join(", ")));
    if (d.lore && d.lore.length) {
      var c = card("시트", "scroll");
      d.lore.forEach(function (s) {
        c.appendChild(el("h3", null, s.section));
        c.appendChild(el("pre", "body", s.body));
      });
      detail.appendChild(c);
    }
    if (d.play && d.play.length) {
      var c2 = card("플레이 기록", "history");
      d.play.forEach(function (s) {
        c2.appendChild(el("h3", null, s.section + (s.updated_turn ? " · t" + s.updated_turn : "")));
        c2.appendChild(el("pre", "body", s.body));
      });
      detail.appendChild(c2);
    }
    if ((!d.lore || !d.lore.length) && (!d.play || !d.play.length)) detail.appendChild(el("p", "state", "비어 있어요."));
  }

  // ---------- 부품 상자 (C72 A5 — 규칙 §5d. 데이터 = {parts:[…], rev, view}) ----------
  //   서버가 화면을 부품 목록으로 보낸다. 모르는 t 는 건너뛰고, 카드 안 카드는 평평하게 편다.
  //   누르기 = POST /api/act {action, args, rev, view} — 응답의 data(새 쪽)로만 다시 그린다(낙관 갱신 0).
  //   영수증 한 줄 + ↶ · 실패 한 줄은 편집 화면과 같은 자리 · 같은 규칙(§5b).
  var partsRev = null, partsView = null, partsTab = null, partsBusy = false, partsApp = null;
  var partsReceipt = null, partsNotice = null, partsFlash = null;
  function withArgs(p, extra) {
    var a = {}, src = (p.act && p.act.args) || {};
    Object.keys(src).forEach(function (k) { a[k] = src[k]; });
    Object.keys(extra).forEach(function (k) { a[k] = extra[k]; });
    return a;
  }
  // [C73] 누르기 한 곳 — 코드 앱 쪽(partsApp)이면 부품의 act 는 {id, data} 라 app.click 으로 감싸 보낸다(규칙 §5d).
  function fire(ctrl, p, extra) {
    if (!p.act) return;
    if (partsApp) {
      var data = {}, src = p.act.data || {};
      Object.keys(src).forEach(function (k) { data[k] = src[k]; });
      Object.keys(extra).forEach(function (k) { data[k] = extra[k]; });
      partsAct(ctrl, "app.click", { app: partsApp, id: String(p.act.id || ""), data: data });
      return;
    }
    partsAct(ctrl, p.act.action, withArgs(p, extra));
  }
  function partSwitch(p) {
    var row = el("div", "p-row");
    var lab = el("span", "p-label", p.label || "");
    var b = el("button", "switch" + (p.on ? " on" : ""));
    b.type = "button";
    b.setAttribute("role", "switch");
    b.setAttribute("aria-checked", p.on ? "true" : "false");
    b.setAttribute("aria-label", p.label || "");
    b.appendChild(el("span", "knob"));
    b.addEventListener("click", function () { fire(b, p, { on: !p.on }); });
    lab.addEventListener("click", function () { b.click(); });     // 줄 전체가 누르는 칸
    row.appendChild(lab);
    row.appendChild(b);
    return row;
  }
  function partSelect(p) {
    var row = el("label", "p-row");
    row.appendChild(el("span", "p-label", p.label || ""));
    var s = document.createElement("select");
    s.className = "p-select";
    (p.options || []).forEach(function (o) {
      var op = document.createElement("option");
      op.value = String(o[0]);
      op.textContent = String(o[1]);
      if (String(o[0]) === String(p.value)) op.selected = true;
      s.appendChild(op);
    });
    s.addEventListener("change", function () { fire(s, p, { value: s.value }); });
    row.appendChild(s);
    return row;
  }
  function partFold(p) {
    var d = document.createElement("details");
    d.className = "fold";
    var sm = document.createElement("summary");
    sm.appendChild(el("span", "grow", p.title || ""));
    sm.appendChild(icon("chevron-down", 16));
    d.appendChild(sm);
    var box = el("div", "fold-body");
    drawParts(box, p.parts || [], true);
    d.appendChild(box);
    return d;
  }
  // [C73] 버튼 — confirm 이 있으면 같은 자리에 확인 줄(브라우저 confirm 안 씀). 규칙 §5d.
  function partButton(p) {
    var wrap = el("div", "p-btnrow");
    var cls = "p-button" + (p.style === "danger" ? " danger" : (p.style === "primary" ? " primary" : ""));
    var b = el("button", cls, p.label || "");
    b.type = "button";
    b.addEventListener("click", function () {
      if (!p.confirm) { fire(b, p, {}); return; }
      clear(wrap);
      var q = el("div", "p-confirm");
      q.appendChild(el("span", "p-label", p.confirm));
      var ok = el("button", "p-button primary", "확인");
      ok.type = "button";
      var no = el("button", "p-button", "취소");
      no.type = "button";
      ok.addEventListener("click", function () { fire(ok, p, {}); });
      no.addEventListener("click", function () { clear(wrap); wrap.appendChild(b); });
      q.appendChild(ok);
      q.appendChild(no);
      wrap.appendChild(q);
    });
    wrap.appendChild(b);
    return wrap;
  }
  // [C73] 칩 줄 — 앱 안 하위 화면 고르기(규칙 §4 · §5d). 고른 칩 = aria-pressed.
  function partChips(p) {
    var row = el("div", "p-chips");
    (p.items || []).forEach(function (it) {
      var on = String(it[0]) === String(p.value);
      var c = el("button", "p-chip" + (on ? " on" : ""), String(it[1]));
      c.type = "button";
      c.setAttribute("aria-pressed", on ? "true" : "false");
      c.addEventListener("click", function () { fire(c, p, { value: String(it[0]) }); });
      row.appendChild(c);
    });
    return row;
  }
  var PART = {
    "text": function (p) { return el("p", "p-text" + (p.muted ? " muted" : ""), p.text || ""); },
    "rows": function (p) { var w = el("div", "p-rows"); rows(w, p.rows || []); return w; },
    "empty": function (p) { return el("p", "state", p.text || ""); },
    "switch": partSwitch,
    "select": partSelect,
    "fold": partFold,
    "button": partButton,
    "chips": partChips
  };
  function partCard(p) {
    var c = el("section", "card");
    var h = el("h2", "card-title");
    h.appendChild(icon(ICONS[p.icon] ? p.icon : "layers", 16));
    h.appendChild(el("span", "grow", p.title || ""));
    if (p.chip) h.appendChild(el("span", "chip", p.chip));
    c.appendChild(h);
    drawParts(c, p.parts || [], true);
    return c;
  }
  function drawParts(container, list, inCard) {
    (list || []).forEach(function (p) {
      if (!p || typeof p !== "object") return;
      if (p.t === "card") {
        if (inCard) { drawParts(container, p.parts || [], true); return; }   // 카드 안 카드 → 평평하게(§4)
        container.appendChild(partCard(p));
        return;
      }
      var fn = PART[p.t];
      if (fn) container.appendChild(fn(p));                                    // 모르는 t 는 건너뛴다
    });
  }
  function drawLines(box) {        // 실패 한 줄 · 한 번 보이는 줄 · 영수증(+↶) — 편집 화면과 같은 자리(§5b)
    if (partsNotice) {
      var p = el("p", "state error");
      p.appendChild(icon("alert", 16));
      p.appendChild(el("span", null, partsNotice));
      box.appendChild(p);
    }
    if (partsFlash) { box.appendChild(el("p", "state", partsFlash)); partsFlash = null; }
    if (partsReceipt) {
      var rc = el("div", "receipt");
      rc.appendChild(icon("check", 16));
      rc.appendChild(el("span", "vtext", partsReceipt.text));
      var rid = partsReceipt.undoId;
      if (rid) rc.appendChild(iconBtn("undo", "되돌리기", function () { partsAct(null, "undo", { id: rid }); }));
      box.appendChild(rc);
    }
  }
  function pageMeta(d) {          // [C73] 코드 앱 쪽은 {app, screen, rev} — view 는 app:<id>
    partsRev = d ? d.rev : null;
    partsApp = d && d.app ? String(d.app) : null;
    partsView = d ? (d.view || (partsApp ? "app:" + partsApp : null)) : null;
    if (partsReceipt && partsReceipt.rev !== partsRev) partsReceipt = null;
  }
  function renderParts(d) {
    if (d && d.screen === "html") { renderFrame(d, false); return; }
    clear(view);
    frame = null;
    pageMeta(d);
    drawLines(view);
    drawParts(view, (d && d.parts) || [], false);
    if (!view.firstChild) message("아직 표시할 게 없어요.");
  }
  async function showParts(tab) {
    partsTab = tab;
    var d = await api("/api/view/" + encodeURIComponent(tab));
    renderParts(d || {});
  }

  // ---------- 코드 앱 HTML 화면 (C73 — 규칙 §5e, §7 의 유일한 예외) ----------
  //   iframe = 우리 frame.html(sandbox="allow-scripts" 하나만 — same-origin 없음 → 세션 · 이 문서에 손이 안 닿는다).
  //   HTML 글은 postMessage 로만 넘긴다(문서에 HTML 문자열을 꽂지 않는다). 받는 메시지는 그 iframe 창에서 온 것 · 모양이 맞는 것만.
  var frame = null;     // {iframe, app, html, ready, play}
  function renderFrame(d, play) {
    pageMeta(d);
    if (frame && frame.app === partsApp && frame.iframe.isConnected && frame.lines && frame.lines.isConnected) {
      clear(frame.lines);
      drawLines(frame.lines);
      frame.html = String(d.html || "");
      frame.play = !!play;
      pushFrame();
      return;
    }
    clear(view);
    var lines = el("div", "frame-lines");
    drawLines(lines);
    view.appendChild(lines);
    var f = document.createElement("iframe");
    f.className = "app-frame";
    f.setAttribute("sandbox", "allow-scripts");
    f.setAttribute("referrerpolicy", "no-referrer");
    f.setAttribute("title", String(d.name || "앱 화면"));
    f.src = "/frame.html?v=" + encodeURIComponent(ASSET_VER);
    frame = { iframe: f, app: partsApp, html: String(d.html || ""), ready: false, play: !!play, lines: lines };
    view.appendChild(f);
  }
  function pushFrame() {
    if (!frame || !frame.ready || !frame.iframe.contentWindow) return;
    frame.iframe.contentWindow.postMessage({ type: "lk-html", html: frame.html, play: frame.play }, "*");
    frame.play = false;
  }
  window.addEventListener("message", function (e) {
    if (!frame || !frame.iframe.contentWindow || e.source !== frame.iframe.contentWindow) return;
    var m = e.data;
    if (!m || typeof m !== "object") return;
    if (m.type === "lk-ready") { frame.ready = true; pushFrame(); return; }
    if (m.type === "lk-size") {
      var h = Math.max(80, Math.min(4000, Number(m.h) || 0));
      frame.iframe.style.height = h + "px";
      return;
    }
    if (m.type === "lk-act" && typeof m.id === "string" && m.id) {
      var data = (m.data && typeof m.data === "object" && !Array.isArray(m.data)) ? m.data : {};
      partsAct(null, "app.click", { app: frame.app, id: m.id.slice(0, 64), data: data });
    }
  });
  async function partsAct(ctrl, action, args) {
    if (partsBusy || !action) return;
    partsBusy = true;
    if (ctrl) ctrl.disabled = true;                     // 보내는 동안 그 칸은 잠긴다(두 번 누르기 0)
    try {
      var res = await apiPost("/api/act", { action: action, args: args, rev: partsRev, view: partsView || "edit" });
      var b = res.body || {};
      if (res.status === 200 && b.ok) {
        partsNotice = null;
        partsReceipt = b.receipt ? { text: b.receipt, undoId: b.undo_id, rev: (b.data || {}).rev } : null;
        if (action === "undo") partsFlash = b.line || "되돌렸어요.";
      } else if (res.status === 200) {
        partsNotice = b.line || "바뀐 게 없어요.";
      } else {
        partsNotice = b.message || ("바꾸기 실패 (" + res.status + ")");
        if (b.error === "stale") partsReceipt = null;
      }
      if (b.data && b.data.screen === "html") renderFrame(b.data, res.status === 200 && !!b.ok);
      else if (b.data && b.data.parts) renderParts(b.data);
      else await showParts(partsTab);
    } catch (e) {
      partsNotice = String((e && e.message) || e);
      try { await showParts(partsTab); } catch (e2) { message(partsNotice, true); }
    } finally {
      partsBusy = false;
      if (frame && frame.ready && frame.iframe.contentWindow) frame.iframe.contentWindow.postMessage({ type: "lk-done" }, "*");
    }
  }

  // ---------- 탭: 범용 카드 (C60 M2b — 새 모듈의 기본 화면. 데이터 = {cards:[{title, icon, rows, text}]}) ----------
  async function showCards(tab) {
    var d = await api("/api/view/" + encodeURIComponent(tab));
    clear(view);
    ((d && d.cards) || []).forEach(function (c0) {
      var c = titledCard(c0.title, ICONS[c0.icon] ? c0.icon : null);
      if (c0.text) c.appendChild(el("pre", "body", c0.text));
      if (c0.rows && c0.rows.length) rows(c, c0.rows);
      view.appendChild(c);
    });
    if (!view.firstChild) message("아직 표시할 게 없어요.");
  }

  // ---------- 앱: 편지 · 소식 · 속마음 (C69 A3 — 데이터 = {items:[{id, turn, new, title, meta, body, entries}], total}) ----------
  // 카드 한 장 = 도착물 한 행, 최신 위. 안 본 행 = 제목 옆 "새" 칩. 규칙 §5c.
  function feedCard(it, iconName) {
    var c = el("section", "card feed");
    var h = el("h2", "card-title");
    h.appendChild(icon(iconName, 16));
    h.appendChild(el("span", "grow", it.title || ""));
    if (it.new) h.appendChild(el("span", "chip new", "새"));
    c.appendChild(h);
    if (it.meta) c.appendChild(el("p", "meta feed-meta", it.meta));
    if (it.body) c.appendChild(el("pre", "body", it.body));
    (it.entries || []).forEach(function (e) {
      var box = el("div", "mind-entry");
      if (e.name) box.appendChild(el("p", "mind-name", e.name));
      (e.lines || []).forEach(function (l) {
        var p = el("p", "mind-line");
        if (l.label) { var b = document.createElement("b"); b.textContent = l.label; p.appendChild(b); p.appendChild(document.createTextNode(" ")); }
        p.appendChild(document.createTextNode(String(l.text || "")));
        box.appendChild(p);
      });
      c.appendChild(box);
    });
    return c;
  }
  async function showFeed(tab) {
    var d = await api("/api/view/" + encodeURIComponent(tab));
    clear(view);
    var a = appOf(tab) || {};
    var ic = ICONS[a.icon] ? a.icon : "mail";
    var items = (d && d.items) || [];
    items.forEach(function (it) { view.appendChild(feedCard(it, ic)); });
    var more = ((d && d.total) || 0) - items.length;
    if (items.length && more > 0) view.appendChild(el("p", "state", "… 그 전 " + more + "개"));
    if (!view.firstChild) message("아직 온 게 없어요.");
  }

  // ---------- 탭: 고치기 (C60 M2c) ----------
  // 한 칸 = 편집 한 건. 연필 → 그 자리 입력칸 → Enter(확인창 없음). 응답의 data 로 다시 그린다(낙관 갱신 0).
  var editRev = null;   // 화면이 본 턴 번호 — 쓰기에 실어 보낸다(그 사이 턴이 지났으면 서버가 409 + 새 데이터)
  var receipt = null;   // {text, undoId, rev} — 방금 고친 것 한 줄 + ↶. 탭을 떠나거나 턴이 지나면 사라진다
  var notice = null;    // 실패·안내 한 줄(오류 색)
  var flash = null;     // 되돌린 뒤 한 번 보이는 한 줄
  var posting = false;
  // [C67 A1] 편집 모드 = 이 앱 몫 묶음만. 고르개 = 앱 선언의 edit(group key, 목록은 list:builtin / list:declared).
  //   null = 거르지 않음(옛 고치기 탭 꼴 — 지금은 서랍에 없다).
  var editFilter = null;
  function editSelector(g) {
    if (g.key === "list") return "list:" + (g.list && g.list.builtin ? "builtin" : "declared");
    return g.key;
  }
  function matchEdit(g) {
    if (!editFilter) return true;
    return editFilter.indexOf(editSelector(g)) >= 0 || editFilter.indexOf(g.key) >= 0;
  }

  function iconBtn(name, label, onClick, type) {
    var b = el("button", "ibtn");
    b.type = type || "button";
    b.title = label;
    b.setAttribute("aria-label", label);
    b.appendChild(icon(name, 16));
    if (onClick) b.addEventListener("click", onClick);
    return b;
  }
  function inputEl(kind, value, opt) {
    opt = opt || {};
    var i;
    if (kind === "select") {
      i = document.createElement("select");
      (opt.options || []).forEach(function (o) {
        var op = document.createElement("option");
        op.value = String(o);
        op.textContent = String(o);
        if (String(o) === String(value)) op.selected = true;
        i.appendChild(op);
      });
    } else {
      i = document.createElement("input");
      if (kind === "number") {
        i.type = "number"; i.inputMode = "numeric"; i.step = "1";
        if (opt.min !== undefined && opt.min !== null) i.min = String(opt.min);
        if (opt.max !== undefined && opt.max !== null) i.max = String(opt.max);
      } else {
        i.type = "text";
        if (opt.maxlength) i.maxLength = opt.maxlength;
      }
      if (value !== undefined && value !== null) i.value = String(value);
      if (opt.placeholder) i.placeholder = opt.placeholder;
    }
    if (opt.cls) i.className = opt.cls;
    if (opt.label) i.setAttribute("aria-label", opt.label);
    return i;
  }
  function unit(text) { return { el: el("span", "unit", text) }; }
  // 값 자리(dd)를 입력 꼴로 바꾼다. parts = [{el, key?}] — key 있는 칸만 값으로 모은다. Enter 제출 · Esc 취소.
  function editForm(dd, parts, onSubmit) {
    var keep = Array.prototype.slice.call(dd.childNodes);
    function restore() { clear(dd); keep.forEach(function (n) { dd.appendChild(n); }); }
    clear(dd);
    var f = el("form", "eform");
    parts.forEach(function (x) { f.appendChild(x.el); });
    f.appendChild(iconBtn("check", "적용", null, "submit"));
    f.appendChild(iconBtn("x", "취소", restore));
    f.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var v = {};
      parts.forEach(function (x) { if (x.key) v[x.key] = x.el.value; });
      onSubmit(v);
    });
    f.addEventListener("keydown", function (ev) { if (ev.key === "Escape") { ev.preventDefault(); restore(); } });
    dd.appendChild(f);
    var first = parts.filter(function (x) { return x.key; })[0];
    if (first) first.el.focus();
  }
  function editRow(dl, label, text, onEdit, extra) {
    var row = el("div", "row");
    row.appendChild(el("dt", null, label));
    var dd = el("dd");
    var line = el("div", "vrow");
    line.appendChild(el("span", "vtext", text));
    if (onEdit) line.appendChild(iconBtn("pencil", label + " 고치기", function () { onEdit(dd); }));
    (extra || []).forEach(function (b) { line.appendChild(b); });
    dd.appendChild(line);
    row.appendChild(dd);
    dl.appendChild(row);
  }
  function same(a, b) { return String(a === null || a === undefined ? "" : a) === String(b === null || b === undefined ? "" : b); }
  function pad2(n) { n = Number(n || 0); return (n < 10 ? "0" : "") + n; }

  async function submitEdit(action, args) {
    if (posting) return;
    posting = true;
    try {
      var res = await apiPost("/api/act", { action: action, args: args, rev: editRev });
      var b = res.body || {};
      if (res.status === 200 && b.ok) {
        notice = null;
        receipt = b.receipt ? { text: b.receipt, undoId: b.undo_id, rev: (b.data || {}).rev } : null;
        if (action === "undo") flash = b.line || "되돌렸어요.";
      } else if (res.status === 200) {
        notice = b.line || "바뀐 게 없어요.";
      } else {
        notice = b.message || ("고치기 실패 (" + res.status + ")");
        if (b.error === "stale") receipt = null;
      }
      if (b.data) renderEdit(b.data); else await showEdit();
    } catch (e) {
      notice = String((e && e.message) || e);
      try { await showEdit(); } catch (e2) { message(notice, true); }
    } finally {
      posting = false;
    }
  }

  function renderScalars(c, items) {
    var dl = el("dl", "rows");
    items.forEach(function (it) {
      var shown = it.kind === "number"
        ? (it.value === null || it.value === undefined ? "—" : String(it.value)) + (it.max !== null && it.max !== undefined ? " / " + it.max : "")
        : (it.value ? String(it.value) : "—");
      editRow(dl, it.label, shown, function (dd) {
        var inp = inputEl(it.kind, it.value, { min: it.min, max: it.max, options: it.options,
                                               maxlength: it.maxlength, label: it.label,
                                               cls: it.kind === "text" ? "grow" : null });
        editForm(dd, [{ el: inp, key: "value" }], function (v) {
          if (same(v.value, it.value)) { showEdit(); return; }
          submitEdit("decl.set", { name: it.name, npc: it.npc || "", value: v.value });
        });
      });
    });
    c.appendChild(dl);
  }

  function renderList(c, L) {
    var pct = L.mode === "progress";
    var hasFields = !!(L.fields && L.fields.length);
    function removeBtn(it) {
      return iconBtn("x", it.name + " 빼기", function () {
        submitEdit("decl.item", { name: L.name, item: it.name, op: "remove" });
      });
    }
    if (!hasFields) {
      var dl = el("dl", "rows");
      (L.items || []).forEach(function (it) {
        editRow(dl, it.name, (it.n === null || it.n === undefined ? "—" : String(it.n)) + (pct ? "%" : ""), function (dd) {
          editForm(dd, [{ el: inputEl("number", it.n, { min: 0, cls: "w-s", label: it.name }), key: "qty" }], function (v) {
            if (same(v.qty, it.n)) { showEdit(); return; }
            submitEdit("decl.item", { name: L.name, item: it.name, op: "set", qty: v.qty });
          });
        }, [removeBtn(it)]);
      });
      if (dl.firstChild) c.appendChild(dl);
    } else {
      // 레코드 목록 — 항목마다 머리줄(이름 + 빼기) 아래 필드 한 칸씩. 한 칸 = 편집 한 건(↶ 도 그 한 칸).
      (L.items || []).forEach(function (it) {
        var h = el("h3", null, it.name);
        h.appendChild(removeBtn(it));
        c.appendChild(h);
        var dl2 = el("dl", "rows");
        L.fields.forEach(function (f) {
          var cur = it.fields ? it.fields[f.name] : null;
          editRow(dl2, f.name, cur === null || cur === undefined ? "—" : String(cur), function (dd) {
            editForm(dd, [{ el: inputEl("number", cur, { min: f.min, max: f.max, cls: "w-s", label: f.name }), key: "qty" }], function (v) {
              if (same(v.qty, cur)) { showEdit(); return; }
              submitEdit("decl.item", { name: L.name, item: it.name, op: "set", field: f.name, qty: v.qty });
            });
          });
        });
        c.appendChild(dl2);
      });
    }
    if (!L.items || !L.items.length) c.appendChild(el("p", "state", "비어 있어요."));
    // 새 항목 — 이름(+ 수량). Enter 로 넣는다.
    var f = el("form", "eform add");
    var nm = inputEl("text", "", { placeholder: "새 항목", cls: "grow", label: L.name + " 새 항목", maxlength: 20 });
    f.appendChild(nm);
    var qty = null;
    if (!hasFields) {
      qty = inputEl("number", pct ? 0 : 1, { min: 0, cls: "w-s", label: "수량" });
      f.appendChild(qty);
    }
    f.appendChild(iconBtn("plus", "넣기", null, "submit"));
    f.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var n = nm.value.trim();
      if (!n) return;
      if (qty) submitEdit("decl.item", { name: L.name, item: n, op: "set", qty: qty.value });
      else submitEdit("decl.item", { name: L.name, item: n, op: "add" });
    });
    c.appendChild(f);
  }

  function renderRelations(c, g) {
    var dl = el("dl", "rows");
    var br = g.bond_range || [-100, 100], tr = g.tension_range || [0, 100];
    (g.relations || []).forEach(function (r) {
      editRow(dl, r.npc, "유대 " + r.bond + " · 긴장 " + r.tension, function (dd) {
        var b = inputEl("number", r.bond, { min: br[0], max: br[1], cls: "w-s", label: "유대" });
        var t = inputEl("number", r.tension, { min: tr[0], max: tr[1], cls: "w-s", label: "긴장" });
        editForm(dd, [unit("유대"), { el: b, key: "bond" }, unit("긴장"), { el: t, key: "tension" }], function (v) {
          var args = { npc: r.npc };
          if (!same(v.bond, r.bond)) args.bond = v.bond;
          if (!same(v.tension, r.tension)) args.tension = v.tension;
          if (args.bond === undefined && args.tension === undefined) { showEdit(); return; }
          submitEdit("rel.set", args);
        });
      });
    });
    c.appendChild(dl);
  }

  function renderWorld(c, g) {
    var k = g.clock || {};
    var dl = el("dl", "rows");
    var shown = (k.year || 1) + "년 " + (k.month || 1) + "월 " + (k.day || 1) + "일 " + pad2(k.hour) + ":" + pad2(k.minute)
      + (k.weather ? " · " + k.weather : "");
    editRow(dl, "시간", shown, function (dd) {
      var keys = ["year", "month", "day", "hour", "minute", "weather"];
      var ins = {
        year: inputEl("number", k.year, { min: 1, cls: "w-s", label: "년" }),
        month: inputEl("number", k.month, { min: 1, max: k.months, cls: "w-s", label: "월" }),
        day: inputEl("number", k.day, { min: 1, max: k.days, cls: "w-s", label: "일" }),
        hour: inputEl("number", k.hour, { min: 0, max: 23, cls: "w-s", label: "시" }),
        minute: inputEl("number", k.minute, { min: 0, max: 59, cls: "w-s", label: "분" }),
        weather: inputEl("text", k.weather, { cls: "grow", label: "날씨", placeholder: "날씨", maxlength: 40 })
      };
      editForm(dd, [{ el: ins.year, key: "year" }, unit("년"), { el: ins.month, key: "month" }, unit("월"),
                    { el: ins.day, key: "day" }, unit("일"), { el: ins.hour, key: "hour" }, unit(":"),
                    { el: ins.minute, key: "minute" }, { el: ins.weather, key: "weather" }], function (v) {
        var args = {};
        keys.forEach(function (key) { if (!same(v[key], k[key])) args[key] = v[key]; });
        if (!Object.keys(args).length) { showEdit(); return; }
        submitEdit("clock.set", args);
      });
    });
    editRow(dl, "장소", g.location || "—", function (dd) {
      editForm(dd, [{ el: inputEl("text", g.location, { cls: "grow", label: "장소", maxlength: 60 }), key: "location" }], function (v) {
        var loc = String(v.location || "").trim();
        if (!loc || loc === g.location) { showEdit(); return; }
        submitEdit("place.set", { location: loc });
      });
    });
    c.appendChild(dl);
  }

  function renderEdit(d) {
    clear(view);
    editRev = d ? d.rev : null;
    if (receipt && receipt.rev !== editRev) receipt = null;
    if (notice) {
      var p = el("p", "state error");
      p.appendChild(icon("alert", 16));
      p.appendChild(el("span", null, notice));
      view.appendChild(p);
    }
    if (flash) { view.appendChild(el("p", "state", flash)); flash = null; }
    if (receipt) {
      var rc = el("div", "receipt");
      rc.appendChild(icon("check", 16));
      rc.appendChild(el("span", "vtext", receipt.text));
      if (receipt.undoId) rc.appendChild(iconBtn("undo", "되돌리기", function () { submitEdit("undo", { id: receipt.undoId }); }));
      view.appendChild(rc);
    }
    var groups = ((d && d.groups) || []).filter(matchEdit);
    groups.forEach(function (g) {
      var c = card(g.title, g.icon || "layers");
      if (g.items) renderScalars(c, g.items);
      else if (g.list) renderList(c, g.list);
      else if (g.relations) renderRelations(c, g);
      else if (g.clock) renderWorld(c, g);
      view.appendChild(c);
    });
    if (!groups.length && !view.firstChild) message("고칠 값이 아직 없어요.");
  }
  async function showEdit() {
    var d = await api("/api/view/edit");
    renderEdit(d || {});
  }

  // ---------- 앱 껍데기 (C67 A1 — 홈 + 앱 두 층. 규칙 문서 §4) ----------
  //   홈 = 지금 카드 · 상태창 요약 · 앱 서랍(기본 → "확장" 구분선 → 확장). 홈에는 아래 바가 없다.
  //   앱 = 옛 렌더러 그대로. 폰은 아래 바 셋(뒤로 · 홈 · 편집 = bottom app bar), PC는 머리줄(뒤로 · 이름 · 편집 · 새로고침).
  //   뒤로 = 내부 스택만(편집 모드면 먼저 편집 끝). 브라우저 history · 디스코드 시스템 뒤로는 안 건드린다.
  //   앱 목록 = 서버 등록부(GET /api/tabs — drawer · kind · edit). 실패하면 아래 옛 넷.
  var apps = [{ tab: "status", label: "상태창", icon: "activity", renderer: "status", drawer: true, kind: "builtin",
                edit: ["pc", "values", "list:declared", "world"] },
              { tab: "archive", label: "보관함", icon: "archive", renderer: "archive", drawer: true, kind: "builtin",
                edit: ["list:builtin"] },
              { tab: "people", label: "인물", icon: "users", renderer: "people", drawer: true, kind: "builtin",
                edit: ["relations"] },
              { tab: "modules", label: "모듈", icon: "blocks", renderer: "parts", drawer: true, kind: "builtin", edit: [] }];
  var RENDER = { status: showStatus, archive: showArchive, people: showPeople, edit: showEdit,
                 feed: showFeed, parts: showParts, frame: showParts };   // [C73] frame = 코드 앱 HTML(같은 입구 — 쪽 모양이 가른다)
  var route = null;      // null = 홈
  var stack = [];        // 뒤로 = 이 스택만
  var editMode = false;

  function normApp(t) {
    return { tab: String(t.tab), label: String(t.label || t.tab), icon: String(t.icon || "layers"),
             renderer: String(t.renderer || "cards"), drawer: t.drawer !== false,
             kind: t.kind === "extension" ? "extension" : "builtin",
             edit: Array.isArray(t.edit) ? t.edit.map(String) : [] };
  }
  function appOf(tab) { return apps.filter(function (a) { return a.tab === tab; })[0] || null; }
  function canEdit(a) { return !!(a && a.edit && a.edit.length); }
  function resetEditState() { receipt = null; notice = null; flash = null;
                              partsReceipt = null; partsNotice = null; partsFlash = null; frame = null; partsApp = null; }

  // 머리줄 · 아래 바 — 버튼은 index.html 에 있고 아이콘·글자는 여기서 그린다.
  var bodyEl = document.body;
  var titleEl = document.getElementById("title");
  var navBack = document.getElementById("nav-back");
  var navEdit = document.getElementById("nav-edit");
  var refreshBtn = document.getElementById("refresh");
  var abBack = document.getElementById("ab-back");
  var abHome = document.getElementById("ab-home");
  var abEdit = document.getElementById("ab-edit");
  function iconOnly(b, name, size) { clear(b); b.appendChild(icon(name, size)); }
  function iconLabel(b, name, text) {
    clear(b);
    var wrap = el("span", "ab-icon");
    wrap.appendChild(icon(name, 22));
    b.appendChild(wrap);
    b.appendChild(el("span", "ab-label", text));
  }
  iconOnly(navBack, "arrow-left", 20);
  iconOnly(navEdit, "pencil", 18);
  iconOnly(refreshBtn, "refresh", 18);

  function drawChrome() {
    var a = route === null ? null : appOf(route);
    bodyEl.classList.toggle("at-home", route === null);
    bodyEl.classList.toggle("in-app", route !== null);
    clear(titleEl);
    titleEl.appendChild(el("span", null, a ? a.label : "홈"));
    if (a && editMode) titleEl.appendChild(el("span", "chip", "편집 모드"));
    var ok = canEdit(a);
    [navEdit, abEdit].forEach(function (b) {
      b.disabled = !ok;
      b.setAttribute("aria-pressed", editMode ? "true" : "false");
      b.classList.toggle("on", editMode);
      b.setAttribute("aria-label", editMode ? "편집 끝" : "편집");
      b.title = editMode ? "편집 끝" : "편집";
    });
    iconLabel(abBack, "arrow-left", "뒤로");
    iconLabel(abHome, "house", "홈");
    iconLabel(abEdit, "pencil", editMode ? "편집 끝" : "편집");
  }

  async function render() {
    try {
      if (route === null) { editFilter = null; await showHome(); }
      else if (editMode) { editFilter = (appOf(route) || {}).edit || []; await showEdit(); }
      else {
        editFilter = null;
        var a = appOf(route) || { renderer: route };
        if (RENDER[a.renderer]) await RENDER[a.renderer](route);   // [C69] feed 는 앱 tab 이 필요하다
        else await showCards(route);
      }
    } catch (e) {
      message(String((e && e.message) || e), true);
    }
  }
  async function openApp(tab) {
    if (route !== tab) stack.push(route);
    route = tab;
    editMode = false;
    resetEditState();
    drawChrome();
    window.scrollTo(0, 0);
    await render();
  }
  async function goHome() {
    stack = [];
    route = null;
    editMode = false;
    resetEditState();
    drawChrome();
    window.scrollTo(0, 0);
    await render();
  }
  async function goBack() {
    if (route === null) return;
    if (editMode) { await toggleEdit(); return; }       // 편집 모드가 한 단계다
    route = stack.length ? stack.pop() : null;
    editMode = false;
    resetEditState();
    drawChrome();
    window.scrollTo(0, 0);
    await render();
  }
  async function toggleEdit() {
    var a = route === null ? null : appOf(route);
    if (!canEdit(a)) return;
    editMode = !editMode;
    resetEditState();
    drawChrome();
    await render();
  }

  // ---------- 홈 ----------
  var badges = {};      // [C69 A3] 앱마다 안 본 행 수 — 홈을 그릴 때 /api/home 에서
  function badgeText(n) { return n >= 100 ? "99+" : String(n); }
  function tile(a) {
    var b = el("button", "tile");
    b.type = "button";
    b.setAttribute("data-app", a.tab);
    var ic = el("span", "tile-icon");
    ic.appendChild(icon(ICONS[a.icon] ? a.icon : "layers", 24));
    var n = Number(badges[a.tab] || 0);
    if (n > 0) {                       // 규칙 §4 배지 — 정보지 버튼이 아니다(누르면 타일 전체가 앱을 연다)
      var bd = el("span", "badge", badgeText(n));
      bd.setAttribute("aria-hidden", "true");
      ic.appendChild(bd);
      b.setAttribute("aria-label", a.label + ", 새 " + n);
    }
    b.appendChild(ic);
    b.appendChild(el("span", "tile-label", a.label));
    b.addEventListener("click", function () { openApp(a.tab); });
    return b;
  }
  function drawerEl() {
    var nav = el("nav", "drawer");
    nav.setAttribute("aria-label", "앱");
    var shown = apps.filter(function (a) { return a.drawer !== false; });
    var basic = shown.filter(function (a) { return a.kind !== "extension"; });
    var ext = shown.filter(function (a) { return a.kind === "extension"; });
    basic.forEach(function (a) { nav.appendChild(tile(a)); });
    if (ext.length) {
      var dv = el("div", "drawer-div");
      dv.appendChild(el("span", null, "확장"));
      dv.appendChild(el("span", "rule"));
      nav.appendChild(dv);
      ext.forEach(function (a) { nav.appendChild(tile(a)); });
    }
    return nav;
  }
  // [C69 A3] 이번 턴 줄 — 라벨 + 칩(아이콘 + 앱 이름 + 수). 칩을 누르면 그 앱. 규칙 §4.
  function thisTurnEl(list) {
    var line = el("div", "this-turn");
    line.appendChild(el("span", "tt-label", "이번 턴"));
    list.forEach(function (x) {
      var a = appOf(x.app);
      var b = el("button", "tt-chip");
      b.type = "button";
      b.setAttribute("data-app", x.app);
      b.appendChild(icon(ICONS[a.icon] ? a.icon : "layers", 14));
      b.appendChild(el("span", null, a.label + (x.count ? " " + x.count : "")));
      b.addEventListener("click", function () { openApp(x.app); });
      line.appendChild(b);
    });
    return line;
  }
  async function showHome() {
    var d = null, failed = false;
    try { d = await api("/api/home"); } catch (e) { failed = true; }   // 실패 → 서랍만
    clear(view);
    badges = (d && d.badges) || {};
    var wrap = el("div", "home");
    var side = el("div", "home-side");
    var now = d && d.now;
    var tt = ((d && d.this_turn) || []).filter(function (x) { return appOf(x.app); });
    if ((now && ((now.rows && now.rows.length) || now.footer)) || tt.length) {
      var c = el("section", "card now");
      if (now && now.footer) {
        var f = el("p", "meta");
        f.appendChild(icon("clock", 14));
        f.appendChild(el("span", null, now.footer));
        c.appendChild(f);
      }
      if (now && now.rows && now.rows.length) rows(c, now.rows);
      if (tt.length) c.appendChild(thisTurnEl(tt));
      side.appendChild(c);
    }
    var sum = (d && d.summary) || [];
    if (sum.length && appOf("status")) {
      var sb = el("button", "card summary");
      sb.type = "button";
      var h = el("span", "card-title");
      h.appendChild(icon("activity", 16));
      h.appendChild(el("span", "grow", appOf("status").label));
      h.appendChild(icon("chevron-right", 16));
      sb.appendChild(h);
      sum.forEach(function (line) { sb.appendChild(el("span", "sum-line", line)); });
      sb.addEventListener("click", function () { openApp("status"); });
      side.appendChild(sb);
    }
    if (!side.firstChild && !failed) side.appendChild(el("p", "state", "아직 표시할 상태가 없어요."));
    if (side.firstChild) wrap.appendChild(side);
    else wrap.classList.add("solo");
    wrap.appendChild(drawerEl());
    view.appendChild(wrap);
  }

  async function loadApps() {
    try {
      var d = await api("/api/tabs");
      if (d && d.tabs && d.tabs.length) apps = d.tabs.map(normApp);
    } catch (e) { /* 옛 넷 그대로 */ }
  }

  navBack.addEventListener("click", function () { goBack(); });
  abBack.addEventListener("click", function () { goBack(); });
  abHome.addEventListener("click", function () { goHome(); });
  navEdit.addEventListener("click", function () { toggleEdit(); });
  abEdit.addEventListener("click", function () { toggleEdit(); });
  refreshBtn.addEventListener("click", function () { render(); });
  drawChrome();

  connect().then(function () { message("불러오는 중…"); return loadApps(); })
    .then(function () { return goHome(); })
    .catch(function (e) { message(String((e && e.message) || e), true); });
})();
