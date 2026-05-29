/**
 * swipe_logic.js — front-end for the Telegram Web App study view.
 *
 * Lifecycle
 * ---------
 *  1. Telegram.WebApp.ready() + expand to full height.
 *  2. Fetch the first word from GET /api/next-word.
 *  3. Listen for swipe gestures on .word-card (touch + mouse).
 *  4. On a successful swipe → POST /api/swipe with direction, then load next.
 *
 * All API calls send X-Telegram-InitData so the server can authenticate
 * the user. In local dev mode (WEBAPP_DEV_USER_ID env var on the server)
 * the server ignores the header and trusts the dev user id.
 */

(function () {
    "use strict";

    const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
    if (tg) {
        tg.ready();
        tg.expand();
        // Match the dark palette in the Telegram client chrome.
        try { tg.setHeaderColor && tg.setHeaderColor("#0F172A"); } catch (_) { /* noop */ }
        try { tg.setBackgroundColor && tg.setBackgroundColor("#0F172A"); } catch (_) { /* noop */ }
    }

    const INIT_DATA = tg ? (tg.initData || "") : "";
    const RING_CIRCUMFERENCE = 326.7;
    const RING_GOAL = 30;  // matches the "Today: X / 30" target

    /** @type {{ vocabId: number|null, word: string|null }} */
    const state = { vocabId: null, word: null, busy: false };

    // ----- DOM refs --------------------------------------------------------
    const elCard = document.getElementById("word-card");
    const elWordText = document.getElementById("word-text");
    const elWordIpa = document.getElementById("word-ipa");
    const elWordsLearned = document.getElementById("words-learned");
    const elTodayCount = document.getElementById("today-count");
    const elRing = document.getElementById("ring-progress");
    const elToast = document.getElementById("toast");
    const elAudio = document.getElementById("audio-btn");

    // ----- Helpers ---------------------------------------------------------
    function showToast(msg, isError) {
        elToast.textContent = msg;
        elToast.classList.toggle("toast-error", !!isError);
        elToast.classList.add("toast-visible");
        clearTimeout(showToast._t);
        showToast._t = setTimeout(() => {
            elToast.classList.remove("toast-visible");
        }, 2200);
    }

    function setStats(user) {
        elWordsLearned.textContent = user.words_learned;
        elTodayCount.textContent = user.words_swiped_today;
        const pct = Math.max(0, Math.min(1, user.words_swiped_today / RING_GOAL));
        elRing.style.strokeDashoffset = String(RING_CIRCUMFERENCE * (1 - pct));
    }

    async function api(path, options) {
        const res = await fetch(path, Object.assign({
            headers: Object.assign({
                "Content-Type": "application/json",
                "X-Telegram-InitData": INIT_DATA,
            }, options && options.headers)
        }, options));
        if (!res.ok) {
            let detail = res.statusText;
            try {
                const body = await res.json();
                if (body && body.detail) detail = body.detail;
            } catch (_) { /* ignore */ }
            throw new Error(`HTTP ${res.status}: ${detail}`);
        }
        return res.json();
    }

    function setCardWord(text) {
        elWordText.textContent = text;
        elWordIpa.textContent = "";  // IPA support is a future addition
    }

    async function loadNextWord() {
        try {
            const data = await api("/api/next-word", { method: "GET" });
            state.vocabId = data.vocab_id;
            state.word = data.word.text;
            setCardWord(data.word.text);
            setStats(data.user);
            elCard.style.transform = "";
            elCard.style.opacity = "";
            elCard.classList.remove("swipe-out-left", "swipe-out-right", "swipe-out-up");
        } catch (err) {
            console.error(err);
            setCardWord("All caught up!");
            showToast(err.message, true);
            state.vocabId = null;
        }
    }

    async function commitSwipe(direction) {
        if (state.busy || state.vocabId == null) return;
        state.busy = true;

        // Visual swipe-out
        const outClass =
            direction === "left" ? "swipe-out-left" :
            direction === "right" ? "swipe-out-right" :
            "swipe-out-up";
        elCard.classList.add(outClass);
        if (tg && tg.HapticFeedback) {
            try { tg.HapticFeedback.impactOccurred("light"); } catch (_) { /* noop */ }
        }

        const vocabId = state.vocabId;
        state.vocabId = null;

        try {
            const data = await api("/api/swipe", {
                method: "POST",
                body: JSON.stringify({ vocab_id: vocabId, direction: direction }),
            });
            setStats(data.user);
        } catch (err) {
            console.error(err);
            showToast(err.message, true);
        } finally {
            // Allow the swipe-out animation to play, then load the next word.
            setTimeout(async () => {
                await loadNextWord();
                state.busy = false;
            }, 260);
        }
    }

    // ----- Gesture detection ----------------------------------------------
    const SWIPE_THRESHOLD_X = 90;   // px before commit on horizontal
    const SWIPE_THRESHOLD_Y = 80;   // px before commit on vertical (up only)
    const ROTATION_FACTOR = 0.05;   // deg per px

    let drag = null;

    function pointerXY(e) {
        if (e.touches && e.touches.length) {
            return { x: e.touches[0].clientX, y: e.touches[0].clientY };
        }
        if (e.changedTouches && e.changedTouches.length) {
            return { x: e.changedTouches[0].clientX, y: e.changedTouches[0].clientY };
        }
        return { x: e.clientX, y: e.clientY };
    }

    function onPointerDown(e) {
        if (state.busy || state.vocabId == null) return;
        const p = pointerXY(e);
        drag = { startX: p.x, startY: p.y, dx: 0, dy: 0 };
        elCard.classList.add("dragging");
    }

    function onPointerMove(e) {
        if (!drag) return;
        const p = pointerXY(e);
        drag.dx = p.x - drag.startX;
        drag.dy = p.y - drag.startY;
        const rotate = drag.dx * ROTATION_FACTOR;
        elCard.style.transform = `translate(${drag.dx}px, ${Math.min(0, drag.dy)}px) rotate(${rotate}deg)`;
        e.preventDefault && e.preventDefault();
    }

    function onPointerUp() {
        if (!drag) return;
        elCard.classList.remove("dragging");

        const { dx, dy } = drag;
        drag = null;

        const absX = Math.abs(dx);
        const absY = Math.abs(dy);

        // Up swipe wins only if vertical motion clearly dominates and is upward.
        if (-dy > SWIPE_THRESHOLD_Y && absY > absX) {
            commitSwipe("up");
            return;
        }
        if (dx > SWIPE_THRESHOLD_X) {
            commitSwipe("right");
            return;
        }
        if (-dx > SWIPE_THRESHOLD_X) {
            commitSwipe("left");
            return;
        }

        // Below threshold — spring back.
        elCard.style.transform = "";
    }

    elCard.addEventListener("touchstart", onPointerDown, { passive: false });
    elCard.addEventListener("touchmove", onPointerMove, { passive: false });
    elCard.addEventListener("touchend", onPointerUp);
    elCard.addEventListener("touchcancel", onPointerUp);

    elCard.addEventListener("mousedown", onPointerDown);
    window.addEventListener("mousemove", (e) => { if (drag) onPointerMove(e); });
    window.addEventListener("mouseup", onPointerUp);

    // Keyboard shortcuts (handy on desktop / Telegram Desktop)
    window.addEventListener("keydown", (e) => {
        // Only act while the STUDY view is the visible one — the Words-tab
        // runner has its own keyboard handling.
        const study = document.getElementById("view-study");
        if (study && study.hidden) return;
        if (state.busy || state.vocabId == null) return;
        if (e.key === "ArrowLeft") commitSwipe("left");
        else if (e.key === "ArrowRight") commitSwipe("right");
        else if (e.key === "ArrowUp") commitSwipe("up");
    });

    // Audio button is a stub for now; wire up to a TTS endpoint later.
    if (elAudio) {
        elAudio.addEventListener("click", (e) => {
            e.stopPropagation();
            showToast("Audio coming soon");
        });
    }

    // Kick off
    loadNextWord();
})();
