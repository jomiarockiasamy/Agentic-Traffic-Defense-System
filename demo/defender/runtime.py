import json
import os
import time
from collections import Counter, deque
from threading import Lock, Thread


def client_ip_for_defense(headers, remote_addr: str | None) -> str:
    """Pick the client identity used for per-client metrics and limits.

    When ``TRUST_X_FORWARDED_FOR=1`` (default), use the first hop in
    ``X-Forwarded-For`` so tools like Locust can send many *simulated* IPs from
    one TCP connection. Otherwise use the real TCP peer (``remote_addr``).

    Set ``TRUST_X_FORWARDED_FOR=0`` behind an edge that does not strip or set
    XFF safely.
    """
    if os.getenv("TRUST_X_FORWARDED_FOR", "1") == "1":
        xff = ""
        if headers is not None:
            xff = (headers.get("X-Forwarded-For") or headers.get("X-Forwarded-for") or "").strip()
        if xff:
            return xff.split(",")[0].strip()
    addr = (remote_addr or "").strip()
    return addr if addr else "unknown"


def _classify_volume_attack(
    avg_rps: int,
    threshold: int,
    stats: dict,
) -> tuple[str, str]:
    """Label high-volume scenarios using simulated client IDs (e.g. X-Forwarded-For)."""
    if avg_rps <= threshold:
        return "none", ""

    uniq = int(stats.get("unique_ips", 0))
    top_share = float(stats.get("top_share", 0.0))
    sampled = int(stats.get("sampled_requests", 0))
    top_ip = str(stats.get("top_ip", ""))

    dist_min = int(os.getenv("ATTACK_DIST_MIN_IPS", "15"))
    dist_max_share = float(os.getenv("ATTACK_DIST_MAX_TOP_SHARE", "0.38"))
    single_min_share = float(os.getenv("ATTACK_SINGLE_MIN_TOP_SHARE", "0.42"))

    if sampled < 15:
        label = f"sustained_high_rps(avg_5s={avg_rps}, samples={sampled})"
        return "sustained_high_rps", label

    if uniq >= dist_min and top_share <= dist_max_share:
        label = (
            f"distributed_flood(avg_5s={avg_rps}, uniq={uniq}, "
            f"top_share={top_share:.2f}, top={top_ip})"
        )
        return "distributed_flood", label

    if top_share >= single_min_share or (uniq <= 3 and sampled >= 30):
        label = (
            f"single_client_flood(avg_5s={avg_rps}, uniq={uniq}, "
            f"top_share={top_share:.2f}, top={top_ip})"
        )
        return "single_client_flood", label

    label = f"mixed_high_volume(avg_5s={avg_rps}, uniq={uniq}, top_share={top_share:.2f})"
    return "mixed_high_volume", label


def _anthropic_client():
    try:
        import anthropic  # type: ignore

        key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if not key:
            return None
        return anthropic.Anthropic(api_key=key)
    except Exception:
        return None


