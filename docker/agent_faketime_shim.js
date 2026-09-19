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
//   * The anchor file is authoritative for BOTH activation and re-anchoring.
//     cron — and anything else started from a scrubbed environment (`su -`,
//     systemd units, `env -i`) — does not inherit the container's env, so
//     NODE_OPTIONS and WCB_FAKE_CLOCK_EPOCH_MS are both dropped. Gating
//     activation on the env var alone silently put every such process back on
//     the REAL host clock. The file is read at load time too, and its default
//     path matches the harness constant so a fully scrubbed process still
//     finds it.
//   * The anchor instant is the file's MTIME, not the moment this process
//     happened to read it. Simulated time is a shared mapping: any process that
//     adopts anchor A must land on the same instant as one that adopted A
//     earlier. Anchoring on read-time made a job spawned N seconds after the
//     harness wrote the anchor report simulated time N seconds behind the
//     agent's own view of it.
//   * A Proxy over the real Date preserves `instanceof Date`, the prototype
//     chain, and the static surface (Date.parse / Date.UTC / etc.), so only
//     the "what time is it now" reads change.
//
// Opt-in: no-op unless an anchor is resolvable — WCB_FAKE_CLOCK_EPOCH_MS set to
// a finite epoch (ms), or a readable anchor file holding one.
// Kill switch: WCB_DISABLE_AGENT_CLOCK_SIM=1 forces the no-op path.
(function installAgentClockShim() {
  if (process.env.WCB_DISABLE_AGENT_CLOCK_SIM === "1") return;

  const fs = require("fs");
  const RealDate = Date;
  // Keep this default in sync with docker_utils.AGENT_SIM_CLOCK_FILE: a
  // scrubbed-env process has no WCB_FAKE_CLOCK_FILE to point the way.
  const EPOCH_FILE = process.env.WCB_FAKE_CLOCK_FILE || "/opt/wcb/clock_epoch";
  // Re-stat at most this often: Date.now() is hot, a syscall per call is not
  // acceptable. Turn boundaries are seconds apart at minimum, so 1s is ample.
  const RESTAT_INTERVAL_MS = 1000;

  // The anchor file carries the target epoch; its mtime is the REAL instant
  // that anchor took effect. Both halves are needed, and both come from the
  // file, so every reader derives an identical mapping.
  function readAnchorFile() {
    try {
      const st = fs.statSync(EPOCH_FILE);
      const target = Number(fs.readFileSync(EPOCH_FILE, "utf8").trim());
      if (!Number.isFinite(target)) return null;
      // mtimeMs carries sub-ms precision; Date.now() is contractually integral,
      // and callers do parse its digits (OpenClaw stamps, JSON round-trips).
      return { target, real: Math.floor(st.mtimeMs) };
    } catch (_) {
      return null; // Absent/unreadable -> env-only mode.
    }
  }

  // (anchorTarget, anchorReal) define the mapping. simulated = anchorTarget +
  // (realNow - anchorReal). Re-anchoring rewrites both, which is what makes a
  // per-turn jump land exactly on the declared instant.
  let anchorTarget;
  let anchorReal;
  let lastStat = 0;
  let lastMtimeMs = -1;

  const initial = readAnchorFile();
  if (initial !== null) {
    // File wins: it is the live value the harness re-writes per turn.
    anchorTarget = initial.target;
    anchorReal = initial.real;
    lastMtimeMs = initial.real;
  } else {
    const raw = process.env.WCB_FAKE_CLOCK_EPOCH_MS;
    if (!raw) return;
    const target = Number(raw);
    if (!Number.isFinite(target)) return;
    // Turn-0 env anchor: the container was created at this instant.
    anchorTarget = target;
    anchorReal = RealDate.now();
  }

  function maybeReanchor() {
    const realNow = RealDate.now();
    if (realNow - lastStat < RESTAT_INTERVAL_MS) return;
    lastStat = realNow;
    const next = readAnchorFile();
    if (next === null) return; // Keep the current anchor.
    // mtime is the dedup key: a rewrite of the SAME epoch is still a new
    // anchor (it restarts "advances in real time" from that instant), and
    // skipping it would desync us from a process that starts afterwards.
    if (next.real === lastMtimeMs) return;
    lastMtimeMs = next.real;
    anchorTarget = next.target;
    anchorReal = next.real;
    if (process.env.WCB_FAKE_CLOCK_DEBUG === "1") {
      // eslint-disable-next-line no-console
      console.error(
        `[wcb-clock-shim] re-anchored: simulated_now=${new RealDate(next.target).toISOString()}`
      );
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
      `[wcb-clock-shim] active: anchor=${new RealDate(anchorTarget).toISOString()} ` +
        `source=${initial !== null ? EPOCH_FILE : "env"} ` +
        `simulated_now=${new RealDate(shiftedNow()).toISOString()}`
    );
  }
})();
