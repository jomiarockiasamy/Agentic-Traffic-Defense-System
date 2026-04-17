# Locust attack presets (demo)

Point Locust at your Flask host (match `PORT`, default `5000`).

## Terminal 1 — Flask

```bash
cd demo
python3 -m pip install -r requirements.txt
PORT=5001 python3 app.py
```

Optional slowdown under load:

```bash
DEMO_MODE=1 PORT=5001 python3 app.py
```

## Simulated client IPs (Locust)

Locust sends traffic from **one machine**, so TCP source IPs are identical. The `locustfile.py` adds a random **`X-Forwarded-For`** on each request; the Flask app reads it when `TRUST_X_FORWARDED_FOR=1` (default) so the defender can show **top clients** and optional **per-IP** limits (`MAX_RPS_PER_IP`). For a real deployment behind a trusted proxy you would only trust this header from that proxy.

When load is above `AI_RPS_THRESHOLD`, the defender **classifies** the spike using the last-60s client mix (simulated IPs):

| `attack_type` | Meaning (tunable via env) |
|----------------|---------------------------|
| `distributed_flood` | Many distinct clients, no single dominant share (`ATTACK_DIST_MIN_IPS`, `ATTACK_DIST_MAX_TOP_SHARE`) |
| `single_client_flood` | One client dominates (`ATTACK_SINGLE_MIN_TOP_SHARE`) or very few clients under high volume |
| `mixed_high_volume` | High RPS but concentration is in between |
| `sustained_high_rps` | Not enough samples yet to classify |

See `detection` in `GET /state` and the monitor’s **volume pattern** line.

## Terminal 2 — Locust (spike: ~100–300 users, 10–20 spawn/sec, 1–2 min)

```bash
cd attacker
python3 -m pip install locust
locust -f locustfile.py \
  --host=http://127.0.0.1:5001 \
  --users 200 \
  --spawn-rate 15 \
  --run-time 120s \
  --headless
```

Tweak:

- `--users` between `100` and `300`
- `--spawn-rate` between `10` and `20`
- `--run-time` `60s`–`120s`

## Verify backend while attack runs

```bash
curl -s http://127.0.0.1:5001/state | python3 -m json.tool
```

You want: `detection.attack_detected`, rising `traffic.blocked_rps_series`, `defense.max_rps` adjusting, non-empty `logs`.

## Live dashboard (Chart.js)

Open in a browser:

`http://127.0.0.1:5001/monitor`

## Claude-driven defense (optional)

```bash
export ANTHROPIC_API_KEY="your-key"
export AI_MODE=claude
PORT=5001 python3 app.py
```

If the API fails, the defender **falls back** to the rule-based loop automatically.