class Defender:
    """Hackathon-friendly defense + metrics runtime.

    Owns:
    - per-second rolling metrics window (last 60s)
    - real rate limiting (429) via max_rps
    - AI loop that emits "tool calls" and executes them
    - action logs for UI visibility
    """

    METRICS_WINDOW_SECONDS = 60

    def __init__(self) -> None:
        self._lock = Lock()

        # Active requests (load indicator)
        self.active_requests = 0
        self._active_lock = Lock()

        # Global counters (lifetime)
        self.request_count = 0
        self.allowed_count = 0
        self.blocked_count = 0

        # STEP 2 — rolling graph window (last 60 seconds)
        self.metrics_window = deque(maxlen=self.METRICS_WINDOW_SECONDS)

        # STEP 3 — per-second counters (reset by collector)
        self._current_sec = int(time.time())
        self._sec_total = 0
        self._sec_allowed = 0
        self._sec_blocked = 0

        # Defense state
        self.max_rps = int(os.getenv("MAX_RPS", "250"))
        # Per-client cap (current second). 0 = disabled (global max_rps only).
        self.max_rps_per_ip = int(os.getenv("MAX_RPS_PER_IP", "0"))
        self._sec_ip_counts: dict[str, int] = {}
        self.blocked_ip_count = 0  # placeholder; can become real blocklist later
        # Rolling (ts_sec, client_id) for top-IP stats (last ~60s, trimmed on write)
        self._ip_ring: deque[tuple[int, str]] = deque()

        # AI state + logs
        self.ai_mode = os.getenv("AI_MODE", "rules").strip().lower()  # rules | claude | claude_stub
        self.ai_last_source = "none"  # claude | rules | none
        self.attack_detected = False
        self.attack_type = "none"  # none | sustained_high_rps | distributed_flood | single_client_flood | mixed_high_volume
        self.attack_profile: dict = {}  # last snapshot when attack_detected (unique_ips, top_share, …)
        self.suspicious_pattern = ""
        self.recovery_state = "stable"  # stable | degrading | recovering
        self.ai_logs = deque(maxlen=30)
        self.tool_actions = deque(maxlen=30)

        # Background threads
        self._threads_started = False
        self._threads_lock = Lock()

    # ---------------------------
    # Internals: metrics rollover
    # ---------------------------
    def _rollover_to(self, now_sec: int) -> None:
        if now_sec <= self._current_sec:
            return
        while self._current_sec < now_sec:
            self.metrics_window.append(
                {
                    "ts": self._current_sec,
                    "rps": self._sec_total,
                    "allowed": self._sec_allowed,
                    "blocked": self._sec_blocked,
                }
            )
            self._current_sec += 1
            self._sec_total = 0
            self._sec_allowed = 0
            self._sec_blocked = 0
            self._sec_ip_counts.clear()

    def _compute_client_stats(self, now_sec: int) -> dict:
        """Aggregate simulated clients in the same ~60s window as top_clients."""
        win_start = now_sec - (self.METRICS_WINDOW_SECONDS - 1)
        ips = [ip for ts, ip in self._ip_ring if ts >= win_start]
        if not ips:
            return {
                "unique_ips": 0,
                "sampled_requests": 0,
                "top_ip": "",
                "top_client_requests": 0,
                "top_share": 0.0,
            }
        cnt = Counter(ips)
        top_ip, top_n = cnt.most_common(1)[0]
        total = len(ips)
        return {
            "unique_ips": len(cnt),
            "sampled_requests": total,
            "top_ip": top_ip,
            "top_client_requests": top_n,
            "top_share": top_n / total if total else 0.0,
        }

    def _metrics_collector_loop(self) -> None:
        while True:
            time.sleep(1.0)
            now_sec = int(time.time())
            with self._lock:
                self._rollover_to(now_sec)

    # ---------------------------
    # AI + tool calls
    # ---------------------------
    def _ai_log(
        self,
        detected: bool,
        reason: str,
        action: str,
        rps: int | None = None,
        *,
        source: str = "rules",
        message: str | None = None,
    ) -> None:
        if message is None:
            message = f"{reason} → {action}"
        self.ai_logs.append(
            {
                "ts": int(time.time()),
                "detected": bool(detected),
                "reason": reason,
                "action": action,
                "rps": rps,
                "source": source,
                "message": message,
            }
        )

    def tool_set_max_rps(self, value: int) -> dict:
        value = int(value)
        if value < 1:
            value = 1
        self.max_rps = value
        return {"ok": True, "max_rps": self.max_rps}

    def execute_tool_calls(self, tool_calls: list[dict]) -> list[dict]:
        results = []
        for call in tool_calls:
            name = str(call.get("tool", ""))
            args = call.get("args") or {}
            if name != "set_max_rps":
                results.append({"tool": name, "ok": False, "error": "unknown_tool"})
                continue
            try:
                res = self.tool_set_max_rps(**args) if isinstance(args, dict) else self.tool_set_max_rps(args)
                results.append({"tool": name, **res})
            except Exception as e:
                results.append({"tool": name, "ok": False, "error": str(e)})
        if results:
            self.tool_actions.append({"ts": int(time.time()), "results": results})
        return results

    def decide_tool_calls(self, metrics: dict) -> tuple[list[dict], str]:
        """Return (tool_calls, mode_used). Shape matches Claude tool calls."""
        avg_rps = int(metrics.get("avg_rps", 0))
        threshold = int(metrics.get("threshold", 0))
        current = int(metrics.get("max_rps", 0))
        min_rps = int(metrics.get("min_rps", 1))
        max_rps_cap = int(metrics.get("max_rps_cap", 1000))
        step_down = int(metrics.get("step_down", 50))
        step_up = int(metrics.get("step_up", 25))

        # Pure rule engine (used when AI_MODE != claude, or as Claude fallback).
        mode_used = "rules"

        if avg_rps > threshold:
            new_limit = max(min_rps, current - step_down)
            if new_limit != current:
                return ([{"tool": "set_max_rps", "args": {"value": new_limit}}], mode_used)
            return ([], mode_used)

        new_limit = min(max_rps_cap, current + step_up)
        if new_limit != current:
            return ([{"tool": "set_max_rps", "args": {"value": new_limit}}], mode_used)
        return ([], mode_used)

    def _claude_decide_tool_calls(
        self,
        avg_rps: int,
        threshold: int,
        min_rps: int,
        max_rps_cap: int,
    ) -> tuple[list[dict], str | None]:
        """Ask Claude to emit tool calls. Returns (calls, error_or_none)."""
        client = _anthropic_client()
        if client is None:
            return [], "no_api_key_or_anthropic"

        model = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")
        tools = [
            {
                "name": "set_max_rps",
                "description": (
                    "Set the global maximum requests per second allowed before returning HTTP 429. "
                    "Lower this under attack to stabilize the service; raise when traffic is normal."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "value": {
                            "type": "integer",
                            "description": "New max_rps cap (minimum 1).",
                        }
                    },
                    "required": ["value"],
                },
            }
        ]
        user_text = json.dumps(
            {
                "avg_rps_5s": avg_rps,
                "attack_threshold": threshold,
                "attack_type": getattr(self, "attack_type", "none"),
                "attack_profile": getattr(self, "attack_profile", {}) or {},
                "current_max_rps": self.max_rps,
                "min_max_rps": min_rps,
                "max_max_rps_cap": max_rps_cap,
                "instruction": (
                    "If traffic looks like a sustained attack (avg_rps_5s above threshold), "
                    "call set_max_rps with a LOWER value to defend. "
                    "If traffic is normal, you may INCREASE max_rps toward the cap to recover. "
                    "attack_type hints: distributed_flood = many clients, low top_share; "
                    "single_client_flood = one dominant client; mixed_high_volume = in between. "
                    "Only call the tool when you want to change max_rps."
                ),
            }
        )
        try:
            msg = client.messages.create(
                model=model,
                max_tokens=512,
                tools=tools,
                messages=[{"role": "user", "content": user_text}],
            )
        except Exception as e:
            return [], str(e)

        calls: list[dict] = []
        for block in getattr(msg, "content", []) or []:
            if isinstance(block, dict):
                btype = block.get("type")
                name = block.get("name", "")
                inp = block.get("input", {})
            else:
                btype = getattr(block, "type", None)
                name = getattr(block, "name", "") or ""
                inp = getattr(block, "input", {}) or {}
            if btype != "tool_use":
                continue
            if name == "set_max_rps" and isinstance(inp, dict) and "value" in inp:
                calls.append({"tool": "set_max_rps", "args": {"value": int(inp["value"])}})
        return calls, None

    def _decide_with_fallback(self, metrics: dict) -> tuple[list[dict], str]:
        """Claude if AI_MODE=claude and key present; else rules."""
        avg_rps = int(metrics.get("avg_rps", 0))
        threshold = int(metrics.get("threshold", 0))
        min_rps = int(metrics.get("min_rps", 1))
        max_rps_cap = int(metrics.get("max_rps_cap", 1000))

        if self.ai_mode == "claude":
            calls, err = self._claude_decide_tool_calls(avg_rps, threshold, min_rps, max_rps_cap)
            if err is None and calls:
                self.ai_last_source = "claude"
                return calls, "claude"
            # Fallback to rules on failure or empty response
            self.ai_last_source = "rules"
            calls2, mode = self.decide_tool_calls(metrics)
            return calls2, f"rules_fallback({err or 'claude_empty'})"

        self.ai_last_source = "rules"
        return self.decide_tool_calls(metrics)

    def _ai_loop(self) -> None:
        # Default: trigger defense under heavy Locust (100+ users); idle traffic stays below this.
        threshold = int(os.getenv("AI_RPS_THRESHOLD", "80"))
        min_rps = int(os.getenv("AI_MIN_MAX_RPS", "50"))
        max_rps_cap = int(os.getenv("AI_MAX_MAX_RPS", "1000"))
        step_down = int(os.getenv("AI_STEP_DOWN", "50"))
        step_up = int(os.getenv("AI_STEP_UP", "25"))

        while True:
            time.sleep(2.0)
            now_sec = int(time.time())
            with self._lock:
                self._rollover_to(now_sec)
                recent = list(self.metrics_window)[-5:]
                avg_rps = int(sum(x["rps"] for x in recent) / max(1, len(recent)))

                if avg_rps > threshold:
                    cstats = self._compute_client_stats(now_sec)
                    atk_type, atk_label = _classify_volume_attack(avg_rps, threshold, cstats)
                    self.attack_detected = True
                    self.attack_type = atk_type if atk_type != "none" else "sustained_high_rps"
                    self.attack_profile = {**cstats, "attack_type": self.attack_type}
                    self.suspicious_pattern = atk_label or f"high_rps_sustained(avg_5s={avg_rps})"
                    self.recovery_state = "degrading"
                    tool_calls, mode_used = self._decide_with_fallback(
                        {
                            "avg_rps": avg_rps,
                            "threshold": threshold,
                            "max_rps": self.max_rps,
                            "min_rps": min_rps,
                            "max_rps_cap": max_rps_cap,
                            "step_down": step_down,
                            "step_up": step_up,
                            "attack_type": self.attack_type,
                            "attack_profile": self.attack_profile,
                        }
                    )
                    results = self.execute_tool_calls(tool_calls) if tool_calls else []
                    if results:
                        new_max = self.max_rps
                        msg = (
                            f"{self.attack_type}: spike (avg_5s={avg_rps}) → reduced max_rps to {new_max} "
                            f"({mode_used})"
                        )
                        self._ai_log(
                            True,
                            self.suspicious_pattern,
                            f"{mode_used}: {results}",
                            rps=avg_rps,
                            source=self.ai_last_source,
                            message=msg,
                        )
                else:
                    if self.attack_detected:
                        self.recovery_state = "recovering"
                    self.attack_detected = False
                    self.attack_type = "none"
                    self.attack_profile = {}
                    self.suspicious_pattern = ""
                    tool_calls, mode_used = self._decide_with_fallback(
                        {
                            "avg_rps": avg_rps,
                            "threshold": threshold,
                            "max_rps": self.max_rps,
                            "min_rps": min_rps,
                            "max_rps_cap": max_rps_cap,
                            "step_down": step_down,
                            "step_up": step_up,
                        }
                    )
                    results = self.execute_tool_calls(tool_calls) if tool_calls else []
                    if results:
                        new_max = self.max_rps
                        msg = f"Traffic normal (avg_5s={avg_rps}) → raised max_rps to {new_max} ({mode_used})"
                        self._ai_log(
                            False,
                            "traffic_normal",
                            f"{mode_used}: {results}",
                            rps=avg_rps,
                            source=self.ai_last_source,
                            message=msg,
                        )
                    if self.recovery_state == "recovering" and avg_rps < max(1, threshold // 2):
                        self.recovery_state = "stable"

    def ensure_started(self) -> None:
        if self._threads_started:
            return
        with self._threads_lock:
            if self._threads_started:
                return
            Thread(target=self._metrics_collector_loop, daemon=True).start()
            Thread(target=self._ai_loop, daemon=True).start()
            self._threads_started = True

    # ---------------------------
    # Flask wiring helpers
    # ---------------------------
    def before_request(self, client_ip: str = "unknown") -> tuple[str, int, dict] | None:
        """Return a Flask-style response tuple if blocked, else None.

        ``client_ip`` should be from :func:`client_ip_for_defense` so simulated
        Locust IPs (X-Forwarded-For) are used instead of the TCP peer only.
        """
        self.ensure_started()

        with self._active_lock:
            self.active_requests += 1

        now_sec = int(time.time())
        with self._lock:
            self.request_count += 1
            self._rollover_to(now_sec)
            self._sec_total += 1

            self._sec_ip_counts[client_ip] = self._sec_ip_counts.get(client_ip, 0) + 1
            self._ip_ring.append((now_sec, client_ip))
            cut = now_sec - self.METRICS_WINDOW_SECONDS
            while self._ip_ring and self._ip_ring[0][0] < cut:
                self._ip_ring.popleft()

            if self.max_rps_per_ip > 0 and self._sec_ip_counts[client_ip] > self.max_rps_per_ip:
                self.blocked_count += 1
                self._sec_blocked += 1
                return (
                    "<h3>429 — Too Many Requests</h3><p>Per-client rate limit (simulated IP). Please retry in a moment.</p>",
                    429,
                    {"Content-Type": "text/html"},
                )

            if self._sec_total > self.max_rps:
                self.blocked_count += 1
                self._sec_blocked += 1
                return (
                    "<h3>429 — Too Many Requests</h3><p>AI defense is rate limiting traffic. Please retry in a moment.</p>",
                    429,
                    {"Content-Type": "text/html"},
                )

            self.allowed_count += 1
            self._sec_allowed += 1

        return None

    def after_request(self) -> None:
        with self._active_lock:
            self.active_requests = max(0, self.active_requests - 1)

    def template_context(self) -> dict:
        return {
            "active_requests": self.active_requests,
            "max_rps": self.max_rps,
            "blocked_count": self.blocked_count,
            "allowed_count": self.allowed_count,
            "attack_detected": bool(self.attack_detected),
            "recovery_state": self.recovery_state,
        }

    def build_state(self) -> dict:
        now_sec = int(time.time())
        with self._lock:
            self._rollover_to(now_sec)
            finalized = {m["ts"]: m for m in list(self.metrics_window)}

            allowed_series = []
            blocked_series = []
            start = now_sec - (self.METRICS_WINDOW_SECONDS - 1)
            for sec in range(start, now_sec + 1):
                if sec == self._current_sec:
                    allowed_series.append(self._sec_allowed)
                    blocked_series.append(self._sec_blocked)
                else:
                    m = finalized.get(sec) or {"allowed": 0, "blocked": 0}
                    allowed_series.append(m["allowed"])
                    blocked_series.append(m["blocked"])

            current_allowed = allowed_series[-1] if allowed_series else 0
            current_blocked = blocked_series[-1] if blocked_series else 0
            current_total = current_allowed + current_blocked

            win_start = now_sec - (self.METRICS_WINDOW_SECONDS - 1)
            top_clients = [
                {"ip": ip, "requests": n}
                for ip, n in Counter(
                    ip for ts, ip in self._ip_ring if ts >= win_start
                ).most_common(12)
            ]

            return {
                "ai_last_source": self.ai_last_source,
                "traffic": {
                    "current_rps_total": current_total,
                    "current_rps_allowed": current_allowed,
                    "current_rps_blocked": current_blocked,
                    "allowed_rps_series": allowed_series,
                    "blocked_rps_series": blocked_series,
                },
                "detection": {
                    "attack_detected": bool(self.attack_detected),
                    "attack_type": self.attack_type,
                    "attack_profile": dict(self.attack_profile) if self.attack_profile else {},
                    "suspicious_pattern": self.suspicious_pattern,
                    "recovery": self.recovery_state,
                },
                "defense": {
                    "max_rps": self.max_rps,
                    "max_rps_per_ip": self.max_rps_per_ip,
                    "blocked_ip_count": self.blocked_ip_count,
                    "blocked_sources": self.blocked_count,
                    "top_clients": top_clients,
                },
                "latency": {"active_requests": self.active_requests},
                "logs": list(self.ai_logs),
                "tool_actions": list(self.tool_actions),
            }


defender = Defender()

