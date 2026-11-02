import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

const ROUTES = ["queue", "patients", "model", "insights"];

export function useHashRoute() {
  const read = () => {
    const r = window.location.hash.replace(/^#\/?/, "").split("?")[0];
    return ROUTES.includes(r) ? r : "queue";
  };
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const on = () => setRoute(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const go = useCallback((r) => {
    window.location.hash = `/${r}`;
  }, []);
  return [route, go];
}

export function useTheme() {
  const initial = () => {
    try {
      const saved = localStorage.getItem("pulse-theme");
      if (saved === "light" || saved === "dark") return saved;
    } catch {
      /* storage unavailable */
    }
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  };
  const [theme, setTheme] = useState(initial);
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("pulse-theme", theme);
    } catch {
      /* storage unavailable */
    }
  }, [theme]);
  return [theme, () => setTheme((t) => (t === "dark" ? "light" : "dark"))];
}

export function useInterval(fn, ms, enabled = true) {
  const saved = useRef(fn);
  useEffect(() => {
    saved.current = fn;
  }, [fn]);
  useEffect(() => {
    if (!enabled || !ms) return undefined;
    const id = setInterval(() => saved.current(), ms);
    return () => clearInterval(id);
  }, [ms, enabled]);
}

export function useDebounced(value, ms) {
  const [v, setV] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setV(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return v;
}

export function useNow(ms = 30000) {
  const [now, setNow] = useState(Date.now());
  useInterval(() => setNow(Date.now()), ms);
  return now;
}

const reduceMotion = () =>
  window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/**
 * FLIP animation for lists that re-rank. Children need data-flip-key.
 * When the order changes, rows glide from their old position to the new one.
 */
export function useFlip(containerRef, deps) {
  const positions = useRef(new Map());
  useLayoutEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const nodes = el.querySelectorAll("[data-flip-key]");
    const next = new Map();
    nodes.forEach((n) => next.set(n.dataset.flipKey, n.getBoundingClientRect().top));
    if (!reduceMotion()) {
      nodes.forEach((n) => {
        const prev = positions.current.get(n.dataset.flipKey);
        const now = next.get(n.dataset.flipKey);
        if (prev === undefined || Math.abs(prev - now) < 1) return;
        n.style.transition = "none";
        n.style.transform = `translateY(${prev - now}px)`;
        requestAnimationFrame(() => {
          n.style.transition = "transform 420ms cubic-bezier(.2,.7,.2,1)";
          n.style.transform = "";
        });
      });
    }
    positions.current = next;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}

export function useToasts() {
  const [toasts, setToasts] = useState([]);
  const push = useCallback((t) => {
    const id = Math.random().toString(36).slice(2);
    const toast = { id, tone: "info", ttl: 5200, ...(typeof t === "string" ? { title: t } : t) };
    setToasts((all) => [...all.slice(-3), toast]);
    setTimeout(() => setToasts((all) => all.filter((x) => x.id !== id)), toast.ttl);
  }, []);
  const dismiss = useCallback((id) => setToasts((all) => all.filter((x) => x.id !== id)), []);
  return [toasts, push, dismiss];
}
