/**
 * words_tab.js — bottom-nav router + the CEFR level catalog ("Words" tab).
 *
 * Screens inside the WORDS view:
 *   1. Level grid   — A1 / A2 / B1 / B2 entry points.
 *   2. Block list   — vertically scrollable blocks (30-50 words) with rings.
 *   3. Runner       — flashcard session for a block + completion summary with
 *                     a "Repeat Missed / Uncertain Words" retry loop.
 *
 * Block mode is fully independent of the STUDY (SRS) loop in swipe_logic.js;
 * it talks to /api/levels, /api/levels/{lvl}/blocks and /api/blocks/...
 */

(function () {
    "use strict";

    const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
    const INIT_DATA = tg ? (tg.initData || "") : "";
    const RING_C = 326.7;             // 2*pi*52, matches the SVG ring radius

    // ----- generic API helper (mirrors swipe_logic.js) ---------------------
    async function api(path, options) {
        const res = await fetch(path, Object.assign({
            headers: Object.assign({
                "Content-Type": "application/json",
                "X-Telegram-InitData": INIT_DATA,
            }, options && options.headers)
        }, options));
        if (!res.ok) {
            let detail = res.statusText;
            try { const b = await res.json(); if (b && b.detail) detail = b.detail; } catch (_) { /* */ }
            throw new Error(`HTTP ${res.status}: ${detail}`);
        }
        return res.json();
    }

    function toast(msg, isError) {
        const el = document.getElementById("toast");
        if (!el) return;
        el.textContent = msg;
        el.classList.toggle("toast-error", !!isError);
        el.classList.add("toast-visible");
        clearTimeout(toast._t);
        toast._t = setTimeout(() => el.classList.remove("toast-visible"), 2200);
    }

    function haptic(kind) {
        if (tg && tg.HapticFeedback) {
            try { tg.HapticFeedback.impactOccurred(kind || "light"); } catch (_) { /* */ }
        }
    }

    function setRing(el, done, total) {
        const pct = total > 0 ? Math.max(0, Math.min(1, done / total)) : 0;
        el.style.strokeDashoffset = String(RING_C * (1 - pct));
    }

    // ----- DOM refs --------------------------------------------------------
    const views = {
        study: document.getElementById("view-study"),
        words: document.getElementById("view-words"),
        stub: document.getElementById("view-stub"),
    };
    const navButtons = Array.prototype.slice.call(document.querySelectorAll(".nav-btn[data-tab]"));

    const elLevelGrid = document.getElementById("level-grid");
    const elBlockList = document.getElementById("block-list");
    const screens = {
        levels: document.getElementById("words-levels"),
        blocks: document.getElementById("words-blocks"),
        runner: document.getElementById("words-runner"),
    };
    const elBlocksTitle = document.getElementById("blocks-title");
    const elRunnerTitle = document.getElementById("runner-title");
    const elRunnerWord = document.getElementById("runner-word");
    const elRunnerCard = document.getElementById("runner-card");
    const elRunnerRing = document.getElementById("runner-ring-fg");
    const elRunnerCount = document.getElementById("runner-count");
    const elRunnerTotal = document.getElementById("runner-total");
    const elSummary = document.getElementById("runner-summary");
    const elSumKnew = document.getElementById("sum-knew");
    const elSumConfusing = document.getElementById("sum-confusing");
    const elSumLearning = document.getElementById("sum-learning");
    const elRetryBtn = document.getElementById("retry-btn");

    // ----- view / tab routing ---------------------------------------------
    const REAL_VIEW = { study: "study", words: "words" };

    function showView(name) {
        Object.values(views).forEach((v) => { if (v) v.hidden = true; });
        if (views[name]) views[name].hidden = false;
    }

    function activateTab(tab) {
        navButtons.forEach((b) => b.classList.toggle("nav-btn-active", b.dataset.tab === tab));
        const view = REAL_VIEW[tab];
        if (view) {
            showView(view);
            if (view === "words") openLevels();
        } else {
            const nameEl = document.getElementById("stub-name");
            if (nameEl) nameEl.textContent = (tab || "").toUpperCase();
            showView("stub");
        }
    }

    navButtons.forEach((btn) => {
        btn.addEventListener("click", () => activateTab(btn.dataset.tab));
    });

    // ----- words-tab internal screen routing -------------------------------
    function showScreen(name) {
        Object.values(screens).forEach((s) => { if (s) s.hidden = true; });
        if (screens[name]) screens[name].hidden = false;
    }

    // ----- screen 1: level grid -------------------------------------------
    async function openLevels() {
        showScreen("levels");
        elLevelGrid.innerHTML = '<div class="words-loading">Loading…</div>';
        try {
            const levels = await api("/api/levels", { method: "GET" });
            elLevelGrid.innerHTML = "";
            levels.forEach((lv) => elLevelGrid.appendChild(levelCard(lv)));
        } catch (err) {
            elLevelGrid.innerHTML = "";
            toast(err.message, true);
        }
    }

    function levelCard(lv) {
        const card = document.createElement("button");
        card.className = "level-card";
        card.innerHTML = `
            <div class="mini-ring">
                <svg viewBox="0 0 120 120"><circle class="ring-bg" cx="60" cy="60" r="52"></circle>
                <circle class="ring-fg" cx="60" cy="60" r="52"></circle></svg>
                <span class="mini-ring-label">${lv.level}</span>
            </div>
            <div class="level-meta">
                <div class="level-name">${lv.level}</div>
                <div class="level-sub">${lv.blocks_completed}/${lv.blocks_total} blocks · ${lv.total_words} words</div>
            </div>`;
        setRing(card.querySelector(".ring-fg"), lv.blocks_completed, lv.blocks_total);
        card.addEventListener("click", () => openBlocks(lv.level));
        return card;
    }

    // ----- screen 2: block list -------------------------------------------
    async function openBlocks(level) {
        showScreen("blocks");
        elBlocksTitle.textContent = `Level ${level}`;
        elBlockList.innerHTML = '<div class="words-loading">Loading…</div>';
        try {
            const blocks = await api(`/api/levels/${level}/blocks`, { method: "GET" });
            elBlockList.innerHTML = "";
            blocks.forEach((b) => elBlockList.appendChild(blockCard(level, b)));
        } catch (err) {
            elBlockList.innerHTML = "";
            toast(err.message, true);
        }
    }

    function blockCard(level, b) {
        const card = document.createElement("button");
        card.className = "block-card" + (b.completed ? " block-card-done" : "");
        card.innerHTML = `
            <div class="mini-ring">
                <svg viewBox="0 0 120 120"><circle class="ring-bg" cx="60" cy="60" r="52"></circle>
                <circle class="ring-fg" cx="60" cy="60" r="52"></circle></svg>
                <span class="mini-ring-label">${b.completed_count}/${b.word_count}</span>
            </div>
            <div class="block-meta">
                <div class="block-name">Block ${b.block_index + 1}${b.completed ? " ✓" : ""}</div>
                <div class="block-sub">${b.word_count} words</div>
            </div>`;
        setRing(card.querySelector(".ring-fg"), b.completed_count, b.word_count);
        card.addEventListener("click", () => startRunner(level, b.block_index));
        return card;
    }

    // ----- screen 3: flashcard runner -------------------------------------
    const runner = {
        level: null, blockIndex: null, words: [], pos: 0, total: 0, busy: false, mode: "full",
    };

    async function startRunner(level, blockIndex, mode) {
        runner.level = level;
        runner.blockIndex = blockIndex;
        runner.mode = mode || "full";
        showScreen("runner");
        elSummary.hidden = true;
        elRunnerCard.hidden = false;
        elRunnerTitle.textContent = `Level ${level} · Block ${blockIndex + 1}` +
            (runner.mode === "retry" ? " · Review" : "");
        elRunnerWord.textContent = "Loading…";
        try {
            const data = await api(`/api/blocks/${level}/${blockIndex}?mode=${runner.mode}`, { method: "GET" });
            runner.words = data.words;
            runner.total = data.words.length;
            runner.pos = 0;
            elRunnerTotal.textContent = String(runner.total);
            if (runner.total === 0) { finishBlock(); return; }
            renderCard();
        } catch (err) {
            toast(err.message, true);
        }
    }

    function renderCard() {
        const w = runner.words[runner.pos];
        elRunnerWord.textContent = w ? w.text : "";
        elRunnerCount.textContent = String(runner.pos);
        setRing(elRunnerRing, runner.pos, runner.total);
        elRunnerCard.style.transform = "";
        elRunnerCard.style.opacity = "";
        elRunnerCard.classList.remove("swipe-out-left", "swipe-out-right", "swipe-out-up");
    }

    async function commitRunnerSwipe(direction) {
        if (runner.busy || runner.pos >= runner.total) return;
        runner.busy = true;
        const word = runner.words[runner.pos];

        const outClass = direction === "left" ? "swipe-out-left"
            : direction === "right" ? "swipe-out-right" : "swipe-out-up";
        elRunnerCard.classList.add(outClass);
        haptic("light");

        try {
            await api(`/api/blocks/${runner.level}/${runner.blockIndex}/swipe`, {
                method: "POST",
                body: JSON.stringify({ word_id: word.id, direction: direction }),
            });
        } catch (err) {
            toast(err.message, true);
        }

        setTimeout(() => {
            runner.pos += 1;
            elRunnerCount.textContent = String(runner.pos);
            setRing(elRunnerRing, runner.pos, runner.total);
            if (runner.pos >= runner.total) {
                finishBlock();
            } else {
                renderCard();
            }
            runner.busy = false;
        }, 240);
    }

    async function finishBlock() {
        elRunnerCard.hidden = true;
        try {
            const data = await api(`/api/blocks/${runner.level}/${runner.blockIndex}/complete`, { method: "POST" });
            const s = data.summary;
            elSumKnew.textContent = String(s.knew);
            elSumConfusing.textContent = String(s.confusing);
            elSumLearning.textContent = String(s.learning);
            elRetryBtn.hidden = !data.has_retry;
            elSummary.hidden = false;
        } catch (err) {
            toast(err.message, true);
        }
    }

    // ----- runner gestures (drag + buttons + keyboard) --------------------
    const THRESH_X = 90, THRESH_Y = 80, ROT = 0.05;
    let drag = null;

    function xy(e) {
        if (e.touches && e.touches.length) return { x: e.touches[0].clientX, y: e.touches[0].clientY };
        if (e.changedTouches && e.changedTouches.length) return { x: e.changedTouches[0].clientX, y: e.changedTouches[0].clientY };
        return { x: e.clientX, y: e.clientY };
    }
    function down(e) {
        if (runner.busy || runner.pos >= runner.total) return;
        const p = xy(e); drag = { sx: p.x, sy: p.y, dx: 0, dy: 0 };
        elRunnerCard.classList.add("dragging");
    }
    function move(e) {
        if (!drag) return;
        const p = xy(e); drag.dx = p.x - drag.sx; drag.dy = p.y - drag.sy;
        elRunnerCard.style.transform = `translate(${drag.dx}px, ${Math.min(0, drag.dy)}px) rotate(${drag.dx * ROT}deg)`;
        if (e.preventDefault) e.preventDefault();
    }
    function up() {
        if (!drag) return;
        elRunnerCard.classList.remove("dragging");
        const { dx, dy } = drag; drag = null;
        if (-dy > THRESH_Y && Math.abs(dy) > Math.abs(dx)) return commitRunnerSwipe("up");
        if (dx > THRESH_X) return commitRunnerSwipe("right");
        if (-dx > THRESH_X) return commitRunnerSwipe("left");
        elRunnerCard.style.transform = "";
    }

    elRunnerCard.addEventListener("touchstart", down, { passive: false });
    elRunnerCard.addEventListener("touchmove", move, { passive: false });
    elRunnerCard.addEventListener("touchend", up);
    elRunnerCard.addEventListener("touchcancel", up);
    elRunnerCard.addEventListener("mousedown", down);
    window.addEventListener("mousemove", (e) => { if (drag) move(e); });
    window.addEventListener("mouseup", up);

    document.querySelectorAll(".swipe-btn[data-dir]").forEach((btn) => {
        btn.addEventListener("click", () => commitRunnerSwipe(btn.dataset.dir));
    });

    window.addEventListener("keydown", (e) => {
        if (views.words.hidden || screens.runner.hidden || !elSummary.hidden) return;
        if (e.key === "ArrowLeft") commitRunnerSwipe("left");
        else if (e.key === "ArrowRight") commitRunnerSwipe("right");
        else if (e.key === "ArrowUp") commitRunnerSwipe("up");
    });

    // ----- navigation buttons ---------------------------------------------
    document.getElementById("blocks-back").addEventListener("click", openLevels);
    document.getElementById("runner-back").addEventListener("click", () => openBlocks(runner.level));
    document.getElementById("summary-done").addEventListener("click", () => openBlocks(runner.level));
    elRetryBtn.addEventListener("click", () => startRunner(runner.level, runner.blockIndex, "retry"));
})();
