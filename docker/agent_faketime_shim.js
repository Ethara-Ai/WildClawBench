// WildClawBench agent clock shim.
//
// Loaded into the OpenClaw agent process via NODE_OPTIONS="--require <this>".
// OpenClaw builds its per-turn wake-up stamp ("[Www YYYY-MM-DD HH:MM UTC] ...")
// from `new Date()` on the container's real host clock. For persona tasks whose
// simulated window is months away from the real run date, that stamp (and any
// Date the agent's own tools read) contradicts the persona timeline the prompt
// asserts. This shim shifts wall-clock Date reads to the simulated persona time.
//
// Design constraints:
//   * Only `Date` is shifted. Node's timers (setTimeout/setInterval) and request
//     timeouts use libuv's MONOTONIC clock, not Date, so retry/timeout budgets
//     stay in real seconds — a far-future shift does not stall or fast-forward
//     the run.
//   * A single fixed offset is applied, so simulated time still ADVANCES in real
//     time from the base anchor (not frozen).
//   * The anchor is RE-READABLE at runtime. Multi-turn tasks declare a distinct
//     timestamp per turn (prompts.json turns[i].timestamp), and the harness
//     re-anchors at each turn boundary. Env vars cannot be mutated on a running
//     container, so the live value is read from WCB_FAKE_CLOCK_FILE; the env
//     var remains the initial value and the fallback. Each re-anchor restarts
//     the "advances in real time" clock from the new instant.
//   * A Proxy over the real Date preserves `instanceof Date`, the prototype
//     chain, and the static surface (Date.parse / Date.UTC / etc.), so only
//     the "what time is it now" reads change.
//
// Opt-in: no-op unless WCB_FAKE_CLOCK_EPOCH_MS is set to a finite epoch (ms).
// Kill switch: WCB_DISABLE_AGENT_CLOCK_SIM=1 forces the no-op path.
(function installAgentClockShim() {
  if (process.env.WCB_DISABLE_AGENT_CLOCK_SIM === "1") return;

  const raw = process.env.WCB_FAKE_CLOCK_EPOCH_MS;
  if (!raw) return;

  const target = Number(raw);
  if (!Number.isFinite(target)) return;

  const fs = require("fs");
  const RealDate = Date;
  const EPOCH_FILE = process.env.WCB_FAKE_CLOCK_FILE || "/opt/wcb/clock_epoch";
  // Re-stat at most this often: Date.now() is hot, a syscall per call is not
  // acceptable. Turn boundaries are seconds apart at minimum, so 1s is ample.
  const RESTAT_INTERVAL_MS = 1000;

  // (anchorTarget, anchorReal) define the mapping. simulated = anchorTarget +
  // (realNow - anchorReal). Re-anchoring rewrites both, which is what makes a
  // per-turn jump land exactly on the declared instant.
  let anchorTarget = target;
  let anchorReal = RealDate.now();
  let lastStat = 0;
  let lastMtimeMs = -1;

  function maybeReanchor() {
    const realNow = RealDate.now();
    if (realNow - lastStat < RESTAT_INTERVAL_MS) return;
    lastStat = realNow;
    try {
      const st = fs.statSync(EPOCH_FILE);
      if (st.mtimeMs === lastMtimeMs) return;
      const next = Number(fs.readFileSync(EPOCH_FILE, "utf8").trim());
      lastMtimeMs = st.mtimeMs;
      if (!Number.isFinite(next) || next === anchorTarget) return;
      anchorTarget = next;
      anchorReal = realNow;
      if (process.env.WCB_FAKE_CLOCK_DEBUG === "1") {
        // eslint-disable-next-line no-console
        console.error(
          `[wcb-clock-shim] re-anchored: simulated_now=${new RealDate(next).toISOString()}`
        );
      }
    } catch (_) {
      // File absent/unreadable -> keep the current anchor (env-only mode).
    }
  }

  const shiftedNow = () => {
    maybeReanchor();
    return anchorTarget + (RealDate.now() - anchorReal);
  };

  const FakeDate = new Proxy(RealDate, {
    // `new Date()` with no args -> simulated now; any explicit args pass through.
    construct(Target, args) {
      if (args.length === 0) return new Target(shiftedNow());
      return new Target(...args);
    },
    // `Date()` called as a plain function returns a string of "now".
    apply() {
      return new RealDate(shiftedNow()).toString();
    },
    get(Target, prop, receiver) {
      if (prop === "now") return shiftedNow;
      return Reflect.get(Target, prop, receiver);
    },
  });

  globalThis.Date = FakeDate;

  if (process.env.WCB_FAKE_CLOCK_DEBUG === "1") {
    // eslint-disable-next-line no-console
    console.error(
      `[wcb-clock-shim] active: offset=${offset}ms simulated_now=${new FakeDate().toISOString()}`
    );
  }
})();
